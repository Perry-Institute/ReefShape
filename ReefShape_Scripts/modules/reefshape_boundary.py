"""
Plot boundary from camera positions -- the computation, with no user interface.

Shared by 08_create_boundary_from_photos.py (the menu tool) and by
reefshape_core, which falls back to this when a plot has no corner markers to
build a boundary from but still needs one for its TagLab exports.

The boundary is the union of a disc around every aligned camera: each photo
covers roughly a circle of seafloor, so the union of those circles is the area
actually surveyed. Computed as a vector union in shapely rather than by
rasterising, which keeps the edges smooth at any zoom and needs no
post-processing.

Dependencies (numpy, shapely) install on first use. numpy is pinned to the
version the other ReefShape scripts use -- an unpinned numpy silently bumps to
2.x and breaks the scipy and open3d wheels the ICP tool depends on.
"""

import Metashape

from modules.pip_auto_install import pip_install

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
# Public entry point
# ---------------------------------------------------------------------------

# Default disc radius around each camera, in metres. Roughly the seafloor a
# single photo covers on a typical ReefShape dive; `create_boundary_from_photos`
# estimates a better value from the actual camera altitudes when it can.
DEFAULT_FOOTPRINT_M = 1.5


def create_boundary_from_photos(chunk, footprint=None, label="Photo Boundary",
                                reporter=None):
    """Create an OuterBoundary polygon enclosing the surveyed area.

    Returns the number of vertices in the created polygon. Raises
    BoundaryError when there is not enough to work from -- too few aligned
    cameras, or a degenerate polygon.

    `footprint` is the per-camera radius in metres; None estimates it from the
    camera altitudes above the tie-point cloud.
    """
    log = reporter.info if reporter is not None else print

    if chunk is None:
        raise BoundaryError("No chunk to create a boundary in.")

    # Project every aligned camera into shape CRS, collecting its chunk-local
    # position alongside. The shape-CRS (X, Y) is the camera's real horizontal
    # position in world space -- what the boundary must enclose. The local
    # positions are used only to calibrate metres-to-CRS-units below.
    transform = chunk.transform.matrix
    shape_crs = chunk.shapes.crs if chunk.shapes else chunk.crs
    positions_crs = []
    positions_local = []
    for camera in chunk.cameras:
        if camera.transform is None or camera.center is None:
            continue
        try:
            projected = shape_crs.project(transform.mulp(camera.center))
        except Exception:
            continue
        positions_crs.append([projected.x, projected.y, projected.z])
        positions_local.append([camera.center.x, camera.center.y])

    log("  boundary from photos: {} of {} cameras are aligned".format(
        len(positions_crs), len(chunk.cameras)))

    if len(positions_crs) < 4:
        raise BoundaryError(
            "Need at least 4 aligned cameras to compute a boundary; this "
            "chunk has {}.".format(len(positions_crs)))

    positions_crs = np.array(positions_crs)
    positions_local = np.array(positions_local)

    if footprint is None:
        footprint = _estimate_photo_footprint(chunk) or DEFAULT_FOOTPRINT_M

    # Calibrate metres -> shape-CRS units empirically. Chunk-local coordinates
    # are always metres; the same cameras projected into shape CRS span some
    # range in CRS units, and the ratio converts between them regardless of
    # CRS type. Derived rather than read from `crs.geographic`, which has
    # proved unreliable for compound CRSs like WGS84+EGM96.
    local_extent = positions_local.max(axis=0) - positions_local.min(axis=0)
    crs_extent = positions_crs[:, :2].max(axis=0) - positions_crs[:, :2].min(axis=0)
    scale_x = crs_extent[0] / local_extent[0] if local_extent[0] > 1e-6 else 1.0
    scale_y = crs_extent[1] / local_extent[1] if local_extent[1] > 1e-6 else 1.0
    scale = 0.5 * (abs(scale_x) + abs(scale_y))

    log("  footprint {:.2f} m, scale {:.3e} CRS units/m".format(footprint, scale))

    boundary_xy = _compute_coverage_polygon(positions_crs[:, :2], footprint * scale)
    if not boundary_xy:
        raise BoundaryError(
            "Could not extract a boundary polygon from the camera positions.")

    # One Z for every vertex, so the polygon stays flat in world space and
    # does not tilt under the top-down ortho projection.
    z_crs = float(np.median(positions_crs[:, 2]))
    _create_outer_boundary_shape_from_crs(chunk, boundary_xy, z_crs, label=label)

    log("  boundary created with {} vertices".format(len(boundary_xy)))
    return len(boundary_xy)


class BoundaryError(Exception):
    """A boundary could not be computed from the camera positions."""
