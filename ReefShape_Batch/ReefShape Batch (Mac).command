#!/bin/bash
# ---------------------------------------------------------------------
# ReefShape Batch launcher (macOS)
#
# Picks a Python interpreter that can actually draw a window, and runs the
# batch GUI on it.
#
# "Can actually draw a window" is the operative part. An earlier version
# guessed at where Metashape keeps its bundled Python from a short list of
# paths; when the guess missed it silently fell through to whatever `python3`
# was on PATH, which on one machine had no Qt binding at all and died with a
# bare ModuleNotFoundError. So candidates are now discovered by searching the
# app bundle, and each one is *tested* -- if it cannot import PySide, it is not
# a candidate, whatever its path suggests.
#
# Unlike Windows -- where batch/qt.py can fix the library search path from
# inside the process with os.add_dll_directory -- the macOS dynamic loader
# reads DYLD_FRAMEWORK_PATH only at process start, so it is exported here
# before any interpreter is tested or launched.
#
# Set REEFSHAPE_METASHAPE to an app bundle to override discovery, e.g.
#   export REEFSHAPE_METASHAPE="/Applications/MetashapePro 2.2.app"
# Set REEFSHAPE_PYTHON to force a specific interpreter.
# ---------------------------------------------------------------------

set -u

APPDIR="$(cd "$(dirname "$0")" && pwd)"

die() {
    echo "ERROR: $1" >&2
    /usr/bin/osascript -e "display dialog \"$1\" with title \"ReefShape Batch\" buttons {\"OK\"} default button \"OK\" with icon stop" >/dev/null 2>&1 || true
    exit 1
}

# True if $1 is an interpreter with a usable Qt binding. Tests QtWidgets
# rather than the top-level package: the package imports fine on an install
# whose Qt shared libraries cannot be loaded, so checking only the top level
# would accept an interpreter that fails later, at the point where there is no
# Qt available to report it with.
has_qt() {
    "$1" -c 'import PySide6.QtWidgets' >/dev/null 2>&1 && return 0
    "$1" -c 'import PySide2.QtWidgets' >/dev/null 2>&1 && return 0
    return 1
}

qt_binding() {
    "$1" -c 'import PySide6; print("PySide6")' 2>/dev/null && return 0
    "$1" -c 'import PySide2; print("PySide2")' 2>/dev/null && return 0
    echo "none"
}

# --- Locate the Metashape app bundle -----------------------------------
MSAPP=""

if [ -n "${REEFSHAPE_METASHAPE:-}" ] && [ -d "$REEFSHAPE_METASHAPE" ]; then
    MSAPP="$REEFSHAPE_METASHAPE"
fi

if [ -z "$MSAPP" ]; then
    # Reverse sort so a newer versioned bundle wins over an older one kept
    # alongside it. Agisoft has shipped the bundle as both "MetashapePro.app"
    # and "Metashape Pro.app", hence the loose glob.
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        if [ -x "$candidate/Contents/MacOS/MetashapePro" ] \
           || [ -x "$candidate/Contents/MacOS/Metashape" ]; then
            MSAPP="$candidate"
            break
        fi
    done < <(ls -d /Applications/*etashape*.app "$HOME"/Applications/*etashape*.app 2>/dev/null | sort -r)
fi

if [ -n "$MSAPP" ]; then
    echo "Metashape:  $MSAPP"
    export REEFSHAPE_METASHAPE="$MSAPP"

    # Must be exported before any interpreter starts; see the header.
    FRAMEWORKS="$MSAPP/Contents/Frameworks"
    if [ -d "$FRAMEWORKS" ]; then
        export DYLD_FRAMEWORK_PATH="${FRAMEWORKS}${DYLD_FRAMEWORK_PATH:+:$DYLD_FRAMEWORK_PATH}"
        export DYLD_LIBRARY_PATH="${FRAMEWORKS}${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
    fi
    if [ -d "$MSAPP/Contents/PlugIns" ]; then
        export QT_PLUGIN_PATH="$MSAPP/Contents/PlugIns"
        export QT_QPA_PLATFORM_PLUGIN_PATH="$MSAPP/Contents/PlugIns/platforms"
    fi
else
    echo "Metashape:  not found (the app can still start; it will ask you to locate it)"
fi

# --- Collect candidate interpreters ------------------------------------
# Order matters: the first one that passes the Qt test wins.
CANDIDATES=()

if [ -n "${REEFSHAPE_PYTHON:-}" ]; then
    CANDIDATES+=("$REEFSHAPE_PYTHON")
fi

if [ -n "$MSAPP" ]; then
    # Known layouts first, so the common case costs nothing.
    for pattern in \
        "$MSAPP"/Contents/Frameworks/python/bin/python3* \
        "$MSAPP"/Contents/MacOS/python/bin/python3* \
        "$MSAPP"/Contents/Resources/python/bin/python3* \
        "$MSAPP"/Contents/Frameworks/Python.framework/Versions/*/bin/python3* \
        "$MSAPP"/Contents/Resources/Python.framework/Versions/*/bin/python3*
    do
        [ -x "$pattern" ] && CANDIDATES+=("$pattern")
    done

    # Nothing in the known layouts: search the bundle. Slower, but it is the
    # difference between working on a layout we have never seen and failing on
    # one. Only reached when the fast paths all miss.
    if [ ${#CANDIDATES[@]} -eq 0 ]; then
        echo "Searching the Metashape bundle for a bundled Python..."
        while IFS= read -r found; do
            [ -x "$found" ] && CANDIDATES+=("$found")
        done < <(find "$MSAPP" -type f -name 'python3*' 2>/dev/null | sort -r)
    fi
fi

# System interpreters last: Metashape's own is preferred because it is the
# one guaranteed to match the Metashape being driven.
for sys_py in "$(command -v python3 || true)" \
              /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3
do
    [ -n "$sys_py" ] && [ -x "$sys_py" ] && CANDIDATES+=("$sys_py")
done

# --- Pick the first candidate that can actually import Qt ---------------
PYEXE=""
TRIED=""
for candidate in "${CANDIDATES[@]:-}"; do
    [ -n "$candidate" ] || continue
    case " $TRIED " in *" $candidate "*) continue ;; esac
    TRIED="$TRIED $candidate"
    if has_qt "$candidate"; then
        PYEXE="$candidate"
        break
    fi
done

if [ -z "$PYEXE" ]; then
    DETAIL=""
    for candidate in $TRIED; do
        DETAIL="$DETAIL
  $candidate"
    done
    [ -z "$DETAIL" ] && DETAIL="
  (no Python interpreters found at all)"
    die "ReefShape Batch needs a Python with the PySide6 (or PySide2) Qt bindings, and none of the interpreters it found has them.

Tried:$DETAIL

The simplest fix is to install PySide6 into your system Python:
    pip3 install PySide6

Then run this launcher again. To use a specific interpreter instead, set REEFSHAPE_PYTHON to its full path."
fi

echo "Python:     $PYEXE"
echo "Qt binding: $(qt_binding "$PYEXE")"

# Run from the app directory so `batch` is importable as a package.
cd "$APPDIR" || die "Could not enter $APPDIR"

exec "$PYEXE" -m batch "$@"
