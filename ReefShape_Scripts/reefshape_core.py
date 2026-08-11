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

# Outcomes of run_workflow.
COMPLETED = "completed"
STOPPED_FOR_MANUAL_REFERENCING = "stopped_for_manual_referencing"


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
                 corner_markers=None,
                 output_dir="",
                 export_report=True,
                 export_gis=True,
                 export_taglab=True,
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
        self.corner_markers = corner_markers or [1, 2, 3, 4]

        self.output_dir = output_dir
        self.export_report = export_report
        self.export_gis = export_gis
        # TagLab exports additionally require a boundary polygon to clip to;
        # without one they are skipped with a warning rather than produced
        # uncropped, which would have to be re-exported before use anyway.
        self.export_taglab = export_taglab

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
            corner_markers=georef.get("corner_markers", [1, 2, 3, 4]),
            output_dir=export.get("output_dir", "") or os.path.dirname(project_path),
            export_report=export.get("report", True),
            export_gis=export.get("gis_outputs", True),
            export_taglab=export.get("taglab_outputs", True),
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

def create_shape_from_markers(chunk, markers):
    """Build an OuterBoundary polygon through `markers`, in the order given."""
    if not chunk:
        print("Empty project, script aborted")
        return False
    if len(markers) < 4:
        print("At least four markers required to create a plot. "
              "Boundary creation aborted.")
        return False

    transform = chunk.transform.matrix
    if not chunk.shapes:
        chunk.shapes = Metashape.Shapes()
        chunk.shapes.crs = chunk.crs
    shape_crs = chunk.shapes.crs

    coords = [shape_crs.project(transform.mulp(m.position)) for m in markers]

    shape = chunk.shapes.addShape()
    shape.label = "Marker Boundary"
    shape.geometry.type = Metashape.Geometry.Type.PolygonType
    shape.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
    shape.geometry = Metashape.Geometry.Polygon(coords)
    return True


def boundary_creation(chunk, corner_markers):
    """Create the plot boundary from the four corner markers.

    Iterates `corner_markers` in the order supplied so the polygon follows
    that cyclic order; listing the corners out of cyclic order produces a
    self-intersecting boundary, which is why the order is a user setting
    rather than sorted here.
    """
    found = []
    for corner in corner_markers:
        for marker in chunk.markers:
            # Skip markers we cannot place: no estimated position, or no
            # digits in the label to match the corner number against.
            if marker.position is None:
                continue
            digits = re.search(r"(\d+)", marker.label)
            if digits is None:
                continue
            if str(corner) == digits.group(0):
                found.append(marker)
                break

    if len(found) < 4:
        print("Could not find all 4 corner markers {} in the chunk. "
              "Boundary creation skipped.".format(corner_markers))
        return False
    return create_shape_from_markers(chunk, found[:4])


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
        "  - all four corner markers were detected and correctly numbered\n"
        "  - the georeferencing file's column layout is set correctly\n\n"
        "Correct the referencing, then re-run to continue from the DEM "
        "onwards.".format(width, height, resolution, cells / 1e9))


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
        reporter.step("Building DEM")
        chunk.buildDem(source_data=Metashape.ModelData,
                       interpolation=Metashape.EnabledInterpolation,
                       resolution=ortho_res, subdivide_task=True,
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

        # Replace the high-res DEM with a coarser one now that the ortho no
        # longer needs it. Rebuilt from the mesh rather than resampled from
        # the existing DEM so the result is clean.
        resample_res = chunk.orthomosaic.resolution * DEM_RESAMPLE_FACTOR
        chunk.elevation = None
        reporter.step("Resampling DEM for storage")
        chunk.buildDem(source_data=Metashape.ModelData,
                       interpolation=Metashape.EnabledInterpolation,
                       resolution=resample_res, subdivide_task=True,
                       workitem_size_tiles=10, max_workgroup_size=100,
                       progress=reporter.progress_callback())
        reporter.info(" --- DEM resampled to {:.4f} m for storage --- "
                      .format(resample_res))
        update_and_save(doc, reporter)

    # Skip when a boundary already exists, e.g. copied from a reference chunk
    # by the timepoint alignment step.
    has_boundary = find_outer_boundary(chunk) is not None
    if not has_boundary:
        reporter.step("Creating boundary")
        has_boundary = boundary_creation(chunk, settings.corner_markers)
        if has_boundary:
            reporter.info(" --- Boundary Polygon Created ---")
        else:
            reporter.info(" --- Boundary polygon was NOT created; downstream "
                          "steps that depend on it will be skipped ---")

    # ---------------- 3. Export ----------------

    # TagLab products are only useful clipped to the plot -- an uncropped one
    # carries the whole survey's overshoot and would have to be re-exported
    # before it could be annotated. So a missing boundary skips that export
    # rather than producing something misleading, and rather than failing a
    # run whose other outputs are perfectly good.
    export_taglab = settings.export_taglab
    if not has_boundary:
        warnings.append(
            "Boundary polygon could not be created automatically -- corner "
            "markers {} were not all found in the chunk. The boundary "
            "shapefile export was skipped. To produce a boundary, run script "
            "06 (corner markers) or script 08 (from camera footprints) and "
            "re-run this workflow.".format(settings.corner_markers))

        if export_taglab:
            export_taglab = False
            warnings.append(
                "TagLab outputs were skipped: they must be clipped to the "
                "plot boundary, and no boundary polygon was available. "
                "Everything else was exported. Create a boundary and re-run "
                "to produce them.")

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

        reporter.step("Exporting TagLab orthomosaic")
        taglab_ortho = os.path.join(
            taglab_dir, "{}_{}.tif".format(project_name, chunk.label))
        chunk.exportRaster(
            path=taglab_ortho, resolution=ortho_res,
            source_data=Metashape.OrthomosaicData, block_width=32767,
            block_height=32767, split_in_blocks=True, image_compression=lzw,
            save_kml=False, save_world=False, save_scheme=False,
            save_alpha=True, image_description="", network_links=True,
            global_profile=False, min_zoom_level=-1, max_zoom_level=-1,
            white_background=True, clip_to_boundary=True,
            title="Orthomosaic", description="Generated by Agisoft Metashape",
            progress=reporter.progress_callback())
        outputs.append(taglab_ortho)

        reporter.step("Exporting TagLab DEM")
        taglab_dem = os.path.join(
            taglab_dir, "{}_{}_DEM.tif".format(project_name, chunk.label))
        chunk.exportRaster(
            path=taglab_dem, resolution=ortho_res, nodata_value=-5,
            source_data=Metashape.ElevationData, block_width=32767,
            block_height=32767, split_in_blocks=True, image_compression=lzw,
            save_kml=False, save_world=False, save_scheme=False,
            save_alpha=True, image_description="", network_links=True,
            global_profile=False, min_zoom_level=-1, max_zoom_level=-1,
            white_background=True, clip_to_boundary=True, title="DEM",
            description="Generated by Agisoft Metashape",
            progress=reporter.progress_callback())
        outputs.append(taglab_dem)

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
