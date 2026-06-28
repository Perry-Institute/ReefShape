#!/bin/bash
# ReefShape installer for macOS — mirrors Install ReefShape (PC).bat.
# Wipes stale ReefShape state in the target dir before copying so that
# renumbered scripts, removed helpers, and old bytecode don't linger.

set -u

DIR="$(cd "$(dirname "$0")"; pwd)"
SOURCE="$DIR/ReefShape_Scripts"
TARGET="$HOME/Library/Application Support/Agisoft/Metashape Pro/scripts"

echo "Installing ReefShape scripts for macOS..."
echo "Source folder: $SOURCE"
echo "Target folder: $TARGET"

mkdir -p "$TARGET"

# Wipe stale state before copying. The list below includes BOTH current
# filenames AND every filename used in any prior released version, because
# scripts have been renumbered between releases and `cp -R` doesn't remove
# files that no longer exist in the source.
#   Current numbering (v1.3+): 01_full_reefshape_workflow, 02_align_chunks,
#     03_align_chunks_ICP, 04_optimization_process, 05_scale_model,
#     06_create_boundary, 07_copy_boundary, 08_create_boundary_from_photos,
#     09_calculate_area_ratio, 10_clean_project, 11_gridded_rugosity
#   Historical: 02a_align_chunks_ICP, 03_optimization_process, 04_scale_model,
#     05_create_boundary, 06_copy_boundary, 07_calculate_area_ratio,
#     08_clean_project, 09_create_boundary_from_photos
#   Helper: ui_components

if [ -d "$TARGET/__pycache__" ]; then
    echo "Removing stale bytecode cache: $TARGET/__pycache__"
    rm -rf "$TARGET/__pycache__"
fi
if [ -d "$TARGET/modules" ]; then
    echo "Removing stale modules folder: $TARGET/modules"
    rm -rf "$TARGET/modules"
fi

REEFSHAPE_FILES=(
    "01_full_reefshape_workflow.py"
    "02_align_chunks.py"
    "02a_align_chunks_ICP.py"
    "03_align_chunks_ICP.py"
    "03_optimization_process.py"
    "04_optimization_process.py"
    "04_scale_model.py"
    "05_scale_model.py"
    "05_create_boundary.py"
    "06_create_boundary.py"
    "06_copy_boundary.py"
    "07_copy_boundary.py"
    "07_calculate_area_ratio.py"
    "09_calculate_area_ratio.py"
    "08_clean_project.py"
    "10_clean_project.py"
    "09_create_boundary_from_photos.py"
    "08_create_boundary_from_photos.py"
    "11_gridded_rugosity.py"
    "ui_components.py"
)
for f in "${REEFSHAPE_FILES[@]}"; do
    if [ -f "$TARGET/$f" ]; then
        rm -f "$TARGET/$f"
    fi
done

# Copy the full payload (current scripts + modules/)
cp -R "$SOURCE/"* "$TARGET/"

echo
echo "Installation complete!"
echo "ReefShape scripts have been installed to:"
echo "  $TARGET"

osascript -e 'display dialog "Metashape scripts installed successfully!" buttons {"OK"} default button 1'
