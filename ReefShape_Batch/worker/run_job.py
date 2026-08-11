"""
Run one ReefShape batch job. Executed headlessly by the GUI:

    metashape.exe -r run_job.py job.json [result.json]

One process per job. That is what gives the batch crash isolation (a segfault
in Metashape fails this job, not the whole queue), cancellation (kill the
process), and parallelism (run several).

The processing itself is not implemented here -- it lives in
`ReefShape_Scripts/reefshape_core.py`, shared with the Metashape menu script so
the batch runner and the menu cannot drift apart. This module is only the
plumbing around it: read the job, set up the document and chunk, add the
photos, run the workflow, report what happened.

Progress and warnings go to stdout as protocol lines (see batch/protocol.py);
the final outcome is additionally written to `result.json` so the parent has a
reliable record even if the pipe was truncated.
"""

import json
import os
import sys
import time
import traceback

import Metashape

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                    # ReefShape_Batch
_REPO = os.path.dirname(_ROOT)                    # repository root

# reefshape_core ships with the Metashape scripts, because the menu scripts
# import it too. Prefer the copy next to this checkout; fall back to the
# installed scripts directory so the batch app keeps working when it has been
# copied somewhere else on its own.
for candidate in (os.path.join(_REPO, "ReefShape_Scripts"),
                  os.path.join(_ROOT, "worker")):
    if os.path.isfile(os.path.join(candidate, "reefshape_core.py")):
        sys.path.insert(0, candidate)
        break
sys.path.insert(0, _ROOT)

import reefshape_core                                    # noqa: E402
import reefshape_align                                   # noqa: E402
from reefshape_core import WorkflowSettings, WorkflowError  # noqa: E402
from batch import protocol                               # noqa: E402


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".tif", ".tiff")


class ProtocolReporter(reefshape_core.Reporter):
    """Turns workflow callbacks into protocol lines on stdout."""

    def __init__(self):
        self.warnings = []
        self.current_step = ""

    def step(self, name, index=None, total=None):
        self.current_step = name
        protocol.emit(protocol.STEP, name=name, index=index, total=total)
        # Also print plainly so the per-job log file reads like a normal
        # Metashape transcript rather than a wall of JSON.
        print(" --- {} --- ".format(name))

    def progress(self, percent):
        protocol.emit(protocol.PROGRESS, pct=percent, step=self.current_step)

    def info(self, message):
        print(message)

    def warn(self, message):
        self.warnings.append(message)
        protocol.emit(protocol.WARN, msg=message)
        print("WARNING: {}".format(message))


def apply_resource_limits(job):
    """Pin this process's GPU/CPU use before any processing starts.

    Only meaningful when several jobs run at once on a machine with more than
    one real GPU. gpu_mask 0 means "leave Metashape's default alone", which is
    the right behaviour for the overwhelmingly common single-GPU case.
    """
    resources = job.get("resources") or {}
    mask = resources.get("gpu_mask", 0)
    if mask:
        Metashape.app.gpu_mask = int(mask)
    if "cpu_enable" in resources:
        Metashape.app.cpu_enable = bool(resources["cpu_enable"])


def list_photos(folders):
    """Every image in `folders`, top level only.

    Deliberately not recursive, matching AddPhotosGroupBox in the menu script:
    a ReefShape photo folder is one dive's worth of images, and recursing would
    silently sweep in a `taglab_outputs` folder or an old export sitting
    alongside them.
    """
    photos = []
    for folder in folders:
        try:
            names = sorted(os.listdir(folder))
        except OSError as exc:
            raise WorkflowError("Could not read photo folder {}: {}".format(folder, exc))
        for name in names:
            if name.lower().endswith(IMAGE_EXTENSIONS):
                photos.append(os.path.join(folder, name))
    if not photos:
        raise WorkflowError(
            "No images found in: {}".format(", ".join(folders)))
    return photos


def chunk_name_from_photos(chunk, fallback):
    """Derive a YYYYMMDD chunk name from the first photo's capture date.

    Mirrors the menu script's auto-naming. Reading EXIF through the camera's
    own metadata rather than a separate EXIF library means we see exactly what
    Metashape saw, and need no extra dependency.
    """
    for camera in chunk.cameras:
        if not camera.photo:
            continue
        meta = camera.photo.meta or {}
        for key in ("Exif/DateTimeOriginal", "Exif/DateTime",
                    "System/FileModifyDate"):
            if key in meta:
                try:
                    from datetime import datetime
                    stamp = datetime.strptime(meta[key], "%Y:%m:%d %H:%M:%S")
                    return stamp.strftime("%Y%m%d")
                except ValueError:
                    continue
        break
    return fallback


def prepare_chunk(doc, job, reporter):
    """Open or create the project and return the chunk to process.

    New-plot jobs get a fresh project (or reuse an existing one, which is how
    an interrupted run resumes). Re-photography jobs open the existing project
    and add a new chunk beside the reference.
    """
    project_path = job["project_path"]
    kind = job.get("kind", "new_plot")

    if kind == "rephoto":
        if not os.path.isfile(project_path):
            raise WorkflowError(
                "Re-photography job points at a project that does not exist: {}"
                .format(project_path))
        doc.open(project_path, read_only=False, ignore_lock=True)
    elif os.path.isfile(project_path):
        # Resuming: the workflow's stage guards decide what still needs doing.
        reporter.info("Opening existing project to continue processing.")
        doc.open(project_path, read_only=False, ignore_lock=True)
    else:
        parent = os.path.dirname(project_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        doc.addChunk()
        doc.save(path=project_path)

    wanted = job.get("chunk_name", "")

    # Reuse a chunk of the same name if there is one -- that is what makes a
    # resumed job continue its own chunk instead of starting a second one.
    if wanted:
        for chunk in doc.chunks:
            if chunk.label == wanted:
                reporter.info("Continuing existing chunk {!r}".format(wanted))
                return chunk

    if kind == "rephoto":
        chunk = doc.addChunk()
    else:
        chunk = doc.chunks[0] if doc.chunks else doc.addChunk()

    if wanted:
        chunk.label = wanted
    return chunk


def add_photos(chunk, job, reporter):
    """Add the job's photos, unless the chunk already has them.

    Skipping when cameras are present keeps a resumed job from adding a second
    copy of every image, which would be both slow and wrong.
    """
    if len(chunk.cameras) > 0:
        reporter.info("Chunk already has {} cameras; skipping photo import."
                      .format(len(chunk.cameras)))
        return

    photos = list_photos(job.get("photo_folders") or [])
    reporter.step("Adding {} photos".format(len(photos)))
    chunk.addPhotos(photos, progress=reporter.progress_callback())

    if not job.get("chunk_name"):
        derived = chunk_name_from_photos(chunk, chunk.label)
        if derived and derived != chunk.label:
            reporter.info("Naming chunk {!r} from photo capture date".format(derived))
            chunk.label = derived


def run(job, reporter):
    doc = Metashape.Document()
    chunk = prepare_chunk(doc, job, reporter)
    add_photos(chunk, job, reporter)
    doc.save()

    settings = WorkflowSettings.from_job_dict(job)
    icp = job.get("icp") or {}
    icp_enabled = job.get("kind") == "rephoto" and icp.get("enabled")

    if job.get("kind") == "rephoto":
        reference_label = job.get("reference_chunk", "")
        reference_chunk = next(
            (c for c in doc.chunks if c.label == reference_label), None)
        if reference_chunk is None:
            raise WorkflowError(
                "Reference chunk {!r} was not found in {}. Available chunks: "
                "{}".format(reference_label, job["project_path"],
                            ", ".join(repr(c.label) for c in doc.chunks)))

        # Skipping when the chunk is already referenced is what lets a
        # re-photography job resume: alignment is not idempotent in the way
        # the workflow's stages are, and re-importing over an already-aligned
        # chunk would reset accuracies that may have been adjusted since.
        already_aligned = any(
            m.reference.location is not None for m in chunk.markers
            if m.reference)
        if icp_enabled:
            # ICP mode: this timepoint is georeferenced and scaled from its own
            # temporary targets, exactly like a new plot, and only afterwards
            # matched onto the reference chunk by surface. Importing the
            # reference chunk's marker positions here would fight that -- the
            # targets are not the same physical objects between visits.
            reporter.info(
                "ICP alignment selected: this timepoint is referenced from its "
                "own targets, then matched to {!r} by surface after the mesh "
                "is built.".format(reference_label))
        elif already_aligned:
            reporter.info("Chunk is already referenced; skipping timepoint "
                          "alignment.")
        else:
            reefshape_align.align_timepoints(
                doc=doc,
                reference_chunk=reference_chunk,
                chunk=chunk,
                target_type=settings.resolve_target_type(),
                damaged_markers=job.get("damaged_markers") or [],
                # Empty means "use the reference chunk's own enabled flags".
                reference_markers=job.get("reference_markers") or None,
                reporter=reporter,
            )

    hook = None
    if icp_enabled:
        hook = _make_icp_hook(doc, job, icp, reference_chunk, reporter)

    return reefshape_core.run_workflow(doc, chunk, settings, reporter,
                                       on_mesh_complete=hook)


def _make_icp_hook(doc, job, icp, reference_chunk, reporter):
    """Build the post-mesh callback that runs ICP.

    Runs after the mesh exists and before the DEM, which is the last moment
    the chunk transform can change without invalidating a product: the mesh
    moves with the transform, whereas the DEM and orthomosaic are rasters in
    world space.
    """
    import reefshape_icp  # noqa: E402 -- deferred; see reefshape_icp docstring

    def on_mesh_complete(chunk):
        reporter.step("ICP alignment to {!r}".format(reference_chunk.label))
        try:
            result = reefshape_icp.align_chunk_to_reference(
                moving_chunk=chunk,
                master_chunk=reference_chunk,
                moving_source=icp.get("moving_source", "mesh"),
                master_source=icp.get("master_source", "mesh"),
                scale_ratio=icp.get("scale_ratio", 1.0),
                target_resolution=icp.get("target_resolution", 0.01),
                use_initial_alignment=icp.get("use_initial_alignment", True),
                crop_to_overlap=icp.get("crop_to_overlap", True),
                use_generalized_icp=icp.get("use_generalized_icp", False),
                reporter=reporter,
            )
        except reefshape_icp.IcpError as exc:
            # Not fatal. Alignment failing leaves the timepoint georeferenced
            # from its own targets -- usable on its own, just not registered
            # to the earlier visit. Better to finish and say so than to throw
            # away a completed mesh.
            reporter.warn(
                "ICP alignment could not run: {}. This timepoint keeps its own "
                "georeferencing and will NOT be registered to {!r}."
                .format(exc, reference_chunk.label))
            return

        protocol.emit("icp", fitness=result.fitness,
                      rmse=result.inlier_rmse,
                      moving_points=result.source_points,
                      master_points=result.target_points)

        # ICP always returns a transform, even a bad one, and with no
        # permanent markers there is nothing independent to check it against.
        # These statistics are the only signal, so a poor fit is surfaced as a
        # warning the user has to read -- but not a failure, because the
        # products are still built and may be perfectly usable.
        min_fitness = float(icp.get("min_fitness", 0.5))
        max_rmse = float(icp.get("max_rmse", 0.05))
        problems = []
        if result.fitness < min_fitness:
            problems.append(
                "only {:.0%} of the new timepoint found a match on the "
                "reference surface (below the {:.0%} you set)".format(
                    result.fitness, min_fitness))
        if result.inlier_rmse > max_rmse:
            problems.append(
                "residual misfit is {:.3f} m (above the {:.3f} m you set)"
                .format(result.inlier_rmse, max_rmse))
        if problems:
            reporter.warn(
                "ICP alignment to {!r} may be poor: {}. The data products were "
                "still built. Check the two timepoints overlay before using "
                "them for change detection."
                .format(reference_chunk.label, "; and ".join(problems)))
        else:
            reporter.info("ICP alignment looks good -- {}".format(
                result.summary()))

    return on_mesh_complete


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: metashape -r run_job.py <job.json> [result.json]\n")
        return 2

    job_path = argv[1]
    result_path = argv[2] if len(argv) > 2 else None

    started = time.time()
    try:
        with open(job_path, "r", encoding="utf-8") as fh:
            job = json.load(fh)
    except (OSError, ValueError) as exc:
        protocol.emit(protocol.FAILED, msg="Could not read job file: {}".format(exc))
        return 1

    reporter = ProtocolReporter()
    outcome = {"job_id": job.get("job_id", ""), "ok": False}

    try:
        apply_resource_limits(job)
        result = run(job, reporter)
        # A run that stopped for manual referencing is *not* a failure: the
        # alignment and mesh are done and saved, and the remaining stages
        # resume cheaply once the user supplies the referencing. Reporting it
        # as ok keeps it out of the batch's failure count and stops
        # stop-on-error from halting a queue over it.
        outcome.update({
            "ok": True,
            "status": result.status,
            "warnings": result.warnings,
            "outputs": result.outputs,
            "seconds": round(time.time() - started, 1),
        })
        protocol.emit(protocol.DONE, status=result.status,
                      warnings=result.warnings, outputs=result.outputs,
                      seconds=outcome["seconds"])
    except WorkflowError as exc:
        outcome.update({"status": "failed", "error": str(exc),
                        "seconds": round(time.time() - started, 1)})
        protocol.emit(protocol.FAILED, msg=str(exc))
        print("ERROR: {}".format(exc))
    except Exception as exc:
        # Anything unexpected: keep the traceback in the log, but give the GUI
        # a one-line summary it can put in the job table.
        trace = traceback.format_exc()
        outcome.update({"status": "failed", "error": str(exc),
                        "traceback": trace,
                        "seconds": round(time.time() - started, 1)})
        protocol.emit(protocol.FAILED, msg="{}: {}".format(type(exc).__name__, exc))
        print(trace)

    if result_path:
        try:
            with open(result_path, "w", encoding="utf-8") as fh:
                json.dump(outcome, fh, indent=2, default=str)
        except OSError:
            pass

    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
