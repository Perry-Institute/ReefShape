"""
Gridded Rugosity
Will Greene, Perry Institute for Marine Science

Computes per-cell 3D-to-2D surface area ratio (rugosity) on a 1-meter grid
within the active chunk's OuterBoundary polygon, and imports the result back
into the chunk as a labeled DEM ("Rugosity"). Optionally also writes it to
disk as a GeoTIFF.

Why a single-pass mesh iteration (vs. clip-per-cell):
  09_calculate_area_ratio.py duplicates and clips the mesh to compute the
  whole-plot rugosity once. Doing that per 1 m cell would be minutes-to-
  hours of mesh ops. Instead, this script iterates the mesh's triangles
  exactly once. For each triangle:
    - compute its 3D area (chunk-local meters, exact)
    - project its centroid into shape CRS and find the grid cell
    - accumulate the 3D area into that cell
  Per-cell rugosity = accumulated 3D area / cell footprint (1 m² for
  interior cells).

Overhang handling:
  An overhang stacks multiple slabs of mesh over the same XY footprint.
  Both slabs contribute their 3D area to the same cell; the denominator
  stays at 1 m². Result: an overhang gives rugosity > 1 in the correct
  ratio, capturing the structure that a DEM-based (2.5D) calculation
  would miss.

Cross-timepoint comparability:
  The grid is derived from the OuterBoundary polygon: bounding box snapped
  to multiples of the cell size in shape CRS units. As long as the boundary
  polygon is identical between timepoints (the workflow copies it across
  via Align Timepoints), the output rasters are pixel-aligned.

Requires:
  - The active chunk has a 3D model.
  - The active chunk has an OuterBoundary polygon shape.

Dependencies: numpy, rasterio. Installed via pip_auto_install on first run.
"""

import math
import os
import tempfile

import Metashape
from PySide2 import QtCore, QtGui, QtWidgets

from modules.pip_auto_install import pip_install

# Deliberately NO matplotlib. An earlier version used matplotlib.path.Path
# for point-in-polygon, but on some installs `from matplotlib.path import
# Path` raised `module 'matplotlib' has no attribute 'rcParams'` — matplotlib
# has heavy import-time machinery and is fragile when its install was last
# touched by an unrelated pip run mid-session. rasterio.features.rasterize
# does the polygon mask in one call with no extra dependency.
#
# numpy must be pinned to the SAME version as 03_align_chunks_ICP.py and
# 08_create_boundary_from_photos.py (numpy==1.26.4). Without a pin, pip
# resolves to the latest numpy (currently 2.5.x), which breaks the scipy
# and open3d wheels — those were compiled against numpy 1.x and refuse to
# load under numpy 2.x. A single unpinned `numpy` here would silently
# poison the install for every other ReefShape script that needs numpy.
# Rasterio is pinned loosely to the 1.4.x line so we get Python 3.12
# wheels but stay below any future breaking 2.0 release.
pip_install("""numpy==1.26.4
rasterio>=1.4,<2
""")

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.features import rasterize as rio_rasterize  # noqa: E402


DEFAULT_CELL_SIZE_M = 1.0   # default grid resolution (slider start position)
MIN_CELL_SIZE_M = 0.25      # slider minimum
MAX_CELL_SIZE_M = 5.0       # slider maximum
CELL_SIZE_STEP_M = 0.25     # slider granularity
NODATA = -9999.0
RASTER_LABEL_PREFIX = "Rugosity"  # final label is e.g. "Rugosity (1.0m grid)"


def _gr_show_error(parent, title, msg):
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Critical)
    box.setWindowTitle("Error")
    box.setText(title)
    box.setInformativeText(msg)
    box.exec_()


# ---------------------------------------------------------------------------
# Progress dialog
# ---------------------------------------------------------------------------

class _GriddedRugosityProgressDialog(QtWidgets.QDialog):
    """Plain QDialog with a status label and determinate progress bar.

    Built by hand (not QProgressDialog) for the same reason pip_auto_install
    rolls its own: QProgressDialog auto-sizes on every setLabelText, which
    fights with setFixedSize when status text varies in width.
    """

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setFixedSize(480, 160)
        # Strip the close button so users can't dismiss mid-compute (the
        # script holds the GIL inside numpy calls anyway, so dismissing
        # wouldn't actually stop work).
        flags = self.windowFlags() & ~QtCore.Qt.WindowCloseButtonHint
        flags &= ~QtCore.Qt.WindowSystemMenuHint
        self.setWindowFlags(flags)

        self._heading = QtWidgets.QLabel("Computing gridded rugosity…")
        self._heading.setWordWrap(True)
        font = self._heading.font()
        font.setBold(True)
        self._heading.setFont(font)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: palette(mid);")
        self._status.setMaximumWidth(440)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(self._heading)
        layout.addWidget(self._status)
        layout.addStretch(1)
        layout.addWidget(self._bar)

    def set_status(self, text):
        self._status.setText(text)
        QtWidgets.QApplication.processEvents()

    def set_progress(self, fraction):
        # Clamp to [0,1] before scaling to the 0..100 widget range.
        f = max(0.0, min(1.0, fraction))
        self._bar.setValue(int(f * 100))
        QtWidgets.QApplication.processEvents()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gr_extract_boundary_ring(geom):
    """Return the outer ring of a polygon geometry as a list of Metashape
    Vectors. Handles both nested-ring (list-of-list-of-Vector) and flat
    (list-of-Vector) conventions — different Metashape versions / shape
    construction paths emit one or the other.
    """
    coords = geom.coordinates
    if not coords:
        raise RuntimeError("Boundary polygon has no coordinates.")
    first = coords[0]
    if isinstance(first, (list, tuple)) and first and hasattr(first[0], "x"):
        return list(first)
    if hasattr(first, "x"):
        return list(coords)
    raise RuntimeError("Could not interpret boundary polygon coordinate format.")


def _gr_import_raster_to_chunk(chunk, path, label):
    """Import `path` as an Elevation product in `chunk` and label it.

    Records the existing elevations' keys, calls importRaster, then finds
    the freshly-added one and renames it. Doesn't touch the chunk's
    currently-active elevation (chunk.elevation) so the rugosity raster
    coexists with the original DEM rather than replacing it.

    Returns the imported Elevation object, or None if it couldn't be
    located after import (which would be surprising — importRaster
    succeeded but the chunk didn't gain an entry).
    """
    before_keys = set()
    if chunk.elevations:
        before_keys = {e.key for e in chunk.elevations}

    # Preserve the currently-active elevation so importRaster doesn't bump
    # us off the project's real DEM.
    prior_active = chunk.elevation

    chunk.importRaster(
        path=path,
        crs=chunk.crs,
        raster_type=Metashape.DataSource.ElevationData,
    )

    new_elev = None
    if chunk.elevations:
        for e in chunk.elevations:
            if e.key not in before_keys:
                new_elev = e
                break

    if new_elev is not None:
        new_elev.label = label

    # Restore the original active DEM (importRaster makes the import active).
    if prior_active is not None and chunk.elevation is not prior_active:
        chunk.elevation = prior_active

    return new_elev


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_gridded_rugosity(chunk, boundary, cell_size_m, progress=None):
    """Returns (rugosity_float32, geotransform, crs_wkt, stats).

    rugosity_float32 is a (n_rows, n_cols) array with NODATA outside the
    boundary mask or where the cell has zero mesh coverage.

    `progress` is an optional object with `set_status(str)` and
    `set_progress(float in [0,1])` methods; if provided, it's updated
    between phases and inside the long Python loops so the dialog stays
    responsive.
    """

    def _step(text, fraction):
        if progress is not None:
            progress.set_status(text)
            progress.set_progress(fraction)

    T = chunk.transform.matrix
    shape_crs = chunk.shapes.crs if (chunk.shapes and chunk.shapes.crs) else chunk.crs
    if shape_crs is None:
        raise RuntimeError("Chunk has no CRS set on shapes or on the chunk itself.")

    # Diagnostic block — surfaces the CRS configuration so an unexpected
    # pixel size on display (e.g. a chunk where shape CRS units aren't
    # what you think they are) is debuggable from a single console paste.
    try:
        print("  chunk.crs:        {}".format(chunk.crs.name if chunk.crs else "(none)"))
    except Exception:
        pass
    try:
        if chunk.shapes and chunk.shapes.crs and chunk.shapes.crs is not chunk.crs:
            print("  chunk.shapes.crs: {}".format(chunk.shapes.crs.name))
        else:
            print("  chunk.shapes.crs: (same as chunk.crs)")
    except Exception:
        pass

    # --- 1. Boundary outer ring in shape CRS (XY) ---
    _step("Reading boundary polygon…", 0.02)
    ring = _gr_extract_boundary_ring(boundary.geometry)
    boundary_xy = np.array([(v.x, v.y) for v in ring], dtype=np.float64)
    if len(boundary_xy) < 3:
        raise RuntimeError("Boundary polygon has fewer than 3 vertices.")

    # Boundary bbox + centroid in shape CRS. Used in two places:
    #   - As the deterministic location for the localframe probe later
    #     (so shape_per_meter is identical between two timepoints sharing
    #     the same boundary polygon — without this, the probe location
    #     would depend on the random mesh vertex sample and drift by tiny
    #     amounts per run).
    #   - As the anchor for the grid snap (so the cells of two
    #     timepoints sharing the same boundary land at identical positions).
    # Anchoring to the boundary itself rather than to the CRS origin matters
    # because at typical reef latitudes |bx_min| / cell_size_units is ~1e7
    # — any 1e-13 wiggle in cell_size_units gets multiplied by that ratio
    # at snap time and shifts the grid origin by entire cells.
    bx_min, by_min = boundary_xy.min(axis=0)
    bx_max, by_max = boundary_xy.max(axis=0)
    boundary_z = float(np.median([float(v.z) for v in ring]))

    # --- 2. Mesh vertices and faces as numpy arrays ---
    # IMPORTANT: chunk.model.vertices stores coordinates in CHUNK-INTERNAL
    # units, not world meters. The chunk transform (which encodes rotation,
    # translation, AND a scale factor) converts internal → world. For a
    # chunk with chunk.transform.scale != 1, treating internal coords as
    # meters silently mis-sizes both the per-cell area calculation AND the
    # shape-CRS cell footprint. We transform every vertex through the
    # chunk transform here so everything downstream works in real meters.
    n_verts = len(chunk.model.vertices)
    n_faces = len(chunk.model.faces)
    if n_faces == 0:
        raise RuntimeError("Chunk model has no faces.")
    print("  mesh: {} vertices, {} faces".format(n_verts, n_faces))

    _step("Extracting {:,} mesh vertices…".format(n_verts), 0.05)
    vert_internal = np.empty((n_verts, 3), dtype=np.float64)
    verts = chunk.model.vertices
    # Batch with periodic event-pump so the dialog updates and stays
    # responsive. The per-vertex coord access is Python overhead, ~1µs/vert,
    # so a 5M-face mesh is several seconds.
    batch = max(1, n_verts // 50)
    for start in range(0, n_verts, batch):
        end = min(start + batch, n_verts)
        for i in range(start, end):
            c = verts[i].coord
            vert_internal[i, 0] = c.x
            vert_internal[i, 1] = c.y
            vert_internal[i, 2] = c.z
        if progress is not None:
            progress.set_progress(0.05 + 0.18 * (end / n_verts))

    # Compose the full model→world transform (handles per-model offsets
    # if model.transform is non-identity). Used only for calibration —
    # we don't apply this matrix to every vertex, just to the 50 sample
    # points below, sidestepping any matrix-extraction headaches.
    T_chunk = chunk.transform.matrix
    if chunk.model.transform is not None:
        T_full = T_chunk * chunk.model.transform
    else:
        T_full = T_chunk

    _step("Extracting {:,} mesh faces…".format(n_faces), 0.28)
    face_verts = np.empty((n_faces, 3), dtype=np.int64)
    faces = chunk.model.faces
    batch = max(1, n_faces // 50)
    for start in range(0, n_faces, batch):
        end = min(start + batch, n_faces)
        for i in range(start, end):
            fv = faces[i].vertices
            face_verts[i, 0] = fv[0]
            face_verts[i, 1] = fv[1]
            face_verts[i, 2] = fv[2]
        if progress is not None:
            progress.set_progress(0.28 + 0.22 * (end / n_faces))

    # --- 3. Triangle areas + centroids in chunk-INTERNAL units ---
    # We compute geometry in internal units first; the meters_per_internal
    # conversion factor (from calibration below) converts the areas to m²
    # exactly when we need them. This avoids transforming every vertex
    # through a manually-extracted T_np matrix — that path turned out to
    # be where the previous attempt got the math wrong. Internal-unit
    # geometry composed with empirical-scale conversion is bulletproof.
    _step("Computing triangle geometry…", 0.55)
    tri = vert_internal[face_verts]  # (n_faces, 3, 3) — internal units
    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    e1 = v1 - v0
    e2 = v2 - v0
    areas_internal = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)  # internal²
    centroids_internal = (v0 + v1 + v2) / 3.0

    # --- 4. Calibrate: meters-per-internal + shape-per-meter + Jacobian ---
    #
    # PRIOR APPROACH (now removed): two 2D-input lstsq affine fits over 50
    # random calibration vertices, used for both unit conversion and
    # centroid projection. Two problems with that:
    #
    #   1. The 2D affine ignores internal_z. For a tilted reef plot,
    #      internal_z correlates with internal_xy, and the ECEF→geographic
    #      projection's z-dependent variation gets absorbed into the
    #      xy-slope of the fit — biasing shape_per_internal up by ~1.5×.
    #      That alone inflated triangle areas by ~2.27× (the squared
    #      scale factor) and the centroid projection by ~1.5× per axis,
    #      so cells scooped up faces from ~2.27× more physical area than
    #      they should. Combined: ~5× over-stated rugosity.
    #   2. The fit's sensitivity to the random vertex sample made the
    #      result non-deterministic between runs, breaking cross-timepoint
    #      pixel alignment.
    #
    # CURRENT APPROACH (three separate, correct calculations):
    #
    #   - meters_per_internal: chunk.transform.scale directly. Metashape's
    #     own mesh.area() and polygon.area() use this same scale, so our
    #     per-cell rugosity is numerically consistent with what users get
    #     from those tools (which is how they validate output). We
    #     additionally probe T_full's per-axis scale and warn if they
    #     disagree — exposes anomalies like non-uniform-scale chunk
    #     transforms or surprising model.transform contributions.
    #
    #   - shape_per_meter: localframe probe at the boundary centroid (see
    #     "shape-per-horizontal-meter calculation" comment below). Already
    #     correct from the earlier fix; just reorganized.
    #
    #   - centroid projection: 3×2 Jacobian J via finite-difference
    #     probes of T_full + shape_crs.project at a reference internal
    #     point. Captures the *full* dependency including internal_z, so
    #     the projection of each face's centroid to shape coords matches
    #     what T_full.mulp + shape_crs.project would give (within the
    #     linearity of the projection over typical reef-plot extents).
    #
    _step("Calibrating transform…", 0.62)

    # ---- meters_per_internal ----
    try:
        ts_reported = float(chunk.transform.scale)
    except Exception:
        ts_reported = None

    # Probe T_full's per-axis scale so any axis-non-uniform behavior or
    # model.transform contribution shows up in the console rather than
    # silently corrupting areas.
    probe_origin = T_full.mulp(Metashape.Vector([0.0, 0.0, 0.0]))
    probe_axes = []
    for axis in range(3):
        unit = [0.0, 0.0, 0.0]
        unit[axis] = 1.0
        probe_w = T_full.mulp(Metashape.Vector(unit))
        probe_axes.append((probe_w - probe_origin).norm())
    print("  T_full axis scales: x={:.4g} y={:.4g} z={:.4g}".format(*probe_axes))

    if ts_reported is not None and ts_reported > 0:
        meters_per_internal = ts_reported
        # Warn if T_full's per-axis probe disagrees with chunk.transform.scale
        # by >1% — could indicate non-uniform scale or a model.transform
        # contribution that mesh.area() doesn't account for.
        probe_mean = sum(probe_axes) / 3.0
        if abs(probe_mean - ts_reported) / ts_reported > 0.01:
            print("  WARNING: chunk.transform.scale ({:.4g}) differs from "
                  "T_full axis-probe mean ({:.4g}) by >1%. Per-cell areas "
                  "will use chunk.transform.scale to match Metashape's "
                  "mesh.area(), but you may see rugosity differ from manual "
                  "checks done with composed-transform tools.".format(
                      ts_reported, probe_mean))
    else:
        meters_per_internal = sum(probe_axes) / 3.0
    print("  meters/internal: {:.6g}".format(meters_per_internal))

    # ---- shape_per_meter (localframe at boundary centroid) ----
    # chunk.crs.localframe(p) returns the matrix that converts ECEF →
    # local east-north-up at p ("matrix to local LSE" per the docs). We
    # want the OPPOSITE direction (LSE → ECEF) so that feeding a (1, 0, 0)
    # "1 m east in LSE" probe gives us the ECEF coordinates of the point
    # 1 m east of ref — invert the matrix.
    #
    # The reference point is the boundary's bbox centroid in shape CRS,
    # unprojected to ECEF. Boundary-derived (not mesh-derived) so two
    # timepoints sharing the same boundary get identical shape_per_meter,
    # which keeps the grid pixel-aligned across timepoints.
    bx_center_shape = 0.5 * (bx_min + bx_max)
    by_center_shape = 0.5 * (by_min + by_max)
    ref_shape_for_probe = Metashape.Vector(
        [float(bx_center_shape), float(by_center_shape), float(boundary_z)])
    try:
        ref_world_vec = shape_crs.unproject(ref_shape_for_probe)
    except Exception:
        # Fall back to the centroid of the chunk transform's translation
        # (any in-mesh world point would work — localframe just needs to
        # be at a sensible ECEF location).
        ref_world_vec = probe_origin

    shape_per_meter = None
    spm_method = ""
    try:
        local_to_ecef = chunk.crs.localframe(ref_world_vec).inv()
        ref_shape = shape_crs.project(ref_world_vec)
        east_world = local_to_ecef.mulp(Metashape.Vector([1.0, 0.0, 0.0]))
        east_shape = shape_crs.project(east_world)
        east_dist = math.hypot(east_shape.x - ref_shape.x,
                               east_shape.y - ref_shape.y)
        north_world = local_to_ecef.mulp(Metashape.Vector([0.0, 1.0, 0.0]))
        north_shape = shape_crs.project(north_world)
        north_dist = math.hypot(north_shape.x - ref_shape.x,
                                north_shape.y - ref_shape.y)
        shape_per_meter = 0.5 * (east_dist + north_dist)
        spm_method = "localframe probe"
    except Exception as e:
        print("  note: chunk.crs.localframe failed ({}); "
              "falling back to assuming shape units == meters".format(e))
        shape_per_meter = 1.0
        spm_method = "assumed (localframe unavailable)"
    print("  shape-CRS units per meter: {:.6g}  ({})".format(
        shape_per_meter, spm_method))

    # ---- Convert internal areas to m² ----
    areas_3d = areas_internal * (meters_per_internal ** 2)

    # ---- Cell size in shape-CRS units ----
    cell_size_units = cell_size_m * shape_per_meter
    print("  cell size: {:.6g} shape-CRS units ({} m on the ground)".format(
        cell_size_units, cell_size_m))

    # ---- 3D-input Jacobian for centroid → shape projection ----
    # J[axis, k] = ∂shape_k / ∂internal_axis at the reference point.
    # Probed via finite differences through the actual T_full +
    # shape_crs.project pipeline, so it picks up the full ECEF→geographic
    # nonlinearity correctly (including the internal_z dependency that
    # the old 2D lstsq fit was incorrectly absorbing into the xy slopes).
    _step("Computing centroid projection Jacobian…", 0.65)
    ref_int_3d = centroids_internal.mean(axis=0)
    ref_world = T_full.mulp(Metashape.Vector(
        [float(ref_int_3d[0]), float(ref_int_3d[1]), float(ref_int_3d[2])]))
    ref_shape_proj = shape_crs.project(ref_world)
    ref_shape_xy = np.array([float(ref_shape_proj.x), float(ref_shape_proj.y)])
    J = np.zeros((3, 2), dtype=np.float64)
    EPS = 1.0  # 1 internal unit per probe — well within the linear regime
    for axis in range(3):
        perturbed = ref_int_3d.copy()
        perturbed[axis] += EPS
        p_world = T_full.mulp(Metashape.Vector(
            [float(perturbed[0]), float(perturbed[1]), float(perturbed[2])]))
        p_shape = shape_crs.project(p_world)
        J[axis, 0] = (p_shape.x - ref_shape_proj.x) / EPS
        J[axis, 1] = (p_shape.y - ref_shape_proj.y) / EPS

    # Project all face centroids: shape = ref_shape + (centroid - ref) @ J
    delta_int = centroids_internal - ref_int_3d  # (n_faces, 3)
    centroids_shape = ref_shape_xy + delta_int @ J  # (n_faces, 2)

    # --- 5. Build grid anchored to the boundary's own bounding box ---
    # Grid origin = boundary's (min_x, max_y) corner. This guarantees that
    # two timepoints sharing the same boundary polygon produce pixel-
    # aligned rasters regardless of any tiny per-run variation in
    # cell_size_units. The previous "snap to CRS origin" approach
    # multiplied bx_min (~−77 deg for a Caribbean plot) by 1/cell_size_units
    # (~1e5 per meter), amplifying any 1e-13 noise into entire-cell
    # offsets. Anchoring to the boundary itself sidesteps that
    # amplification: left_x = bx_min exactly, and the boundary is
    # identical between runs.
    _step("Building grid…", 0.75)
    left_x = float(bx_min)
    top_y = float(by_max)
    n_cols = max(1, int(math.ceil((bx_max - bx_min) / cell_size_units)))
    n_rows = max(1, int(math.ceil((by_max - by_min) / cell_size_units)))
    right_x = left_x + n_cols * cell_size_units
    bottom_y = top_y - n_rows * cell_size_units
    print("  grid: {} cols x {} rows ({} cells total)".format(
        n_cols, n_rows, n_cols * n_rows))

    transform = from_origin(left_x, top_y, cell_size_units, cell_size_units)

    # --- 6. Boundary mask via rasterio.features.rasterize ---
    # Pass the polygon as a GeoJSON-like dict; rasterize handles the
    # in/out determination using GDAL's polygon rasterizer (no shapely
    # dependency needed). all_touched=False matches our centroid-based
    # face assignment (cells whose center is inside the polygon).
    _step("Building boundary mask…", 0.80)
    poly_geo = {
        "type": "Polygon",
        "coordinates": [boundary_xy.tolist()],
    }
    inside_mask = rio_rasterize(
        [(poly_geo, 1)],
        out_shape=(n_rows, n_cols),
        transform=transform,
        fill=0,
        dtype=np.uint8,
        all_touched=False,
    ).astype(bool)

    # --- 7. Assign each face's 3D area to its centroid's cell ---
    _step("Assigning {:,} faces to cells…".format(n_faces), 0.85)
    cols = np.floor((centroids_shape[:, 0] - left_x) / cell_size_units).astype(np.int64)
    rows = np.floor((top_y - centroids_shape[:, 1]) / cell_size_units).astype(np.int64)
    valid = (cols >= 0) & (cols < n_cols) & (rows >= 0) & (rows < n_rows)
    accumulator = np.zeros((n_rows, n_cols), dtype=np.float64)
    # np.add.at handles the unbuffered scatter-add correctly when multiple
    # faces map to the same cell.
    np.add.at(accumulator, (rows[valid], cols[valid]), areas_3d[valid])

    # --- 8. Rugosity = accumulated 3D area / cell footprint (m²) ---
    _step("Computing rugosity…", 0.95)
    cell_footprint_m2 = cell_size_m * cell_size_m
    rugosity = accumulator / cell_footprint_m2

    rugosity_out = rugosity.astype(np.float32)
    rugosity_out[~inside_mask] = NODATA
    no_coverage = inside_mask & (accumulator == 0)
    rugosity_out[no_coverage] = NODATA

    # --- 9. Stats summary ---
    valid_mask = inside_mask & (accumulator > 0)
    n_valid = int(valid_mask.sum())
    if n_valid > 0:
        vals = rugosity[valid_mask]
        stats = {
            "n_cells": n_valid,
            "mean": float(np.mean(vals)),
            "median": float(np.median(vals)),
            "min": float(np.min(vals)),
            "max": float(np.max(vals)),
            "global": float(accumulator[valid_mask].sum() / (n_valid * cell_footprint_m2)),
        }
    else:
        stats = {"n_cells": 0, "mean": 0, "median": 0,
                 "min": 0, "max": 0, "global": 0}

    _step("Done.", 1.0)
    return rugosity_out, transform, shape_crs.wkt, stats


def _gr_write_geotiff(path, data, transform, crs_wkt):
    """Write a single-band float32 GeoTIFF with LZW compression."""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs=crs_wkt,
        transform=transform,
        nodata=NODATA,
        compress="lzw",
    ) as dst:
        dst.write(data, 1)


def _gr_format_stats_text(raster_label, cell_size_m, stats, disk_path=None):
    '''Build the human-readable summary used by both the completion popup
    and the (optional) sibling .txt file. Centralised so the popup and the
    on-disk record can't drift apart — the popup is what users see in the
    moment; the .txt is what they'll come back to weeks later when doing
    cross-timepoint analysis. Same text in both keeps records honest.

    `disk_path` is the GeoTIFF location; included as a trailing line so the
    .txt is self-describing (you can tell which raster it goes with even
    if filenames get rearranged).
    '''
    lines = [
        'Imported as DEM "{}" in the active chunk.'.format(raster_label),
        '',
        'Cell size: {:.2f} m'.format(cell_size_m),
        'Cells with data: {}'.format(stats['n_cells']),
        'Mean: {:.3f}'.format(stats['mean']),
        'Median: {:.3f}'.format(stats['median']),
        'Max: {:.3f}'.format(stats['max']),
        '',
        'Global rugosity (sum of 3D area / total cell footprint): {:.3f}'.format(
            stats['global']),
        '(Matches what "Calculate Surface Area Ratio" reports for the whole plot.)',
    ]
    if disk_path:
        lines += ['', 'Also saved to: {}'.format(disk_path)]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class GriddedRugosityDlg(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Gridded Rugosity")

        self.doc = Metashape.app.document
        self.chunk = self.doc.chunk if self.doc else None
        self.project_folder = (os.path.dirname(self.doc.path)
                               if (self.doc and self.doc.path) else "")
        self.output_dir = self.project_folder

        # Persisted settings (cell size, last save-to-disk choice). Stored
        # under a separate "GriddedRugosity" key so this tool's preferences
        # don't entangle with the Full Workflow dialog's settings.
        self.settings = QtCore.QSettings("ReefShape", "GriddedRugosity")

        # --- Widgets ---
        intro = QtWidgets.QLabel(
            "Computes per-cell rugosity (3D / 2D surface area ratio) on a "
            "user-selected grid within the active chunk's OuterBoundary "
            "polygon. The result is imported back into the chunk as a "
            'labeled DEM, alongside (not replacing) the project\'s real DEM.\n\n'
            "Captures overhangs correctly via single-pass mesh iteration — "
            "no per-cell mesh clipping, so it's fast even on large meshes.\n\n"
            "Requires a 3D model and an OuterBoundary polygon in the active "
            "chunk."
        )
        intro.setWordWrap(True)

        # Cell-size slider. QSlider is integer-valued, so we work in steps
        # of CELL_SIZE_STEP_M (0.25 m); the displayed/stored value is the
        # step count × step size. Reasonable range is 0.25 m (fine-grained,
        # bigger output raster) to 5 m (coarse, tiny output raster).
        self.labelCellSize = QtWidgets.QLabel("Cell size:")
        self.sliderCellSize = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        n_steps_min = int(round(MIN_CELL_SIZE_M / CELL_SIZE_STEP_M))
        n_steps_max = int(round(MAX_CELL_SIZE_M / CELL_SIZE_STEP_M))
        n_steps_default = int(round(DEFAULT_CELL_SIZE_M / CELL_SIZE_STEP_M))
        # Restore the last-used cell size from QSettings; fall back to the
        # default if the saved value is missing or outside the slider range
        # (which could happen if MIN/MAX/STEP are changed in a future release).
        saved_cell_size_m = self.settings.value(
            "cell_size_m", DEFAULT_CELL_SIZE_M, type=float)
        n_steps_initial = int(round(saved_cell_size_m / CELL_SIZE_STEP_M))
        n_steps_initial = max(n_steps_min, min(n_steps_max, n_steps_initial))
        self.sliderCellSize.setRange(n_steps_min, n_steps_max)
        self.sliderCellSize.setValue(n_steps_initial)
        self.sliderCellSize.setTickPosition(QtWidgets.QSlider.TicksBelow)
        # A tick at each integer-meter mark so the slider is easy to land on.
        self.sliderCellSize.setTickInterval(int(round(1.0 / CELL_SIZE_STEP_M)))
        self.sliderCellSize.setToolTip(
            "Grid cell size in meters. Smaller cells give a finer rugosity "
            "raster but a larger output file; larger cells give a smaller, "
            "coarser raster. 1 m is the typical default for reef plots.")
        self.labelCellSizeValue = QtWidgets.QLabel()
        self.labelCellSizeValue.setMinimumWidth(60)
        self._refreshCellSizeLabel()
        self.sliderCellSize.valueChanged.connect(self._refreshCellSizeLabel)

        self.checkSaveDisk = QtWidgets.QCheckBox(
            "Also save raster to disk (GeoTIFF)")
        # Restore the saved choice for both export checkboxes. Defaults
        # to False on first launch — most users compute many times before
        # they need an on-disk copy.
        self.checkSaveDisk.setChecked(
            self.settings.value("save_to_disk", False, type=bool))
        self.checkSaveDisk.toggled.connect(self._onSaveDiskToggled)

        self.checkSaveStats = QtWidgets.QCheckBox(
            "Also save stats .txt alongside the raster")
        self.checkSaveStats.setChecked(
            self.settings.value("save_stats", False, type=bool))
        self.checkSaveStats.setToolTip(
            "When the raster is saved, also write a sibling .txt file "
            "(same basename, .txt extension) containing the cell-size, "
            "per-cell statistics, and global rugosity — i.e. everything "
            "shown in the completion popup. Useful for record-keeping and "
            "for downstream analysis scripts that need to read the summary "
            "without opening the GeoTIFF.")

        self.labelOutDir = QtWidgets.QLabel("Output Folder:")
        self.txtOutDir = QtWidgets.QPlainTextEdit(self.output_dir or "(no folder selected)")
        self.txtOutDir.setFixedHeight(40)
        self.txtOutDir.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtOutDir.setReadOnly(True)
        self.btnOutDir = QtWidgets.QPushButton("Select Folder")
        self.btnOutDir.clicked.connect(self.pickOutDir)

        self.btnOk = QtWidgets.QPushButton("Compute")
        self.btnOk.setFixedSize(100, 40)
        self.btnClose = QtWidgets.QPushButton("Close")
        self.btnClose.setFixedSize(100, 40)

        # --- Layout ---
        cell_layout = QtWidgets.QHBoxLayout()
        cell_layout.addWidget(self.labelCellSize)
        cell_layout.addWidget(self.sliderCellSize, 1)
        cell_layout.addWidget(self.labelCellSizeValue)

        dir_layout = QtWidgets.QHBoxLayout()
        dir_layout.addWidget(self.labelOutDir)
        dir_layout.addWidget(self.txtOutDir)
        dir_layout.addWidget(self.btnOutDir)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.btnOk)
        btn_layout.addWidget(self.btnClose)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(intro)
        main_layout.addLayout(cell_layout)
        main_layout.addWidget(self.checkSaveDisk)
        main_layout.addWidget(self.checkSaveStats)
        main_layout.addLayout(dir_layout)
        main_layout.addStretch(1)
        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

        sb_extent = QtWidgets.QApplication.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent)
        self.setMinimumWidth(main_layout.sizeHint().width() + sb_extent + 20)

        # Folder picker is disabled until the user opts into disk export.
        self._onSaveDiskToggled(self.checkSaveDisk.isChecked())

        # --- Signals ---
        self.btnOk.clicked.connect(self.run)
        self.btnClose.clicked.connect(self.reject)

    def _cellSize(self):
        '''Current slider value converted to meters.'''
        return self.sliderCellSize.value() * CELL_SIZE_STEP_M

    def _refreshCellSizeLabel(self):
        '''Slot wired to slider valueChanged.'''
        self.labelCellSizeValue.setText("{:.2f} m".format(self._cellSize()))

    def _onSaveDiskToggled(self, checked):
        # The stats .txt is written alongside the raster, so it only makes
        # sense when the raster itself is being saved to disk. Disable
        # (but don't uncheck) when the raster save is off — preserves the
        # user's preference for next time they enable disk export.
        self.labelOutDir.setEnabled(checked)
        self.txtOutDir.setEnabled(checked)
        self.btnOutDir.setEnabled(checked)
        self.checkSaveStats.setEnabled(checked)

    def pickOutDir(self):
        start = self.output_dir or self.project_folder or ""
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select output folder", start)
        if d:
            self.output_dir = d
            self.txtOutDir.setPlainText(d)

    def run(self):
        # Wrapper that always re-enables the dialog on error so the user can
        # adjust and retry without restarting Metashape.
        try:
            self._runImpl()
        except Exception as e:
            import traceback
            traceback.print_exc()
            _gr_show_error(self, "Gridded rugosity failed", str(e))
            self.setEnabled(True)

    def _runImpl(self):
        if not self.chunk:
            raise RuntimeError("No active chunk.")
        if not self.chunk.model:
            raise RuntimeError(
                "Active chunk has no 3D model. Build a mesh before running "
                "this script.")
        if not self.chunk.shapes:
            raise RuntimeError(
                "Active chunk has no shapes. Create an OuterBoundary polygon "
                "(see scripts 06 or 08) before running this script.")
        boundary = next(
            (s for s in self.chunk.shapes
             if s.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary),
            None,
        )
        if boundary is None:
            raise RuntimeError(
                "No OuterBoundary polygon found in chunk shapes. Create one "
                "(see scripts 06 or 08) before running this script.")

        save_to_disk = self.checkSaveDisk.isChecked()
        save_stats = save_to_disk and self.checkSaveStats.isChecked()
        if save_to_disk and (not self.output_dir or not os.path.isdir(self.output_dir)):
            raise RuntimeError(
                "Disk export is checked but no valid output folder is "
                "selected. Either uncheck \"Also save raster to disk\" or "
                "pick a folder.")

        cell_size_m = self._cellSize()
        # Remember the slider choice and the two save checkboxes for next
        # launch — most users settle on one or two resolutions for their
        # plot sizes and don't want to re-dial them every time.
        self.settings.setValue("cell_size_m", cell_size_m)
        self.settings.setValue("save_to_disk", save_to_disk)
        self.settings.setValue("save_stats", self.checkSaveStats.isChecked())
        # Filename token in centimeters keeps the value integer regardless
        # of the chosen step (25cm, 50cm, 100cm, ...) so we never end up
        # with awkward decimals in filenames.
        cell_size_cm = int(round(cell_size_m * 100))
        raster_label = "{} ({:.2f}m grid)".format(RASTER_LABEL_PREFIX, cell_size_m)

        # Build a target filename (used both for disk export and as the
        # importRaster source name; Metashape sometimes uses the filename
        # for the chunk-product label until we rename it).
        project_name = os.path.basename(self.doc.path or "untitled")
        for ext in (".psx", ".psz", ".files"):
            if project_name.lower().endswith(ext):
                project_name = project_name[:-len(ext)]
                break
        chunk_label = self.chunk.label or "chunk"
        out_basename = "{}_{}_rugosity_{}cm.tif".format(
            project_name, chunk_label, cell_size_cm)

        self.setEnabled(False)
        progress = _GriddedRugosityProgressDialog(self, "Gridded Rugosity")
        progress.show()
        QtWidgets.QApplication.processEvents()

        temp_dir = tempfile.mkdtemp(prefix="reefshape_rugosity_")
        temp_path = os.path.join(temp_dir, out_basename)
        try:
            print("Gridded Rugosity:")
            print("  cell size: {:.2f} m".format(cell_size_m))
            print("  temp file: {}".format(temp_path))

            data, transform, crs_wkt, stats = compute_gridded_rugosity(
                self.chunk, boundary, cell_size_m, progress=progress)

            progress.set_status("Writing GeoTIFF…")
            progress.set_progress(0.95)
            _gr_write_geotiff(temp_path, data, transform, crs_wkt)

            progress.set_status("Importing into chunk as DEM…")
            progress.set_progress(0.98)
            new_elev = _gr_import_raster_to_chunk(self.chunk, temp_path, raster_label)
            if new_elev is None:
                print("  WARNING: importRaster succeeded but no new elevation "
                      "entry was found in the chunk. Skipping rename.")

            disk_path = None
            stats_path = None
            if save_to_disk:
                # Copy the temp file to the user's chosen folder. We could
                # write it directly there too, but routing through temp
                # keeps the import step uniform regardless of save choice.
                import shutil
                disk_path = os.path.join(self.output_dir, out_basename)
                shutil.copy2(temp_path, disk_path)
                print("  saved to: {}".format(disk_path))
                if save_stats:
                    # Sibling .txt with the same basename (e.g.
                    # foo_rugosity_100cm.tif → foo_rugosity_100cm.txt).
                    # Matches the popup contents exactly so the file is a
                    # complete record without round-tripping through the
                    # GUI.
                    stats_path = os.path.splitext(disk_path)[0] + ".txt"
                    stats_body = _gr_format_stats_text(
                        raster_label, cell_size_m, stats, disk_path=disk_path)
                    with open(stats_path, "w", encoding="utf-8") as f:
                        f.write(stats_body)
                    print("  stats: {}".format(stats_path))

            progress.set_status("Done.")
            progress.set_progress(1.0)

            print("  done.")
            print("  cells with data: {}".format(stats["n_cells"]))
            if stats["n_cells"] > 0:
                print("  mean: {:.3f} | median: {:.3f} | min: {:.3f} | "
                      "max: {:.3f}".format(stats["mean"], stats["median"],
                                           stats["min"], stats["max"]))
                print("  global rugosity: {:.3f}".format(stats["global"]))

            summary = _gr_format_stats_text(
                raster_label, cell_size_m, stats, disk_path=disk_path)
            if stats_path:
                summary += "\nStats text: {}".format(stats_path)

            # Close the progress dialog before showing the result so it
            # doesn't sit behind the message box.
            progress.close()
            QtWidgets.QMessageBox.information(self, "Gridded Rugosity", summary)
            self.accept()
        finally:
            progress.close()
            self.setEnabled(True)
            # Clean up temp file + dir. shutil.rmtree handles non-empty
            # dirs; ignore_errors covers the unlikely race with antivirus
            # scanners holding the file open briefly after the import.
            import shutil
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


def run_script():
    try:
        app = QtWidgets.QApplication.instance()
        parent = app.activeWindow() if app else None
        dlg = GriddedRugosityDlg(parent)
        dlg.exec()
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "Error", str(e))


# --- Menu registration ---
label = "ReefShape/Tools/Gridded Rugosity"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
