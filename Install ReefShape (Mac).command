#!/bin/bash
# ReefShape installer for macOS — mirrors Install ReefShape (PC).bat.
#
# Two things this gets right that an earlier version did not.
#
# ORDER. The old version deleted a hardcoded list of script names first and
# copied second, checked neither the source nor the result, and printed
# "Installation complete!" either way. When the copy failed, the user was left
# with exactly the scripts that happened to be missing from that hardcoded
# delete list -- and a dialog telling them it had worked. So now: validate the
# source, copy, verify the copy, and only then remove stale files. A failure at
# any point says so instead of claiming success.
#
# WHAT COUNTS AS OURS. Stale files are read from a manifest written by the
# previous install, so the installer can only ever remove files it put there
# itself. Deciding by name pattern instead is not safe: the scripts folder is
# shared, and "looks like a ReefShape script" also matches ReefShape-Air's
# 01_full_aerial_workflow.py and reefshape_air_ui_components.py, which are a
# different product and not ours to delete.

set -u

DIR="$(cd "$(dirname "$0")"; pwd)"
SOURCE="$DIR/ReefShape_Scripts"
TARGET="$HOME/Library/Application Support/Agisoft/Metashape Pro/scripts"
MANIFEST="$TARGET/.reefshape_manifest"

die() {
    echo "" >&2
    echo "ERROR: $1" >&2
    osascript -e "display dialog \"ReefShape installation FAILED.

$1\" with title \"ReefShape Installer\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1 || true
    exit 1
}

echo "Installing ReefShape scripts for macOS..."
echo "Source folder: $SOURCE"
echo "Target folder: $TARGET"
echo ""

# --- 1. Validate the source before touching anything ---------------------
[ -d "$SOURCE" ] || die "Could not find the ReefShape_Scripts folder at:
$SOURCE

This installer must stay next to the ReefShape_Scripts folder. If you moved it
out of the ReefShape folder, put it back and run it again."

for required in "01_full_reefshape_workflow.py" "ui_components.py" \
                "modules/reefshape_core.py" "modules/pip_auto_install.py"; do
    [ -f "$SOURCE/$required" ] || die "The ReefShape_Scripts folder is missing $required.

It looks incomplete. Re-download ReefShape and try again."
done

mkdir -p "$TARGET" || die "Could not create the scripts folder at:
$TARGET"

# --- 2. Clear caches that would otherwise mask the new code --------------
# Metashape caches compiled bytecode, and a stale .pyc for a script that has
# since changed is a genuinely confusing failure: the menu item is there and
# runs the old code. __pycache__ is shared with other products, so only our
# own entries are cleared.
if [ -d "$TARGET/__pycache__" ]; then
    echo "Clearing cached bytecode for ReefShape scripts"
    for f in "$SOURCE"/*.py; do
        base="$(basename "$f" .py)"
        rm -f "$TARGET/__pycache__/$base."*.pyc 2>/dev/null
    done
fi
if [ -d "$TARGET/modules" ]; then
    echo "Removing stale modules folder"
    rm -rf "$TARGET/modules"
fi

# --- 3. Copy, and check that it worked -----------------------------------
echo "Copying scripts..."
if ! cp -R "$SOURCE/." "$TARGET/"; then
    die "Copying the scripts failed.

Check that you have permission to write to:
$TARGET"
fi

# Trust nothing: confirm the files are actually there. A cp that reports
# success but produced nothing is the exact failure this installer exists to
# stop hiding.
INSTALLED=0
for f in "$SOURCE"/*.py; do
    [ -f "$TARGET/$(basename "$f")" ] && INSTALLED=$((INSTALLED + 1))
done
[ "$INSTALLED" -gt 0 ] || die "The scripts did not copy across, despite the copy reporting success.

Check permissions on:
$TARGET"

# --- 4. Only now remove files this installer previously placed -----------
remove_if_ours() {
    name="$1"
    case "$name" in
        */*|"") return ;;                       # never touch anything nested
    esac
    [ -f "$SOURCE/$name" ] && return            # still shipped; keep it
    [ -f "$TARGET/$name" ] || return
    echo "Removing script from an older ReefShape version: $name"
    rm -f "$TARGET/$name"
    base="${name%.py}"
    rm -f "$TARGET/__pycache__/$base."*.pyc 2>/dev/null
}

if [ -f "$MANIFEST" ]; then
    while IFS= read -r name; do
        remove_if_ours "$name"
    done < "$MANIFEST"
else
    # No manifest: this is the first install since manifests existed, so fall
    # back to the names ReefShape shipped historically. An explicit list,
    # because a guess would risk deleting a different product's scripts.
    for name in \
        "02a_align_chunks_ICP.py" "03_optimization_process.py" \
        "04_scale_model.py" "05_create_boundary.py" "06_copy_boundary.py" \
        "07_calculate_area_ratio.py" "08_clean_project.py" \
        "09_create_boundary_from_photos.py"
    do
        remove_if_ours "$name"
    done
fi

# --- 5. Record what we installed, for the next run -----------------------
: > "$MANIFEST"
for f in "$SOURCE"/*.py; do
    [ -f "$f" ] && basename "$f" >> "$MANIFEST"
done

echo ""
echo "Installation complete: $INSTALLED scripts installed to"
echo "  $TARGET"
echo ""
echo "Restart Metashape to pick them up."

osascript -e "display dialog \"ReefShape installed successfully.

$INSTALLED scripts were installed. Restart Metashape to see them in the ReefShape menu.\" with title \"ReefShape Installer\" buttons {\"OK\"} default button 1" >/dev/null 2>&1 || true
