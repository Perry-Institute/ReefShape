# Aligns one chunk to another using ICP, with a choice of source data per chunk:
# either sparse tie points or a 3D mesh model's vertices.
#
# Use case: repeat photogrammetry surveys (e.g. coral reef monitoring over multiple
# years) where you have no shared ground control markers, but the two chunks cover
# overlapping terrain. One chunk is the "master" (target) and the other is the
# "moving" chunk; the script computes a rigid transform and applies it to the
# moving chunk's chunk.transform.matrix, which co-registers cameras, tie points,
# and any future dense cloud / model in that chunk.
#
# Source-data tradeoffs:
#  - Tie points: fast, no preprocessing required. Sparse and uneven — good enough
#    for a first-pass alignment. Best with "Use initial alignment" (ICP only).
#  - Mesh vertices: denser and more uniform, gives a much tighter ICP fit. Requires
#    that you've already built a mesh in each chunk. Recommended for final
#    high-quality coregistration after a rough first pass.
#
# Notes:
#  - Scale ratio defaults to 1.0 (same scene at different times, same scale).
#  - "Use initial alignment" (ICP only, no global feature matching) is the default.
#    FPFH-based global registration is unreliable on sparse tie points; on dense
#    meshes it can work but is rarely needed if chunks are roughly aligned.
#    If chunks are not at least roughly aligned to begin with, pre-align them
#    manually first (e.g. a few rough markers, or a coarse rotation).
#  - Consider cropping each chunk to the actually-overlapping region first —
#    extraneous points pull ICP toward wrong correspondences.
#
# This is python script for Metashape Pro. Adapted from the align_model_to_model.py
# script in https://github.com/agisoft-llc/metashape-scripts

import Metashape
from PySide2 import QtGui, QtCore, QtWidgets

import os, sys, copy, time, itertools, tempfile
from pathlib import Path

import urllib.request, tempfile
from modules.pip_auto_install import pip_install, user_packages_location, _is_already_installed

# Metashape version check. The ICP utilities here were developed against 2.0+;
# we warn rather than raise on other versions so the script still loads (most
# of the open3d/scipy work is independent of Metashape's Python API surface).
_supported_min = (2, 0)
try:
    _ms_version = tuple(int(p) for p in Metashape.app.version.split('.')[:2])
except (ValueError, AttributeError):
    _ms_version = (0, 0)
if _ms_version < _supported_min:
    print("WARNING: 02a_align_chunks_ICP was developed against Metashape "
          "{}.{}+ — running on {} may produce unexpected behavior."
          .format(_supported_min[0], _supported_min[1], Metashape.app.version))

# Top-level deps only — pip resolves open3d's transitive deps (dash, plotly,
# Flask, jupyter, ipywidgets, …) from open3d's own metadata, so listing them
# explicitly is just noise and slows the "is anything missing?" check on
# every Metashape startup. We previously also pinned matplotlib==3.8.4 here
# as a defensive measure even though this script doesn't import matplotlib;
# removed for the same reason (open3d doesn't need it for ICP).
requirements_txt = """open3d==0.19.0
scipy==1.12.0
numpy==1.26.4"""

pip_install(requirements_txt)

import open3d as o3d
from scipy.spatial import ConvexHull, cKDTree
import numpy as np

try:
    o3d_registration = o3d.registration
except AttributeError:
    o3d_registration = o3d.pipelines.registration

def align_two_point_clouds(points1_source, points2_target, scale_ratio=None,
                           target_resolution=None, no_global_alignment=False,
                           use_generalized_icp=False, crop_to_overlap=True,
                           progress_callback=None, preview_collector=None):
    # For example let:
    #  - points2_target - tree with height1=10 and resolution1=0.1 (in its coordinates system)
    #  - points2_target - the same tree but with height2=50 (because of another coordinates system) and resolution2=1.0
    # Then:
    #  - scale_ratio should be height2/height1=50/10=5 or (if scale_ratio=None) it will be guessed based on convex hulls (this works good only for closed objects without noise - like furniture object or house without ground surface around it)
    #  - target_resolution=resolution2=1.0 or (if target_resolution=None) it will be guessed as rough average distance between two points
    #
    # So if you want to align two models/point clouds with not-100% overlap (or for not closed objects) - you should measure and specify scale ratio
    # (note that between LIDAR point clouds scale ratio is mostly 1.0)

    assert(isinstance(points1_source, np.ndarray) and isinstance(points2_target, np.ndarray))
    assert(points1_source.shape[1] == points2_target.shape[1] == 3)
    v1, v2 = points1_source, points2_target

    c1 = np.mean(v1, axis=0)
    c2 = np.mean(v2, axis=0)
    if no_global_alignment:
        c1[:] = 0.0
        c2[:] = 0.0

    v1 = v1 - c1
    v2 = v2 - c2

    if scale_ratio is None:
        if no_global_alignment:
            scale_ratio = 1.0
        else:
            print("Warning! No scale ratio!")
            print("It will be estimated based on convex hulls of point clouds/models and so alignment may fail if object is not closed!")
            print("So if alignment will fail - please manually measure and specify scale ratio!")
            start = time.time()
            v1_subsampled = subsample_points(v1, 100000)
            v2_subsampled = subsample_points(v2, 100000)
            hull_size1 = estimate_convex_hull_size(v1_subsampled)
            hull_size2 = estimate_convex_hull_size(v2_subsampled)
            scale_ratio = hull_size2 / hull_size1
            print("    scale_ratio={} (size1={}, size2={})".format(scale_ratio, hull_size1, hull_size2))
            print("    estimated in {} s".format(time.time() - start))
    if target_resolution is None:
        print("Warning! No target resolution!")
        print("It will be estimated based on rough average distance between points!")
        start = time.time()
        v1_subsampled = subsample_points(v1, 1000)
        source_resolution = 1.5 * estimate_resolution(v1_subsampled) / np.sqrt(len(v1) / len(v1_subsampled)) * scale_ratio
        v2_subsampled = subsample_points(v2, 1000)
        target_resolution = 1.5 * estimate_resolution(v2_subsampled) / np.sqrt(len(v2) / len(v2_subsampled))
        resolution = np.max([source_resolution, target_resolution])
        print("    target_resolution={} (resolution1={}, resolution2={})".format(resolution, source_resolution, target_resolution))
        print("    estimated in {} s".format(time.time() - start))
        target_resolution = resolution

    print("scale_ratio={} target_resolution={}".format(scale_ratio, target_resolution))
    Metashape.app.update()

    v1 = v1 * scale_ratio

    stage = 0
    # Stages: [global if not no_global_alignment] + coarse_p2p + fine_p2p + [gicp if enabled]
    total_stages = 2
    if not no_global_alignment:
        total_stages += 1
    if use_generalized_icp:
        total_stages += 1

    # Helper to update progress (no-op if no callback)
    def _progress(stage_idx, stage_name):
        if progress_callback is not None:
            progress_callback(stage_idx, total_stages, stage_name)

    # Initial preview snapshot (before any alignment)
    if preview_collector is not None:
        preview_collector.add_stage("Initial", v1, v2, np.eye(4))

    if no_global_alignment:
        transformation = np.eye(4)
    else:
        stage += 1
        _progress(stage, "Global registration")
        print("{}/{}: Global registration...".format(stage, total_stages))
        start = time.time()
        source_down1, target_down1, global_registration_result = global_registration(v1, v2, global_voxel_size=64.0 * target_resolution)
        print("    estimated in {} s".format(time.time() - start))
        Metashape.app.update()
        transformation = global_registration_result.transformation
        if preview_collector is not None:
            preview_collector.add_stage("Global registration", v1, v2, transformation)

    downscale1 = 8.0
    stage += 1
    _progress(stage, "Coarse ICP")
    print("{}/{}: Coarse ICP registration...".format(stage, total_stages))
    start = time.time()
    icp_voxel_size1 = downscale1 * target_resolution
    source_down1 = downscale_point_cloud(to_point_cloud(v1), icp_voxel_size1)
    target_down1 = downscale_point_cloud(to_point_cloud(v2), icp_voxel_size1)
    icp_result1 = icp_registration(source_down1, target_down1, voxel_size=icp_voxel_size1, transform_init=transformation, max_iterations=100)
    print("    estimated in {} s".format(time.time() - start))
    Metashape.app.update()
    transformation = icp_result1.transformation
    if preview_collector is not None:
        preview_collector.add_stage("Coarse ICP", v1, v2, transformation)

    # ---- Crop to overlap zone (optional) ----
    # Now that coarse ICP has roughly aligned the clouds, we can identify which
    # parts actually overlap geometrically and drop the rest. Outlier zones
    # (areas covered by one survey but not the other) get matched to whatever's
    # nearest in the other cloud — wrong correspondences that pull the rotation
    # off in the fine pass. Cropping removes them entirely.
    if crop_to_overlap:
        # Transform source into the target's frame so "near in Euclidean
        # distance" means "in the overlap zone".
        v1_aligned = apply_transform_to_points(v1, transformation)
        # Threshold matches the coarse ICP correspondence threshold so we keep
        # anything coarse ICP itself could have matched.
        overlap_distance = 8.0 * target_resolution
        mask1, mask2 = compute_overlap_masks(v1_aligned, v2, overlap_distance)
        n1_before, n2_before = len(v1), len(v2)
        n1_after, n2_after = int(mask1.sum()), int(mask2.sum())
        min_kept = min(n1_after, n2_after)
        min_before = min(n1_before, n2_before)

        if min_kept < 100:
            # Cropping would leave too few points to align with — coarse
            # alignment is almost certainly bad. Skip cropping, proceed with
            # full clouds; the user will see the problem in the preview.
            print("    WARNING: overlap crop would keep very few points "
                  "({} v1, {} v2). Coarse alignment may be wrong; skipping crop "
                  "and using full clouds.".format(n1_after, n2_after))
        else:
            if min_kept < 0.05 * min_before:
                print("    WARNING: overlap crop removed >95% of points "
                      "(min_kept {} of {}). Coarse alignment may have placed "
                      "clouds far apart.".format(min_kept, min_before))
            v1 = v1[mask1]
            v2 = v2[mask2]
            print("    Cropped to overlap: v1 {} -> {}, v2 {} -> {} "
                  "(overlap_distance={:.4f})".format(
                      n1_before, n1_after, n2_before, n2_after, overlap_distance))

    downscale2 = 1.0
    stage += 1
    _progress(stage, "Fine ICP")
    print("{}/{}: Fine ICP registration (point-to-point)...".format(stage, total_stages))
    start = time.time()
    icp_voxel_size2 = downscale2 * target_resolution
    # Build point cloud objects once and reuse them between fine and optional
    # generalized ICP passes.
    pc1_fine = to_point_cloud(v1)
    pc2_fine = to_point_cloud(v2)
    icp_result2 = icp_registration(
        pc1_fine, pc2_fine,
        voxel_size=icp_voxel_size2, transform_init=transformation, max_iterations=100)
    print("    estimated in {} s".format(time.time() - start))
    Metashape.app.update()
    transformation = icp_result2.transformation
    if preview_collector is not None:
        preview_collector.add_stage("Fine ICP", v1, v2, transformation)

    if use_generalized_icp:
        stage += 1
        _progress(stage, "Generalized ICP refinement")
        print("{}/{}: Generalized ICP refinement...".format(stage, total_stages))
        start = time.time()
        # Same voxel scale as fine pass; gICP further refines via covariance-
        # based plane-to-plane matching.
        gicp_result = icp_registration_generalized(
            pc1_fine, pc2_fine,
            voxel_size=icp_voxel_size2, transform_init=transformation, max_iterations=100)
        print("    estimated in {} s".format(time.time() - start))
        Metashape.app.update()
        transformation = gicp_result.transformation
        if preview_collector is not None:
            preview_collector.add_stage("Generalized ICP", v1, v2, transformation)

    T1 = np.diag([1.0, 1.0, 1.0, 1.0])
    T1[:3, 3] = -c1.reshape(3)

    S = np.diag([scale_ratio, scale_ratio, scale_ratio, 1.0])

    T2 = np.diag([1.0, 1.0, 1.0, 1.0])
    T2[:3, 3] = c2.reshape(3)

    M = np.dot(T2, np.dot(transformation, np.dot(S, T1)))
    M = Metashape.Matrix(M)
    print("Estimated transformation matrix:")
    print(M)
    Metashape.app.update()
    return M


def subsample_points(vs, n):
    if len(vs) <= n:
        return vs.copy()
    np.random.seed(len(vs))
    vs = vs.copy()
    np.random.shuffle(vs)
    return vs[:n]


def estimate_convex_hull_size(vs):
    hull = ConvexHull(vs)
    indices = np.unique(hull.vertices)
    hull_vs = vs[indices]
    dists = hull_vs[:, None, :] - hull_vs[None, :, :]
    dists = dists.reshape(-1, 3)
    dists = np.sum(dists * dists, axis=-1)
    size = np.sqrt(np.max(dists))
    return size


def estimate_resolution(vs):
    dists = vs[:, None, :] - vs[None, :, :]
    dists = np.sum(dists * dists, axis=-1)
    dists[dists == 0] = np.max(dists)
    min_dists = np.min(dists, axis=-1)
    resolution = np.sqrt(np.median(min_dists))
    return resolution


def apply_transform_to_points(v, transformation_4x4):
    """Apply a 4x4 transformation matrix to an Nx3 point array."""
    n = len(v)
    homog = np.hstack([v, np.ones((n, 1), dtype=v.dtype)])  # Nx4
    transformed = homog @ transformation_4x4.T  # Nx4
    return transformed[:, :3].astype(v.dtype)


def compute_overlap_masks(v1_aligned, v2, overlap_distance):
    """Return (mask1, mask2) booleans marking points in v1_aligned and v2 that
    are within overlap_distance of any point in the other cloud.

    v1_aligned should be the source points transformed into the target's frame
    so that "near" in Euclidean distance corresponds to "in the overlap zone".

    Uses scipy.spatial.cKDTree — much faster than naive O(N²) and faster than
    Open3D's KDTree for this batch nearest-neighbor pattern.
    """
    tree1 = cKDTree(v1_aligned)
    tree2 = cKDTree(v2)
    # For each v2 point, distance to its nearest v1_aligned neighbor
    d2, _ = tree1.query(v2, k=1)
    mask2 = d2 < overlap_distance
    # For each v1_aligned point, distance to its nearest v2 neighbor
    d1, _ = tree2.query(v1_aligned, k=1)
    mask1 = d1 < overlap_distance
    return mask1, mask2


def to_point_cloud(vs):
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector(vs.copy())
    return pc


def downscale_point_cloud(pcd, voxel_size):
    pcd_down = pcd.voxel_down_sample(voxel_size)
    return pcd_down


def estimate_points_features(pcd_down, voxel_size):
    radius_normal = voxel_size * 2
    pcd_down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=radius_normal, max_nn=30))
    radius_feature = voxel_size * 5
    pcd_fpfh = o3d_registration.compute_fpfh_feature(pcd_down, o3d.geometry.KDTreeSearchParamHybrid(radius=radius_feature, max_nn=100))
    return pcd_fpfh


def global_registration(v1, v2, global_voxel_size):
    # See http://www.open3d.org/docs/release/tutorial/Advanced/global_registration.html#global-registration
    source = to_point_cloud(v1)
    target = to_point_cloud(v2)
    source_down = downscale_point_cloud(source, global_voxel_size)
    target_down = downscale_point_cloud(target, global_voxel_size)
    source_fpfh = estimate_points_features(source_down, global_voxel_size)
    target_fpfh = estimate_points_features(target_down, global_voxel_size)

    distance_threshold = global_voxel_size * 2.0
    max_validation = np.min([len(source_down.points), len(target_down.points)]) // 2
    kwargs = {
        "source": source_down,
        "target": target_down,
        "source_feature": source_fpfh,
        "target_feature": target_fpfh,
        "max_correspondence_distance": distance_threshold,
        "estimation_method": o3d_registration.TransformationEstimationPointToPoint(False),
        "ransac_n": 4,
        "checkers": [
            o3d_registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
            o3d_registration.CorrespondenceCheckerBasedOnDistance(distance_threshold)
        ],
        "criteria": o3d_registration.RANSACConvergenceCriteria(4000000, max_validation),
    }
    if o3d.__version__ not in ["0.{}.0".format(v) for v in range(12)]:
        # Introduced in 0.12.0 release
        kwargs["mutual_filter"] = True
    global_registration_result = o3d_registration.registration_ransac_based_on_feature_matching(**kwargs)
    return source_down, target_down, global_registration_result


def icp_registration(source, target, voxel_size, transform_init, max_iterations):
    """Point-to-point ICP. Robust to bad initial alignment; less precise for
    rotation on surface data. Use for the coarse pass."""
    # See http://www.open3d.org/docs/release/tutorial/Basic/icp_registration.html#icp-registration
    threshold = 8.0 * voxel_size
    reg_p2p = o3d_registration.registration_icp(
        source, target, threshold, transform_init,
        o3d_registration.TransformationEstimationPointToPoint(),
        o3d_registration.ICPConvergenceCriteria(max_iteration=max_iterations))
    return reg_p2p


def icp_registration_point_to_plane(source, target, voxel_size, transform_init, max_iterations):
    """Point-to-plane ICP. Cost = squared distance to the target's local tangent
    plane (residual projected onto the surface normal at each correspondence).
    Source points slide tangentially along the target surface for free; only
    motion off the surface is penalized. Much sharper rotational convergence
    on surface-like data (reefs, terrain, walls). Requires target normals.

    IMPORTANT: this method needs reliable surface normals on the target. It
    works well on dense, evenly-sampled data (mesh vertices, dense clouds)
    but fails on sparse irregular data like photogrammetric tie points, where
    normal estimation is too noisy to give a meaningful tangent plane. Use
    point-to-point on tie-point sources instead.
    """
    threshold = 8.0 * voxel_size
    # Estimate normals on the target if it doesn't already have them.
    # Hybrid search: up to 30 neighbors within radius 2*voxel_size — standard
    # Open3D convention for ICP normal estimation.
    #
    # Note: we deliberately do NOT call orient_normals_consistent_tangent_plane.
    # Open3D's point-to-plane cost is (n·(p-q))², which is symmetric to flipping
    # the sign of n — so consistent orientation doesn't help the optimization.
    # On sparse/disconnected clouds the orientation graph fails to reach all
    # clusters, leaving entire patches with flipped normals; the resulting
    # mixed-sign gradient pushes the source cloud away from the correct answer
    # rather than toward it.
    if not target.has_normals():
        target.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=2.0 * voxel_size, max_nn=30))
    reg_p2pl = o3d_registration.registration_icp(
        source, target, threshold, transform_init,
        o3d_registration.TransformationEstimationPointToPlane(),
        o3d_registration.ICPConvergenceCriteria(max_iteration=max_iterations))
    return reg_p2pl


def icp_registration_generalized(source, target, voxel_size, transform_init, max_iterations):
    """Generalized ICP (Segal et al. 2009). Models each correspondence as a
    plane-to-plane match using local covariance ellipsoids on BOTH clouds,
    so anisotropic sampling and surface curvature are handled jointly.
    Typically gives the tightest final precision on noisy real-world surfaces,
    at slightly higher per-iteration cost. Open3D computes covariances
    internally from k-nearest-neighbors."""
    threshold = 8.0 * voxel_size
    # Generalized ICP wants covariances on both clouds. It will compute them
    # internally if absent, but having normals helps consistency, and we may
    # have already computed them on target from the point-to-plane pass.
    if not source.has_normals():
        source.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=2.0 * voxel_size, max_nn=30))
    if not target.has_normals():
        target.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=2.0 * voxel_size, max_nn=30))
    reg_gicp = o3d_registration.registration_generalized_icp(
        source, target, threshold, transform_init,
        o3d_registration.TransformationEstimationForGeneralizedICP(),
        o3d_registration.ICPConvergenceCriteria(max_iteration=max_iterations))
    return reg_gicp


def draw_registration_result(source, target, transformation=None, title="Visualization"):
    Metashape.app.update()
    if isinstance(source, np.ndarray):
        source = to_point_cloud(source)
    if isinstance(target, np.ndarray):
        target = to_point_cloud(target)
    source_temp = copy.deepcopy(source)
    target_temp = copy.deepcopy(target)
    source_temp.paint_uniform_color([1, 0.706, 0])
    target_temp.paint_uniform_color([0, 0.651, 0.929])
    if transformation is not None:
        source_temp.transform(transformation)

    vis = o3d.visualization.Visualizer()
    vis.create_window(window_name=title)
    vis.add_geometry(source_temp)
    vis.add_geometry(target_temp)
    vis.run()
    vis.destroy_window()


class PreviewCollector:
    """Collects alignment snapshots from each stage and renders them as
    matplotlib 3D figures. Used to build a tabbed preview window shown
    once the whole alignment pipeline has finished — replacing the per-stage
    Open3D popups that block until each is closed.
    """

    # Subsample to keep matplotlib responsive — millions of points lag badly.
    MAX_POINTS_PER_CLOUD = 5000

    def __init__(self, rotation_for_display=None):
        self.stages = []  # list of (label, figure)
        # Optional 3x3 rotation applied to all plotted points so local 'up'
        # aligns with the plot's +Z axis. For ECEF / WGS84 chunks this is the
        # difference between a tilted-looking plot and a Z-up one. Identity
        # for projected or local CRS chunks.
        if rotation_for_display is None:
            self.rotation = np.eye(3)
        else:
            self.rotation = np.asarray(rotation_for_display, dtype=np.float64)

    def add_stage(self, label, v1, v2, transformation_4x4):
        """Snapshot the current alignment as a matplotlib figure.
        v1 is the moving cloud (will be transformed); v2 is the master cloud."""
        from matplotlib.figure import Figure
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 — registers 3d projection

        v1_t = apply_transform_to_points(v1, transformation_4x4)

        # Reorient for display: rotate so local up aligns with +Z.
        # Row-vector convention: p_new = p_old @ R.T applies the rotation R.
        if not np.allclose(self.rotation, np.eye(3)):
            v1_t = v1_t @ self.rotation.T
            v2_plot_source = v2 @ self.rotation.T
        else:
            v2_plot_source = v2

        # Subsample for speed; same seed per call so subsamples are reproducible.
        rng = np.random.default_rng(42)
        if len(v1_t) > self.MAX_POINTS_PER_CLOUD:
            idx1 = rng.choice(len(v1_t), self.MAX_POINTS_PER_CLOUD, replace=False)
            v1_t = v1_t[idx1]
        if len(v2_plot_source) > self.MAX_POINTS_PER_CLOUD:
            idx2 = rng.choice(len(v2_plot_source), self.MAX_POINTS_PER_CLOUD, replace=False)
            v2_plot = v2_plot_source[idx2]
        else:
            v2_plot = v2_plot_source

        fig = Figure(figsize=(8, 6), dpi=100)
        ax = fig.add_subplot(111, projection='3d')
        ax.scatter(v2_plot[:, 0], v2_plot[:, 1], v2_plot[:, 2],
                   c='#3a7ec0', s=1, alpha=0.5, label='Master (target)')
        ax.scatter(v1_t[:, 0], v1_t[:, 1], v1_t[:, 2],
                   c='#e89a2a', s=1, alpha=0.5, label='Moving (transformed)')
        ax.set_xlabel('X (East)' if not np.allclose(self.rotation, np.eye(3)) else 'X')
        ax.set_ylabel('Y (North)' if not np.allclose(self.rotation, np.eye(3)) else 'Y')
        ax.set_zlabel('Z (Up)' if not np.allclose(self.rotation, np.eye(3)) else 'Z')
        ax.set_title(label)
        ax.legend(loc='upper right', markerscale=8)
        # 45° elevation looking down on the scene; azimuth gives a 3/4 view
        # that distinguishes all three axes.
        ax.view_init(elev=45, azim=-60)
        # Equal axis scaling so spatial relationships aren't distorted.
        all_pts = np.vstack([v1_t, v2_plot])
        mins, maxs = all_pts.min(axis=0), all_pts.max(axis=0)
        center = (mins + maxs) / 2
        half_range = (maxs - mins).max() / 2
        ax.set_xlim(center[0] - half_range, center[0] + half_range)
        ax.set_ylim(center[1] - half_range, center[1] + half_range)
        ax.set_zlim(center[2] - half_range, center[2] + half_range)
        fig.tight_layout()

        self.stages.append((label, fig))


class PreviewWindow(QtWidgets.QDialog):
    """Tabbed dialog showing each stage's alignment as an interactive 3D plot.
    Returns QDialog.Accepted if user clicks OK, Rejected on Cancel/close.
    """

    def __init__(self, stages, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Alignment preview — review and accept")
        self.resize(950, 780)

        # Lazy import: matplotlib's Qt backend works alongside Metashape's
        # existing QApplication.
        from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
        from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT

        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            "Review each stage. Click OK to apply the final transform to "
            "the moving chunk, or Cancel to discard. Drag to rotate the 3D view; "
            "use the toolbar to zoom or pan.")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.tabs = QtWidgets.QTabWidget()
        for label, fig in stages:
            tab = QtWidgets.QWidget()
            tab_layout = QtWidgets.QVBoxLayout(tab)
            canvas = FigureCanvasQTAgg(fig)
            toolbar = NavigationToolbar2QT(canvas, tab)
            tab_layout.addWidget(toolbar)
            tab_layout.addWidget(canvas)
            self.tabs.addTab(tab, label)
        # Default to showing the last stage (the final alignment)
        if len(stages) > 0:
            self.tabs.setCurrentIndex(len(stages) - 1)
        layout.addWidget(self.tabs, stretch=1)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)


def compute_preview_rotation(chunk, centroid_world):
    """Compute a 3x3 rotation that takes world-frame vectors (e.g. ECEF for
    WGS84 chunks) to a local ENU-style frame at the given centroid, so the
    local 'up' direction aligns with +Z. Returns identity if the chunk has
    no CRS or if localframe can't be computed.

    Why this exists: chunks referenced to a geographic/geocentric CRS (WGS84,
    NAD83, etc.) produce world coords in ECEF Cartesian metres, where +Z points
    to Earth's rotation pole. Without reorienting, the matplotlib preview plot
    looks dramatically tilted — e.g. ~65 degrees in the Bahamas — because the
    plot's Z-axis is ECEF Z, not local up at the site. For projected CRSes
    (UTM etc.) and purely local frames this typically returns identity or
    near-identity.
    """
    if chunk.crs is None:
        return np.eye(3)
    try:
        center_vec = Metashape.Vector([float(centroid_world[0]),
                                       float(centroid_world[1]),
                                       float(centroid_world[2])])
        lf = chunk.crs.localframe(center_vec)  # 4x4 Metashape.Matrix
        # Extract the upper-left 3x3 rotation block as a numpy array
        R = np.array([[lf[i, j] for j in range(3)] for i in range(3)], dtype=np.float64)
        return R
    except Exception as e:
        print("    (could not compute preview rotation from CRS, using identity: {})".format(e))
        return np.eye(3)


def get_chunk_tie_points_in_world(chunk):
    """Extract a chunk's sparse tie points as an Nx3 numpy array in world frame.

    Tie point coordinates are stored in the chunk's internal coordinate system.
    To make two chunks' points comparable, we transform them by the chunk's
    transform.matrix so they end up in the same world frame.
    """
    if chunk.tie_points is None:
        raise Exception("Chunk '{}' has no tie points.".format(chunk.label))

    points = chunk.tie_points.points
    if len(points) == 0:
        raise Exception("Chunk '{}' has 0 tie points.".format(chunk.label))

    T = chunk.transform.matrix  # internal -> world

    coords = []
    for p in points:
        if not p.valid:
            continue
        wp = T * p.coord
        w = wp[3] if wp[3] != 0 else 1.0
        coords.append([wp[0] / w, wp[1] / w, wp[2] / w])

    if len(coords) == 0:
        raise Exception("Chunk '{}' has 0 valid tie points.".format(chunk.label))

    return np.array(coords, dtype=np.float32)


def get_model_vertices_in_world(chunk, model):
    """Extract a model's vertices as an Nx3 numpy array in world frame.

    Mesh vertices live in chunk-internal coordinates (possibly with an extra
    per-model transform). We compose model.transform (if present) with the
    chunk's internal->world transform so vertices come out in the same world
    frame used by get_chunk_tie_points_in_world.

    Note: model.Vertex.coord is a 3-component Vector (unlike TiePoint.coord
    which is 4-component homogeneous), so we use Matrix.mulp() to apply the
    4x4 transform — it treats the input as a point with implicit w=1.
    """
    if model is None:
        raise Exception("Chunk '{}' has no selected model.".format(chunk.label))
    if len(model.vertices) == 0:
        raise Exception("Model '{}' in chunk '{}' has 0 vertices.".format(model.label, chunk.label))

    # Compose: world = chunk.transform.matrix * model.transform * vertex
    T_internal_to_world = chunk.transform.matrix
    if model.transform is not None:
        T_full = T_internal_to_world * model.transform
    else:
        T_full = T_internal_to_world

    coords = np.empty((len(model.vertices), 3), dtype=np.float32)
    for i, v in enumerate(model.vertices):
        wp = T_full.mulp(v.coord)  # 3-vec point -> 3-vec point
        coords[i, 0] = wp.x
        coords[i, 1] = wp.y
        coords[i, 2] = wp.z
    return coords


def get_chunk_source_points(chunk, source):
    """Dispatch to the right extractor based on the source descriptor.

    `source` is one of:
      ("tie_points", None)         -> use chunk tie points
      ("model", <Metashape.Model>) -> use the given model's vertices
    """
    kind, payload = source
    if kind == "tie_points":
        return get_chunk_tie_points_in_world(chunk)
    elif kind == "model":
        return get_model_vertices_in_world(chunk, payload)
    else:
        raise Exception("Unknown source kind: {}".format(kind))


def chunk_has_alignable_data(chunk):
    """Cheap check: does this chunk have *some* alignable source?

    Crucially this does NOT touch model.vertices / model.faces (which would
    force Metashape to load the mesh from disk) or iterate tie point validity.
    We only look at object presence, not contents.
    """
    has_tie = chunk.tie_points is not None
    has_models = len(chunk.models) > 0
    return has_tie or has_models


def list_chunk_sources(chunk):
    """Return a list of (label, source_descriptor) options available in chunk.

    Kept deliberately lightweight: we do NOT access model.vertices or
    model.faces here, because in Metashape that triggers loading the mesh
    from disk. With many large meshes per chunk that makes the dialog
    intolerably slow to open. The actual data only gets touched when the
    user hits OK and ICP runs.
    """
    options = []
    # Tie points option, only if the chunk has any tie points object at all.
    # We don't count valid points (would require iterating); empty case is
    # caught at extract time by get_chunk_tie_points_in_world.
    if chunk.tie_points is not None:
        options.append(("Tie points", ("tie_points", None)))
    # One option per model in the chunk. Just label, no counts.
    for i, model in enumerate(chunk.models):
        label = model.label if model.label else "3D Model {}".format(i + 1)
        options.append(("Mesh: {}".format(label), ("model", model)))
    return options


class ICPAlignChunksDlg(QtWidgets.QDialog):

    def __init__(self, parent):

        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Align chunks (ICP)")

        self.doc = Metashape.app.document

        # Collect chunks that have *something* alignable (tie points or a mesh).
        # We use a cheap existence check so opening the dialog doesn't force
        # loading mesh data from disk for every chunk.
        self.chunks = []
        for ch in self.doc.chunks:
            if chunk_has_alignable_data(ch):
                label = ch.label if ch.label else "Chunk"
                self.chunks.append((ch, label))

        if len(self.chunks) < 2:
            raise Exception(
                "Need at least 2 chunks with tie points or a mesh. Found {}.".format(len(self.chunks)))

        # ---- widgets ----
        self.labelMoving = QtWidgets.QLabel("Moving chunk (will be transformed):")
        self.labelMaster = QtWidgets.QLabel("Master chunk (target, stays put):")
        self.labelMovingSrc = QtWidgets.QLabel("Moving source:")
        self.labelMasterSrc = QtWidgets.QLabel("Master source:")

        self.cmbMoving = QtWidgets.QComboBox()
        self.cmbMaster = QtWidgets.QComboBox()
        for (_, label) in self.chunks:
            self.cmbMoving.addItem(label)
            self.cmbMaster.addItem(label)
        if len(self.chunks) >= 2:
            self.cmbMaster.setCurrentIndex(1)

        self.cmbMovingSrc = QtWidgets.QComboBox()
        self.cmbMasterSrc = QtWidgets.QComboBox()
        # Track source options per combo so we can read the descriptor back on OK
        self._moving_sources = []  # list of (label, descriptor)
        self._master_sources = []
        self._refresh_sources(self.cmbMoving, self.cmbMovingSrc, "_moving_sources")
        self._refresh_sources(self.cmbMaster, self.cmbMasterSrc, "_master_sources")

        # Wire up: when a chunk selection changes, rebuild the source dropdown for it.
        self.cmbMoving.currentIndexChanged.connect(self._on_moving_chunk_changed)
        self.cmbMaster.currentIndexChanged.connect(self._on_master_chunk_changed)

        self.txtScaleRatio = QtWidgets.QLabel("Scale ratio:")
        self.edtScaleRatio = QtWidgets.QLineEdit()
        self.edtScaleRatio.setText("1.0")
        scale_ratio_tooltip = ("Ratio master_size / moving_size. For repeat surveys of the same scene this is 1.0. "
                               "Leave blank to auto-estimate via convex hull (unreliable for open scenes like reefs).")
        self.txtScaleRatio.setToolTip(scale_ratio_tooltip)
        self.edtScaleRatio.setToolTip(scale_ratio_tooltip)

        self.txtTargetResolution = QtWidgets.QLabel("Target resolution:")
        self.edtTargetResolution = QtWidgets.QLineEdit()
        self.edtTargetResolution.setText("0.01")
        target_resolution_tooltip = ("Approximate spacing between points in master chunk units (meters). "
                                     "Default 0.01 (1cm) suits typical mesh-based reef alignment. "
                                     "Lower (e.g. 0.002) for sub-cm precision once roughly aligned; "
                                     "higher (e.g. 0.1) for sparse tie-point first passes. "
                                     "Leave blank to auto-estimate from data density.")
        self.txtTargetResolution.setToolTip(target_resolution_tooltip)
        self.edtTargetResolution.setToolTip(target_resolution_tooltip)

        self.chkUseInitialAlignment = QtWidgets.QCheckBox("Use initial alignment (skip global registration)")
        self.chkUseInitialAlignment.setChecked(True)
        self.chkUseInitialAlignment.setToolTip(
            "Recommended. Runs only ICP starting from the chunks' current relative pose. "
            "Uncheck only if chunks are wildly misaligned and you want to attempt FPFH-based global registration.")

        self.chkPreview = QtWidgets.QCheckBox("Show preview before applying")
        self.chkPreview.setChecked(True)
        self.chkPreview.setToolTip(
            "After alignment runs, show a tabbed window with each stage's "
            "result (3D scatter, 45° view). Click OK to apply the transform, "
            "or Cancel to discard. Uncheck to apply silently.")

        self.chkGeneralizedICP = QtWidgets.QCheckBox("Add Generalized ICP refinement pass")
        self.chkGeneralizedICP.setChecked(False)
        self.chkGeneralizedICP.setToolTip(
            "Adds a third ICP pass using Generalized ICP (covariance-based plane-to-plane matching). "
            "Slower per iteration but typically gives the tightest final precision on noisy surface "
            "data like reefs. Recommended for the final refinement pass when chasing mm-scale alignment.")

        self.chkCropToOverlap = QtWidgets.QCheckBox("Crop to overlap zone after coarse alignment")
        self.chkCropToOverlap.setChecked(True)
        self.chkCropToOverlap.setToolTip(
            "After the coarse ICP pass roughly aligns the clouds, drop points that don't have a "
            "near neighbor in the other cloud — i.e. areas only one survey covered. This stops "
            "outlier zones from biasing the rotation in the fine pass. Recommended on. Disable "
            "only for debugging or when surveys have ~100%% overlap and no extraneous extent.")

        self.btnOk = QtWidgets.QPushButton("Ok")
        self.btnOk.setFixedSize(90, 50)
        self.btnOk.setToolTip("Run ICP and apply transform to the moving chunk")

        self.btnQuit = QtWidgets.QPushButton("Close")
        self.btnQuit.setFixedSize(90, 50)

        # ---- layout ----
        layout = QtWidgets.QGridLayout()
        # Row 0: chunk pickers
        layout.addWidget(self.labelMoving, 0, 0)
        layout.addWidget(self.cmbMoving, 0, 1)
        layout.addWidget(self.labelMaster, 0, 2)
        layout.addWidget(self.cmbMaster, 0, 3)
        # Row 1: source pickers
        layout.addWidget(self.labelMovingSrc, 1, 0)
        layout.addWidget(self.cmbMovingSrc, 1, 1)
        layout.addWidget(self.labelMasterSrc, 1, 2)
        layout.addWidget(self.cmbMasterSrc, 1, 3)
        # Row 2: scale & resolution
        layout.addWidget(self.txtScaleRatio, 2, 0)
        layout.addWidget(self.edtScaleRatio, 2, 1)
        layout.addWidget(self.txtTargetResolution, 2, 2)
        layout.addWidget(self.edtTargetResolution, 2, 3)
        # Row 3: checkboxes
        layout.addWidget(self.chkUseInitialAlignment, 3, 1)
        layout.addWidget(self.chkPreview, 3, 3)
        # Row 4: optional refinement / cropping
        layout.addWidget(self.chkCropToOverlap, 4, 1)
        layout.addWidget(self.chkGeneralizedICP, 4, 3)
        # Row 5: buttons
        layout.addWidget(self.btnOk, 5, 1)
        layout.addWidget(self.btnQuit, 5, 3)

        self.setLayout(layout)

        self.btnOk.clicked.connect(self.align)
        self.btnQuit.clicked.connect(self.reject)

    def _refresh_sources(self, chunk_combo, source_combo, attr_name):
        """Repopulate `source_combo` with the source options for the chunk currently
        selected in `chunk_combo`. Caches the list on self.<attr_name>."""
        idx = chunk_combo.currentIndex()
        if idx < 0:
            return
        chunk, _ = self.chunks[idx]
        sources = list_chunk_sources(chunk)
        setattr(self, attr_name, sources)
        source_combo.blockSignals(True)
        source_combo.clear()
        for (label, _desc) in sources:
            source_combo.addItem(label)
        source_combo.blockSignals(False)

    def _on_moving_chunk_changed(self, _idx):
        self._refresh_sources(self.cmbMoving, self.cmbMovingSrc, "_moving_sources")

    def _on_master_chunk_changed(self, _idx):
        self._refresh_sources(self.cmbMaster, self.cmbMasterSrc, "_master_sources")

    def align(self):
        print("Script started...")

        moving_idx = self.cmbMoving.currentIndex()
        master_idx = self.cmbMaster.currentIndex()
        if moving_idx == master_idx:
            raise Exception("Moving chunk and master chunk must be different.")

        moving_chunk, moving_label = self.chunks[moving_idx]
        master_chunk, master_label = self.chunks[master_idx]

        moving_src_idx = self.cmbMovingSrc.currentIndex()
        master_src_idx = self.cmbMasterSrc.currentIndex()
        if moving_src_idx < 0 or master_src_idx < 0:
            raise Exception("Please select a source for each chunk.")
        moving_src_label, moving_src = self._moving_sources[moving_src_idx]
        master_src_label, master_src = self._master_sources[master_src_idx]

        print("Aligning '{}' [{}] -> '{}' [{}]".format(
            moving_label, moving_src_label, master_label, master_src_label))

        # Extract points from both chunks, expressed in their respective world frames.
        v_moving = get_chunk_source_points(moving_chunk, moving_src)
        v_master = get_chunk_source_points(master_chunk, master_src)
        print("Point counts: moving={}, master={}".format(len(v_moving), len(v_master)))

        scale_ratio = None if self.edtScaleRatio.text().strip() == '' else float(self.edtScaleRatio.text())
        target_resolution = None if self.edtTargetResolution.text().strip() == '' else float(self.edtTargetResolution.text())
        no_global_alignment = self.chkUseInitialAlignment.isChecked()
        preview_enabled = self.chkPreview.isChecked()
        use_generalized_icp = self.chkGeneralizedICP.isChecked()
        crop_to_overlap = self.chkCropToOverlap.isChecked()

        # Build progress dialog. We update it from the alignment cascade via
        # progress_callback. The "max" is set to a generous upper bound;
        # align_two_point_clouds computes the real total and we'll match it.
        progress = QtWidgets.QProgressDialog(
            "Preparing alignment...", "", 0, 100, self)
        progress.setWindowTitle("Aligning chunks")
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setCancelButton(None)  # no mid-stage cancel — ICP iterations are uninterruptible
        progress.setValue(0)
        progress.show()
        QtWidgets.QApplication.processEvents()

        def progress_cb(stage_idx, total, name):
            # Scale 0..total -> 0..100
            pct = int(round(100 * stage_idx / max(1, total)))
            progress.setMaximum(100)
            progress.setValue(pct)
            progress.setLabelText("Stage {}/{}: {}".format(stage_idx, total, name))
            QtWidgets.QApplication.processEvents()

        # Preview collector only built if user wants the preview. We also
        # compute a rotation from the master chunk's CRS so that the plot is
        # Z-up rather than tilted by ECEF (the typical case for WGS84 chunks).
        if preview_enabled:
            master_centroid_world = np.mean(v_master, axis=0)
            preview_rotation = compute_preview_rotation(master_chunk, master_centroid_world)
            collector = PreviewCollector(rotation_for_display=preview_rotation)
        else:
            collector = None

        try:
            # M_world maps points in moving's world frame to master's world frame.
            M_world = align_two_point_clouds(v_moving, v_master,
                                             scale_ratio, target_resolution,
                                             no_global_alignment,
                                             use_generalized_icp=use_generalized_icp,
                                             crop_to_overlap=crop_to_overlap,
                                             progress_callback=progress_cb,
                                             preview_collector=collector)
            progress.setValue(100)
            progress.setLabelText("Done.")
            QtWidgets.QApplication.processEvents()
        finally:
            progress.close()

        # Optional preview gate: user reviews and approves before we write
        # the transform to the chunk.
        if collector is not None and len(collector.stages) > 0:
            preview_dlg = PreviewWindow(collector.stages, parent=self)
            result = preview_dlg.exec()
            if result != QtWidgets.QDialog.Accepted:
                print("User cancelled — moving chunk left unchanged.")
                self.reject()
                return

        # moving_chunk.transform.matrix maps internal -> moving-world. To make
        # moving-world points land in master-world, left-multiply by M_world.
        # This shifts cameras, tie points, and any current/future products in the
        # moving chunk so they align with the master chunk.
        moving_chunk.transform.matrix = M_world * moving_chunk.transform.matrix

        print("Applied transform to moving chunk '{}'.".format(moving_chunk.label))
        print("Script finished!")
        self.reject()


def show_alignment_dialog():
    app = QtWidgets.QApplication.instance()
    parent = app.activeWindow()

    dlg = ICPAlignChunksDlg(parent)
    dlg.exec()


label = "ReefShape/Tools/Align Timepoints (ICP)"
Metashape.app.removeMenuItem(label)
Metashape.app.removeMenuItem("ReefShape/Align Timepoints (ICP)")
Metashape.app.removeMenuItem("Scripts/Align chunks (ICP, tie points or mesh)")
Metashape.app.addMenuItem(label, show_alignment_dialog)
print("To execute this script press {}".format(label))
