"""
Create Boundary from Photos

Generates an OuterBoundary polygon for the active chunk by tracing the outline
of aligned-camera positions on a binary raster, dilated by the expected photo
footprint. Holes in the coverage
area are filled.

Useful when corner markers aren't available (e.g. after ICP-aligned timepoints
with no shared GCPs), or when you want a boundary that follows actual photo
coverage rather than a marker-to-marker rectangle.

Coordinate-system note:
  Camera positions are projected into shape CRS *first* and the dilation is
  done in shape-CRS units. Earlier versions did the dilation in chunk-local
  XY and only converted at the end — but chunk-local XY is a tilted plane in
  world space (the chunk transform usually has a few degrees of tilt from
  markers at slightly different depths), so the projected 2D footprint of the
  polygon didn't line up with where the cameras actually appear in the ortho
  view. Doing the dilation in shape CRS removes the tilt entirely.

  The meter-to-CRS-unit scale is calibrated empirically from chunk-local
  distances (always in meters) vs. the same distances in shape CRS. This
  works for local, projected, and geographic CRSs alike — no reliance on
  `crs.geographic`, which is unreliable for compound CRSs like WGS84+EGM96.

Algorithm:
  1. Project each aligned camera into shape CRS — these are the actual
     horizontal positions in world space.
  2. Calibrate scale = (shape-CRS extent) / (chunk-local extent) in meters.
  3. Splat positions onto a 2D raster (in shape-CRS units, but sized to
     ~5 cm/cell after applying the scale).
  4. Dilate by photo_footprint × scale, using a disk kernel.
  5. Keep largest connected component (drops outlier cameras).
  6. Fill holes (binary_fill_holes) → single hole-free region.
  7. Marching-squares contour at level=0.5 → ordered shape-CRS vertices.
  8. Wrap each vertex in `Metashape.Vector(...)` with a common median Z so
     the polygon is flat in shape CRS, and add it as an OuterBoundary.
"""

import Metashape
from PySide2 import QtCore, QtGui, QtWidgets
from modules.pip_auto_install import pip_install

# Auto-install deps. numpy is pinned to the same version the other
# ReefShape scripts use (1.26.4) — see modules/pip_auto_install.py for
# why an unpinned numpy would silently bump to 2.x and break scipy/open3d
# wheels. shapely is the only geometry library we need here: the coverage
# polygon is computed as a vector buffered-union of the camera positions,
# no rasterization in the loop.
pip_install("""numpy==1.26.4
shapely>=2.0,<3
""")

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union


# ---------------------------------------------------------------------------
# Footprint estimation
# ---------------------------------------------------------------------------

def _estimate_seafloor_z(chunk):
    """Return median Z of the chunk's tie-point cloud, or None if unavailable.

    Used as the "seafloor altitude" in chunk-local coords. Sampled rather than
    iterated fully (the median is robust and Python-side iteration over big
    tie-point clouds is slow).
    """
    if not chunk.tie_points or not chunk.tie_points.points:
        return None
    pts = chunk.tie_points.points
    sample = min(2000, len(pts))
    step = max(1, len(pts) // sample)
    z_samples = [pts[i].coord.z for i in range(0, len(pts), step)
                 if pts[i].valid]
    if not z_samples:
        return None
    return float(np.median(z_samples))


def _estimate_photo_footprint(chunk):
    """Estimate the inscribed-circle radius (m) of a single photo's ground
    footprint, from camera altitude + lens FOV.

    Returns None if any required input is missing (no tie points, no sensor
    calibration, no aligned cameras). The dialog falls back to a 0.75 m default
    in that case.

    Math: half_footprint = altitude * min(sensor.width, sensor.height) / (2 * f)
    This is the radius of the largest circle that fits entirely inside the
    rectangular photo footprint on the ground (the "inscribed circle"). It's
    a conservative estimate of "how far out from the camera center the photo
    reliably covers" — using the half-diagonal instead would overshoot by
    ~30%+ for typical 3:2 sensors and produce boundaries clearly too far out.
    """
    if not chunk.sensors:
        return None
    sensor = chunk.sensors[0]
    if not sensor.calibration or sensor.calibration.f <= 0:
        return None

    seafloor_z = _estimate_seafloor_z(chunk)
    if seafloor_z is None:
        return None

    cam_zs = [cam.center.z for cam in chunk.cameras if cam.transform is not None]
    if not cam_zs:
        return None
    altitude = abs(float(np.median(cam_zs)) - seafloor_z)
    if altitude <= 0:
        return None

    short_side_pixels = min(sensor.width, sensor.height)
    return altitude * short_side_pixels / (2.0 * sensor.calibration.f)


# ---------------------------------------------------------------------------
# Coverage polygon (raster morphology)
# ---------------------------------------------------------------------------

def _compute_coverage_polygon(camera_xy, footprint):
    """Compute the outer boundary of camera coverage via vector buffered union.

    Each camera position is buffered into a circle of radius `footprint`,
    all circles are unioned with shapely, and the exterior ring of the
    largest component is returned. This is a pure vector pipeline — no
    rasterization in the loop, so the result is smooth by construction
    (the only geometric approximation is the per-circle vertex count
    set by `quad_segs` below) and there is no resolution-dependent
    staircase to clean up afterwards.

    History: an earlier version of this function splatted camera positions
    onto a binary raster, dilated with scipy.ndimage, polygonized with
    rasterio.features.shapes, and then ran RDP + Chaikin to fight the
    pixel-edge staircase artifacts. All of that was working around the
    rasterization step, which existed only because scipy.ndimage made
    binary dilation/union/hole-fill easy without an extra dep. Switching
    to shapely's vector union eliminates the round-trip through raster
    entirely; no post-processing needed.

    `camera_xy` is an (N, 2) numpy array of camera positions (in any
    units). `footprint` is the per-camera buffer radius (in the same
    units). Returns a list of (x, y) tuples — the boundary polygon's
    exterior, with the closing-vertex duplicate dropped.
    """
    if len(camera_xy) == 0 or footprint <= 0:
        return []
    # quad_segs is the number of vertices per quadrant of each circle —
    # 16 gives 64 vertices per buffered point, smooth at any zoom level
    # that makes geometric sense for a reef plot. Higher values are
    # progressively wasted; lower values would bring back visible
    # polygonal facets.
    circles = [Point(float(x), float(y)).buffer(footprint, quad_segs=16)
               for x, y in camera_xy]
    union = unary_union(circles)
    if union.is_empty:
        return []
    # Multi-polygon means cameras formed disjoint clusters; keep the
    # largest by area (drops outlier cameras far from the main cluster).
    if union.geom_type == "MultiPolygon":
        union = max(union.geoms, key=lambda p: p.area)
    # Reconstruct from just the exterior ring to drop any interior holes
    # (e.g. small uncovered patches between cameras) — users want a single
    # hole-free boundary for clipping outputs.
    outer = Polygon(union.exterior)
    # Light Douglas-Peucker simplification trims co-linear vertices that
    # shapely's union sometimes leaves along straight stretches where many
    # circles butt up tangentially. Tolerance is a tiny fraction of the
    # footprint — visually indistinguishable, but cuts vertex count.
    outer = outer.simplify(footprint * 0.01)
    coords = list(outer.exterior.coords)
    # shapely's exterior.coords closes the ring with a duplicate of the
    # first vertex; drop it so downstream code doesn't have to special-case.
    if coords and coords[0] == coords[-1]:
        coords = coords[:-1]
    return [(float(x), float(y)) for x, y in coords]


# ---------------------------------------------------------------------------
# Metashape shape creation
# ---------------------------------------------------------------------------

def _create_outer_boundary_shape_from_crs(chunk, boundary_xy_crs, z_crs, label):
    """Add an OuterBoundary polygon to the chunk from vertices already in shape CRS.

    All vertices share the same `z_crs` so the polygon is flat (a tilted
    polygon would appear horizontally offset when projected top-down in the
    ortho view). Z barely matters for OuterBoundary clipping (it's a 2D
    operation) — it just has to be consistent across vertices.

    After creation, calls `chunk.shapes.updateAltitudes([shape])` (added in
    Metashape 2.3) so the polygon's vertices are snapped to the DEM surface
    rather than left at the median camera altitude. The method is missing on
    older Metashape versions, so we guard with hasattr.
    """
    if not chunk.shapes:
        chunk.shapes = Metashape.Shapes()
        chunk.shapes.crs = chunk.crs
    coords = [Metashape.Vector([float(x), float(y), float(z_crs)])
              for x, y in boundary_xy_crs]

    shape = chunk.shapes.addShape()
    shape.label = label
    shape.geometry.type = Metashape.Geometry.Type.PolygonType
    shape.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
    shape.geometry = Metashape.Geometry.Polygon(coords)

    # Clamp vertices to the DEM surface (no-op on Metashape <2.3, which
    # didn't expose this method to Python — the polygon stays at z_crs).
    if hasattr(chunk.shapes, "updateAltitudes"):
        try:
            chunk.shapes.updateAltitudes([shape])
        except Exception as exc:
            print("Note: updateAltitudes failed ({}). "
                  "Polygon left at constant Z.".format(exc))

    return shape


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class PhotoBoundaryDlg(QtWidgets.QDialog):
    def __init__(self, parent):
        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Create Boundary from Photos")
        self.setMinimumWidth(440)

        self.chunk = Metashape.app.document.chunk if Metashape.app.document else None

        # Auto-estimate footprint; fall back to a sensible UW default if we
        # can't (e.g. chunk not aligned yet, no tie points)
        auto_footprint = _estimate_photo_footprint(self.chunk) if self.chunk else None
        if auto_footprint is None or not (0.0 <= auto_footprint <= 10.0):
            auto_footprint = 0.75

        info = QtWidgets.QLabel(
            "Generates an outer-boundary polygon by tracing the outline of "
            "aligned-camera positions dilated by the photo footprint. "
            "Any holes in the coverage area are filled.")
        info.setWordWrap(True)

        self.lblFootprint = QtWidgets.QLabel("Photo footprint radius (m):")
        self.spinFootprint = QtWidgets.QDoubleSpinBox()
        # Lower bound > 0 because a footprint of 0 would mean "no dilation"
        # and we'd get one tiny polygon per camera position.
        self.spinFootprint.setRange(0.05, 10.0)
        self.spinFootprint.setSingleStep(0.05)
        self.spinFootprint.setDecimals(2)
        self.spinFootprint.setValue(round(auto_footprint, 2))
        self.spinFootprint.setToolTip(
            "Estimated automatically from the average altitude of aligned "
            "cameras above the tie-point cloud and the camera FOV. Increase "
            "to make the boundary wider, decrease to make it tighter.")

        # No "smoothing" spinbox anymore — the shapely vector union
        # produces a naturally smooth polygon (circle approximations) so
        # there's nothing to smooth. The only relevant parameter is the
        # footprint radius itself.

        self.btnOk = QtWidgets.QPushButton("Create Boundary")
        self.btnCancel = QtWidgets.QPushButton("Cancel")

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        grid.addWidget(info, 0, 0, 1, 2)
        grid.addWidget(self.lblFootprint, 1, 0)
        grid.addWidget(self.spinFootprint, 1, 1)
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.btnCancel)
        btns.addWidget(self.btnOk)
        grid.addLayout(btns, 2, 0, 1, 2)
        self.setLayout(grid)

        self.btnOk.clicked.connect(self._on_create)
        self.btnCancel.clicked.connect(self.reject)

    def _on_create(self):
        if not self.chunk:
            Metashape.app.messageBox("No active chunk.")
            return

        # Project every aligned camera into shape CRS at the same time as
        # collecting its chunk-local position. The shape-CRS (X, Y) is the
        # camera's actual horizontal position in world space — this is what
        # Metashape uses to render the camera dots in the ortho view, and
        # this is what we want the boundary polygon to enclose.
        #
        # We collect chunk-local positions in parallel only to calibrate the
        # meter-to-shape-CRS-unit scale empirically (see below) — never used
        # for the actual dilation or polygon geometry.
        T = self.chunk.transform.matrix
        shape_crs = self.chunk.shapes.crs if self.chunk.shapes else self.chunk.crs
        positions_crs = []   # (X_crs, Y_crs, Z_crs) per camera
        positions_local = []  # (X_local, Y_local) per camera, meters
        for cam in self.chunk.cameras:
            if cam.transform is None or cam.center is None:
                continue
            try:
                world = T.mulp(cam.center)
                p = shape_crs.project(world)
            except Exception:
                continue
            positions_crs.append([p.x, p.y, p.z])
            positions_local.append([cam.center.x, cam.center.y])

        print("Aligned cameras used: {} / {} total cameras"
              .format(len(positions_crs), len(self.chunk.cameras)))

        if len(positions_crs) < 4:
            Metashape.app.messageBox(
                "Need at least 4 aligned cameras to compute a boundary.")
            return

        positions_crs = np.array(positions_crs)
        positions_local = np.array(positions_local)

        # Empirically calibrate the conversion from meters → shape CRS units.
        # Chunk-local coords are always in meters (Metashape scales them via
        # the scalebars). The same cameras, projected into shape CRS, span
        # some range in shape-CRS units. The ratio gives us shape-CRS-units-
        # per-meter, independent of CRS type (local/projected/geographic) so
        # we don't rely on `crs.geographic` which has proved unreliable for
        # compound CRSs like WGS84+EGM96.
        local_extent = positions_local.max(axis=0) - positions_local.min(axis=0)
        crs_extent_xy = positions_crs[:, :2].max(axis=0) - positions_crs[:, :2].min(axis=0)
        # Guard against degenerate (single-row) plots
        scale_x = crs_extent_xy[0] / local_extent[0] if local_extent[0] > 1e-6 else 1.0
        scale_y = crs_extent_xy[1] / local_extent[1] if local_extent[1] > 1e-6 else 1.0
        # Use the average — for small reef plots away from the poles, scale_x
        # and scale_y differ by <1% (geographic CRSs only; for projected and
        # local CRSs they're identical).
        scale_avg = 0.5 * (abs(scale_x) + abs(scale_y))

        footprint = float(self.spinFootprint.value())
        # Convert footprint from meters → shape-CRS units. The boundary
        # polygon stays in shape-CRS coords throughout (no round-trip
        # through meters); shapely operates on whatever units it's given.
        footprint_crs = footprint * scale_avg

        print("Photo coverage boundary: footprint={:.2f} m, "
              "scale={:.3e} CRS/m".format(footprint, scale_avg))

        try:
            boundary_xy_crs = _compute_coverage_polygon(
                positions_crs[:, :2], footprint_crs)
        except Exception as exc:
            Metashape.app.messageBox(
                "Failed to compute coverage polygon: {}".format(exc))
            return

        if not boundary_xy_crs:
            Metashape.app.messageBox(
                "Could not extract a boundary polygon from the camera "
                "positions. Check that cameras are aligned and try again.")
            return

        # Polygon Z: pick the median camera elevation in shape CRS — flat in
        # world space, consistent across all vertices so the polygon doesn't
        # tilt (matters for the top-down ortho projection).
        z_crs = float(np.median(positions_crs[:, 2]))

        try:
            _create_outer_boundary_shape_from_crs(
                self.chunk, boundary_xy_crs, z_crs, label="Photo Boundary")
        except Exception as exc:
            Metashape.app.messageBox(
                "Failed to create boundary shape: {}".format(exc))
            print("Error: {}".format(exc))
            return

        Metashape.app.update()
        print("Photo boundary created with {} vertices.".format(len(boundary_xy_crs)))
        Metashape.app.messageBox(
            "Created outer boundary polygon ({} vertices) from "
            "{} aligned cameras.".format(len(boundary_xy_crs), len(positions_crs)))
        self.accept()


# ---------------------------------------------------------------------------
# Menu registration
# ---------------------------------------------------------------------------

def show_dialog():
    app = QtWidgets.QApplication.instance()
    parent = app.activeWindow() if app is not None else None
    dlg = PhotoBoundaryDlg(parent)
    dlg.exec()


label = "ReefShape/Tools/Create Boundary from Photos"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, show_dialog)
print("To execute this script press {}".format(label))
