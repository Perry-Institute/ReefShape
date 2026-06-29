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


CELL_SIZE_M = 1.0  # 1-meter grid cells
NODATA = -9999.0
RASTER_LABEL = "Rugosity"  # what the imported DEM is labeled in the chunk


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

    # --- 1. Boundary outer ring in shape CRS (XY) ---
    _step("Reading boundary polygon…", 0.02)
    ring = _extract_boundary_ring(boundary.geometry)
    boundary_xy = np.array([(v.x, v.y) for v in ring], dtype=np.float64)
    if len(boundary_xy) < 3:
        raise RuntimeError("Boundary polygon has fewer than 3 vertices.")

    # --- 2. Mesh vertices and faces as numpy arrays (chunk-local meters) ---
    n_verts = len(chunk.model.vertices)
    n_faces = len(chunk.model.faces)
    if n_faces == 0:
        raise RuntimeError("Chunk model has no faces.")
    print("  mesh: {} vertices, {} faces".format(n_verts, n_faces))

    _step("Extracting {:,} mesh vertices…".format(n_verts), 0.05)
    vert_coords = np.empty((n_verts, 3), dtype=np.float64)
    verts = chunk.model.vertices
    # Batch with periodic event-pump so the dialog updates and stays
    # responsive. The per-vertex coord access is Python overhead, ~1µs/vert,
    # so a 5M-face mesh is several seconds.
    batch = max(1, n_verts // 50)
    for start in range(0, n_verts, batch):
        end = min(start + batch, n_verts)
        for i in range(start, end):
            c = verts[i].coord
            vert_coords[i, 0] = c.x
            vert_coords[i, 1] = c.y
            vert_coords[i, 2] = c.z
        if progress is not None:
            progress.set_progress(0.05 + 0.25 * (end / n_verts))

    _step("Extracting {:,} mesh faces…".format(n_faces), 0.30)
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
            progress.set_progress(0.30 + 0.20 * (end / n_faces))

    # --- 3. Triangle 3D areas + centroids, vectorized in chunk-local meters ---
    _step("Computing triangle areas…", 0.55)
    tri = vert_coords[face_verts]  # (n_faces, 3, 3)
    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    e1 = v1 - v0
    e2 = v2 - v0
    areas_3d = 0.5 * np.linalg.norm(np.cross(e1, e2), axis=1)  # m²
    centroids_local = (v0 + v1 + v2) / 3.0

    # --- 4. Linearized chunk-local-XY → shape-CRS-XY projection ---
    # Sample ~50 vertices, project each through T.mulp + shape_crs.project,
    # fit a 2D affine. For LOCAL_CS this is exact. For projected/geographic
    # CRSes over a typical reef plot (< ~100 m) the linear approximation is
    # accurate to well under a millimeter. Seeded random sample → identical
    # affine across runs/timepoints with identical mesh, so cell assignment
    # is reproducible.
    _step("Calibrating chunk → shape-CRS projection…", 0.65)
    rng = np.random.RandomState(42)
    n_calib = min(50, n_verts)
    calib_idx = rng.choice(n_verts, n_calib, replace=False)
    calib_local = vert_coords[calib_idx]
    calib_shape = np.empty((n_calib, 2), dtype=np.float64)
    for i, lc in enumerate(calib_local):
        world = T.mulp(Metashape.Vector([lc[0], lc[1], lc[2]]))
        s = shape_crs.project(world)
        calib_shape[i] = (s.x, s.y)

    aug = np.column_stack([calib_local[:, :2], np.ones(n_calib)])
    coeffs_x, *_ = np.linalg.lstsq(aug, calib_shape[:, 0], rcond=None)
    coeffs_y, *_ = np.linalg.lstsq(aug, calib_shape[:, 1], rcond=None)

    centroids_aug = np.column_stack([centroids_local[:, :2], np.ones(n_faces)])
    centroids_shape = np.column_stack([centroids_aug @ coeffs_x,
                                       centroids_aug @ coeffs_y])

    # Scale = shape CRS units per meter, from the affine matrix's mean axis
    # length. For LOCAL/UTM this is ~1; for geographic ~1/111000.
    sx = math.hypot(coeffs_x[0], coeffs_y[0])
    sy = math.hypot(coeffs_x[1], coeffs_y[1])
    scale = (sx + sy) / 2.0
    cell_size_units = cell_size_m * scale
    print("  CRS scale: {:.6g} shape-CRS units per meter".format(scale))
    print("  cell size: {:.6g} shape-CRS units ({} m)".format(cell_size_units, cell_size_m))

    # --- 5. Build grid: bbox of boundary, snapped to cell_size multiples ---
    _step("Building grid…", 0.75)
    bx_min, by_min = boundary_xy.min(axis=0)
    bx_max, by_max = boundary_xy.max(axis=0)
    left_x = math.floor(bx_min / cell_size_units) * cell_size_units
    right_x = math.ceil(bx_max / cell_size_units) * cell_size_units
    bottom_y = math.floor(by_min / cell_size_units) * cell_size_units
    top_y = math.ceil(by_max / cell_size_units) * cell_size_units
    n_cols = max(1, int(round((right_x - left_x) / cell_size_units)))
    n_rows = max(1, int(round((top_y - bottom_y) / cell_size_units)))
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


def write_geotiff(path, data, transform, crs_wkt):
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

        # --- Widgets ---
        intro = QtWidgets.QLabel(
            "Computes per-cell rugosity (3D / 2D surface area ratio) on a "
            "1-meter grid within the active chunk's OuterBoundary polygon. "
            "The result is imported back into the chunk as a labeled DEM "
            '("Rugosity"), alongside (not replacing) the project\'s real DEM.\n\n'
            "Captures overhangs correctly via single-pass mesh iteration — "
            "no per-cell mesh clipping, so it's fast even on large meshes.\n\n"
            "Requires a 3D model and an OuterBoundary polygon in the active "
            "chunk."
        )
        intro.setWordWrap(True)

        self.checkSaveDisk = QtWidgets.QCheckBox(
            "Also save raster to disk (GeoTIFF)")
        self.checkSaveDisk.setChecked(False)
        self.checkSaveDisk.toggled.connect(self._onSaveDiskToggled)

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
        main_layout.addWidget(self.checkSaveDisk)
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

    def _onSaveDiskToggled(self, checked):
        self.labelOutDir.setEnabled(checked)
        self.txtOutDir.setEnabled(checked)
        self.btnOutDir.setEnabled(checked)

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
            _show_error(self, "Gridded rugosity failed", str(e))
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
        if save_to_disk and (not self.output_dir or not os.path.isdir(self.output_dir)):
            raise RuntimeError(
                "Disk export is checked but no valid output folder is "
                "selected. Either uncheck \"Also save raster to disk\" or "
                "pick a folder.")

        # Build a target filename (used both for disk export and as the
        # importRaster source name; Metashape sometimes uses the filename
        # for the chunk-product label until we rename it).
        project_name = os.path.basename(self.doc.path or "untitled")
        for ext in (".psx", ".psz", ".files"):
            if project_name.lower().endswith(ext):
                project_name = project_name[:-len(ext)]
                break
        chunk_label = self.chunk.label or "chunk"
        out_basename = "{}_{}_rugosity_1m.tif".format(project_name, chunk_label)

        self.setEnabled(False)
        progress = _ProgressDialog(self, "Gridded Rugosity")
        progress.show()
        QtWidgets.QApplication.processEvents()

        temp_dir = tempfile.mkdtemp(prefix="reefshape_rugosity_")
        temp_path = os.path.join(temp_dir, out_basename)
        try:
            print("Gridded Rugosity:")
            print("  temp file: {}".format(temp_path))

            data, transform, crs_wkt, stats = compute_gridded_rugosity(
                self.chunk, boundary, CELL_SIZE_M, progress=progress)

            progress.set_status("Writing GeoTIFF…")
            progress.set_progress(0.95)
            write_geotiff(temp_path, data, transform, crs_wkt)

            progress.set_status("Importing into chunk as DEM…")
            progress.set_progress(0.98)
            new_elev = _import_raster_to_chunk(self.chunk, temp_path, RASTER_LABEL)
            if new_elev is None:
                print("  WARNING: importRaster succeeded but no new elevation "
                      "entry was found in the chunk. Skipping rename.")

            disk_path = None
            if save_to_disk:
                # Copy the temp file to the user's chosen folder. We could
                # write it directly there too, but routing through temp
                # keeps the import step uniform regardless of save choice.
                import shutil
                disk_path = os.path.join(self.output_dir, out_basename)
                shutil.copy2(temp_path, disk_path)
                print("  saved to: {}".format(disk_path))

            progress.set_status("Done.")
            progress.set_progress(1.0)

            print("  done.")
            print("  cells with data: {}".format(stats["n_cells"]))
            if stats["n_cells"] > 0:
                print("  mean: {:.3f} | median: {:.3f} | min: {:.3f} | "
                      "max: {:.3f}".format(stats["mean"], stats["median"],
                                           stats["min"], stats["max"]))
                print("  global rugosity: {:.3f}".format(stats["global"]))

            summary = (
                "Imported as DEM \"{}\" in the active chunk.\n\n"
                "Cells with data: {n}\nMean: {mean:.3f}\n"
                "Median: {median:.3f}\nMax: {max:.3f}\n\n"
                "Global rugosity (sum of 3D area / total cell footprint): "
                "{glob:.3f}\n(Matches what \"Calculate Surface Area Ratio\" "
                "reports for the whole plot.)".format(
                    RASTER_LABEL,
                    n=stats["n_cells"], mean=stats["mean"],
                    median=stats["median"], max=stats["max"],
                    glob=stats["global"],
                )
            )
            if disk_path:
                summary += "\n\nAlso saved to: {}".format(disk_path)

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
