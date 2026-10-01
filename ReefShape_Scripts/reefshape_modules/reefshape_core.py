"""
ReefShape workflow core -- the processing pipeline, with no user interface.

This is the single implementation of the ReefShape underwater photogrammetry
workflow. Two front ends drive it:

  * 01_full_reefshape_workflow.py -- the Metashape menu dialog
  * ReefShape_Batch/worker/run_job.py -- the headless batch worker

Keeping one implementation matters because the alternative is two copies that
drift: a fix made for the batch runner that never reaches the menu script, or
worse, the reverse.

Nothing here imports Qt, and nothing calls `Metashape.app` for anything
interactive. That is what makes the module usable under `metashape.exe -r`,
where there is no window to put a message box on and no user to answer it.
Everything the dialog would have asked or announced goes through two channels
instead:

  * `Reporter` -- progress, log lines and non-fatal warnings flow outward.
  * `WorkflowSettings` -- every decision is made before the run starts.

A related principle: this module prefers stopping usefully over failing. When
referencing has not worked, alignment and the mesh are still saved and the run
returns STOPPED_FOR_MANUAL_REFERENCING, so the expensive work survives and a
re-run resumes from the DEM. When a boundary is missing, the exports that
need one are skipped and the rest still happen. In an unattended batch, a job
that fails outright wastes hours and can take the queue behind it down with
it; one that stops with a clear warning does not.

Note the explicit `import os`. Metashape's *GUI* script host injects `os` into
script globals, which is why the menu scripts have historically got away with
using `os.path` while only importing `from os import path`. The headless
runner does not inject it -- verified -- so a module that relies on that gift
works from the menu and dies in a batch.
"""

import csv
import os
import re
import statistics
from datetime import datetime

import Metashape


REEFSHAPE_VERSION = "1.3"

# Camera alignment accuracy. 1 == "High" in the GUI.
ALIGN_QUALITY = 1

# Tie-point filter thresholds used during camera optimization.
RECONSTRUCTION_UNCERTAINTY = 25.0
PROJECTION_ACCURACY = 15.0

# The stored DEM is rebuilt at this multiple of the orthomosaic's cell size.
# A DEM at full ortho resolution is only needed to keep resampling artifacts
# out of the ortho while it is being built; keeping it afterwards costs a lot
# of disk for no downstream benefit.
DEM_RESAMPLE_FACTOR = 4

# Default-resolution mode has no ratio to work from: the stored DEM is whatever
# Metashape picks for the mesh. A DEM still within this multiple of the
# orthomosaic's pixel size was built to feed the orthomosaic and has not been
# shrunk yet; the default for a mesh is several times coarser than that.
DEFAULT_MODE_UNSHRUNK_FACTOR = 2.0

# In default mode the DEM is built at an estimate of the orthomosaic's pixel
# size, scaled by this so it errs fine rather than coarse -- a finer DEM only
# costs time, a coarser one costs ortho quality. It is deleted afterwards.
DEFAULT_MODE_DEM_MARGIN = 0.8
# How much coarser than the real orthomosaic the DEM may turn out before the
# run warns about it.
DEFAULT_MODE_DEM_TOLERANCE = 1.25

# Where the plot boundary comes from.
#
# Markers is the default and the best option wherever corner targets exist:
# the boundary is tied to the plot itself, so it is identical every visit and
# timepoints stay directly comparable. Photo coverage follows where the
# photographer actually swam, which shifts between visits. None skips the
# boundary entirely -- and with it the TagLab exports, which have nothing to
# clip to.
BOUNDARY_MARKERS = "markers"
BOUNDARY_PHOTOS = "photos"
BOUNDARY_NONE = "none"

BOUNDARY_SOURCES = [
    ("Corner markers (convex hull)", BOUNDARY_MARKERS),
    ("Photo coverage", BOUNDARY_PHOTOS),
    ("Do not create a boundary", BOUNDARY_NONE),
]

# Outcomes of run_workflow.
COMPLETED = "completed"
STOPPED_FOR_MANUAL_REFERENCING = "stopped_for_manual_referencing"
# The user declined to carry on with a photo-coverage boundary. Everything
# built so far is saved, so a re-run resumes at the boundary.
STOPPED_FOR_BOUNDARY = "stopped_for_boundary"


class WorkflowError(Exception):
    """A condition that stops processing and needs the user to act.

    Distinct from a warning: warnings are collected and reported at the end
    while the run continues, whereas raising this aborts. Front ends turn it
    into a dialog or a failed job as appropriate.
    """


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

class Reporter:
    """Where progress, log output and warnings go.

    The default implementation prints, which is exactly right under
    `metashape.exe -r` where stdout is captured by the parent process. The
    dialog subclasses it to drive a progress bar; the batch worker subclasses
    it to emit machine-readable protocol lines.

    Subclasses should override the hooks they care about and leave the rest.
    """

    def step(self, name, index=None, total=None):
        """A named stage of the pipeline is starting."""
        if index and total:
            print(" --- [{}/{}] {} --- ".format(index, total, name))
        else:
            print(" --- {} --- ".format(name))

    def progress(self, percent):
        """Progress within the current step, 0-100."""

    def info(self, message):
        """A log line worth keeping but not worth interrupting anyone for."""
        print(message)

    def warn(self, message):
        """Something was skipped or degraded. Collected and surfaced at the end."""
        print("WARNING: {}".format(message))

    def confirm(self, title, message):
        """Ask an OK/Cancel question. Returns True for OK.

        Non-interactive by default: with nobody to ask (the batch worker runs
        under `metashape.exe -r`) it logs the question and answers OK, so an
        unattended run carries on exactly as it did before the question
        existed. The menu dialog overrides this to show a real prompt.
        """
        print("{}: continuing without asking (non-interactive run).".format(title))
        return True

    def progress_callback(self):
        """A callable suitable for Metashape's `progress=` keyword.

        Metashape reports fractional percentages many times a second. Throttling
        to whole numbers here keeps a GUI progress bar from repainting itself
        into a slideshow, and keeps the batch protocol from drowning its own
        log in progress lines.
        """
        state = {"last": -1}

        def callback(percent):
            whole = int(percent)
            if whole != state["last"]:
                state["last"] = whole
                self.progress(whole)

        return callback


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

class WorkflowSettings:
    """Everything the workflow needs to know, decided before it starts.

    Field names match `ReefShape_Batch/batch/models.py` so that a batch job's
    JSON maps across without translation, and so the two stay legible against
    each other.

    `target_type` arrives as a Metashape constant *name* rather than the
    constant, because the batch GUI runs in a process with no Metashape module
    and cannot name one. `resolve_target_type` turns it back into the real
    thing on this side of the boundary.
    """

    def __init__(self,
                 crs=None,
                 generic_preselection=True,
                 mesh_quality_downscale=4,
                 vertex_colors=False,
                 ortho_resolution=0.0005,
                 use_default_resolution=False,
                 georef_enabled=False,
                 target_type="CircularTarget12bit",
                 scalebar_path="",
                 georef_path="",
                 ref_formatting=None,
                 boundary_source=BOUNDARY_MARKERS,
                 output_dir="",
                 export_report=True,
                 export_gis=True,
                 export_taglab=True,
                 export_taglab_legacy=False,
                 project_name=""):
        self.crs = crs
        self.generic_preselection = generic_preselection
        self.mesh_quality_downscale = mesh_quality_downscale
        self.vertex_colors = vertex_colors
        self.ortho_resolution = ortho_resolution
        self.use_default_resolution = use_default_resolution

        self.georef_enabled = georef_enabled
        self.target_type = target_type
        self.scalebar_path = scalebar_path
        self.georef_path = georef_path
        # Column layout of the georeferencing CSV, 1-based, in the order
        # referenceModel expects: label, x, y, z, x_acc, y_acc, z_acc, skip.
        self.ref_formatting = ref_formatting or [1, 3, 2, 4, 5, 5, 6, 2]
        # See BOUNDARY_SOURCES.
        self.boundary_source = boundary_source

        self.output_dir = output_dir
        self.export_report = export_report
        self.export_gis = export_gis
        # TagLab exports additionally require a boundary polygon to clip to;
        # without one they are skipped with a warning rather than produced
        # uncropped, which would have to be re-exported before use anyway.
        self.export_taglab = export_taglab
        # The same products in the form unmodified TagLab accepts: LZW, split
        # into 32767 px blocks, DEM on the orthomosaic's grid. Written to its
        # own folder, and subject to the same boundary requirement.
        self.export_taglab_legacy = export_taglab_legacy

        self.project_name = project_name

    def effective_ortho_resolution(self):
        """0 hands the choice to Metashape, matching the dialog's checkbox."""
        return 0.0 if self.use_default_resolution else self.ortho_resolution

    def resolve_target_type(self):
        """Map the target-type name to its Metashape constant.

        Accepts an actual constant too, and passes it straight through: the
        menu dialog already holds one (it can import Metashape), so making it
        convert to a string just for us to convert back would be pointless.
        """
        if not isinstance(self.target_type, str):
            return self.target_type
        constant = getattr(Metashape, self.target_type, None)
        if constant is None:
            raise WorkflowError(
                "Unknown marker target type: {!r}".format(self.target_type))
        return constant

    @classmethod
    def from_job_dict(cls, job):
        """Build settings from a batch job dict (see batch/models.py).

        Tolerant of missing sections so a hand-written or older job file still
        runs on defaults rather than raising.
        """
        processing = job.get("processing") or {}
        georef = job.get("georef") or {}
        export = job.get("export") or {}

        crs_wkt = processing.get("crs_wkt")
        crs = Metashape.CoordinateSystem(crs_wkt) if crs_wkt else None

        # Mirrors models.mesh_downscale: the dialog's combo index feeds
        # 2 ** index, so the quality names map onto that progression.
        quality_map = {"Ultra High": 1, "High": 2, "Medium": 4,
                       "Low": 8, "Lowest": 16}
        downscale = quality_map.get(processing.get("mesh_quality", "Medium"), 4)

        ref_formatting = [
            georef.get("col_label", 1),
            georef.get("col_x", 3),
            georef.get("col_y", 2),
            georef.get("col_z", 4),
            georef.get("col_x_accuracy", 5),
            georef.get("col_y_accuracy", 5),
            georef.get("col_z_accuracy", 6),
            georef.get("skip_rows", 2),
        ]

        project_path = job.get("project_path", "")
        return cls(
            crs=crs,
            generic_preselection=processing.get("generic_preselection", True),
            mesh_quality_downscale=downscale,
            vertex_colors=processing.get("vertex_colors", False),
            ortho_resolution=processing.get("ortho_resolution", 0.0005),
            use_default_resolution=processing.get("use_default_resolution", False),
            georef_enabled=georef.get("enabled", False),
            target_type=georef.get("target_type", "CircularTarget12bit"),
            scalebar_path=georef.get("scalebar_path", ""),
            georef_path=georef.get("georef_path", ""),
            ref_formatting=ref_formatting,
            boundary_source=processing.get("boundary_source", BOUNDARY_MARKERS),
            output_dir=export.get("output_dir", "") or os.path.dirname(project_path),
            export_report=export.get("report", True),
            export_gis=export.get("gis_outputs", True),
            export_taglab=export.get("taglab_outputs", True),
            export_taglab_legacy=export.get("taglab_legacy_outputs", False),
            project_name=os.path.splitext(os.path.basename(project_path))[0],
        )


class WorkflowResult:
    """What happened. Returned rather than announced, so callers can present it."""

    def __init__(self, status, warnings=None, outputs=None):
        self.status = status
        self.warnings = warnings or []
        self.outputs = outputs or []

    @property
    def completed(self):
        return self.status == COMPLETED


# --------------------------------------------------------------------------
# Scaling and georeferencing
# --------------------------------------------------------------------------

def create_scalebars(chunk, path):
    """Create scalebars from a CSV of `marker1,marker2,distance,accuracy` rows.

    Returns None on success or an error string on failure, matching the
    original's contract. Markers named in the file but absent from the chunk
    are reported and skipped rather than treated as fatal -- a plot commonly
    carries fewer scalebars than the shared master scalebar file lists.
    """
    scalebar_count = len(chunk.scalebars)
    if len(chunk.markers) == 0:
        raise WorkflowError("No markers found! Unable to create scalebars.")
    if scalebar_count > 0:
        print("There are already {} scalebars in this project.".format(scalebar_count))

    missing = []
    try:
        with open(path) as handle:
            for line in handle:
                if not line.strip():
                    continue
                point1, point2, dist, acc = line.split(",")
                point1, point2 = point1.strip(), point2.strip()

                # Reuse an existing scalebar between the same pair if there is
                # one, in either order, rather than adding a duplicate.
                existing = None
                for scalebar in chunk.scalebars:
                    if scalebar.label in (point1 + "_" + point2,
                                          point2 + "_" + point1):
                        existing = scalebar
                        break

                if existing is None:
                    marker1 = _find_marker(chunk, point1)
                    marker2 = _find_marker(chunk, point2)
                    if marker1 is None or marker2 is None:
                        for label, marker in ((point1, marker1), (point2, marker2)):
                            if marker is None:
                                missing.append(label)
                        continue
                    existing = chunk.addScalebar(marker1, marker2)

                existing.reference.distance = float(dist)
                existing.reference.accuracy = float(acc)
    except WorkflowError:
        raise
    except Exception:
        return "Script error: There was a problem reading scalebar data\n"

    if missing:
        print("Scalebar markers not found in this chunk (skipped): {}".format(
            ", ".join(sorted(set(missing)))))
    return None


def _find_marker(chunk, label):
    for marker in chunk.markers:
        if marker.label == label:
            return marker
    return None


def reference_model(chunk, path, formatting):
    """Import marker georeferencing from a CSV with a user-specified layout.

    Rewrites the user's file into a normalized temporary CSV before handing it
    to Metashape, which is what allows arbitrary column orders to be supported
    without asking the user to rearrange their spreadsheet.

    Returns None on success or an error string on failure.
    """
    # Config is 1-based (it mirrors spreadsheet columns as the user sees them).
    label_col = formatting[0] - 1
    x_col = formatting[1] - 1
    y_col = formatting[2] - 1
    z_col = formatting[3] - 1
    x_acc_col = formatting[4] - 1
    y_acc_col = formatting[5] - 1
    z_acc_col = formatting[6] - 1
    skip = formatting[7] - 1

    new_path = path[:-4] + "_reformat.csv"
    try:
        rows = []
        with open(path) as handle:
            for _ in range(skip):
                handle.readline()
            for line in handle:
                if not line.strip():
                    continue
                fields = line.strip().split(",")
                row = [fields[label_col], fields[x_col], fields[y_col],
                       fields[z_col], fields[x_acc_col], fields[y_acc_col],
                       fields[z_acc_col]]
                for item in row[1:]:
                    try:
                        float(item)
                    except ValueError:
                        print("Script error: '{}' cannot be read as a coordinate "
                              "value. Your column assignments may be incorrect."
                              .format(item))
                        raise
                rows.append(row)

        with open(new_path, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["label", "x", "y", "z", "X_acc", "Y_acc", "Z_acc"])
            writer.writerows(rows)

        chunk.importReference(
            path=new_path, format=Metashape.ReferenceFormatCSV, delimiter=",",
            columns="nxyzXYZ", skip_rows=skip, crs=chunk.crs,
            ignore_labels=False, create_markers=False, threshold=0.1,
            shutter_lag=0)
    except Exception:
        return "Script error: There was a problem reading georeferencing data\n"
    finally:
        # Always clean up the intermediate file, including on the error path,
        # so a failed run doesn't litter the user's georeferencing folder with
        # half-written _reformat.csv files that look like real inputs.
        try:
            os.remove(new_path)
        except OSError:
            pass

    return None


# --------------------------------------------------------------------------
# Alignment and optimization
# --------------------------------------------------------------------------

# Keys that only a real optimizeCameras() call writes.
#
# The obvious test -- "does chunk.meta have any OptimizeCameras/ key?" -- is
# wrong, and wrong in the direction that silently skips work. alignCameras
# writes `OptimizeCameras/sigma0 = 0` as a placeholder, so the bare prefix is
# already present the moment alignment finishes.
#
# Measured on two real projects. A chunk that had only been aligned carried
# exactly one such key, sigma0 = 0. A chunk that had genuinely been optimized
# carried sigma0 = 0.394 plus fit_flags, duration and adaptive_fitting. Only
# the latter group is evidence.
OPTIMIZED_MARKER_KEYS = ("OptimizeCameras/fit_flags",
                         "OptimizeCameras/duration")


def is_optimized(chunk):
    """True if camera optimization has genuinely run on this chunk.

    Used to skip re-optimizing a chunk the user already optimized by hand in
    Metashape, or that an earlier run of this workflow processed. Detects
    either, since both go through optimizeCameras().

    Getting this wrong in the "already done" direction costs accuracy without
    any visible symptom -- the workflow simply never filters tie points or
    refines the camera calibration, and still produces a mesh and an
    orthomosaic that look fine. See OPTIMIZED_MARKER_KEYS.
    """
    try:
        keys = set(chunk.meta.keys())
    except (AttributeError, TypeError):
        return False
    return any(key in keys for key in OPTIMIZED_MARKER_KEYS)


def grad_selects_optimization(chunk):
    """Drop high-error tie points, then optimize camera alignment."""
    filt = Metashape.TiePoints.Filter()
    filt.init(chunk, Metashape.TiePoints.Filter.ReconstructionUncertainty)
    filt.removePoints(RECONSTRUCTION_UNCERTAINTY)

    filt = Metashape.TiePoints.Filter()
    filt.init(chunk, Metashape.TiePoints.Filter.ProjectionAccuracy)
    filt.removePoints(PROJECTION_ACCURACY)

    chunk.optimizeCameras(
        fit_f=True, fit_cx=True, fit_cy=True, fit_b1=True, fit_b2=True,
        fit_k1=True, fit_k2=True, fit_k3=True, fit_k4=True,
        fit_p1=True, fit_p2=True, fit_corrections=True,
        adaptive_fitting=False, tiepoint_covariance=False)


# --------------------------------------------------------------------------
# Boundary
# --------------------------------------------------------------------------

def project_markers(chunk, markers):
    """Project markers into shape CRS as [(x, y, marker), ...].

    Markers with no estimated position are dropped -- they were never placed,
    or never converged, so there is no location to build a boundary from.
    """
    transform = chunk.transform.matrix
    shape_crs = chunk.shapes.crs if chunk.shapes else chunk.crs
    projected = []
    for marker in markers:
        if marker.position is None:
            continue
        point = shape_crs.project(transform.mulp(marker.position))
        projected.append((point.x, point.y, marker))
    return projected


def _monotone_chain(points):
    """Convex hull of [(x, y, payload), ...], counter-clockwise.

    Andrew's monotone chain. Hand-rolled rather than taken from scipy or
    shapely because this module is imported on every Metashape startup by
    script 01, and a twenty-line algorithm is not worth making every user
    wait on a pip install for.
    """
    pts = sorted(points, key=lambda p: (p[0], p[1]))
    if len(pts) <= 2:
        return pts

    def half(sequence):
        out = []
        for p in sequence:
            # Pop while the last turn is not a left turn, which drops both
            # interior points and collinear ones. Collinear vertices add
            # nothing to the polygon's shape, so losing them is harmless.
            while len(out) >= 2:
                (x1, y1) = out[-2][0], out[-2][1]
                (x2, y2) = out[-1][0], out[-1][1]
                if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) <= 0:
                    out.pop()
                else:
                    break
            out.append(p)
        return out

    return half(pts)[:-1] + half(list(reversed(pts)))[:-1]


def hull_order(chunk, markers):
    """Order `markers` counter-clockwise around their convex hull.

    This is what removes the need to tell ReefShape which target sits at
    which corner. Stringing markers together in whatever order they were
    listed produces a self-intersecting polygon most of the time -- with four
    corners, 16 of the 24 possible orderings cross -- and the hull is the same
    polygon regardless of input order.

    Convex rather than a concave ordering (angular sort about the centroid)
    on purpose: a marker that is georeferenced but sits *inside* the plot, as
    a control point rather than a corner, is ignored by the hull. An angular
    sort would thread the boundary through it and cut a notch out of the plot.
    The trade is that a genuinely concave plot gets over-covered, which for
    rectangular reef plots does not arise -- and where it does, the
    photo-coverage boundary describes the surveyed area better anyway.
    """
    projected = project_markers(chunk, markers)
    if len(projected) < 3:
        return [entry[2] for entry in projected]
    return [entry[2] for entry in _monotone_chain(projected)]


def corner_markers(chunk):
    """Markers that define the plot outline.

    A marker with a reference location is one whose real-world position was
    imported from the georeferencing file (or, for a revisit, carried over
    from the earlier timepoint), which is exactly the set that marks the plot.
    Scalebar targets are excluded automatically because they carry scale, not
    position -- with no name matching, no digit parsing and no fixed count, so
    a plot with six corners works the same as one with four.
    """
    return [m for m in chunk.markers
            if m.position is not None
            and m.reference is not None
            and m.reference.location is not None]


def create_shape_from_markers(chunk, markers, label="Marker Boundary"):
    """Build an OuterBoundary polygon through `markers`, in the order given.

    Order is taken as authoritative -- `hull_order` supplies it for the
    automatic path, and script 06 supplies the user's own sequence when they
    want a shape the hull would not produce.
    """
    if not chunk:
        print("Empty project, script aborted")
        return False
    if len(markers) < 3:
        print("At least three markers are required to create a boundary; "
              "got {}.".format(len(markers)))
        return False

    transform = chunk.transform.matrix
    if not chunk.shapes:
        chunk.shapes = Metashape.Shapes()
        chunk.shapes.crs = chunk.crs
    shape_crs = chunk.shapes.crs

    coords = [shape_crs.project(transform.mulp(m.position)) for m in markers]

    shape = chunk.shapes.addShape()
    shape.label = label
    shape.geometry.type = Metashape.Geometry.Type.PolygonType
    shape.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
    shape.geometry = Metashape.Geometry.Polygon(coords)
    return True


def boundary_creation(chunk, reporter=None):
    """Create the plot boundary from the chunk's georeferenced markers.

    Takes every marker with a reference location and walks their convex hull,
    so the result does not depend on how the targets were numbered or in what
    order they happen to appear.
    """
    log = reporter.info if reporter is not None else print

    candidates = corner_markers(chunk)
    if len(candidates) < 3:
        log("  only {} georeferenced marker(s) in this chunk; a boundary "
            "needs at least 3.".format(len(candidates)))
        return False

    ordered = hull_order(chunk, candidates)
    if len(ordered) < 3:
        log("  the georeferenced markers are collinear, so they enclose no "
            "area.")
        return False

    if len(ordered) < len(candidates):
        # Interior markers, or collinear ones along an edge. Both are fine to
        # drop -- the polygon still encloses every marker.
        log("  {} of {} georeferenced markers form the plot outline; the rest "
            "fall inside it.".format(len(ordered), len(candidates)))

    log("  boundary from markers: {}".format(
        ", ".join(m.label for m in ordered)))
    return create_shape_from_markers(chunk, ordered)


def find_outer_boundary(chunk):
    """The chunk's OuterBoundary polygon, or None."""
    if not chunk.shapes:
        return None
    for shape in chunk.shapes:
        if shape.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary:
            return shape
    return None


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------

# Ceiling on DEM/orthomosaic size. Calibrated against a real survey: the
# TimsReef2 plot is 65536 x 73727 px (4.8 gigapixels) at 0.5 mm, so this
# leaves roughly 20x headroom for a genuinely large plot while still catching
# a runaway. At 4 bytes per cell, 100 gigapixels is 400 GB for a single band --
# unambiguously not something any machine is going to produce.
MAX_RASTER_CELLS = 1e11


def estimate_raster_cells(chunk, resolution):
    """Rough cell count for a raster covering the chunk at `resolution`.

    Returns (cells, width_m, height_m), or None if the extent cannot be
    determined.

    Works from `chunk.region` -- the reconstruction region, which the workflow
    has just reset to encompass the data -- scaled into metres by the chunk
    transform. Projecting corners through the CRS instead would give degrees
    for a compound geographic CRS, needing a latitude-dependent conversion
    before it could be compared against a resolution in metres; the transform
    scale gives metres directly.
    """
    try:
        if resolution <= 0:
            return None
        if not chunk.transform or not chunk.transform.scale:
            return None
        size = chunk.region.size
        if size is None:
            return None
        scale = chunk.transform.scale
        width = abs(size.x) * scale
        height = abs(size.y) * scale
        return (width / resolution) * (height / resolution), width, height
    except Exception:
        return None


def referencing_problem(chunk, resolution, reporter):
    """Describe why this chunk isn't ready for a DEM, or None if it is.

    Returns a message rather than raising, because the right response is to
    stop cleanly after the mesh rather than to fail the job. Alignment and
    mesh building are the expensive part and their results stay valid; once
    the user supplies the missing referencing, a re-run skips straight past
    them to the DEM. Failing outright would throw that work away, and in a
    batch would do it while twenty more plots waited behind it.

    Two conditions, both meaning the chunk's coordinates cannot be trusted at
    a resolution expressed in metres:

    *No transform scale.* The chunk was never scaled, so its coordinates are
    in arbitrary internal units. This is where a chunk lands when the scalebar
    file names targets that were not detected and there are too few
    georeferenced markers to solve a transform. Observed: a 12-photo chunk
    with one corner marker and no scalebars reached 41 GB resident inside
    buildDem before being killed. A correctly scaled chunk always has a
    scale -- TimsReef2's is 0.368.

    *Absurd extent.* A transform exists but is wrong, typically from markers
    matched to the wrong target numbers, implying a plot kilometres across.

    `resolution` of 0 means Metashape picks the resolution from the data, so
    it cannot be asked for something the machine has no memory for.
    """
    if resolution <= 0:
        return None

    if not chunk.transform or not chunk.transform.scale:
        return (
            "This chunk has no scale, so its coordinates are in arbitrary "
            "units and a DEM at {} m resolution cannot be built. Alignment "
            "and the mesh are complete and have been saved.\n\n"
            "The chunk has {} marker(s) and {} scalebar(s). Scaling needs "
            "scalebars whose targets were actually detected in these photos, "
            "or enough georeferenced markers to solve a transform. Check that "
            "the scalebar file matches the targets in this plot and that "
            "marker detection found them.\n\n"
            "Add the scaling and georeferencing information, then re-run to "
            "continue from the DEM onwards."
            .format(resolution, len(chunk.markers), len(chunk.scalebars)))

    estimate = estimate_raster_cells(chunk, resolution)
    if estimate is None:
        return None
    cells, width, height = estimate

    reporter.info("  region extent: {:.1f} m x {:.1f} m -> {:.2f} gigapixels "
                  "at {} m".format(width, height, cells / 1e9, resolution))

    if cells <= MAX_RASTER_CELLS:
        return None

    return (
        "This chunk's scale implies a plot of {:.0f} m x {:.0f} m, which at "
        "{} m resolution would be a DEM of about {:.0f} gigapixels -- "
        "hundreds of gigabytes of memory. Alignment and the mesh are complete "
        "and have been saved.\n\n"
        "A reef plot this size is almost certainly a scaling problem rather "
        "than a real survey extent. Check that:\n"
        "  - the scalebar file matches the targets actually in this chunk\n"
        "  - the corner markers were detected and georeferenced\n"
        "  - the georeferencing file's column layout is set correctly\n\n"
        "Correct the referencing, then re-run to continue from the DEM "
        "onwards.".format(width, height, resolution, cells / 1e9))


# --------------------------------------------------------------------------
# TagLab project files
# --------------------------------------------------------------------------

def read_tiff_size(path):
    """(width, height) of a TIFF, without any imaging library.

    Handles classic TIFF and BigTIFF, which is required rather than optional:
    these exports set tiff_big, so a large plot comes out as BigTIFF, whose
    header layout differs from classic (8-byte offsets, 20-byte IFD entries).

    Reading the file is the only way to get these numbers honestly. The
    orthomosaic's own width and height describe the *unclipped* raster, while
    the TagLab export is clipped to the plot boundary, so they disagree --
    and TagLab uses the dimensions to place annotations.
    """
    with open(path, "rb") as handle:
        header = handle.read(16)
        if len(header) < 8:
            raise ValueError("{} is too short to be a TIFF".format(path))

        byte_order = header[0:2]
        if byte_order == b"II":
            endian = "<"
        elif byte_order == b"MM":
            endian = ">"
        else:
            raise ValueError("{} is not a TIFF".format(path))

        import struct
        version = struct.unpack(endian + "H", header[2:4])[0]

        if version == 42:            # classic TIFF
            offset = struct.unpack(endian + "I", header[4:8])[0]
            entry_size, count_format, count_size = 12, "H", 2
            value_format, value_size = "I", 4
        elif version == 43:          # BigTIFF
            offset = struct.unpack(endian + "Q", header[8:16])[0]
            entry_size, count_format, count_size = 20, "Q", 8
            value_format, value_size = "Q", 8
        else:
            raise ValueError("{} has unknown TIFF version {}".format(path, version))

        handle.seek(offset)
        entry_count = struct.unpack(
            endian + count_format, handle.read(count_size))[0]

        width = height = None
        for _ in range(entry_count):
            entry = handle.read(entry_size)
            if len(entry) < entry_size:
                break
            tag, field_type = struct.unpack(endian + "HH", entry[:4])
            if tag not in (256, 257):       # ImageWidth, ImageLength
                continue
            raw = entry[entry_size - value_size:]
            # These tags are SHORT (3) or LONG (4); BigTIFF may use LONG8 (16).
            if field_type == 3:
                value = struct.unpack(endian + "H", raw[:2])[0]
            elif field_type == 4:
                value = struct.unpack(endian + "I", raw[:4])[0]
            else:
                value = struct.unpack(endian + value_format, raw[:value_size])[0]
            if tag == 256:
                width = value
            else:
                height = value

        if width is None or height is None:
            raise ValueError("{} has no image dimensions".format(path))
        return width, height


def _taglab_path(path):
    """Absolute path with forward slashes, as TagLab writes them."""
    return os.path.abspath(path).replace(os.sep, "/")


def write_taglab_project(taglab_dir, project_name, chunk, ortho_path,
                         dem_path, ortho_resolution):
    """Write a TagLab project (.json) describing this timepoint.

    Written for every plot, re-photography or not. A single-timepoint project
    opens directly in TagLab, and a revisit can be appended onto an existing
    project from inside TagLab -- which is a better division of labour than
    trying to merge annotation projects from out here, where we cannot see
    what the user has already annotated.

    The schema mirrors a project saved by TagLab itself; empty collections are
    written as TagLab writes them so the file loads without complaint.
    """
    label = chunk.label or "timepoint"
    json_path = os.path.join(
        taglab_dir, "{}_{}.json".format(project_name, label))

    width, height = read_tiff_size(ortho_path)

    # TagLab works in millimetres per pixel; the workflow works in metres.
    # Prefer the orthomosaic's actual resolution over the requested one, which
    # is 0 when Metashape was left to choose.
    resolution_m = ortho_resolution or (
        chunk.orthomosaic.resolution if chunk.orthomosaic else 0.0)
    px_to_mm = "{:g}".format(resolution_m * 1000.0)

    # TagLab wants an ISO date; the ReefShape convention makes the chunk label
    # the capture date already.
    try:
        acquisition_date = datetime.strptime(label, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        acquisition_date = ""

    ortho = _taglab_path(ortho_path)
    project = {
        "filename": _taglab_path(json_path),
        "working_area": None,
        "working_area_angle": 0.0,
        "images": [
            {
                "rect": [0.0, 0.0, 0.0, 0.0],
                "map_px_to_mm_factor": px_to_mm,
                "width": width,
                "height": height,
                "annotations": {"regions": [], "points": []},
                "layers": [],
                "channels": [
                    {"filename": ortho, "type": "RGB"},
                    {"filename": _taglab_path(dem_path), "type": "DEM"},
                ],
                "id": label,
                "name": label,
                "workspace": [],
                "export_dataset_area": [],
                "acquisition_date": acquisition_date,
                "georef_filename": ortho,
                "metadata": {},
                "sampling_areas": [],
                "grid": None,
            }
        ],
        "correspondences": {},
        "genet": {},
        "region_attributes": {"name": "", "description": "", "data": []},
        "spatial_reference_system": None,
        "metadata": {},
        "image_metadata_template": {},
        "markers": {},
    }

    import json
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(project, handle, indent=1)
    return json_path


# Unmodified TagLab refuses images larger than this on either side, and
# refuses JPEG-compressed ones. The legacy export works within both limits.
TAGLAB_LEGACY_BLOCK_PX = 32767
TAGLAB_LEGACY_FOLDER = "taglab_outputs_legacy"


def export_taglab_legacy_products(chunk, output_dir, project_name,
                                  ortho_resolution, lzw, reporter):
    """Write the orthomosaic and DEM in the form unmodified TagLab accepts.

    The standard TagLab export relies on a modified TagLab that takes large,
    JPEG-compressed images. The stock program needs LZW, blocks of at most
    32767 px, and the DEM on the same grid as the orthomosaic -- this is the
    export the workflow produced before that changed, kept for anyone still
    running the stock version.

    The DEM is exported at the orthomosaic's resolution, which resamples the
    stored (coarser) DEM back up. That is fine: both rasters then share a
    pixel grid, so their blocks line up one to one, which is what the stock
    program assumes. Neither JPEG compression nor a single file is possible
    here, so the files are large and numerous; that is the cost of
    compatibility.

    Both rasters take one resolution, resolved here rather than passed through
    as 0. With 0 Metashape picks a resolution per raster from its own data, so
    the two can land on different grids.

    No TagLab project file is written: the output is a set of blocks, not one
    image, so there is no single map to describe.

    Returns the output folder.
    """
    legacy_dir = os.path.join(output_dir, TAGLAB_LEGACY_FOLDER)
    os.makedirs(legacy_dir, exist_ok=True)

    resolution = ortho_resolution or chunk.orthomosaic.resolution
    stem = "{}_{}".format(project_name, chunk.label)

    common = dict(
        resolution=resolution, block_width=TAGLAB_LEGACY_BLOCK_PX,
        block_height=TAGLAB_LEGACY_BLOCK_PX, split_in_blocks=True,
        image_compression=lzw, save_kml=False, save_world=False,
        save_scheme=False, save_alpha=True, image_description="",
        network_links=True, global_profile=False, min_zoom_level=-1,
        max_zoom_level=-1, white_background=True, clip_to_boundary=True)

    chunk.exportRaster(
        path=os.path.join(legacy_dir, stem + ".tif"),
        source_data=Metashape.OrthomosaicData, title="Orthomosaic",
        description="Generated by Agisoft Metashape",
        progress=reporter.progress_callback(), **common)

    chunk.exportRaster(
        path=os.path.join(legacy_dir, stem + "_DEM.tif"),
        source_data=Metashape.ElevationData, nodata_value=-5, title="DEM",
        description="Generated by Agisoft Metashape",
        progress=reporter.progress_callback(), **common)

    reporter.info("  legacy TagLab outputs: {}".format(legacy_dir))
    return legacy_dir


PHOTO_BOUNDARY_PROMPT = (
    "This chunk has no plot boundary, and one could not be built from corner "
    "markers. The legacy TagLab outputs need one, so the workflow will fall "
    "back to a boundary drawn from photo coverage.\n\n"
    "A photo-coverage boundary follows where the photographer swam, so it will "
    "not match the boundary of other timepoints, and the TagLab exports will "
    "not line up properly with earlier ones.\n\n"
    "If this is a repeat visit, first check that the orthomosaic aligns with "
    "the previous timepoint, then use the Copy Boundary tool "
    "(ReefShape > Tools > Copy Boundary) to bring the boundary across from the "
    "earlier chunk.\n\n"
    "Click OK to carry on with a photo-coverage boundary anyway.\n"
    "Click Cancel to stop here. Everything built so far is saved, so you can "
    "fix the boundary and run the workflow again to finish.")


def estimate_default_ortho_resolution(chunk):
    """Estimate the pixel size Metashape will pick for the orthomosaic.

    Returns metres per pixel, or None if it cannot be worked out.

    Needed because the DEM an orthomosaic is built over should be at least as
    fine as the orthomosaic, and with the default resolution Metashape does not
    choose the orthomosaic's pixel size until it builds it -- by which point
    the DEM already exists. There is no API to ask in advance.

    The default is the ground sampling distance of the images: how much
    seafloor one pixel covers, which is the camera's height above the surface
    divided by its focal length in pixels. Both are known once the cameras are
    aligned. Height is the vertical offset between the cameras and the median
    tie point; it is in chunk units, so transform.scale converts it to metres.
    The median over cameras keeps a few strays from moving the answer.

    Assumes the chunk's Z axis is roughly vertical, which holds for the usual
    downward-looking reef survey. Anything it cannot work out returns None and
    the caller falls back to letting Metashape choose.
    """
    try:
        scale = chunk.transform.scale
        points = chunk.tie_points.points if chunk.tie_points else None
        if not scale or not points:
            return None

        step = max(1, len(points) // 2000)
        heights = [points[i].coord.z for i in range(0, len(points), step)
                   if points[i].valid]
        if not heights:
            return None
        floor_z = statistics.median(heights)

        sizes = []
        for camera in chunk.cameras:
            if camera.transform is None or camera.center is None:
                continue
            calibration = camera.sensor.calibration if camera.sensor else None
            if calibration is None or calibration.f <= 0:
                continue
            height = abs(camera.center.z - floor_z) * scale
            if height > 0:
                sizes.append(height / calibration.f)
        return statistics.median(sizes) if sizes else None
    except Exception:
        return None


def storage_dem_resolution(chunk):
    """The resolution the DEM is kept at once the orthomosaic exists."""
    return chunk.orthomosaic.resolution * DEM_RESAMPLE_FACTOR


def dem_shrink_plan(chunk, default_mode=False):
    """(active DEM, target resolution) if the DEM should be shrunk, else None.

    True only when an orthomosaic exists and the active DEM is finer than it
    needs to be for storage. Shared by the up-front question and the step that
    acts on the answer, so the two cannot disagree about whether there is
    anything to ask.

    With an explicit resolution the target is the orthomosaic's pixel size
    times DEM_RESAMPLE_FACTOR, so it follows whatever resolution was chosen.
    With the default resolution (`default_mode`) there is no ratio: the target
    is None, meaning "let Metashape choose for this mesh", and a DEM counts as
    unshrunk while it is still close to the orthomosaic's own resolution.
    """
    if chunk.orthomosaic is None or chunk.elevation is None:
        return None
    current = chunk.elevation

    if default_mode:
        if current.resolution > (chunk.orthomosaic.resolution
                                 * DEFAULT_MODE_UNSHRUNK_FACTOR):
            return None
        return current, None

    target = storage_dem_resolution(chunk)
    # Metashape stores resolutions with accumulated rounding (0.0005 comes
    # back as 0.0005000000000033308), so compare with a margin, not equality.
    if current.resolution >= target * 0.99:
        return None
    return current, target


def _resample_prompt(chunk, current, target):
    """The question asked before deleting a full-resolution DEM."""
    size = ""
    try:
        # Uncompressed float32, so an upper bound; stored tiles are compressed.
        gb = current.width * current.height * 4 / 1024.0 ** 3
        size = " The full-resolution copy takes up to about {:.1f} GB.".format(gb)
    except Exception:
        pass

    if target is None:
        return (
            "This chunk's DEM is at {cur:.4f} m, close to the orthomosaic's "
            "{ortho:.4f} m. A DEM that fine is only needed while the "
            "orthomosaic is being built. The resolution is set to Metashape's "
            "default, so it would be replaced by the DEM resolution Metashape "
            "picks for this mesh, which is much coarser.{size}"
            "\n\n"
            "Click OK to rebuild the DEM at that default resolution and delete "
            "the {cur:.4f} m one.\n"
            "Click Cancel to keep the {cur:.4f} m DEM as it is and carry on "
            "with the rest of the workflow.".format(
                cur=current.resolution, ortho=chunk.orthomosaic.resolution,
                size=size))

    return (
        "This chunk's DEM is at {cur:.4f} m, finer than the {tgt:.4f} m storage "
        "resolution ({factor} times the orthomosaic's {ortho:.4f} m). A DEM "
        "that fine is only needed while the orthomosaic is being built.{size}"
        "\n\n"
        "Click OK to rebuild the DEM at {tgt:.4f} m and delete the {cur:.4f} m "
        "one.\n"
        "Click Cancel to keep the {cur:.4f} m DEM as it is and carry on with "
        "the rest of the workflow.".format(
            cur=current.resolution, tgt=target, factor=DEM_RESAMPLE_FACTOR,
            ortho=chunk.orthomosaic.resolution, size=size))


def remove_elevation_asset(chunk, key, keep_key=None):
    """Delete the elevation asset with `key` from the project. Returns a count.

    `chunk.elevation = None` does not do this. It only clears which DEM is
    active; the asset stays in `chunk.elevations` and its tiles stay on disk
    (checked on Metashape 2.3: still listed, still saved). And buildDem takes
    replace_asset=False by default, so building a new DEM adds an asset and
    leaves the old one behind. Deleting needs an explicit chunk.remove.

    Selected by key, never "everything but the active one": other tools put
    elevation assets in a chunk on purpose (Compare Rugosity imports rasters
    that must coexist with the DEM), and those are not ours to remove.
    """
    stale = [e for e in chunk.elevations
             if e.key == key and e.key != keep_key]
    if stale:
        chunk.remove(stale)
    return len(stale)


def shrink_dem_for_storage(doc, chunk, reporter, default_mode=False):
    """Swap a DEM finer than the storage resolution for a coarser one.

    The orthomosaic needs a DEM at its own resolution while it is being built,
    to avoid resampling artifacts. Afterwards that DEM is dead weight -- at
    0.5 mm it is sixteen times the size of the 2 mm one that serves every
    downstream use -- so it is replaced and the original deleted.

    The replacement is rebuilt from the mesh rather than resampled from the
    existing DEM, so the result is clean. The original is only removed once
    that has succeeded; if the rebuild fails it is made active again, so the
    chunk is never left without a DEM.

    Does nothing unless an orthomosaic exists and the active DEM is finer than
    the target, so it is safe to call on every run and on projects processed
    by older versions.

    `default_mode` is for the default orthomosaic resolution: there is no
    ratio to apply, so the DEM is rebuilt at Metashape's own default for the
    mesh (resolution 0) instead of at a multiple of the orthomosaic's.

    Returns True if a DEM was replaced.
    """
    plan = dem_shrink_plan(chunk, default_mode)
    if plan is None:
        return False
    current, target = plan
    build_res = 0 if target is None else target

    old_key = current.key
    old_resolution = current.resolution

    reporter.step("Resampling DEM for storage")
    chunk.elevation = None
    try:
        chunk.buildDem(source_data=Metashape.ModelData,
                       interpolation=Metashape.EnabledInterpolation,
                       resolution=build_res, subdivide_task=True,
                       workitem_size_tiles=10, max_workgroup_size=100,
                       progress=reporter.progress_callback())
        replacement = chunk.elevation
        if replacement is None:
            raise WorkflowError("Rebuilding the DEM at {} produced nothing."
                                .format("{:.4f} m".format(build_res) if build_res
                                        else "Metashape's default resolution"))
    except Exception:
        original = next((e for e in chunk.elevations if e.key == old_key), None)
        if original is not None:
            chunk.elevation = original
        raise

    removed = remove_elevation_asset(chunk, old_key, keep_key=replacement.key)
    reporter.info(" --- DEM resampled to {:.4f} m for storage; {} --- ".format(
        replacement.resolution,
        "the {:.4f} m original was deleted".format(old_resolution) if removed
        else "the original was already gone"))
    return True


def clean_project(chunk):
    """Drop intermediates that are large and cheap to regenerate."""
    ortho = chunk.orthomosaic
    if ortho:
        ortho.removeOrthophotos()

    tie_points = chunk.tie_points
    if tie_points:
        tie_points.removeKeypoints()

    depth_maps = chunk.depth_maps
    if depth_maps:
        depth_maps.clear()


def format_date_label(date_str):
    """"20250612" -> "June 12, 2025"; anything else passes through unchanged."""
    try:
        return datetime.strptime(date_str, "%Y%m%d").strftime("%B %d, %Y")
    except ValueError:
        return date_str


def update_and_save(doc, reporter):
    reporter.info("Saving Project...")
    doc.save()
    reporter.info("Project Saved")


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------

def run_workflow(doc, chunk, settings, reporter=None, on_mesh_complete=None):
    """Run the full ReefShape workflow on `chunk`.

    Returns a WorkflowResult. Raises WorkflowError for conditions the user has
    to resolve before the run can continue.

    Every stage is guarded on whether its product already exists, and the
    project is saved after each one. That is what makes the workflow resumable:
    re-running after a crash, a power cut or a cancelled batch job skips
    straight to the first incomplete stage. The batch runner's retry button
    depends on this property, so the guards are load-bearing -- do not
    "simplify" them away.

    `on_mesh_complete(chunk)` runs once the mesh exists and the chunk is known
    to be properly referenced, but before the DEM. That is the only point at
    which the chunk transform can still be changed without invalidating
    anything: the mesh moves with the transform, while the DEM and
    orthomosaic are rasters in world space and would have to be rebuilt. ICP
    timepoint alignment uses this.
    """
    reporter = reporter or Reporter()
    warnings = []
    outputs = []

    if settings.crs is not None:
        chunk.crs = settings.crs

    ortho_res = settings.effective_ortho_resolution()
    dem_res = 0  # let Metashape choose; the stored DEM is resampled below
    project_name = settings.project_name or "project"
    output_dir = settings.output_dir or os.path.dirname(doc.path or "")

    # An orthomosaic that already exists alongside a DEM finer than the storage
    # resolution means that DEM is left over -- from an interrupted run, or a
    # project processed by an older version. Replacing it deletes the original
    # for good, so ask now, before any work, rather than part way through. A
    # DEM built during this run is a different matter: it is the normal
    # pipeline, replaced once the orthomosaic no longer needs it, with no
    # question asked.
    default_mode = not ortho_res      # resolution left to Metashape
    resample_dem = True
    plan = dem_shrink_plan(chunk, default_mode)
    if plan is not None:
        resample_dem = reporter.confirm(
            "Resample the DEM?", _resample_prompt(chunk, *plan))
        if not resample_dem:
            reporter.info(" --- Keeping the {:.4f} m DEM; it will not be "
                          "resampled or deleted --- ".format(plan[0].resolution))

    # ---------------- 1. Align & scale ----------------

    # Markers may have been imported from a prior timepoint before alignment
    # runs. Any marker that physically shifted between timepoints has
    # inconsistent image-space projections across photos, which can break
    # alignment or pull camera optimization toward a worse solution. Disable
    # every existing marker for the duration, and restore each one's original
    # state once optimization is done.
    suppressed = []
    try:
        if chunk.tie_points is None:
            for marker in chunk.markers:
                suppressed.append((marker, marker.enabled))
                marker.enabled = False

            reporter.step("Matching photos")
            chunk.matchPhotos(
                downscale=ALIGN_QUALITY, keypoint_limit_per_mpx=300,
                generic_preselection=settings.generic_preselection,
                reference_preselection=True, filter_mask=False,
                mask_tiepoints=True, filter_stationary_points=True,
                keypoint_limit=40000, tiepoint_limit=4000, keep_keypoints=True,
                guided_matching=False, reset_matches=False, subdivide_task=True,
                workitem_size_cameras=20, workitem_size_pairs=80,
                max_workgroup_size=100,
                progress=reporter.progress_callback())

            reporter.step("Aligning cameras")
            chunk.alignCameras(adaptive_fitting=True, min_image=2,
                               reset_alignment=True, subdivide_task=True,
                               progress=reporter.progress_callback())
            # A second pass frequently picks up photos the first pass missed.
            chunk.alignCameras(adaptive_fitting=True, min_image=2,
                               reset_alignment=False, subdivide_task=True,
                               progress=reporter.progress_callback())
            update_and_save(doc, reporter)
            reporter.info(" --- Initial alignment completed -- Refining alignment --- ")

            # Removing and re-adding the photos that failed to align resets
            # their matching state, which gives the second attempt a chance
            # that simply re-running alignment would not.
            unaligned = []
            for camera in chunk.cameras:
                if not camera.transform:
                    unaligned.append(camera.photo.path)
                    chunk.remove([camera])
            if unaligned:
                reporter.info("Retrying {} unaligned photos".format(len(unaligned)))
                chunk.addPhotos(unaligned)

            # Generic preselection is deliberately off here: it is the setting
            # most likely to have caused the misses in the first place.
            reporter.step("Re-matching unaligned photos")
            chunk.matchPhotos(
                downscale=ALIGN_QUALITY, keypoint_limit_per_mpx=300,
                generic_preselection=False, reference_preselection=True,
                filter_mask=False, mask_tiepoints=True,
                filter_stationary_points=True, keypoint_limit=40000,
                tiepoint_limit=4000, keep_keypoints=True, guided_matching=False,
                reset_matches=False, subdivide_task=True,
                workitem_size_cameras=20, workitem_size_pairs=80,
                max_workgroup_size=100,
                progress=reporter.progress_callback())
            chunk.alignCameras(adaptive_fitting=True, min_image=2,
                               reset_alignment=False, subdivide_task=True,
                               progress=reporter.progress_callback())

            reporter.info(" --- Cameras are aligned and sparse point cloud generated --- ")
            update_and_save(doc, reporter)

        # Detect markers only when there are none yet -- re-detecting would
        # duplicate markers the user or a previous timepoint already placed.
        if len(chunk.markers) == 0 and settings.georef_enabled:
            reporter.step("Detecting markers")
            chunk.detectMarkers(
                target_type=settings.resolve_target_type(), tolerance=20,
                filter_mask=False, inverted=False, noparity=False,
                maximum_residual=5, minimum_size=0, minimum_dist=5,
                progress=reporter.progress_callback())
            reporter.info(" --- Markers Detected --- ")

        if len(chunk.scalebars) == 0 and settings.georef_enabled:
            reporter.step("Scaling and referencing")
            ref_error = reference_model(chunk, settings.georef_path,
                                        settings.ref_formatting)
            scale_error = ""
            if not ref_error:
                scale_error = create_scalebars(chunk, settings.scalebar_path) or ""

            if scale_error or ref_error:
                raise WorkflowError(
                    "Unable to scale and reference model:\n"
                    + (scale_error or "") + (ref_error or "")
                    + "Check that the files are formatted correctly and try "
                      "again, or add markers and scalebars through the "
                      "Metashape GUI.")
            chunk.updateTransform()

        if chunk.model is None:
            if not is_optimized(chunk):
                reporter.step("Optimizing cameras")
                grad_selects_optimization(chunk)
                reporter.info(" --- Camera Optimization Complete --- ")
                update_and_save(doc, reporter)
    finally:
        # Restoring in `finally` guarantees the chunk is never left with every
        # marker disabled because something failed mid-alignment -- a state
        # that is confusing to diagnose and easy to mistake for data loss.
        if suppressed:
            _restore_markers(chunk, suppressed, doc, reporter)
            suppressed = []

    # ---------------- 2. Generate products ----------------

    if chunk.model is None:
        # Reset the region so the mesh covers the whole plot rather than
        # whatever subset a previous operation left selected.
        chunk.resetRegion()
        # Log the region extent: a region inflated by outlier tie points or a
        # missing updateTransform shows up here immediately, instead of 25
        # minutes later as a cryptic `MemoryError: bad allocation` out of
        # buildDem.
        size = chunk.region.size
        reporter.info("  chunk region size (internal units): "
                      "{:.3g} x {:.3g} x {:.3g}".format(size.x, size.y, size.z))
        update_and_save(doc, reporter)

        # Built as a Task rather than chunk.buildDepthMaps() so the hidden
        # `pm_enable` preference can be set, which is not exposed as a keyword.
        reporter.step("Building depth maps")
        task = Metashape.Tasks.BuildDepthMaps()
        task.downscale = settings.mesh_quality_downscale
        task.filter_mode = Metashape.FilterMode.MildFiltering
        task.reuse_depth = True
        task.max_neighbors = 16
        task.subdivide_task = True
        task.workitem_size_cameras = 20
        task.max_workgroup_size = 100
        task["pm_enable"] = "1"
        task.apply(chunk, progress=reporter.progress_callback())
        update_and_save(doc, reporter)

        reporter.step("Building mesh")
        chunk.buildModel(
            surface_type=Metashape.Arbitrary,
            interpolation=Metashape.EnabledInterpolation,
            face_count=Metashape.HighFaceCount,
            face_count_custom=1000000,
            source_data=Metashape.DepthMapsData,
            keep_depth=True,
            vertex_colors=False,
            progress=reporter.progress_callback())
        reporter.info(" --- Mesh Generated --- ")
        update_and_save(doc, reporter)

        if settings.vertex_colors:
            reporter.step("Colorizing model")
            chunk.colorizeModel(progress=reporter.progress_callback())
            update_and_save(doc, reporter)

    # Without referencing there is no transform, so there is nothing sensible
    # to build a DEM or orthomosaic in. Stop here and let the user reference,
    # level and scale by hand, then re-run.
    if not settings.georef_enabled and len(chunk.markers) == 0:
        reporter.info("Exiting workflow for manual referencing")
        return WorkflowResult(STOPPED_FOR_MANUAL_REFERENCING, warnings, outputs)

    # Referencing may have been attempted and not worked -- markers missed,
    # scalebar targets absent from the photos, a mis-set column layout. Stop
    # here rather than fail: the alignment and mesh above are the expensive
    # part, they are saved, and they stay valid. A re-run after the user fixes
    # the referencing picks up from the DEM.
    problem = referencing_problem(chunk, ortho_res, reporter)
    if problem:
        warnings.append(problem)
        reporter.warn(problem)
        reporter.info("Exiting workflow for manual referencing")
        return WorkflowResult(STOPPED_FOR_MANUAL_REFERENCING, warnings, outputs)

    # Last chance to move the chunk. See the docstring.
    if on_mesh_complete is not None:
        on_mesh_complete(chunk)
        update_and_save(doc, reporter)

    if chunk.elevation is None:
        # The orthomosaic is built over this DEM, so it has to be at least as
        # fine as the orthomosaic or the ortho is resampled from a coarser
        # surface. With an explicit resolution that is simply the ortho's. With
        # the default Metashape has not chosen one yet and a DEM at resolution 0
        # would be several times coarser than it, so estimate the orthomosaic's
        # pixel size from the images and build the DEM there. It is replaced
        # with Metashape's own default for the mesh once the ortho is done.
        dem_first_res = ortho_res
        if default_mode:
            estimate = estimate_default_ortho_resolution(chunk)
            if estimate:
                dem_first_res = estimate * DEFAULT_MODE_DEM_MARGIN
                reporter.info(
                    "  default resolution: building the DEM at {:.4f} m to "
                    "match the orthomosaic Metashape will choose (estimated "
                    "{:.4f} m from the camera height and focal length)".format(
                        dem_first_res, estimate))
            else:
                reporter.info(
                    "  default resolution: could not estimate the "
                    "orthomosaic's pixel size, so Metashape will choose the "
                    "DEM resolution too")
        reporter.step("Building DEM")
        chunk.buildDem(source_data=Metashape.ModelData,
                       interpolation=Metashape.EnabledInterpolation,
                       resolution=dem_first_res, subdivide_task=True,
                       workitem_size_tiles=10, max_workgroup_size=100,
                       progress=reporter.progress_callback())
        reporter.info(" --- Hi-Res DEM Built --- ")

    if chunk.orthomosaic is None:
        reporter.step("Building orthomosaic")
        chunk.buildOrthomosaic(
            resolution=ortho_res, surface_data=Metashape.ElevationData,
            blending_mode=Metashape.MosaicBlending, fill_holes=True,
            ghosting_filter=False, cull_faces=False, refine_seamlines=False,
            subdivide_task=True, workitem_size_cameras=20,
            workitem_size_tiles=10, max_workgroup_size=100,
            progress=reporter.progress_callback())
        reporter.info(" --- Orthomosaic Built --- ")
        update_and_save(doc, reporter)

        # The DEM was sized from an estimate, so check it really is at least as
        # fine as the orthomosaic Metashape then chose. Close is fine; much
        # coarser means the ortho was resampled from a coarser surface.
        if (default_mode and chunk.elevation is not None
                and chunk.elevation.resolution
                > chunk.orthomosaic.resolution * DEFAULT_MODE_DEM_TOLERANCE):
            warnings.append(
                "The orthomosaic was built at {:.4f} m, finer than the "
                "{:.4f} m DEM it was built over (the pixel size had to be "
                "estimated in advance). It may show resampling artifacts; "
                "set the resolution explicitly to avoid this.".format(
                    chunk.orthomosaic.resolution, chunk.elevation.resolution))

    # Runs whenever an orthomosaic exists, not only on the pass that built it.
    # A run that died between the ortho and this step would otherwise resume
    # past it for good, leaving the full-resolution DEM in the project.
    if resample_dem and shrink_dem_for_storage(doc, chunk, reporter,
                                               default_mode=default_mode):
        update_and_save(doc, reporter)

    # Skip when a boundary already exists, e.g. copied from a reference chunk
    # by the timepoint alignment step.
    has_boundary = find_outer_boundary(chunk) is not None
    source = settings.boundary_source

    def try_photo_boundary(reason):
        """Derive a boundary from camera coverage. True if one was created."""
        reporter.step("Creating boundary from photo coverage")
        try:
            from reefshape_modules import reefshape_boundary
            vertices = reefshape_boundary.create_boundary_from_photos(
                chunk, reporter=reporter)
        except Exception as exc:
            # Never fatal: the orthomosaic, DEM and report are already built
            # and are worth keeping.
            reporter.warn("Could not derive a boundary from photo coverage: "
                          "{}".format(exc))
            return False
        if find_outer_boundary(chunk) is None:
            return False
        update_and_save(doc, reporter)
        warnings.append(
            "The plot boundary was derived from photo coverage ({} vertices) "
            "{}. It follows the area actually photographed, so it will not "
            "match the boundary of other timepoints exactly. Check it before "
            "comparing timepoints. For a repeat visit, check the orthomosaic "
            "against the earlier timepoint and use Copy Boundary to bring the "
            "boundary across.".format(vertices, reason))
        return True

    if has_boundary:
        reporter.info(" --- Existing boundary polygon kept --- ")
    elif source == BOUNDARY_NONE:
        reporter.info(" --- Boundary creation is turned off for this run --- ")
    elif source == BOUNDARY_PHOTOS:
        has_boundary = try_photo_boundary("because photo coverage was chosen "
                                          "as the boundary source")
    else:
        reporter.step("Creating boundary from markers")
        has_boundary = boundary_creation(chunk, reporter)
        if has_boundary:
            reporter.info(" --- Boundary Polygon Created --- ")
        else:
            warnings.append(
                "Boundary polygon could not be created from the chunk's "
                "georeferenced markers -- there were fewer than three, or "
                "they enclose no area.")
            # Only worth falling back when something downstream needs it.
            if settings.export_taglab or settings.export_taglab_legacy:
                # The legacy outputs are for the unmodified TagLab, where
                # timepoints are compared block against block, so a boundary
                # that differs between visits is a real problem and not just a
                # cosmetic one. Ask first; everything expensive is already
                # built and saved, so stopping here costs nothing.
                if (settings.export_taglab_legacy and not reporter.confirm(
                        "No boundary from corner markers",
                        PHOTO_BOUNDARY_PROMPT)):
                    update_and_save(doc, reporter)
                    reporter.info(" --- Stopped before the exports so the "
                                  "boundary can be fixed --- ")
                    warnings.append(
                        "Stopped before the TagLab exports at your request. "
                        "Everything up to the orthomosaic and DEM is saved. "
                        "Check the orthomosaic against the previous "
                        "timepoint, bring the boundary across with Copy "
                        "Boundary, then run the workflow again.")
                    return WorkflowResult(STOPPED_FOR_BOUNDARY, warnings, outputs)
                has_boundary = try_photo_boundary(
                    "because the corner markers were not found")

    # ---------------- 3. Export ----------------

    # TagLab products are only useful clipped to the plot -- an uncropped one
    # carries the whole survey's overshoot and would have to be re-exported
    # before it could be annotated. So no boundary means no TagLab export.
    export_taglab = settings.export_taglab
    export_taglab_legacy = settings.export_taglab_legacy
    if not has_boundary:
        skipped = [name for name, wanted in (
            ("TagLab", export_taglab),
            ("TagLab (legacy)", export_taglab_legacy)) if wanted]
        if skipped:
            export_taglab = export_taglab_legacy = False
            what = " and ".join(skipped)
            if source == BOUNDARY_NONE:
                warnings.append(
                    "{} outputs were skipped because boundary creation is "
                    "turned off for this run, and TagLab products must be "
                    "clipped to the plot boundary. Everything else was "
                    "exported.".format(what))
            else:
                warnings.append(
                    "{} outputs were skipped: they must be clipped to the "
                    "plot boundary, and none could be created. Everything "
                    "else was exported.".format(what))
        if source != BOUNDARY_NONE:
            warnings.append(
                "The boundary shapefile export was skipped. To produce a "
                "boundary, use Create Boundary from Markers or Create "
                "Boundary from Photos, then re-run this workflow.")

    jpg = Metashape.ImageCompression()
    jpg.tiff_compression = Metashape.ImageCompression.TiffCompressionJPEG
    jpg.jpeg_quality = 90
    jpg.tiff_big = True
    jpg.tiff_overviews = True
    # Tiled rather than stripped: libtiff caps JPEG-compressed strips at
    # 65500 px, which reef-plot orthomosaics routinely exceed. Tiled JPEG
    # compresses each internal tile independently, so the limit doesn't
    # apply. GIS software reads both identically.
    jpg.tiff_tiled = True

    lzw = Metashape.ImageCompression()
    lzw.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW
    lzw.tiff_big = True
    lzw.tiff_overviews = True

    if settings.export_report:
        report_path = os.path.join(
            output_dir, "{}_{}.pdf".format(project_name, chunk.label))
        if not os.path.exists(report_path):
            reporter.step("Exporting report")
            _export_report(chunk, report_path, project_name, reporter)
            outputs.append(report_path)

    if settings.export_gis:
        ortho_path = os.path.join(
            output_dir, "{}_{}.tif".format(project_name, chunk.label))
        dem_path = os.path.join(
            output_dir, "{}_{}_DEM.tif".format(project_name, chunk.label))

        if not os.path.exists(ortho_path):
            reporter.step("Exporting orthomosaic")
            chunk.exportRaster(
                path=ortho_path, resolution=ortho_res,
                source_data=Metashape.OrthomosaicData, split_in_blocks=False,
                image_compression=jpg, save_kml=False, save_world=False,
                save_scheme=False, save_alpha=True, image_description="",
                network_links=True, global_profile=False, min_zoom_level=-1,
                max_zoom_level=-1, white_background=True, clip_to_boundary=False,
                title="Orthomosaic",
                description="Generated by Agisoft Metashape with ReefShape",
                progress=reporter.progress_callback())
        outputs.append(ortho_path)

        if not os.path.exists(dem_path):
            reporter.step("Exporting DEM")
            chunk.exportRaster(
                path=dem_path, resolution=dem_res, nodata_value=-5,
                source_data=Metashape.ElevationData, split_in_blocks=False,
                image_compression=lzw, save_kml=False, save_world=False,
                save_scheme=False, save_alpha=True, image_description="",
                network_links=True, global_profile=False, min_zoom_level=-1,
                max_zoom_level=-1, white_background=True, clip_to_boundary=False,
                title="DEM",
                description="Generated by Agisoft Metashape with ReefShape",
                progress=reporter.progress_callback())
        outputs.append(dem_path)

        # Shapefiles go in their own folder because the format is really five
        # sidecar files. Skipped without a boundary, which would otherwise
        # produce an empty folder and a polygon-less shapefile.
        if has_boundary:
            shape_dir = os.path.join(
                output_dir, "{}_{}_boundary".format(project_name, chunk.label))
            if not os.path.exists(shape_dir):
                os.mkdir(shape_dir)
            shape_path = os.path.join(
                shape_dir, "{}_{}_boundary.shp".format(project_name, chunk.label))
            reporter.step("Exporting boundary shapefile")
            chunk.exportShapes(
                path=shape_path, save_points=False, save_polylines=False,
                save_polygons=True, format=Metashape.ShapesFormatSHP,
                polygons_as_polylines=False, save_labels=True,
                save_attributes=True)
            outputs.append(shape_path)

    if export_taglab:
        taglab_dir = os.path.join(output_dir, "taglab_outputs")
        os.makedirs(taglab_dir, exist_ok=True)

        # Single files, JPEG-compressed. TagLab used to require images under
        # 32767 px per side and uncompressed, which forced these exports to be
        # split into blocks and written LZW. It now accepts large JPEG-
        # compressed images, so a plot arrives as one file at a fraction of
        # the size.
        reporter.step("Exporting TagLab orthomosaic")
        taglab_ortho = os.path.join(
            taglab_dir, "{}_{}.tif".format(project_name, chunk.label))
        chunk.exportRaster(
            path=taglab_ortho, resolution=ortho_res,
            source_data=Metashape.OrthomosaicData, split_in_blocks=False,
            image_compression=jpg,
            save_kml=False, save_world=False, save_scheme=False,
            save_alpha=True, image_description="", network_links=True,
            global_profile=False, min_zoom_level=-1, max_zoom_level=-1,
            white_background=True, clip_to_boundary=True,
            title="Orthomosaic", description="Generated by Agisoft Metashape",
            progress=reporter.progress_callback())
        outputs.append(taglab_ortho)

        # The DEM keeps LZW: it holds float elevations, and JPEG is lossy in a
        # way that would corrupt the measurements TagLab reads off it.
        # resolution=dem_res (0) exports at the DEM's own resolution rather
        # than resampling it up to the orthomosaic's -- typically 2 mm against
        # a 0.5 mm ortho, which is the whole point of the earlier resample
        # step. Passing ortho_res here would have undone it.
        reporter.step("Exporting TagLab DEM")
        taglab_dem = os.path.join(
            taglab_dir, "{}_{}_DEM.tif".format(project_name, chunk.label))
        chunk.exportRaster(
            path=taglab_dem, resolution=dem_res, nodata_value=-5,
            source_data=Metashape.ElevationData, split_in_blocks=False,
            image_compression=lzw,
            save_kml=False, save_world=False, save_scheme=False,
            save_alpha=True, image_description="", network_links=True,
            global_profile=False, min_zoom_level=-1, max_zoom_level=-1,
            white_background=True, clip_to_boundary=True, title="DEM",
            description="Generated by Agisoft Metashape",
            progress=reporter.progress_callback())
        outputs.append(taglab_dem)

        reporter.step("Writing TagLab project")
        try:
            taglab_project = write_taglab_project(
                taglab_dir, project_name, chunk, taglab_ortho, taglab_dem,
                ortho_res)
            outputs.append(taglab_project)
            reporter.info("  TagLab project: {}".format(taglab_project))
        except Exception as exc:
            # A missing .json costs the user a few minutes setting the
            # timepoint up by hand; it is not worth discarding the rasters
            # that took hours to build.
            warnings.append(
                "TagLab project file could not be written ({}). The "
                "orthomosaic and DEM were exported and can be loaded into "
                "TagLab manually.".format(exc))

    if export_taglab_legacy:
        reporter.step("Exporting TagLab outputs (legacy)")
        legacy_dir = export_taglab_legacy_products(
            chunk, output_dir, project_name, ortho_res, lzw, reporter)
        outputs.append(legacy_dir)

    # ---------------- 4. Clean up ----------------

    reporter.step("Cleaning project")
    clean_project(chunk)
    update_and_save(doc, reporter)

    for warning in warnings:
        reporter.warn(warning)

    return WorkflowResult(COMPLETED, warnings, outputs)


def _restore_markers(chunk, suppressed, doc, reporter):
    """Re-enable markers disabled for alignment, and recompute the transform.

    The updateTransform call is not optional. Re-enabling markers does not by
    itself tell Metashape to recompute the chunk transform from the reference
    information that just became live again -- the transform stays at whatever
    pre-alignment state it had. Without this, `chunk.region` projects to
    nonsense world extents, and resetRegion followed by buildDem can produce a
    multi-thousand-kilometre DEM bounding box that fails to allocate. Observed
    on a chunk whose markers and boundary were copied from a reference chunk
    but never got a transform recompute before the mesh and DEM build.
    """
    for marker, was_enabled in suppressed:
        marker.enabled = was_enabled
    chunk.updateTransform()
    update_and_save(doc, reporter)


def _export_report(chunk, report_path, project_name, reporter):
    """Export the processing report with the boundary temporarily disabled.

    The report should show the full survey, not the clipped plot, so any
    OuterBoundary is downgraded to NoBoundary for the duration.

    The restore runs in `finally`. Without that, a failed export leaves the
    boundary permanently marked NoBoundary -- the chunk still has its polygon,
    but nothing recognizes it as a boundary any more. Downstream that means
    TagLab exports silently stop clipping, and the next run sees no boundary
    and adds a *second* polygon. This is not hypothetical: it is the state the
    TimsReef2 project was found in.
    """
    disabled = []
    try:
        if chunk.shapes:
            for shape in chunk.shapes:
                if (shape.geometry
                        and shape.geometry.type == Metashape.Geometry.Type.PolygonType
                        and shape.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary):
                    disabled.append((shape, shape.boundary_type))
                    shape.boundary_type = Metashape.Shape.BoundaryType.NoBoundary

        human_date = format_date_label(chunk.label)
        # Metashape 2.3 renamed include_system_info -> save_system_info.
        version = tuple(int(p) for p in Metashape.app.version.split(".")[:2])
        system_info_kwarg = ("save_system_info" if version >= (2, 3)
                             else "include_system_info")
        chunk.exportReport(
            path=report_path,
            title=project_name,
            description=("\nProcessing report for " + project_name
                         + " photographed on " + human_date
                         + "\nCreated with ReefShape v" + REEFSHAPE_VERSION
                         + "\nProcessed on:"),
            font_size=12,
            page_numbers=True,
            **{system_info_kwarg: True})
    finally:
        for shape, original_type in disabled:
            shape.boundary_type = original_type
