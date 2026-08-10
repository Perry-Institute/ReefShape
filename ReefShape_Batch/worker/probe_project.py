"""
Read a Metashape project's structure and write it out as JSON.

Run headlessly by the GUI:

    metashape.exe -r probe_project.py <project.psx> <output.json>

Re-photography jobs need to know what is *inside* an existing project before
the user can configure them: which chunk to align against, and which of that
chunk's markers were damaged between timepoints. The GUI process cannot answer
that itself -- it has no `Metashape` module -- so it asks this worker, which
opens the project read-only and reports.

Parsing the .psx XML directly would avoid the ~1 s process spin-up, but it
would also mean reimplementing Metashape's project format and re-testing that
guess against every future version. Asking Metashape is slower and correct.

Exit code is 0 on success, 1 on failure; either way the output JSON is written
with an `ok` flag, so the caller has one place to look rather than having to
correlate an exit code with stderr.
"""

import json
import os
import sys
import traceback

import Metashape


def _safe(fn, default=None):
    """Evaluate `fn()`, returning `default` if it raises.

    The probe runs against arbitrary projects, including ones half-built by an
    interrupted run or written by an older Metashape. Any single attribute may
    be missing or throw; none of them is worth failing the whole probe over,
    because the caller mostly needs chunk and marker names and can cope with a
    null everywhere else.
    """
    try:
        return fn()
    except Exception:
        return default


def describe_marker(marker, scalebar_labels=()):
    """One marker, from the perspective of configuring an alignment.

    `reference_enabled` is what decides whether a marker contributes to the
    chunk transform, and it is the flag Align Timepoints filters the exported
    reference file on -- so the GUI needs it to explain why a marker might not
    be usable as a reference point.

    `in_scalebar` marks a marker that forms one end of a scalebar. Scalebars
    are repositioned on every visit, so those targets are never in the same
    place twice and are useless as alignment references -- the GUI hides them
    rather than inviting the user to reason about them.
    """
    label = _safe(lambda: marker.label, "")
    return {
        "label": label,
        "key": _safe(lambda: marker.key),
        "enabled": _safe(lambda: bool(marker.enabled), True),
        "reference_enabled": _safe(
            lambda: bool(marker.reference.enabled), False),
        "has_position": _safe(lambda: marker.position is not None, False),
        "has_reference_location": _safe(
            lambda: marker.reference.location is not None, False),
        "in_scalebar": label in scalebar_labels,
    }


def scalebar_marker_labels(chunk):
    """Labels of every marker that forms one end of a scalebar.

    A scalebar's endpoints can be markers or cameras; only markers have a
    label worth reporting, so anything else is skipped.
    """
    labels = set()
    for scalebar in _safe(lambda: list(chunk.scalebars), []) or []:
        for end in (_safe(lambda: scalebar.point0), _safe(lambda: scalebar.point1)):
            label = _safe(lambda: end.label) if end is not None else None
            if label:
                labels.add(label)
    return labels


# Depth-map downscale -> the quality name the UI shows. Mirrors
# batch/models.MESH_QUALITIES.
_DOWNSCALE_NAMES = {1: "Ultra High", 2: "High", 4: "Medium",
                    8: "Low", 16: "Lowest"}


def describe_mesh_quality(chunk):
    """The downscale a chunk's mesh was built at, and its quality name.

    Recorded by Metashape as `BuildDepthMaps/downscale` on the *model*, not on
    the chunk -- the depth maps themselves are usually cleared by the
    workflow's cleanup step, so reading it from `chunk.depth_maps` would find
    nothing on any finished project.

    Matters for re-photography: a revisit processed at a different mesh
    quality than the timepoint it is being compared against gives a
    difference that is partly an artifact of processing rather than of the
    reef.
    """
    downscale = None
    model = _safe(lambda: chunk.model)
    if model is not None:
        raw = _safe(lambda: model.meta["BuildDepthMaps/downscale"])
        if raw is not None:
            try:
                downscale = int(raw)
            except (TypeError, ValueError):
                downscale = None
    return downscale, _DOWNSCALE_NAMES.get(downscale)


def describe_chunk(chunk):
    """One chunk: identity, contents, and how far processing has got.

    The completion flags mirror the guards the workflow itself uses to decide
    what to skip (`chunk.tie_points is None`, `chunk.model is None`, and so
    on), which is what makes them meaningful in the GUI: they predict exactly
    which stages a re-run would actually perform.
    """
    scalebar_labels = scalebar_marker_labels(chunk)
    markers = _safe(
        lambda: [describe_marker(m, scalebar_labels) for m in chunk.markers],
        []) or []
    downscale, quality_name = describe_mesh_quality(chunk)

    has_outer_boundary = False
    shapes = _safe(lambda: chunk.shapes)
    if shapes:
        has_outer_boundary = _safe(
            lambda: any(
                s.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary
                for s in shapes),
            False) or False

    return {
        "key": _safe(lambda: chunk.key),
        "label": _safe(lambda: chunk.label, ""),
        "enabled": _safe(lambda: bool(chunk.enabled), True),

        "n_cameras": _safe(lambda: len(chunk.cameras), 0),
        "n_aligned_cameras": _safe(
            lambda: sum(1 for c in chunk.cameras if c.transform), 0),
        "n_markers": len(markers),
        "n_scalebars": _safe(lambda: len(chunk.scalebars), 0),
        "markers": markers,
        "scalebar_markers": sorted(scalebar_labels),

        # Processing settings a revisit should match. See
        # describe_mesh_quality.
        "depth_map_downscale": downscale,
        "mesh_quality": quality_name,

        "crs_name": _safe(lambda: chunk.crs.name if chunk.crs else None),
        "crs_wkt": _safe(lambda: chunk.crs.wkt if chunk.crs else None),

        # Processing state, in workflow order.
        "has_tie_points": _safe(lambda: chunk.tie_points is not None, False),
        "has_model": _safe(lambda: chunk.model is not None, False),
        "has_elevation": _safe(lambda: chunk.elevation is not None, False),
        "has_orthomosaic": _safe(lambda: chunk.orthomosaic is not None, False),
        "has_outer_boundary": has_outer_boundary,
        "is_optimized": _safe(
            lambda: any(k.startswith("OptimizeCameras/")
                        for k in chunk.meta.keys()), False),
        "orthomosaic_resolution": _safe(
            lambda: chunk.orthomosaic.resolution if chunk.orthomosaic else None),
    }


def probe(project_path):
    doc = Metashape.Document()
    # read_only guarantees we cannot damage a project just by looking at it.
    # ignore_lock matters in practice: users routinely have the project open
    # in the Metashape GUI while setting up a batch against it, and a stale
    # lock file left by an earlier crash would otherwise block them with no
    # obvious remedy. Neither is a risk while read-only.
    doc.open(project_path, read_only=True, ignore_lock=True)

    chunks = [describe_chunk(c) for c in doc.chunks]
    return {
        "ok": True,
        "path": project_path,
        "metashape_version": _safe(lambda: Metashape.app.version, ""),
        "n_chunks": len(chunks),
        "chunks": chunks,
    }


def main(argv):
    if len(argv) < 3:
        sys.stderr.write(
            "usage: metashape -r probe_project.py <project.psx> <output.json>\n")
        return 2

    project_path, output_path = argv[1], argv[2]

    if not os.path.isfile(project_path):
        result = {"ok": False,
                  "path": project_path,
                  "error": "Project file does not exist."}
    else:
        try:
            result = probe(project_path)
        except Exception as exc:
            result = {
                "ok": False,
                "path": project_path,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }

    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    except OSError as exc:
        sys.stderr.write("Could not write {}: {}\n".format(output_path, exc))
        return 1

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
