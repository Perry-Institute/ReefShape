"""
Gridded Rugosity
Will Greene, Perry Institute for Marine Science

Computes per-cell 3D-to-2D surface area ratio (rugosity) on a 1-meter grid
within the active chunk's OuterBoundary polygon, and writes the result as a
GeoTIFF in the chunk's shape CRS.

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

Output: <project>_<chunk>_rugosity_1m.tif at user-selected folder. Float32
pixel values; nodata for cells outside the boundary or with zero mesh.
"""

import math
import os

import Metashape
from PySide2 import QtCore, QtGui, QtWidgets

from modules.pip_auto_install import pip_install

pip_install("""numpy
matplotlib
rasterio
""")

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402


CELL_SIZE_M = 1.0  # 1-meter grid cells
NODATA = -9999.0


def _show_error(parent, title, msg):
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Critical)
    box.setWindowTitle("Error")
    box.setText(title)
    box.setInformativeText(msg)
    box.exec_()


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
    # Nested form: coords is a list of rings; outer ring is coords[0]
    if isinstance(first, (list, tuple)) and first and hasattr(first[0], "x"):
        return list(first)
    # Flat form: coords is the outer ring directly
    if hasattr(first, "x"):
        return list(coords)
    raise RuntimeError("Could not interpret boundary polygon coordinate format.")


def compute_gridded_rugosity(chunk, boundary, cell_size_m):
    """Core computation. Returns (rugosity_float32, geotransform, crs_wkt, stats).

    rugosity_float32 is a (n_rows, n_cols) array with NODATA outside the
    boundary mask or where the cell has zero mesh coverage.
    """
    T = chunk.transform.matrix
    shape_crs = chunk.shapes.crs if (chunk.shapes and chunk.shapes.crs) else chunk.crs
    if shape_crs is None:
        raise RuntimeError("Chunk has no CRS set on shapes or on the chunk itself.")

    # --- 1. Boundary outer ring in shape CRS (XY) ---
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

    vert_coords = np.empty((n_verts, 3), dtype=np.float64)
    for i, v in enumerate(chunk.model.vertices):
        c = v.coord
        vert_coords[i, 0] = c.x
        vert_coords[i, 1] = c.y
        vert_coords[i, 2] = c.z

    face_verts = np.empty((n_faces, 3), dtype=np.int64)
    for i, f in enumerate(chunk.model.faces):
        fv = f.vertices
        face_verts[i, 0] = fv[0]
        face_verts[i, 1] = fv[1]
        face_verts[i, 2] = fv[2]

    # --- 3. Triangle 3D areas + centroids, vectorized in chunk-local meters ---
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

    # --- 6. Boundary mask via point-in-polygon on cell centers ---
    col_idx = np.arange(n_cols)
    row_idx = np.arange(n_rows)
    cell_cx = left_x + (col_idx + 0.5) * cell_size_units            # (n_cols,)
    cell_cy = top_y - (row_idx + 0.5) * cell_size_units             # (n_rows,)
    gx, gy = np.meshgrid(cell_cx, cell_cy)
    cell_points = np.column_stack([gx.ravel(), gy.ravel()])
    poly_path = MplPath(boundary_xy)
    inside = poly_path.contains_points(cell_points).reshape(n_rows, n_cols)

    # --- 7. Assign each face's 3D area to its centroid's cell ---
    cols = np.floor((centroids_shape[:, 0] - left_x) / cell_size_units).astype(np.int64)
    rows = np.floor((top_y - centroids_shape[:, 1]) / cell_size_units).astype(np.int64)
    valid = (cols >= 0) & (cols < n_cols) & (rows >= 0) & (rows < n_rows)
    accumulator = np.zeros((n_rows, n_cols), dtype=np.float64)
    # np.add.at handles the unbuffered scatter-add correctly when multiple
    # faces map to the same cell.
    np.add.at(accumulator, (rows[valid], cols[valid]), areas_3d[valid])

    # --- 8. Rugosity = accumulated 3D area / cell footprint (m²) ---
    cell_footprint_m2 = cell_size_m * cell_size_m
    rugosity = accumulator / cell_footprint_m2

    # Cells outside the boundary mask: nodata.
    # Cells inside but with no face centroids: also nodata (likely the cell
    # straddles the boundary edge and most face centroids fell outside).
    rugosity_out = rugosity.astype(np.float32)
    rugosity_out[~inside] = NODATA
    no_coverage = inside & (accumulator == 0)
    rugosity_out[no_coverage] = NODATA

    # --- 9. Stats summary ---
    valid_mask = inside & (accumulator > 0)
    n_valid = int(valid_mask.sum())
    if n_valid > 0:
        vals = rugosity[valid_mask]
        stats = {
            "n_cells": n_valid,
            "mean": float(np.mean(vals)),
            "median": float(np.median(vals)),
            "min": float(np.min(vals)),
            "max": float(np.max(vals)),
            # Global rugosity matches what 09_calculate_area_ratio reports
            # (sum of all in-mask 3D area / total in-mask footprint area).
            "global": float(accumulator[valid_mask].sum() / (n_valid * cell_footprint_m2)),
        }
    else:
        stats = {"n_cells": 0, "mean": 0, "median": 0,
                 "min": 0, "max": 0, "global": 0}

    # --- 10. Build GeoTIFF affine (origin = top-left, north-up) ---
    transform = from_origin(left_x, top_y, cell_size_units, cell_size_units)

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
            "Output is a GeoTIFF at 1 m resolution in the chunk's shape CRS, "
            "with one float pixel value per cell.\n\n"
            "Captures overhangs correctly via single-pass mesh iteration — "
            "no per-cell mesh clipping, so it's fast even on large meshes.\n\n"
            "Requires a 3D model and an OuterBoundary polygon in the active "
            "chunk."
        )
        intro.setWordWrap(True)

        self.labelOutDir = QtWidgets.QLabel("Output Folder:")
        self.txtOutDir = QtWidgets.QPlainTextEdit(self.output_dir or "(no folder selected)")
        self.txtOutDir.setFixedHeight(40)
        self.txtOutDir.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtOutDir.setReadOnly(True)
        self.btnOutDir = QtWidgets.QPushButton("Select Folder")

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
        main_layout.addLayout(dir_layout)
        main_layout.addStretch(1)
        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

        sb_extent = QtWidgets.QApplication.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent)
        self.setMinimumWidth(main_layout.sizeHint().width() + sb_extent + 20)

        # --- Signals ---
        self.btnOutDir.clicked.connect(self.pickOutDir)
        self.btnOk.clicked.connect(self.run)
        self.btnClose.clicked.connect(self.reject)

    def pickOutDir(self):
        start = self.output_dir or self.project_folder or ""
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select output folder", start)
        if d:
            self.output_dir = d
            self.txtOutDir.setPlainText(d)

    def run(self):
        # Wrapper that always re-enables the dialog on error so the user can
        # adjust and retry without restarting Metashape (same pattern as
        # 01/02 — Light/Dark theme disables the title-bar X too when the
        # whole dialog is disabled).
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
        if not self.output_dir or not os.path.isdir(self.output_dir):
            raise RuntimeError("Please select a valid output folder.")

        # Build a filename matching the rest of ReefShape's exports.
        project_name = os.path.basename(self.doc.path or "untitled")
        for ext in (".psx", ".psz", ".files"):
            if project_name.lower().endswith(ext):
                project_name = project_name[:-len(ext)]
                break
        chunk_label = self.chunk.label or "chunk"
        output_path = os.path.join(
            self.output_dir,
            "{}_{}_rugosity_1m.tif".format(project_name, chunk_label),
        )

        self.setEnabled(False)
        try:
            print("Gridded Rugosity:")
            print("  output: {}".format(output_path))
            data, transform, crs_wkt, stats = compute_gridded_rugosity(
                self.chunk, boundary, CELL_SIZE_M)
            write_geotiff(output_path, data, transform, crs_wkt)

            print("  done.")
            print("  cells with data: {}".format(stats["n_cells"]))
            if stats["n_cells"] > 0:
                print("  mean: {:.3f} | median: {:.3f} | min: {:.3f} | "
                      "max: {:.3f}".format(stats["mean"], stats["median"],
                                           stats["min"], stats["max"]))
                print("  global rugosity: {:.3f}".format(stats["global"]))

            QtWidgets.QMessageBox.information(
                self,
                "Gridded Rugosity",
                "Wrote rugosity raster.\n\nFile: {path}\n\n"
                "Cells with data: {n}\nMean: {mean:.3f}\n"
                "Median: {median:.3f}\nMax: {max:.3f}\n\n"
                "Global rugosity (sum of 3D area / total cell footprint): "
                "{glob:.3f}\n(This matches what \"Calculate Surface Area "
                "Ratio\" reports for the whole plot.)".format(
                    path=output_path,
                    n=stats["n_cells"],
                    mean=stats["mean"],
                    median=stats["median"],
                    max=stats["max"],
                    glob=stats["global"],
                ),
            )
            self.accept()
        finally:
            self.setEnabled(True)


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
