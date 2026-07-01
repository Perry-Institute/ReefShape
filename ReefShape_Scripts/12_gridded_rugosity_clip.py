"""
Gridded Rugosity (Exact)
Will Greene, Perry Institute for Marine Science

Per-cell 3D-to-2D surface area ratio (rugosity) on a grid within the active
chunk's OuterBoundary polygon. Result is imported back into the chunk as a
labeled DEM alongside the project's real DEM, with optional GeoTIFF + stats
sidecar written to disk.

Difference from 11_gridded_rugosity.py:

  This tool computes each cell's 3D area by asking Metashape directly —
  clipping a duplicated copy of the mesh to the cell's rectangle and
  reading mesh.area() on the result. The other tool does per-triangle
  math in numpy, going through a chain of coordinate transforms
  (chunk.transform, model.transform, ECEF→lat/long) that has to be
  re-implemented and re-validated for each CRS type. Both should give
  the same answer for a correctly configured chunk, but the numpy
  version has had a long trail of transform-related bugs on geographic
  CRSes, and its per-cell values could disagree with what you get from
  a manual "clip mesh to 1 m² polygon, read mesh.area()" check by 20%
  or more. This tool matches that manual check by construction: it
  literally does the manual clip-and-measure operation, once per cell.

  Cost is speed. Each cell takes a few seconds (mesh clip + duplicate +
  area read), so a 350-cell plot takes 10–20 minutes instead of the ~4
  minutes the numpy tool takes. Progress dialog reports "Cell N of M"
  with elapsed time and ETA. Cells outside the boundary are skipped
  entirely — for a typical non-rectangular plot that's 30–40% of the
  grid, so the actual runtime is proportionally lower.

Requires: numpy, rasterio (both auto-installed on first run).
"""

import math
import os
import tempfile
import time

import Metashape
from PySide2 import QtCore, QtGui, QtWidgets

from modules.pip_auto_install import pip_install

pip_install("""numpy==1.26.4
rasterio>=1.4,<2
""")

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from rasterio.features import rasterize as rio_rasterize  # noqa: E402


DEFAULT_CELL_SIZE_M = 1.0
MIN_CELL_SIZE_M = 0.25
MAX_CELL_SIZE_M = 5.0
CELL_SIZE_STEP_M = 0.25
NODATA = -9999.0
RASTER_LABEL_PREFIX = "Rugosity (Exact)"
CELL_SHAPE_LABEL = "Rugosity cell temp"  # marker for cleanup on failure
ROW_STRIP_SHAPE_LABEL = "Rugosity row strip temp"  # same, for row-strip mode
ROW_STRIP_MODEL_LABEL_PREFIX = "Rugosity row"  # so we can find/delete strays


# ---------------------------------------------------------------------------
# Error dialog
# ---------------------------------------------------------------------------

def _show_error(parent, title, msg):
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Critical)
    box.setWindowTitle("Error")
    box.setText(title)
    box.setInformativeText(msg)
    box.exec_()


# ---------------------------------------------------------------------------
# Progress dialog
# ---------------------------------------------------------------------------

class _ProgressDialog(QtWidgets.QDialog):
    """Progress dialog with a status label, cell counter, elapsed/ETA
    readouts, and a determinate progress bar.

    Custom QDialog rather than QProgressDialog for the same reason
    pip_auto_install rolls its own — QProgressDialog auto-sizes on every
    setLabelText() and fights setFixedSize.
    """

    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.setFixedSize(500, 230)
        # Strip the close button so users can't dismiss mid-compute (the
        # Cancel button below is the sanctioned escape hatch).
        flags = self.windowFlags() & ~QtCore.Qt.WindowCloseButtonHint
        flags &= ~QtCore.Qt.WindowSystemMenuHint
        self.setWindowFlags(flags)

        self.cancelled = False

        self._heading = QtWidgets.QLabel("Computing gridded rugosity (exact)…")
        self._heading.setWordWrap(True)
        font = self._heading.font()
        font.setBold(True)
        self._heading.setFont(font)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: palette(mid);")
        self._status.setMaximumWidth(460)

        self._counter = QtWidgets.QLabel("")
        self._timing = QtWidgets.QLabel("")

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)

        self._cancel_btn = QtWidgets.QPushButton("Cancel")
        self._cancel_btn.setToolTip(
            "Stop after the current cell finishes. Metashape's in-flight "
            "DuplicateAsset task can't be interrupted mid-run, so the "
            "cancel takes effect between cells (up to ~30s wait on a "
            "large mesh).")
        self._cancel_btn.clicked.connect(self._onCancel)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._cancel_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(8)
        layout.addWidget(self._heading)
        layout.addWidget(self._status)
        layout.addWidget(self._counter)
        layout.addWidget(self._timing)
        layout.addStretch(1)
        layout.addWidget(self._bar)
        layout.addLayout(btn_row)

    def _onCancel(self):
        self.cancelled = True
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling…")
        self._status.setText(
            "Cancel requested — will stop after the current cell/strip.")
        QtWidgets.QApplication.processEvents()

    def set_status(self, text):
        self._status.setText(text)
        QtWidgets.QApplication.processEvents()

    def set_counter(self, current, total):
        if total > 0:
            self._counter.setText("Cell {} of {}".format(current, total))
        else:
            self._counter.setText("")
        QtWidgets.QApplication.processEvents()

    def set_timing(self, elapsed_s, eta_s):
        parts = []
        if elapsed_s is not None:
            parts.append("elapsed {}".format(_fmt_seconds(elapsed_s)))
        if eta_s is not None:
            parts.append("ETA {}".format(_fmt_seconds(eta_s)))
        self._timing.setText(" — ".join(parts))
        QtWidgets.QApplication.processEvents()

    def set_progress(self, fraction):
        f = max(0.0, min(1.0, fraction))
        self._bar.setValue(int(f * 100))
        QtWidgets.QApplication.processEvents()


def _fmt_seconds(s):
    """Human-friendly duration formatter: '32s', '2m 14s', '1h 07m'."""
    if s < 60:
        return "{}s".format(int(round(s)))
    if s < 3600:
        m = int(s // 60)
        sec = int(round(s - 60 * m))
        return "{}m {:02d}s".format(m, sec)
    h = int(s // 3600)
    m = int((s - 3600 * h) // 60)
    return "{}h {:02d}m".format(h, m)


# ---------------------------------------------------------------------------
# Boundary + raster helpers (shared shape with 11's implementation)
# ---------------------------------------------------------------------------

def _extract_boundary_ring(geom):
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


def _import_raster_to_chunk(chunk, path, label):
    """Import `path` as an Elevation product in `chunk` and label it.

    Records existing elevation keys, calls importRaster, then finds the
    freshly-added one and renames it. Preserves the previously-active
    elevation as the chunk's active DEM so the rugosity raster coexists
    with the real DEM rather than replacing it.
    """
    before_keys = set()
    if chunk.elevations:
        before_keys = {e.key for e in chunk.elevations}
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

    if prior_active is not None and chunk.elevation is not prior_active:
        chunk.elevation = prior_active

    return new_elev


def _format_stats_text(raster_label, cell_size_m, stats, disk_path=None):
    """Build the summary text used by both the completion popup and the
    optional sibling .txt file. Shared to keep on-screen and on-disk
    records identical.
    """
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


def write_geotiff(path, data, transform, crs_wkt):
    """Write a single-band float32 GeoTIFF with LZW compression."""
    with rasterio.open(
        path, "w", driver="GTiff",
        height=data.shape[0], width=data.shape[1], count=1,
        dtype="float32", crs=crs_wkt, transform=transform,
        nodata=NODATA, compress="lzw",
    ) as dst:
        dst.write(data, 1)


# ---------------------------------------------------------------------------
# Per-cell clip-and-measure
# ---------------------------------------------------------------------------

class _RugosityCancelled(Exception):
    """Raised by the compute loop when the user hits Cancel on the
    progress dialog. Caught in _runImpl to skip the "compute failed"
    error box and just tear down cleanly."""
    pass


def _silent_progress(percent):
    """No-op progress callback for Metashape task.apply() to suppress the
    default GUI progress dialog. Defined at module level (not a lambda) —
    some Metashape versions inspect the callable's signature via `inspect`
    and lambdas fail that check, causing the API to fall back to the
    default GUI progress and show the popup anyway.
    """
    return None


class _DuplicateModelDialogSuppressor(QtCore.QObject):
    """Application-wide event filter that auto-hides Metashape's
    'Duplicating model...' progress popup whenever it appears.

    The `progress=_silent_progress` callback on task.apply() controls what
    gets *reported* into the popup, but the GUI shell still spawns one
    for every DuplicateAsset call. With row-strip preprocessing we run
    ~n_rows + n_cells DuplicateAsset tasks per plot (hundreds of them),
    so the flicker is disruptive.

    Match rule: any top-level QWidget (window) whose title contains any
    of the known task-progress phrases (case-insensitive). We match on
    QWidget rather than QDialog because Metashape's progress popup isn't
    guaranteed to be a QDialog subclass in every version.

    We deliberately skip anything whose title contains 'rugosity' so
    the script's own _ProgressDialog isn't caught.

    First few matches print a one-line diagnostic identifying the widget
    class and title, so if this doesn't work as expected we can see
    exactly what's being spawned and refine the match.
    """

    _MATCH_SUBSTRS = ("duplicat", "processing", "loading model",
                      "loaded mesh", "task")
    _SKIP_SUBSTRS = ("rugosity",)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_budget = 3  # print info for the first 3 matches, then quiet

    def _matches(self, title_lower):
        if not title_lower:
            return False
        if any(s in title_lower for s in self._SKIP_SUBSTRS):
            return False
        return any(s in title_lower for s in self._MATCH_SUBSTRS)

    def eventFilter(self, obj, event):
        try:
            et = event.type()
            if et not in (QtCore.QEvent.Show, QtCore.QEvent.WindowActivate):
                return False
            if not isinstance(obj, QtWidgets.QWidget):
                return False
            if not obj.isWindow():
                return False
            title = obj.windowTitle() or ""
            title_l = title.lower()
            if not self._matches(title_l):
                return False
            if self._log_budget > 0:
                self._log_budget -= 1
                print("  suppressing task popup: class={} title={!r}".format(
                    type(obj).__name__, title))
            # Must use close() (or reject/done) — Metashape drives the
            # popup with a modal exec() loop, so hide()/WA_DontShowOnScreen
            # deadlocks the task. Queue the close via singleShot(0) so
            # Qt's event dispatch isn't interrupted mid-Show.
            QtCore.QTimer.singleShot(0, obj.close)
        except Exception:
            # An event filter must never raise — Qt will terminate the
            # app. Swallow anything unexpected.
            pass
        return False


def _measure_cell_area(chunk, left, right, bottom, top, z, source_model_key):
    """Add a cell rectangle as an OuterBoundary, duplicate the source model
    clipped to it, read mesh.area(), and clean up. Returns the clipped
    3D area in real m² (Metashape does the CRS/transform math itself).

    Assumes the plot's real OuterBoundary polygon has been temporarily
    demoted to NoBoundary by the caller — the cell rectangle is the only
    active OuterBoundary during the clip.

    Raises on Metashape task failure (caller decides whether to abort or
    record a NaN for that cell and continue).
    """
    cell_shape = chunk.shapes.addShape()
    cell_shape.label = CELL_SHAPE_LABEL
    cell_shape.geometry.type = Metashape.Geometry.Type.PolygonType
    cell_shape.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
    # CCW winding — Metashape doesn't strictly require it since
    # boundary_type is explicit, but some versions have been picky and
    # CCW is the harmless choice.
    corners = [
        Metashape.Vector([float(left), float(bottom), float(z)]),
        Metashape.Vector([float(right), float(bottom), float(z)]),
        Metashape.Vector([float(right), float(top), float(z)]),
        Metashape.Vector([float(left), float(top), float(z)]),
    ]
    cell_shape.geometry = Metashape.Geometry.Polygon(corners)

    try:
        task = Metashape.Tasks.DuplicateAsset()
        task.asset_key = source_model_key
        task.asset_type = Metashape.DataSource.ModelData
        task.clip_to_boundary = True
        # Pass a no-op progress callback to route progress reporting away
        # from Metashape's built-in GUI progress dialog. Uses a proper
        # module-level `def` rather than a lambda because some Metashape
        # versions do signature introspection on the callback and reject
        # lambdas, silently falling back to the default GUI dialog.
        task.apply(chunk, progress=_silent_progress)
        # chunk.model is now the duplicated (clipped) mesh
        try:
            area = float(chunk.model.area())
        except Exception:
            # Empty/degenerate clip result — count as zero.
            area = 0.0
        # Remove the duplicate. Metashape reverts chunk.model to whichever
        # model remains in the chunk (i.e. the original source model, since
        # we duplicated from it).
        chunk.remove(chunk.model)
    finally:
        # Always remove the cell shape, even if the duplicate/measure step
        # threw. Prevents cell polygons from accumulating in the chunk
        # across failed iterations.
        try:
            chunk.shapes.remove(cell_shape)
        except Exception:
            pass

    return area


def _build_row_strip(chunk, row, left_x, right_x, top_y, cell_size_units,
                     cell_z, source_model_key):
    """Duplicate the source mesh clipped to a horizontal strip covering
    all columns of one row. Returns the strip model handle for the caller
    to track and later delete.

    Row-strip preprocessing is the main speed lever for large source
    meshes: a per-cell DuplicateAsset walks the source's *entire* face
    list on every clip (O(source_faces)), so 269 cells × 152 M faces is
    the cost we started with. Building one strip per row (22 clips of
    the source at ~15 s each) and then per-cell clipping the *strip*
    (small — maybe 7 M faces for a 1 m tall × plot-wide strip) drops per-
    cell cost from ~30 s to ~1.5 s. Net: ~15 min instead of ~2.5 h.

    Returns None if the clip fails; caller falls back to using the full
    source for cells in that row.
    """
    strip_top = top_y - row * cell_size_units
    strip_bottom = top_y - (row + 1) * cell_size_units
    strip_shape = chunk.shapes.addShape()
    strip_shape.label = ROW_STRIP_SHAPE_LABEL
    strip_shape.geometry.type = Metashape.Geometry.Type.PolygonType
    strip_shape.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
    corners = [
        Metashape.Vector([float(left_x), float(strip_bottom), float(cell_z)]),
        Metashape.Vector([float(right_x), float(strip_bottom), float(cell_z)]),
        Metashape.Vector([float(right_x), float(strip_top), float(cell_z)]),
        Metashape.Vector([float(left_x), float(strip_top), float(cell_z)]),
    ]
    strip_shape.geometry = Metashape.Geometry.Polygon(corners)

    try:
        task = Metashape.Tasks.DuplicateAsset()
        task.asset_key = source_model_key
        task.asset_type = Metashape.DataSource.ModelData
        task.clip_to_boundary = True
        task.apply(chunk, progress=_silent_progress)
        row_model = chunk.model
        row_model.label = "{} {} temp".format(ROW_STRIP_MODEL_LABEL_PREFIX, row)
        return row_model
    except Exception as exc:
        print("  row {} strip build failed: {}; falling back to source "
              "for this row".format(row, exc))
        return None
    finally:
        try:
            chunk.shapes.remove(strip_shape)
        except Exception:
            pass


def _cleanup_stray_shapes(chunk):
    """Remove any cell rectangles / row-strip rectangles left in
    chunk.shapes from a prior failed run. Matches by label so we don't
    accidentally delete unrelated user-created shapes.
    """
    if not chunk.shapes:
        return
    strays = [s for s in chunk.shapes
              if s.label in (CELL_SHAPE_LABEL, ROW_STRIP_SHAPE_LABEL)]
    for s in strays:
        try:
            chunk.shapes.remove(s)
        except Exception:
            pass


def _cleanup_stray_row_models(chunk):
    """Delete any row-strip models left behind from a prior failed run.
    Matches by label prefix so we don't accidentally delete unrelated
    user-created models.
    """
    if not chunk.models:
        return
    strays = [m for m in chunk.models
              if m.label and m.label.startswith(ROW_STRIP_MODEL_LABEL_PREFIX)]
    for m in strays:
        try:
            chunk.remove(m)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_gridded_rugosity_exact(chunk, boundary, cell_size_m,
                                   use_row_strips=True, progress=None):
    """Returns (rugosity_float32, geotransform, crs_wkt, stats).

    For each cell whose center is inside `boundary`, duplicates the mesh
    clipped to that cell's rectangle and reads mesh.area() to get the 3D
    surface area in real m². Rugosity is that area divided by the cell's
    ground footprint (cell_size_m²).

    `use_row_strips`: when True (default), preprocess each row of cells
    by first clipping the source mesh to a full-width row strip; then
    do per-cell clips against the strip mesh instead of the source. Much
    faster for large source meshes — see _build_row_strip() for the
    reasoning. When False, per-cell clips run against the full source
    (slower but simpler code path; useful for A/B comparison).

    `progress` is an optional object with set_status/set_counter/
    set_timing/set_progress methods — updated between phases and per-cell
    so the dialog stays responsive.
    """
    def _step(text, fraction):
        if progress is not None:
            progress.set_status(text)
            progress.set_progress(fraction)

    if not chunk.model:
        raise RuntimeError("Active chunk has no model to measure.")
    source_model = chunk.model
    source_model_key = source_model.key

    shape_crs = chunk.shapes.crs if (chunk.shapes and chunk.shapes.crs) else chunk.crs
    if shape_crs is None:
        raise RuntimeError("Chunk has no CRS set on shapes or on the chunk itself.")

    # --- 1. Boundary polygon + bbox + reference Z ---
    _step("Reading boundary polygon…", 0.02)
    ring = _extract_boundary_ring(boundary.geometry)
    boundary_xy = np.array([(v.x, v.y) for v in ring], dtype=np.float64)
    if len(boundary_xy) < 3:
        raise RuntimeError("Boundary polygon has fewer than 3 vertices.")
    bx_min, by_min = boundary_xy.min(axis=0)
    bx_max, by_max = boundary_xy.max(axis=0)
    # Median Z from the plot boundary — used verbatim for the corners of
    # every cell rectangle. Z doesn't affect 2D clipping but Metashape
    # stores it on the shape regardless, and matching the plot boundary's
    # Z keeps the temporary shapes visually consistent if the loop is
    # interrupted and the user goes looking at chunk.shapes.
    cell_z = float(np.median([float(v.z) for v in ring]))
    print("  boundary bbox: [{:.6g}, {:.6g}] × [{:.6g}, {:.6g}] shape units".format(
        bx_min, bx_max, by_min, by_max))
    print("  cell Z: {:.6g}".format(cell_z))

    # --- 2. Cell size in shape CRS units (localframe probe) ---
    # We only need `shape_per_meter` for two things: to size the cell
    # rectangles on the ground correctly, and to write the GeoTIFF's
    # affine transform. No area math depends on it — that all goes
    # through Metashape's mesh.area().
    _step("Calibrating shape-CRS units per meter…", 0.05)
    bx_center = 0.5 * (bx_min + bx_max)
    by_center = 0.5 * (by_min + by_max)
    ref_shape_vec = Metashape.Vector(
        [float(bx_center), float(by_center), cell_z])
    shape_per_meter = None
    try:
        ref_world_vec = shape_crs.unproject(ref_shape_vec)
        local_to_ecef = chunk.crs.localframe(ref_world_vec).inv()
        ref_shape_proj = shape_crs.project(ref_world_vec)
        east_world = local_to_ecef.mulp(Metashape.Vector([1.0, 0.0, 0.0]))
        east_shape = shape_crs.project(east_world)
        east_dist = math.hypot(east_shape.x - ref_shape_proj.x,
                               east_shape.y - ref_shape_proj.y)
        north_world = local_to_ecef.mulp(Metashape.Vector([0.0, 1.0, 0.0]))
        north_shape = shape_crs.project(north_world)
        north_dist = math.hypot(north_shape.x - ref_shape_proj.x,
                                north_shape.y - ref_shape_proj.y)
        shape_per_meter = 0.5 * (east_dist + north_dist)
    except Exception as e:
        # Fallback for CRSes where localframe isn't implemented — assume
        # shape units == meters (LOCAL_CS / UTM / other projected metric).
        print("  note: localframe unavailable ({}); assuming shape units == "
              "meters".format(e))
        shape_per_meter = 1.0

    cell_size_units = cell_size_m * shape_per_meter
    print("  shape-CRS units per meter: {:.6g}".format(shape_per_meter))
    print("  cell size: {:.6g} shape-CRS units ({} m on the ground)".format(
        cell_size_units, cell_size_m))

    # --- 3. Grid anchored to boundary bbox corner ---
    # Anchoring to (bx_min, by_max) guarantees cross-timepoint pixel
    # alignment when the same boundary polygon is used on multiple
    # timepoints — the boundary is deterministic, so cell corners land
    # at deterministic lat/long positions.
    _step("Building grid…", 0.08)
    left_x = float(bx_min)
    top_y = float(by_max)
    n_cols = max(1, int(math.ceil((bx_max - bx_min) / cell_size_units)))
    n_rows = max(1, int(math.ceil((by_max - by_min) / cell_size_units)))
    transform = from_origin(left_x, top_y, cell_size_units, cell_size_units)
    print("  grid: {} cols x {} rows ({} cells total)".format(
        n_cols, n_rows, n_cols * n_rows))

    # --- 4. Inside-boundary mask ---
    # Same rasterization as 11: mask a cell if its CENTER is inside the
    # boundary polygon (all_touched=False), matching that tool's behavior
    # so users can compare outputs directly.
    _step("Building boundary mask…", 0.10)
    poly_geo = {"type": "Polygon", "coordinates": [boundary_xy.tolist()]}
    inside_mask = rio_rasterize(
        [(poly_geo, 1)],
        out_shape=(n_rows, n_cols),
        transform=transform,
        fill=0,
        dtype=np.uint8,
        all_touched=False,
    ).astype(bool)

    inside_rows, inside_cols = np.where(inside_mask)
    n_to_process = len(inside_rows)
    print("  cells to process (inside boundary): {}".format(n_to_process))
    if n_to_process == 0:
        raise RuntimeError(
            "No grid cells fall inside the boundary polygon. Check that "
            "the boundary is not empty and that the cell size isn't larger "
            "than the boundary.")

    # --- 5. Per-cell clip-and-measure loop ---
    #
    # State discipline:
    #
    #   Loop-level (once):
    #     - Save plot_boundary's boundary_type.
    #     - Demote it to NoBoundary so cell rectangles are the sole
    #       OuterBoundary during clipping.
    #     - Save chunk's currently-active model so we can restore it
    #       after the loop juggles duplicates and row strips.
    #     - Cleanup guarantees plot_boundary gets restored to whatever it
    #       was, any stray cell/strip shapes get deleted, any stray row-
    #       strip models get deleted, and chunk.model gets restored.
    #
    #   Per-row (when row-strip preprocessing is enabled):
    #     - Delete the previous row's strip model (if any).
    #     - Build a new strip: add row-strip rectangle as OuterBoundary,
    #       DuplicateAsset from the ORIGINAL source model, keep the
    #       result. This costs ~15 s once per row (~22 for a typical
    #       plot) but shrinks the mesh subsequent per-cell clips work
    #       against by ~1/n_rows.
    #     - Point the per-cell source key at this row's strip model.
    #     - If the strip build fails (rare), fall back to the original
    #       source for cells in this row.
    #
    #   Per-cell (each iteration):
    #     - Add cell rectangle as OuterBoundary.
    #     - DuplicateAsset from the current source (strip or original).
    #     - Read mesh.area() from the duplicate.
    #     - Delete the duplicate.
    #     - Remove the cell rectangle.
    #
    # We deliberately do NOT restore plot_boundary between cells (per the
    # earlier design discussion) — the toggle would be n_cells * 2
    # unnecessary state changes. Just disable once, restore once.
    original_boundary_type = boundary.boundary_type
    original_active_model = chunk.model
    accumulator = np.zeros((n_rows, n_cols), dtype=np.float64)

    _step("Clipping and measuring cells…", 0.12)
    loop_start = time.time()
    boundary.boundary_type = Metashape.Shape.BoundaryType.NoBoundary

    # Compute the row-strip's full-width extent once; every row uses the
    # same X bounds, only the Y range changes per row.
    strip_left = left_x
    strip_right = left_x + n_cols * cell_size_units

    current_row = -1
    current_row_model = None
    current_source_key = source_model_key  # what to clip *from* per cell

    # Suppress Metashape's per-task 'Duplicating model...' popup for the
    # duration of the loop. Every DuplicateAsset call spawns one; without
    # this we'd get hundreds of popups flickering across the screen.
    dup_suppressor = _DuplicateModelDialogSuppressor()
    qt_app = QtWidgets.QApplication.instance()
    if qt_app is not None:
        qt_app.installEventFilter(dup_suppressor)

    try:
        for i in range(n_to_process):
            # Honor a user cancel between cells. Metashape's in-flight
            # DuplicateAsset can't be interrupted, so cancellation is
            # necessarily coarse-grained (~one cell late).
            if progress is not None and getattr(progress, "cancelled", False):
                raise _RugosityCancelled(
                    "cancelled by user after cell {} of {}".format(
                        i, n_to_process))

            row = int(inside_rows[i])
            col = int(inside_cols[i])

            # Row transition: build a fresh strip for this row (if
            # row-strip preprocessing is enabled).
            if use_row_strips and row != current_row:
                # Delete previous row's strip model, if any.
                if current_row_model is not None:
                    try:
                        chunk.remove(current_row_model)
                    except Exception as exc:
                        print("  note: could not remove previous row strip "
                              "model ({}); will be swept in cleanup".format(exc))
                    current_row_model = None

                if progress is not None:
                    progress.set_status(
                        "Building row strip {} of {}…".format(
                            row + 1, n_rows))
                current_row_model = _build_row_strip(
                    chunk, row, strip_left, strip_right, top_y,
                    cell_size_units, cell_z, source_model_key)
                if current_row_model is not None:
                    current_source_key = current_row_model.key
                else:
                    # Strip build failed — fall back to full source for
                    # this row's cells.
                    current_source_key = source_model_key
                current_row = row
                if progress is not None:
                    progress.set_status(
                        "Clipping and measuring cells…")

            left = left_x + col * cell_size_units
            right = left_x + (col + 1) * cell_size_units
            top = top_y - row * cell_size_units
            bottom = top_y - (row + 1) * cell_size_units

            try:
                area = _measure_cell_area(
                    chunk, left, right, bottom, top, cell_z, current_source_key)
            except Exception as exc:
                # If a single cell's clip fails (unusual, but possible for
                # degenerate mesh regions), log and record 0 so the loop
                # keeps going. The cell will end up as nodata below.
                print("  cell ({}, {}) failed: {}".format(row, col, exc))
                area = 0.0

            accumulator[row, col] = area

            # Progress reporting: pump every cell so the counter doesn't
            # look frozen. ETA is a straight linear extrapolation from
            # the average time-per-cell so far (row-strip build time is
            # included in "elapsed", so the ETA absorbs those 22 costly
            # steps into a slightly higher effective per-cell rate).
            if progress is not None:
                elapsed = time.time() - loop_start
                done = i + 1
                eta = elapsed * (n_to_process - done) / done if done > 0 else None
                progress.set_counter(done, n_to_process)
                progress.set_timing(elapsed, eta)
                # Reserve 12–95% of the overall progress bar for the loop;
                # remaining 5% for post-loop tasks (stats + GeoTIFF write).
                progress.set_progress(0.12 + 0.83 * (done / n_to_process))
    finally:
        # Remove the duplicate-model popup suppressor first thing — if
        # cleanup below raises, we don't want to leak a global event
        # filter into the app.
        if qt_app is not None:
            try:
                qt_app.removeEventFilter(dup_suppressor)
            except Exception:
                pass
        # Delete the final row's strip model, if any.
        if current_row_model is not None:
            try:
                chunk.remove(current_row_model)
            except Exception:
                pass
            current_row_model = None
        # Belt-and-suspenders: sweep up any cell/strip rectangles and any
        # stray row-strip models that might have survived failure paths.
        _cleanup_stray_shapes(chunk)
        _cleanup_stray_row_models(chunk)
        # Restore plot boundary type no matter what happened in the loop.
        boundary.boundary_type = original_boundary_type
        # Restore the chunk's originally-active model. Removing duplicates
        # and row strips can leave chunk.model pointing at whichever mesh
        # happens to remain — usually the original source, but not
        # guaranteed if there are user-created extra models. Set it back
        # explicitly.
        if (original_active_model is not None
                and chunk.model is not original_active_model):
            try:
                chunk.model = original_active_model
            except Exception:
                pass

    # --- 6. Rugosity + stats ---
    _step("Computing rugosity + stats…", 0.96)
    cell_footprint_m2 = cell_size_m * cell_size_m
    rugosity = accumulator / cell_footprint_m2
    rugosity_out = rugosity.astype(np.float32)
    rugosity_out[~inside_mask] = NODATA
    # Cells inside the boundary but with zero mesh coverage → nodata.
    no_coverage = inside_mask & (accumulator == 0)
    rugosity_out[no_coverage] = NODATA

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
            "global": float(accumulator[valid_mask].sum()
                            / (n_valid * cell_footprint_m2)),
        }
    else:
        stats = {"n_cells": 0, "mean": 0, "median": 0,
                 "min": 0, "max": 0, "global": 0}

    _step("Done.", 1.0)
    return rugosity_out, transform, shape_crs.wkt, stats


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class GriddedRugosityExactDlg(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Gridded Rugosity (Exact)")

        self.doc = Metashape.app.document
        self.chunk = self.doc.chunk if self.doc else None
        self.project_folder = (os.path.dirname(self.doc.path)
                               if (self.doc and self.doc.path) else "")
        self.output_dir = self.project_folder

        # Persisted settings — separate namespace from the numpy tool so
        # both can be tuned independently.
        self.settings = QtCore.QSettings("ReefShape", "GriddedRugosityExact")

        # --- Widgets ---
        intro = QtWidgets.QLabel(
            "Computes per-cell rugosity (3D / 2D surface area ratio) on a "
            "user-selected grid within the active chunk's OuterBoundary "
            "polygon. For each cell inside the boundary, a duplicate of "
            "the mesh is clipped to the cell's rectangle and its area "
            "read via Metashape's own mesh.area() — the same measurement "
            "you would get from a manual clip-and-measure. Slower than "
            "the numpy version but validated by construction. The result "
            "is imported back into the chunk as a labeled DEM.\n\n"
            "Requires a 3D model and an OuterBoundary polygon in the "
            "active chunk."
        )
        intro.setWordWrap(True)

        # Cell-size slider (persisted, restored on next launch).
        self.labelCellSize = QtWidgets.QLabel("Cell size:")
        self.sliderCellSize = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        n_steps_min = int(round(MIN_CELL_SIZE_M / CELL_SIZE_STEP_M))
        n_steps_max = int(round(MAX_CELL_SIZE_M / CELL_SIZE_STEP_M))
        n_steps_default = int(round(DEFAULT_CELL_SIZE_M / CELL_SIZE_STEP_M))
        saved_cell_size_m = self.settings.value(
            "cell_size_m", DEFAULT_CELL_SIZE_M, type=float)
        n_steps_initial = int(round(saved_cell_size_m / CELL_SIZE_STEP_M))
        n_steps_initial = max(n_steps_min, min(n_steps_max, n_steps_initial))
        self.sliderCellSize.setRange(n_steps_min, n_steps_max)
        self.sliderCellSize.setValue(n_steps_initial)
        self.sliderCellSize.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.sliderCellSize.setTickInterval(int(round(1.0 / CELL_SIZE_STEP_M)))
        self.sliderCellSize.setToolTip(
            "Grid cell size in meters. Smaller cells give a finer rugosity "
            "raster but many more cells to process — expect roughly N² "
            "runtime relative to the number of cells across the plot. "
            "1 m is the typical default for reef plots.")
        self.labelCellSizeValue = QtWidgets.QLabel()
        self.labelCellSizeValue.setMinimumWidth(60)
        self._refreshCellSizeLabel()
        self.sliderCellSize.valueChanged.connect(self._refreshCellSizeLabel)

        self.checkRowStrip = QtWidgets.QCheckBox(
            "Row-strip preprocessing (much faster for large meshes)")
        self.checkRowStrip.setChecked(
            self.settings.value("use_row_strips", True, type=bool))
        self.checkRowStrip.setToolTip(
            "Once per row of cells, clip the source mesh down to a "
            "full-width row strip before doing per-cell clips against it. "
            "Per-cell clips walk O(source_faces) triangles; shrinking the "
            "per-cell source by ~1/n_rows drops per-cell time from ~30 s "
            "to ~2 s on typical reef plots with a ~150 M face source, "
            "trading ~5 min of row-strip build time for hours of per-cell "
            "savings. No accuracy tradeoff — same clip operation, applied "
            "to a smaller working mesh. Uncheck for A/B comparison or if "
            "the source mesh is small enough that per-cell clips are "
            "already fast.")

        self.checkSaveDisk = QtWidgets.QCheckBox(
            "Also save raster to disk (GeoTIFF)")
        self.checkSaveDisk.setChecked(
            self.settings.value("save_to_disk", False, type=bool))
        self.checkSaveDisk.toggled.connect(self._onSaveDiskToggled)

        self.checkSaveStats = QtWidgets.QCheckBox(
            "Also save stats .txt alongside the raster")
        self.checkSaveStats.setChecked(
            self.settings.value("save_stats", False, type=bool))
        self.checkSaveStats.setToolTip(
            "When the raster is saved, also write a sibling .txt file "
            "(same basename, .txt extension) containing the cell size, "
            "per-cell statistics, and global rugosity — everything the "
            "completion popup shows.")

        self.labelOutDir = QtWidgets.QLabel("Output Folder:")
        self.txtOutDir = QtWidgets.QPlainTextEdit(
            self.output_dir or "(no folder selected)")
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
        main_layout.addWidget(self.checkRowStrip)
        main_layout.addWidget(self.checkSaveDisk)
        main_layout.addWidget(self.checkSaveStats)
        main_layout.addLayout(dir_layout)
        main_layout.addStretch(1)
        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

        sb_extent = QtWidgets.QApplication.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent)
        self.setMinimumWidth(main_layout.sizeHint().width() + sb_extent + 20)

        self._onSaveDiskToggled(self.checkSaveDisk.isChecked())

        self.btnOk.clicked.connect(self.run)
        self.btnClose.clicked.connect(self.reject)

    def _cellSize(self):
        return self.sliderCellSize.value() * CELL_SIZE_STEP_M

    def _refreshCellSizeLabel(self):
        self.labelCellSizeValue.setText("{:.2f} m".format(self._cellSize()))

    def _onSaveDiskToggled(self, checked):
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
        try:
            self._runImpl()
        except Exception as e:
            import traceback
            traceback.print_exc()
            _show_error(self, "Gridded rugosity (exact) failed", str(e))
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
        use_row_strips = self.checkRowStrip.isChecked()
        if save_to_disk and (not self.output_dir or not os.path.isdir(self.output_dir)):
            raise RuntimeError(
                "Disk export is checked but no valid output folder is "
                "selected. Either uncheck \"Also save raster to disk\" or "
                "pick a folder.")

        cell_size_m = self._cellSize()
        self.settings.setValue("cell_size_m", cell_size_m)
        self.settings.setValue("save_to_disk", save_to_disk)
        self.settings.setValue("save_stats", self.checkSaveStats.isChecked())
        self.settings.setValue("use_row_strips", use_row_strips)

        cell_size_cm = int(round(cell_size_m * 100))
        raster_label = "{} ({:.2f}m grid)".format(RASTER_LABEL_PREFIX, cell_size_m)

        project_name = os.path.basename(self.doc.path or "untitled")
        for ext in (".psx", ".psz", ".files"):
            if project_name.lower().endswith(ext):
                project_name = project_name[:-len(ext)]
                break
        chunk_label = self.chunk.label or "chunk"
        out_basename = "{}_{}_rugosity_exact_{}cm.tif".format(
            project_name, chunk_label, cell_size_cm)

        self.setEnabled(False)
        progress = _ProgressDialog(self, "Gridded Rugosity (Exact)")
        progress.show()
        QtWidgets.QApplication.processEvents()

        temp_dir = tempfile.mkdtemp(prefix="reefshape_rugosity_exact_")
        temp_path = os.path.join(temp_dir, out_basename)
        try:
            print("Gridded Rugosity (Exact):")
            print("  cell size: {:.2f} m".format(cell_size_m))
            print("  temp file: {}".format(temp_path))
            try:
                print("  chunk.crs:        {}".format(
                    self.chunk.crs.name if self.chunk.crs else "(none)"))
                if (self.chunk.shapes and self.chunk.shapes.crs
                        and self.chunk.shapes.crs is not self.chunk.crs):
                    print("  chunk.shapes.crs: {}".format(
                        self.chunk.shapes.crs.name))
                else:
                    print("  chunk.shapes.crs: (same as chunk.crs)")
            except Exception:
                pass

            try:
                data, transform, crs_wkt, stats = compute_gridded_rugosity_exact(
                    self.chunk, boundary, cell_size_m,
                    use_row_strips=use_row_strips, progress=progress)
            except _RugosityCancelled as exc:
                print("  {}".format(exc))
                progress.close()
                QtWidgets.QMessageBox.information(
                    self, "Gridded Rugosity (Exact)",
                    "Compute cancelled — no raster was written or imported.")
                self.reject()
                return

            progress.set_status("Writing GeoTIFF…")
            write_geotiff(temp_path, data, transform, crs_wkt)

            progress.set_status("Importing into chunk as DEM…")
            new_elev = _import_raster_to_chunk(
                self.chunk, temp_path, raster_label)
            if new_elev is None:
                print("  WARNING: importRaster succeeded but no new elevation "
                      "entry was found in the chunk. Skipping rename.")

            disk_path = None
            stats_path = None
            if save_to_disk:
                import shutil
                disk_path = os.path.join(self.output_dir, out_basename)
                shutil.copy2(temp_path, disk_path)
                print("  saved to: {}".format(disk_path))
                if save_stats:
                    stats_path = os.path.splitext(disk_path)[0] + ".txt"
                    stats_body = _format_stats_text(
                        raster_label, cell_size_m, stats, disk_path=disk_path)
                    with open(stats_path, "w", encoding="utf-8") as f:
                        f.write(stats_body)
                    print("  stats: {}".format(stats_path))

            print("  done.")
            print("  cells with data: {}".format(stats["n_cells"]))
            if stats["n_cells"] > 0:
                print("  mean: {:.3f} | median: {:.3f} | min: {:.3f} | "
                      "max: {:.3f}".format(stats["mean"], stats["median"],
                                           stats["min"], stats["max"]))
                print("  global rugosity: {:.3f}".format(stats["global"]))

            summary = _format_stats_text(
                raster_label, cell_size_m, stats, disk_path=disk_path)
            if stats_path:
                summary += "\nStats text: {}".format(stats_path)

            progress.close()
            QtWidgets.QMessageBox.information(
                self, "Gridded Rugosity (Exact)", summary)
            self.accept()
        finally:
            progress.close()
            self.setEnabled(True)
            import shutil
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


def run_script():
    try:
        app = QtWidgets.QApplication.instance()
        parent = app.activeWindow() if app else None
        dlg = GriddedRugosityExactDlg(parent)
        dlg.exec()
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "Error", str(e))


label = "ReefShape/Tools/Gridded Rugosity (Exact)"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
