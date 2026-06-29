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

# Auto-install deps. These are a subset of (ICP + rugosity) requirements,
# so if either of those scripts has been run this is a no-op fast-path.
# numpy must be pinned to the same version the other ReefShape scripts use
# (1.26.4) — see modules/pip_auto_install.py for why an unpinned numpy
# would silently bump to 2.x and break scipy/open3d wheels.
pip_install("""numpy==1.26.4
scipy
rasterio>=1.4,<2
""")

import numpy as np
from scipy import ndimage
from rasterio.features import shapes as rio_shapes


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

def _rdp_simplify_open(verts, tolerance):
    """Ramer-Douglas-Peucker simplification on an OPEN polyline (numpy 2D array).

    Recursive: find the vertex with maximum perpendicular distance from the
    chord connecting first and last; if that distance is below `tolerance`,
    drop every intermediate vertex; otherwise split at that vertex and recurse
    on each half.
    """
    if len(verts) < 3:
        return verts.copy()

    start = verts[0]
    end = verts[-1]
    chord = end - start
    chord_len_sq = chord[0] ** 2 + chord[1] ** 2

    if chord_len_sq < 1e-24:
        # Degenerate chord (endpoints coincide); keep just the two endpoints.
        return np.array([start, end])

    # Perpendicular distance from each vertex to the chord, computed as
    # |(p - start) × chord| / |chord|. The 2D cross product gives a signed
    # scalar; we take its absolute value.
    deltas = verts - start
    cross_z = deltas[:, 0] * chord[1] - deltas[:, 1] * chord[0]
    perp_dist = np.abs(cross_z) / math.sqrt(chord_len_sq)

    max_idx = int(np.argmax(perp_dist))
    if perp_dist[max_idx] < tolerance:
        # Whole run within tolerance of the chord — keep only the endpoints.
        return np.array([start, end])

    left = _rdp_simplify_open(verts[: max_idx + 1], tolerance)
    right = _rdp_simplify_open(verts[max_idx:], tolerance)
    # Concatenate; drop the duplicate vertex at the split point.
    return np.concatenate([left[:-1], right])


def _rdp_simplify(verts, tolerance):
    """RDP simplification on a CLOSED polygon.

    Collapses runs of near-collinear vertices into single edges. Critical
    for boundaries traced from a binary raster: the polygonized pixel-edge
    outline marches along a staircase at the raster's pixel scale, so
    hundreds of consecutive vertices fall (almost) on the same line and
    can be discarded without changing the polygon's shape outside
    `tolerance`. With tolerance set to ~half a pixel, the simplified
    polygon stays within one pixel of the original outline while losing
    the high-frequency zigzag entirely — leaving Chaikin smoothing
    something useful (large-scale corners) to round off rather than just
    softening a still-jagged outline.

    `verts` is an iterable of (x, y) tuples treated as a closed loop.
    `tolerance` is in the same units as the vertex coordinates.
    Returns a new list of (x, y) tuples.
    """
    if tolerance <= 0 or len(verts) < 4:
        return list(verts)
    pts = np.asarray(verts, dtype=np.float64)

    # For a closed polygon, split at the vertex farthest from pts[0],
    # run RDP on each resulting open polyline, then rejoin. (A naive
    # call of RDP on the closed loop's index order would collapse the
    # entire shape to a single segment because the chord from start to
    # end has length 0 for a closed polygon.)
    distances_from_0 = np.linalg.norm(pts - pts[0], axis=1)
    split_idx = int(np.argmax(distances_from_0))
    if split_idx == 0:
        return [(float(pts[0, 0]), float(pts[0, 1]))]

    half1 = pts[: split_idx + 1]
    # half2 wraps around through the end and back to pts[0] so RDP treats
    # the closing edge as part of the polyline.
    half2 = np.concatenate([pts[split_idx:], pts[:1]])
    simp1 = _rdp_simplify_open(half1, tolerance)
    simp2 = _rdp_simplify_open(half2, tolerance)
    # Drop the shared vertex between halves and the closing duplicate.
    result = np.concatenate([simp1[:-1], simp2[:-1]])
    return [(float(p[0]), float(p[1])) for p in result]


def _chaikin_smooth(verts, iterations):
    """Smooth a closed polygon via Chaikin's corner-cutting algorithm.

    Each iteration replaces every edge AB with two new vertices at 1/4 and
    3/4 along it, dropping the original corners. The result rounds off
    sharp angles while preserving the overall shape; the polygon converges
    to a quadratic B-spline through the original vertices as iterations
    increase. Vertex count grows by ~2× per iteration, so 1–2 iterations
    is the sweet spot for removing pixel-staircase artifacts from
    rasterized polygons without exploding the vertex count.

    `verts` is a list of (x, y) tuples treated as a closed loop (last
    vertex implicitly connects back to the first). Returns a new list.
    """
    if iterations <= 0 or len(verts) < 3:
        return list(verts)
    pts = np.asarray(verts, dtype=np.float64)
    for _ in range(iterations):
        nxt = np.roll(pts, -1, axis=0)
        q = 0.75 * pts + 0.25 * nxt   # 1/4 along each edge from the start
        r = 0.25 * pts + 0.75 * nxt   # 3/4 along each edge (1/4 from end)
        # Interleave q and r in their original edge order: q0, r0, q1, r1, …
        pts = np.empty((2 * len(pts), 2), dtype=np.float64)
        pts[0::2] = q
        pts[1::2] = r
    return [tuple(p) for p in pts]


def _compute_coverage_polygon(camera_xy, dilation_radius, resolution,
                              smoothing_iterations=2):
    """Compute the outer boundary of the dilated camera-position raster.

    Returns a list of (x, y) tuples in the same coords as `camera_xy` (chunk-
    local meters). Empty list if no coverage region could be found.

    `smoothing_iterations` controls Chaikin corner-cutting passes applied
    to the raw pixel-edge polygon. 0 disables smoothing entirely (jagged
    pixel staircase); 1 lightly rounds; 2 (default) gives visibly smooth
    output suitable for clipping reports/ortho exports.
    """
    xs = camera_xy[:, 0]
    ys = camera_xy[:, 1]
    pad = dilation_radius * 1.5
    xmin, xmax = float(xs.min()) - pad, float(xs.max()) + pad
    ymin, ymax = float(ys.min()) - pad, float(ys.max()) + pad

    nx = int(np.ceil((xmax - xmin) / resolution))
    ny = int(np.ceil((ymax - ymin) / resolution))

    # Splat camera positions onto raster
    point_raster = np.zeros((ny, nx), dtype=bool)
    cols = np.clip(((xs - xmin) / resolution).astype(int), 0, nx - 1)
    rows = np.clip(((ys - ymin) / resolution).astype(int), 0, ny - 1)
    point_raster[rows, cols] = True

    # Dilate with a disk kernel of radius = photo footprint
    radius_cells = max(1, int(np.ceil(dilation_radius / resolution)))
    yy, xx = np.ogrid[-radius_cells:radius_cells + 1,
                      -radius_cells:radius_cells + 1]
    disk = (xx * xx + yy * yy) <= (radius_cells * radius_cells)
    coverage = ndimage.binary_dilation(point_raster, structure=disk)

    # Keep largest connected component — drops outlier cameras that happen
    # to be far from the main cluster (e.g. badly aligned strays).
    labeled, n_components = ndimage.label(coverage)
    if n_components == 0:
        return []
    sizes = ndimage.sum(coverage, labeled, range(1, n_components + 1))
    largest = labeled == (int(np.argmax(sizes)) + 1)

    # Fill holes — user wants a single hole-free polygon
    largest = ndimage.binary_fill_holes(largest)

    # Trace the outer contour using rasterio.features.shapes (GDAL's
    # polygonize under the hood). For each connected region of equal value
    # in the input raster, it yields a (geometry, value) pair where
    # geometry is a GeoJSON-like Polygon dict whose outer ring traces the
    # pixel boundaries. We previously used matplotlib's marching-squares
    # contour, which gave sub-pixel-interpolated contours but pulled in
    # matplotlib + kiwisolver + pyparsing + cycler + contourpy + fonttools
    # + pillow as deps. The pixel-edge polygon from rasterio is slightly
    # blockier but indistinguishable in practice for a boundary that
    # already starts from a dilation of the camera positions — and 11
    # already needs rasterio.
    #
    # `connectivity=8` matches the diagonal-neighbour connectivity that
    # binary_fill_holes used above; without this, near-diagonal pixel
    # arrangements could break a connected region into multiple polygons.
    # The mask must be int (uint8 is enough) — rasterio.features.shapes
    # doesn't accept bool directly.
    longest_verts = None
    longest_len = 0
    for geom, val in rio_shapes(largest.astype(np.uint8), connectivity=8):
        if val != 1:
            continue  # background polygon
        outer_ring = geom["coordinates"][0]  # outer ring; ignore holes
        if len(outer_ring) > longest_len:
            longest_len = len(outer_ring)
            longest_verts = outer_ring

    if longest_verts is None:
        return []

    # Pixel (col, row) → chunk-local (x, y). rasterio's shapes returns
    # coordinates in pixel space when no transform is supplied.
    verts_chunk = [(xmin + float(col) * resolution,
                    ymin + float(row) * resolution)
                   for col, row in longest_verts]

    # Two-stage smoothing:
    #   (1) RDP collapses the pixel-edge staircase into straight segments.
    #       Tolerance = half a pixel ensures the simplified polygon stays
    #       within one pixel of the original outline. Without this step,
    #       Chaikin alone just softens individual zigzag corners without
    #       eliminating the high-frequency noise, leaving the boundary
    #       visibly jagged even at moderate plot zoom.
    #   (2) Chaikin rounds the remaining (now-meaningful) corners between
    #       straight segments. With RDP first, far fewer vertices feed in,
    #       so the corner-cutting actually produces a smooth-looking curve
    #       rather than just dampening the staircase amplitude.
    verts_chunk = _rdp_simplify(verts_chunk, tolerance=resolution * 0.5)
    return _chaikin_smooth(verts_chunk, smoothing_iterations)


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

        self.lblSmoothing = QtWidgets.QLabel("Smoothing passes:")
        self.spinSmoothing = QtWidgets.QSpinBox()
        self.spinSmoothing.setRange(0, 5)
        self.spinSmoothing.setValue(2)
        self.spinSmoothing.setToolTip(
            "Chaikin corner-cutting passes applied to the boundary polygon. "
            "0 = raw pixel staircase (no smoothing); 1 = light rounding; "
            "2 (default) = visibly smooth; higher values keep rounding but "
            "double the vertex count each pass.")

        self.btnOk = QtWidgets.QPushButton("Create Boundary")
        self.btnCancel = QtWidgets.QPushButton("Cancel")

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(10)
        grid.addWidget(info, 0, 0, 1, 2)
        grid.addWidget(self.lblFootprint, 1, 0)
        grid.addWidget(self.spinFootprint, 1, 1)
        grid.addWidget(self.lblSmoothing, 2, 0)
        grid.addWidget(self.spinSmoothing, 2, 1)
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.btnCancel)
        btns.addWidget(self.btnOk)
        grid.addLayout(btns, 3, 0, 1, 2)
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
        smoothing = int(self.spinSmoothing.value())
        dilation_m = footprint

        # Convert all distances from meters → shape-CRS units for the raster
        # math, then convert back implicitly (we just keep the boundary in
        # shape-CRS coords throughout).
        dilation_crs = dilation_m * scale_avg
        resolution_m = max(0.02, min(0.10, dilation_m / 10.0))
        resolution_crs = resolution_m * scale_avg

        print("Photo coverage boundary: footprint={:.2f} m, "
              "smoothing={} passes, raster={:.3f} m/cell, scale={:.3e} CRS/m"
              .format(footprint, smoothing, resolution_m, scale_avg))

        try:
            boundary_xy_crs = _compute_coverage_polygon(
                positions_crs[:, :2], dilation_crs, resolution_crs,
                smoothing_iterations=smoothing)
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
