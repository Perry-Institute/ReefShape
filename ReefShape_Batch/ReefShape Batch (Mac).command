#!/bin/bash
# ---------------------------------------------------------------------
# ReefShape Batch launcher (macOS)
#
# Runs the batch GUI on the Python interpreter bundled inside MetashapePro.app
# so the user needs no Python installation of their own.
#
# Unlike Windows -- where batch/qt.py can fix the library search path from
# inside the process with os.add_dll_directory -- the macOS dynamic loader
# reads DYLD_FRAMEWORK_PATH only at process start. So the framework path has
# to be exported *here*, before Python launches, or PySide2 will fail to find
# Metashape's Qt frameworks.
#
# Set REEFSHAPE_METASHAPE to an app bundle to override discovery, e.g.
#   export REEFSHAPE_METASHAPE="/Applications/MetashapePro 2.2.app"
#
# NOTE: the exact bundled-Python and framework layout inside MetashapePro.app
# has not yet been verified on a real Mac; the globs below are deliberately
# permissive. See the "macOS support and end-to-end verification" task.
# ---------------------------------------------------------------------

set -u

APPDIR="$(cd "$(dirname "$0")" && pwd)"

die() {
    # A .command file opens Terminal, so echo is visible -- but also raise a
    # dialog in case it was launched some other way.
    echo "ERROR: $1" >&2
    /usr/bin/osascript -e "display dialog \"$1\" with title \"ReefShape Batch\" buttons {\"OK\"} default button \"OK\" with icon stop" >/dev/null 2>&1 || true
    exit 1
}

# --- Locate the Metashape app bundle -----------------------------------
MSAPP=""

if [ -n "${REEFSHAPE_METASHAPE:-}" ] && [ -d "$REEFSHAPE_METASHAPE" ]; then
    MSAPP="$REEFSHAPE_METASHAPE"
fi

if [ -z "$MSAPP" ]; then
    # Reverse sort so a newer versioned bundle ("MetashapePro 2.3.app") wins
    # over an older one kept alongside it. Agisoft has shipped the bundle as
    # both "MetashapePro.app" and "Metashape Pro.app", hence the loose glob.
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        if [ -x "$candidate/Contents/MacOS/MetashapePro" ]; then
            MSAPP="$candidate"
            break
        fi
    done < <(ls -d /Applications/*etashape*.app "$HOME"/Applications/*etashape*.app 2>/dev/null | sort -r)
fi

# --- Pick an interpreter -----------------------------------------------
# Prefer Metashape's bundled Python. Falling back to a system python3 still
# gets the user to the app's own "Metashape not found" dialog, where they can
# point at the install by hand -- better than dying here.
PYEXE=""
if [ -n "$MSAPP" ]; then
    export REEFSHAPE_METASHAPE="$MSAPP"

    for base in "$MSAPP/Contents/Frameworks/python" \
                "$MSAPP/Contents/MacOS/python" \
                "$MSAPP/Contents/Resources/python"; do
        [ -d "$base" ] || continue
        for candidate in "$base"/bin/python3.*; do
            if [ -x "$candidate" ]; then PYEXE="$candidate"; break; fi
        done
        [ -z "$PYEXE" ] && [ -x "$base/bin/python3" ] && PYEXE="$base/bin/python3"
        [ -n "$PYEXE" ] && break
    done

    # Must be exported before Python starts; see the header comment.
    FRAMEWORKS="$MSAPP/Contents/Frameworks"
    if [ -d "$FRAMEWORKS" ]; then
        export DYLD_FRAMEWORK_PATH="${FRAMEWORKS}${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
        export DYLD_LIBRARY_PATH="${FRAMEWORKS}${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
    fi
    if [ -d "$MSAPP/Contents/PlugIns" ]; then
        export QT_PLUGIN_PATH="$MSAPP/Contents/PlugIns"
        export QT_QPA_PLATFORM_PLUGIN_PATH="$MSAPP/Contents/PlugIns/platforms"
    fi
fi

if [ -z "$PYEXE" ]; then
    PYEXE="$(command -v python3 || true)"
fi

if [ -z "$PYEXE" ]; then
    die "Could not find a Python interpreter to run ReefShape Batch. Agisoft Metashape Professional does not appear to be installed in a standard location. Set REEFSHAPE_METASHAPE to its .app bundle and try again."
fi

# Run from the app directory so `batch` is importable as a package.
cd "$APPDIR" || die "Could not enter $APPDIR"

exec "$PYEXE" -m batch "$@"
