"""
Timepoint alignment -- the align-chunks pipeline, with no user interface.

Companion to `reefshape_core`, and the same arrangement: one implementation,
driven by both the Metashape menu dialog (02_align_chunks.py) and the headless
batch worker.

What it does, and why it works: the first timepoint's markers carry Metashape's
*estimated* positions, which are far more precise than the source coordinates
that were measured in the field. Exporting those estimates at sub-millimetre
precision and importing them as the second timepoint's marker references makes
Metashape warp the new data onto the old, so the two orthomosaics line up
pixel to pixel -- even though nobody knows where the reef is on Earth to
anything like that precision.

Markers that were damaged or moved between visits would drag that fit toward a
wrong answer, so they are imported with a slack accuracy and effectively
ignored. See `apply_marker_accuracy`.

Nothing here imports Qt or calls `Metashape.app` interactively, so it runs
under `metashape.exe -r`. Note the explicit `import os`, for the same reason
as in reefshape_core: Metashape's GUI script host injects `os` into script
globals and the headless runner does not.
"""

import csv
import os

import Metashape

from reefshape_core import Reporter, WorkflowError


# Marker reference accuracy, in metres.
#
# Aligned markers get a very tight accuracy so Metashape treats the reference
# positions as near-truth and warps the new timepoint onto the old. Damaged
# markers get a metre, which is so loose relative to the others that they
# contribute effectively nothing to the fit without having to be deleted --
# they stay in the chunk, visible and available, just not trusted.
ALIGNED_ACCURACY = 0.0001
DAMAGED_ACCURACY = 1.0

# Decimal places for the exported estimated reference. In decimal degrees, 9
# places is roughly 0.1 mm -- below the precision of the alignment itself, so
# nothing is lost to rounding.
EXPORT_PRECISION = 9


def export_estimated_reference(reference_chunk, path):
    """Write the reference chunk's estimated marker positions to `path`.

    Estimated rather than source positions: the source coordinates came off a
    handheld GPS or a tape measure, while the estimates come out of the bundle
    adjustment and are internally consistent to a fraction of a millimetre.
    Alignment needs the latter.
    """
    reference_chunk.exportReference(
        path=path, format=Metashape.ReferenceFormatCSV,
        items=Metashape.ReferenceItemsMarkers, columns="nouvwUVW",
        delimiter=",", precision=EXPORT_PRECISION)


def filter_to_enabled_markers(reference_chunk, path):
    """Strip markers that were not used for georeferencing in timepoint one.

    Metashape's exported `enabled` flag cannot be relied on -- it comes out set
    for every marker regardless -- so the flag is read from the chunk instead
    and the file rewritten to contain only the markers whose reference was
    actually enabled. Without this, a marker the user deliberately excluded
    from the first timepoint's solution would quietly steer the second one.

    Rewrites `path` in place, leaving a single header row (the source file has
    two: a CRS line and a column line). Returns the number of markers kept.

    Rows are matched to markers by label rather than by position in the file.
    The original implementation walked both in parallel on the assumption that
    Metashape exports markers in index order; that holds today, but a silent
    off-by-one here would misassign every reference coordinate in the chunk,
    which is a bad thing to leave resting on an assumption.
    """
    enabled = {}
    for marker in reference_chunk.markers:
        try:
            if marker.reference.enabled:
                enabled[marker.label] = marker
        except AttributeError:
            continue

    with open(path, newline="") as handle:
        rows = list(csv.reader(handle))

    if len(rows) < 2:
        raise WorkflowError(
            "The exported reference file for chunk {!r} is empty. It has {} "
            "marker(s); alignment needs markers with estimated positions."
            .format(reference_chunk.label, len(reference_chunk.markers)))

    # rows[0] is the CRS line, rows[1] the column header.
    header = rows[1]
    kept = [row for row in rows[2:] if row and row[0] in enabled]

    if not kept:
        raise WorkflowError(
            "No usable reference markers in chunk {!r}. Alignment needs "
            "markers whose reference is enabled; this chunk has {} marker(s), "
            "none of them enabled for referencing."
            .format(reference_chunk.label, len(reference_chunk.markers)))

    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(kept)

    return len(kept)


def apply_marker_accuracy(chunk, damaged_labels, reporter):
    """Tighten every marker's accuracy, then loosen the damaged ones.

    Returns (aligned_count, damaged_found). Damaged labels that match no
    marker are reported rather than ignored: a typo in a marker name would
    otherwise silently leave a moved target driving the alignment, which is
    exactly the failure this is meant to prevent, and it would show up only as
    a subtly misaligned mosaic much later.
    """
    damaged = set(damaged_labels or [])
    found = set()
    aligned = 0

    for marker in chunk.markers:
        if marker.label in damaged:
            marker.reference.accuracy = Metashape.Vector(
                [DAMAGED_ACCURACY] * 3)
            found.add(marker.label)
        else:
            marker.reference.accuracy = Metashape.Vector(
                [ALIGNED_ACCURACY] * 3)
            aligned += 1

    missing = damaged - found
    if missing:
        reporter.warn(
            "These markers were listed as damaged but are not in the new "
            "chunk, so nothing was excluded for them: {}. Check the labels "
            "match the reference chunk's markers."
            .format(", ".join(sorted(missing))))

    return aligned, len(found)


def copy_outer_boundary(reference_chunk, chunk, reporter):
    """Copy the plot boundary from the reference chunk, if there is one.

    Lets the full workflow skip boundary regeneration on the new chunk, and
    more importantly keeps the plot outline identical between timepoints, so
    the two orthomosaics cover exactly the same ground and can be compared
    directly.

    No-op when the reference has no outer boundary or the target already has
    one. Returns True if a boundary was copied.
    """
    if not reference_chunk.shapes:
        return False

    source = next(
        (s for s in reference_chunk.shapes
         if s.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary),
        None)
    if source is None:
        reporter.warn(
            "The reference chunk {!r} has no outer boundary polygon to copy, "
            "so the new chunk will generate its own from its corner markers. "
            "The two plots may not cover exactly the same area."
            .format(reference_chunk.label))
        return False

    if chunk.shapes:
        for shape in chunk.shapes:
            if shape.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary:
                return False  # already has one; leave it alone
    else:
        chunk.shapes = Metashape.Shapes()
        chunk.shapes.crs = reference_chunk.shapes.crs

    copied = chunk.shapes.addShape()
    copied.label = "Copied Boundary"
    copied.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
    copied.geometry = source.geometry
    reporter.info(" --- Outer boundary copied from reference chunk --- ")
    return True


def align_timepoints(doc, reference_chunk, chunk, target_type,
                     damaged_markers=None, reporter=None, keep_reference_file=False):
    """Align `chunk` (a new timepoint) onto `reference_chunk` (an earlier one).

    Detects markers in the new chunk if it has none, imports the reference
    chunk's estimated marker positions, weights them, recomputes the
    transform, and copies the plot boundary across.

    This does not align cameras or build anything -- it establishes the
    georeferencing that makes the subsequent full workflow produce products
    registered to the earlier timepoint. Run `reefshape_core.run_workflow` on
    the same chunk afterwards.
    """
    reporter = reporter or Reporter()

    if reference_chunk is None or chunk is None:
        raise WorkflowError("Both a reference chunk and a new chunk are "
                            "required to align timepoints.")
    if reference_chunk == chunk:
        raise WorkflowError(
            "The reference chunk and the chunk being aligned are the same "
            "chunk ({!r}). Pick the earlier timepoint as the reference."
            .format(chunk.label))
    if len(reference_chunk.markers) == 0:
        raise WorkflowError(
            "The reference chunk {!r} has no markers, so there is nothing to "
            "align the new timepoint to.".format(reference_chunk.label))

    project_folder = os.path.dirname(doc.path or "")
    est_ref_path = os.path.join(
        project_folder, reference_chunk.label + "_est_ref.csv")

    try:
        reporter.step("Exporting estimated reference from {!r}".format(
            reference_chunk.label))
        export_estimated_reference(reference_chunk, est_ref_path)

        kept = filter_to_enabled_markers(reference_chunk, est_ref_path)
        reporter.info("  {} of {} markers are enabled for referencing".format(
            kept, len(reference_chunk.markers)))

        # Only detect when the chunk has none: re-detecting would duplicate
        # markers the user placed by hand or a previous run already found.
        if len(chunk.markers) == 0:
            reporter.step("Detecting markers in {!r}".format(chunk.label))
            chunk.detectMarkers(
                target_type=target_type, tolerance=20, filter_mask=False,
                inverted=False, noparity=False, maximum_residual=5,
                minimum_size=0, minimum_dist=5,
                progress=reporter.progress_callback())
            reporter.info("  {} markers detected".format(len(chunk.markers)))

        if len(chunk.markers) == 0:
            raise WorkflowError(
                "No markers were detected in chunk {!r}, so it cannot be "
                "aligned to the earlier timepoint. Check that the photos show "
                "the targets and that the target type is correct."
                .format(chunk.label))

        reporter.step("Importing reference into {!r}".format(chunk.label))
        chunk.importReference(
            path=est_ref_path, format=Metashape.ReferenceFormatCSV,
            delimiter=",", columns="noxyz", skip_rows=1,
            crs=reference_chunk.crs, ignore_labels=False,
            create_markers=False, threshold=0.1, shutter_lag=0)

        aligned, damaged_found = apply_marker_accuracy(
            chunk, damaged_markers, reporter)
        reporter.info("  {} markers weighted for alignment, {} excluded as "
                      "damaged".format(aligned, damaged_found))

        # Markers shared between the two chunks are what make the alignment
        # work. None in common means the import matched nothing, and the
        # transform below would be meaningless.
        shared = {m.label for m in chunk.markers} & {
            m.label for m in reference_chunk.markers}
        if not shared:
            raise WorkflowError(
                "Chunk {!r} shares no marker labels with the reference chunk "
                "{!r}, so there is nothing to align on. The two timepoints "
                "must have targets in common."
                .format(chunk.label, reference_chunk.label))

        chunk.updateTransform()
        copy_outer_boundary(reference_chunk, chunk, reporter)

        reporter.info(" --- Timepoints aligned on {} shared marker(s) --- "
                      .format(len(shared)))
        doc.save()
        return sorted(shared)

    finally:
        # The exported reference is an intermediate. Removing it keeps it from
        # being mistaken for a real georeferencing input later -- it sits in
        # the project folder next to the files that are.
        if not keep_reference_file:
            try:
                os.remove(est_ref_path)
            except OSError:
                pass
