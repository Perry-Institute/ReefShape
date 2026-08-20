"""
ICP timepoint alignment -- the numeric core, with no user interface.

Companion to reefshape_core and reefshape_align. Driven by both the Metashape
menu dialog (03_align_chunks_ICP.py) and the headless batch worker.

When to use this rather than reefshape_align: that module aligns two visits by
matching *markers*, which needs permanent targets that stayed put between
visits. Where no permanent markers are installed, people place temporary
targets and collect georeference and scale information separately each time.
The two timepoints then land in roughly the right place -- typically within
0.1-2 m -- but not registered to each other. ICP closes that gap by matching
the surfaces themselves.

Adapted from align_model_to_model.py in agisoft-llc/metashape-scripts.

Dependencies (open3d, scipy, numpy) are NOT imported at module load. They are
heavy, and only ICP jobs need them, so importing them here would make every
batch worker pay for a feature most jobs never use -- and would make the whole
module unimportable on a machine where the install has not run yet. Call
`ensure_dependencies()` first; `require()` does it for you.
"""

import time

import Metashape


# Pinned to match 03_align_chunks_ICP.py. open3d in particular is sensitive to
# version drift in its registration API.
REQUIREMENTS = """open3d==0.19.0
scipy==1.12.0
numpy==1.26.4"""

# Filled in by ensure_dependencies().
np = None
o3d = None
o3d_registration = None
ConvexHull = None
cKDTree = None

_ready = False


def dependencies_available():
    """True if the ICP dependencies are already importable, without installing.

    Lets the UI tell the user an install is coming *before* they queue an
    overnight batch, rather than having the first ICP job stall for several
    minutes while pip runs.
    """
    import importlib.util
    return all(importlib.util.find_spec(name) is not None
               for name in ("open3d", "scipy", "numpy"))


def ensure_dependencies():
    """Import open3d/scipy/numpy, installing them on first use if needed."""
    global np, o3d, o3d_registration, ConvexHull, cKDTree, _ready
    if _ready:
        return

    if not dependencies_available():
        from modules.pip_auto_install import pip_install
        pip_install(REQUIREMENTS)

    import numpy as _np
    import open3d as _o3d
    from scipy.spatial import ConvexHull as _ConvexHull, cKDTree as _cKDTree

    np, o3d = _np, _o3d
    ConvexHull, cKDTree = _ConvexHull, _cKDTree
    try:
        o3d_registration = _o3d.registration
    except AttributeError:
        o3d_registration = _o3d.pipelines.registration
    _ready = True


class IcpResult:
    """Outcome of an ICP run.

    `fitness` is the proportion of source points that found a correspondence
    within the threshold -- roughly "how much of the moving cloud landed on
    the master". `inlier_rmse` is the residual over those correspondences, in
    chunk units (metres for a scaled reef plot).

    Both come from the final pass. They are the only quantitative handle on
    whether the alignment worked: with no permanent markers there is nothing
    independent to check it against, so a caller that ignores these is
    trusting a number it never looked at.
    """

    def __init__(self, matrix, fitness, inlier_rmse, stages, scale_ratio,
                 target_resolution, source_points, target_points):
        self.matrix = matrix
        self.fitness = fitness
        self.inlier_rmse = inlier_rmse
        self.stages = stages
        self.scale_ratio = scale_ratio
        self.target_resolution = target_resolution
        self.source_points = source_points
        self.target_points = target_points

    def summary(self):
        return ("fitness {:.3f} (proportion of the new timepoint matched), "
                "inlier RMSE {:.4f} m".format(self.fitness, self.inlier_rmse))


# --------------------------------------------------------------------------
# Point extraction
# --------------------------------------------------------------------------

def get_chunk_tie_points_in_world(chunk):
    """A chunk's sparse tie points as an Nx3 array in world coordinates.

    Tie points are stored in chunk-internal coordinates, so they are pushed
    through chunk.transform.matrix to make two chunks' points comparable.
    """
    require()
    if chunk.tie_points is None:
        raise IcpError("Chunk '{}' has no tie points.".format(chunk.label))

    points = chunk.tie_points.points
    if len(points) == 0:
        raise IcpError("Chunk '{}' has 0 tie points.".format(chunk.label))

    transform = chunk.transform.matrix
    coords = []
    for point in points:
        if not point.valid:
            continue
        world = transform * point.coord
        w = world[3] if world[3] != 0 else 1.0
        coords.append([world[0] / w, world[1] / w, world[2] / w])

    if not coords:
        raise IcpError("Chunk '{}' has 0 valid tie points.".format(chunk.label))
    return np.array(coords, dtype=np.float32)


def get_model_vertices_in_world(chunk, model=None):
    """A model's vertices as an Nx3 array in world coordinates.

    Denser and more evenly sampled than tie points, which is why it gives a
    much tighter ICP fit -- and why the batch runner builds the mesh before
    aligning rather than after.

    Vertex.coord is a 3-vector (unlike TiePoint.coord, which is homogeneous),
    so the transform is applied with mulp().
    """
    require()
    if model is None:
        model = chunk.model
    if model is None:
        raise IcpError("Chunk '{}' has no model.".format(chunk.label))
    if len(model.vertices) == 0:
        raise IcpError("Model in chunk '{}' has 0 vertices.".format(chunk.label))

    transform = chunk.transform.matrix
    if model.transform is not None:
        transform = transform * model.transform

    coords = np.empty((len(model.vertices), 3), dtype=np.float32)
    for i, vertex in enumerate(model.vertices):
        world = transform.mulp(vertex.coord)
        coords[i] = (world.x, world.y, world.z)
    return coords


def get_points(chunk, source):
    """Extract points for `source`, which is "mesh", "tie_points" or "auto".

    "auto" prefers the mesh and falls back to tie points -- the right default
    for a batch, where the reference chunk normally has a mesh but a project
    part-way through processing might not.
    """
    require()
    if source == "mesh":
        return get_model_vertices_in_world(chunk)
    if source == "tie_points":
        return get_chunk_tie_points_in_world(chunk)

    if chunk.model is not None:
        try:
            return get_model_vertices_in_world(chunk)
        except IcpError:
            pass
    return get_chunk_tie_points_in_world(chunk)


class IcpError(Exception):
    """ICP could not run. Distinct from ICP running and fitting badly."""


def require():
    if not _ready:
        ensure_dependencies()


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

def subsample_points(points, n):
    if len(points) <= n:
        return points.copy()
    np.random.seed(len(points))
    shuffled = points.copy()
    np.random.shuffle(shuffled)
    return shuffled[:n]


def estimate_convex_hull_size(points):
    hull = ConvexHull(points)
    hull_points = points[np.unique(hull.vertices)]
    diffs = (hull_points[:, None, :] - hull_points[None, :, :]).reshape(-1, 3)
    return np.sqrt(np.max(np.sum(diffs * diffs, axis=-1)))


def estimate_resolution(points):
    diffs = points[:, None, :] - points[None, :, :]
    dists = np.sum(diffs * diffs, axis=-1)
    dists[dists == 0] = np.max(dists)
    return np.sqrt(np.median(np.min(dists, axis=-1)))


def to_point_cloud(points):
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(points.copy())
    return cloud


def downscale_point_cloud(cloud, voxel_size):
    return cloud.voxel_down_sample(voxel_size)


def apply_transform_to_points(points, matrix):
    homogeneous = np.hstack([points, np.ones((len(points), 1), dtype=points.dtype)])
    return (homogeneous @ matrix.T)[:, :3].astype(points.dtype)


def compute_overlap_masks(source_aligned, target, overlap_distance):
    """Mark points in each cloud that have a near neighbour in the other.

    Uses cKDTree; much faster than the naive O(N^2) comparison and faster than
    Open3D's KDTree for this batch nearest-neighbour pattern.
    """
    tree_source = cKDTree(source_aligned)
    tree_target = cKDTree(target)
    d_target, _ = tree_source.query(target, k=1)
    d_source, _ = tree_target.query(source_aligned, k=1)
    return d_source < overlap_distance, d_target < overlap_distance


def estimate_points_features(cloud, voxel_size):
    cloud.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=voxel_size * 2, max_nn=30))
    return o3d_registration.compute_fpfh_feature(
        cloud, o3d.geometry.KDTreeSearchParamHybrid(
            radius=voxel_size * 5, max_nn=100))


def global_registration(source_points, target_points, global_voxel_size):
    source = downscale_point_cloud(to_point_cloud(source_points), global_voxel_size)
    target = downscale_point_cloud(to_point_cloud(target_points), global_voxel_size)
    source_features = estimate_points_features(source, global_voxel_size)
    target_features = estimate_points_features(target, global_voxel_size)

    distance_threshold = global_voxel_size * 2.0
    max_validation = min(len(source.points), len(target.points)) // 2
    kwargs = {
        "source": source, "target": target,
        "source_feature": source_features, "target_feature": target_features,
        "max_correspondence_distance": distance_threshold,
        "estimation_method":
            o3d_registration.TransformationEstimationPointToPoint(False),
        "ransac_n": 4,
        "checkers": [
            o3d_registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
            o3d_registration.CorrespondenceCheckerBasedOnDistance(distance_threshold),
        ],
        "criteria": o3d_registration.RANSACConvergenceCriteria(4000000, max_validation),
    }
    if o3d.__version__ not in ["0.{}.0".format(v) for v in range(12)]:
        kwargs["mutual_filter"] = True
    return o3d_registration.registration_ransac_based_on_feature_matching(**kwargs)


def icp_registration(source, target, voxel_size, transform_init, max_iterations):
    """Point-to-point ICP. Robust to a poor starting pose."""
    return o3d_registration.registration_icp(
        source, target, 8.0 * voxel_size, transform_init,
        o3d_registration.TransformationEstimationPointToPoint(),
        o3d_registration.ICPConvergenceCriteria(max_iteration=max_iterations))


def icp_registration_generalized(source, target, voxel_size, transform_init,
                                 max_iterations):
    """Generalized ICP (Segal et al. 2009).

    Models each correspondence as a plane-to-plane match using local
    covariance on both clouds, so anisotropic sampling and surface curvature
    are handled together. Tightest final precision on noisy real surfaces, at
    a higher per-iteration cost.
    """
    threshold = 8.0 * voxel_size
    for cloud in (source, target):
        if not cloud.has_normals():
            cloud.estimate_normals(
                o3d.geometry.KDTreeSearchParamHybrid(
                    radius=2.0 * voxel_size, max_nn=30))
    return o3d_registration.registration_generalized_icp(
        source, target, threshold, transform_init,
        o3d_registration.TransformationEstimationForGeneralizedICP(),
        o3d_registration.ICPConvergenceCriteria(max_iteration=max_iterations))


def align_two_point_clouds(source_points, target_points, scale_ratio=1.0,
                           target_resolution=0.01, no_global_alignment=True,
                           use_generalized_icp=False, crop_to_overlap=True,
                           progress_callback=None, preview_collector=None,
                           log=print):
    """Register `source_points` onto `target_points`.

    Returns an IcpResult. Defaults match the menu dialog's: repeat surveys of
    the same scene are the same scale, and start close enough that global
    feature matching is unnecessary and unreliable.

    `preview_collector` is optional and only used by the interactive dialog;
    the batch runner passes None.
    """
    require()
    assert source_points.shape[1] == target_points.shape[1] == 3

    v1, v2 = source_points, target_points
    c1 = np.mean(v1, axis=0)
    c2 = np.mean(v2, axis=0)
    if no_global_alignment:
        c1 = np.zeros(3)
        c2 = np.zeros(3)
    v1 = v1 - c1
    v2 = v2 - c2

    if scale_ratio is None:
        if no_global_alignment:
            scale_ratio = 1.0
        else:
            log("No scale ratio given; estimating from convex hulls. This is "
                "unreliable for open scenes like reefs.")
            size1 = estimate_convex_hull_size(subsample_points(v1, 100000))
            size2 = estimate_convex_hull_size(subsample_points(v2, 100000))
            scale_ratio = size2 / size1
            log("  scale_ratio={}".format(scale_ratio))

    if target_resolution is None:
        log("No target resolution given; estimating from point density.")
        sub1 = subsample_points(v1, 1000)
        sub2 = subsample_points(v2, 1000)
        res1 = (1.5 * estimate_resolution(sub1)
                / np.sqrt(len(v1) / len(sub1)) * scale_ratio)
        res2 = 1.5 * estimate_resolution(sub2) / np.sqrt(len(v2) / len(sub2))
        target_resolution = float(np.max([res1, res2]))
        log("  target_resolution={}".format(target_resolution))

    log("scale_ratio={} target_resolution={}".format(scale_ratio, target_resolution))
    v1 = v1 * scale_ratio

    total_stages = 2 + (0 if no_global_alignment else 1) + (1 if use_generalized_icp else 0)
    stage = 0

    def progress(name):
        if progress_callback is not None:
            progress_callback(stage, total_stages, name)

    if preview_collector is not None:
        preview_collector.add_stage("Initial", v1, v2, np.eye(4))

    if no_global_alignment:
        transformation = np.eye(4)
    else:
        stage += 1
        progress("Global registration")
        log("{}/{}: Global registration...".format(stage, total_stages))
        start = time.time()
        result = global_registration(v1, v2, 64.0 * target_resolution)
        transformation = result.transformation
        log("    done in {:.1f} s".format(time.time() - start))
        if preview_collector is not None:
            preview_collector.add_stage("Global registration", v1, v2, transformation)

    stage += 1
    progress("Coarse ICP")
    log("{}/{}: Coarse ICP...".format(stage, total_stages))
    start = time.time()
    coarse_voxel = 8.0 * target_resolution
    coarse = icp_registration(
        downscale_point_cloud(to_point_cloud(v1), coarse_voxel),
        downscale_point_cloud(to_point_cloud(v2), coarse_voxel),
        voxel_size=coarse_voxel, transform_init=transformation, max_iterations=100)
    transformation = coarse.transformation
    log("    done in {:.1f} s".format(time.time() - start))
    if preview_collector is not None:
        preview_collector.add_stage("Coarse ICP", v1, v2, transformation)

    # Drop points that only one survey covered. Coarse ICP has roughly aligned
    # the clouds, so "no near neighbour" now genuinely means "outside the
    # overlap" -- and such points otherwise get matched to whatever is nearest,
    # which pulls the rotation off in the fine pass.
    if crop_to_overlap:
        overlap_distance = 8.0 * target_resolution
        mask1, mask2 = compute_overlap_masks(
            apply_transform_to_points(v1, transformation), v2, overlap_distance)
        kept = min(int(mask1.sum()), int(mask2.sum()))
        if kept < 100:
            log("    WARNING: overlap crop would keep only {} points; coarse "
                "alignment is probably wrong. Skipping the crop.".format(kept))
        else:
            if kept < 0.05 * min(len(v1), len(v2)):
                log("    WARNING: overlap crop removed >95% of points. The "
                    "clouds may be far apart.")
            log("    Cropped to overlap: {} -> {}, {} -> {}".format(
                len(v1), int(mask1.sum()), len(v2), int(mask2.sum())))
            v1 = v1[mask1]
            v2 = v2[mask2]

    stage += 1
    progress("Fine ICP")
    log("{}/{}: Fine ICP...".format(stage, total_stages))
    start = time.time()
    fine_voxel = target_resolution
    cloud1 = to_point_cloud(v1)
    cloud2 = to_point_cloud(v2)
    fine = icp_registration(cloud1, cloud2, voxel_size=fine_voxel,
                            transform_init=transformation, max_iterations=100)
    transformation = fine.transformation
    final = fine
    log("    done in {:.1f} s".format(time.time() - start))
    if preview_collector is not None:
        preview_collector.add_stage("Fine ICP", v1, v2, transformation)

    if use_generalized_icp:
        stage += 1
        progress("Generalized ICP refinement")
        log("{}/{}: Generalized ICP...".format(stage, total_stages))
        start = time.time()
        generalized = icp_registration_generalized(
            cloud1, cloud2, voxel_size=fine_voxel,
            transform_init=transformation, max_iterations=100)
        transformation = generalized.transformation
        final = generalized
        log("    done in {:.1f} s".format(time.time() - start))
        if preview_collector is not None:
            preview_collector.add_stage("Generalized ICP", v1, v2, transformation)

    # Compose: undo the source centroid shift, apply scale, apply the ICP
    # transform, then restore the target centroid.
    shift_source = np.eye(4)
    shift_source[:3, 3] = -c1.reshape(3)
    scale = np.diag([scale_ratio, scale_ratio, scale_ratio, 1.0])
    shift_target = np.eye(4)
    shift_target[:3, 3] = c2.reshape(3)
    matrix = np.dot(shift_target, np.dot(transformation, np.dot(scale, shift_source)))

    log("Estimated transformation:\n{}".format(matrix))
    log("Final fit: fitness={:.4f} inlier_rmse={:.5f}".format(
        final.fitness, final.inlier_rmse))

    return IcpResult(
        matrix=Metashape.Matrix(matrix),
        fitness=float(final.fitness),
        inlier_rmse=float(final.inlier_rmse),
        stages=stage,
        scale_ratio=scale_ratio,
        target_resolution=target_resolution,
        source_points=len(source_points),
        target_points=len(target_points),
    )


def align_chunk_to_reference(moving_chunk, master_chunk,
                             moving_source="auto", master_source="auto",
                             scale_ratio=1.0, target_resolution=0.01,
                             use_initial_alignment=True,
                             crop_to_overlap=True, use_generalized_icp=False,
                             reporter=None):
    """Register `moving_chunk` onto `master_chunk` and apply the transform.

    Everything in the moving chunk moves with it -- cameras, tie points, mesh,
    and anything built later -- because the transform is applied to
    chunk.transform.matrix rather than to any one product. That is what makes
    it correct to run this after the mesh is built but before the DEM and
    orthomosaic: the mesh comes along, and the rasters are then generated in
    the corrected frame.

    Returns an IcpResult.
    """
    require()
    log = reporter.info if reporter is not None else print

    if moving_chunk == master_chunk:
        raise IcpError("The moving chunk and the master chunk are the same "
                       "chunk ({!r}).".format(moving_chunk.label))

    moving_points = get_points(moving_chunk, moving_source)
    master_points = get_points(master_chunk, master_source)
    log("ICP: aligning {!r} ({} points) onto {!r} ({} points)".format(
        moving_chunk.label, len(moving_points),
        master_chunk.label, len(master_points)))

    result = align_two_point_clouds(
        moving_points, master_points,
        scale_ratio=scale_ratio,
        target_resolution=target_resolution,
        no_global_alignment=use_initial_alignment,
        use_generalized_icp=use_generalized_icp,
        crop_to_overlap=crop_to_overlap,
        log=log,
    )

    moving_chunk.transform.matrix = result.matrix * moving_chunk.transform.matrix
    log("ICP: transform applied to {!r} -- {}".format(
        moving_chunk.label, result.summary()))
    return result
