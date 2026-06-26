"""
Minimal pip auto-installer for ReefShape Metashape scripts.

Some ReefShape scripts (notably the ICP timepoint alignment) depend on
third-party Python packages that aren't bundled with Metashape — open3d,
scipy, numpy, matplotlib, etc. This module provides `pip_install()` so those
scripts can declare a requirements.txt-style string and have the packages
installed automatically into a user-writable directory on first run, without
the user having to drop to a shell.

API kept compatible with Agisoft's reference `pip_auto_install` so scripts
adapted from agisoft-llc/metashape-scripts work unchanged:
  - pip_install(requirements_txt)
  - user_packages_location()
  - _is_already_installed(name)
"""

import importlib
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path


def _packages_dir_name() -> str:
    """Match Metashape's native packages-dir naming: `user-packages-py{X}{Y}`.

    Metashape Pro auto-adds a directory at `<metashape user dir>/user-packages-
    py{major}{minor}/` to sys.path on startup. Writing into that directory
    means our installs co-exist with anything Metashape's own scripts install,
    and survive Metashape upgrades naturally. Falls back to a generic
    `python-packages` if sys.version_info is somehow unavailable.
    """
    try:
        return "user-packages-py{}{}".format(sys.version_info.major,
                                             sys.version_info.minor)
    except AttributeError:
        return "python-packages"


# Some PyPI distribution names differ from the importable module name.
# Only entries that actually appear in ReefShape's requirements need to be
# listed; unknown names fall through to a `-` → `_` rewrite which covers
# the common case.
_IMPORT_NAME_ALIASES = {
    "opencv-python": "cv2",
    "Pillow": "PIL",
    "pillow": "PIL",
    "PyYAML": "yaml",
    "scikit-image": "skimage",
    "scikit-learn": "sklearn",
    "pywin32": "win32api",
}


def user_packages_location() -> str:
    """Return (and ensure-on-sys.path) the directory we install packages into.

    Lives alongside Metashape's per-user data so installs survive Metashape
    upgrades, are per-user (no admin rights needed), and don't pollute the
    bundled site-packages. Path is derived from platform conventions rather
    than Metashape's API so we don't depend on Settings internals.
    """
    pkg_dir = _packages_dir_name()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        target = base / "Agisoft" / "Metashape Pro" / pkg_dir
    elif sys.platform == "darwin":
        target = (Path.home() / "Library" / "Application Support"
                  / "Agisoft" / "Metashape Pro" / pkg_dir)
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        target = Path(xdg) / "Agisoft" / "Metashape Pro" / pkg_dir
    target.mkdir(parents=True, exist_ok=True)
    target_str = str(target)
    if target_str not in sys.path:
        sys.path.insert(0, target_str)
    return target_str


def _import_name(dist_name: str) -> str:
    if dist_name in _IMPORT_NAME_ALIASES:
        return _IMPORT_NAME_ALIASES[dist_name]
    return dist_name.replace("-", "_")


def _is_already_installed(dist_name: str) -> bool:
    """True if `dist_name` (PyPI name) is importable in the current process.

    Kept for API compatibility with Agisoft scripts that import this helper.
    Note: importable-by-name is the wrong question for our skip-pip-if-installed
    check because distribution names (Werkzeug, MarkupSafe, PyYAML, …) don't
    always match import names. `pip_install` uses `_distributions_installed_in`
    against the target dir instead, which reads dist-info metadata directly.
    """
    return importlib.util.find_spec(_import_name(dist_name)) is not None


def _normalize_dist(name: str) -> str:
    """PEP 503 distribution-name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _distributions_installed_in(target: str) -> set:
    """Set of PEP 503-normalized distribution names already in `target`.

    Reads .dist-info directories directly via importlib.metadata so the answer
    is independent of sys.path, the path-importer cache, and case sensitivity.
    """
    try:
        import importlib.metadata as md
    except ImportError:
        return set()
    try:
        installed = set()
        for dist in md.distributions(path=[target]):
            name = dist.metadata["Name"] if dist.metadata else None
            if name:
                installed.add(_normalize_dist(name))
        return installed
    except Exception:
        return set()


def _marker_applies(marker_str: str) -> bool:
    """Best-effort PEP 508 environment-marker evaluation.

    Returns True if the marker applies on the current interpreter (i.e. pip
    would install the requirement here). For unrecognized markers we return
    True so the requirement is treated as in-scope and pip remains the
    fallback authority — better to do an unnecessary pip check than to skip
    a requirement we should have tracked.

    Without this, `pywin32==306; sys_platform == 'win32'` causes the parser
    to yield `pywin32` on every platform; on macOS/Linux pip correctly skips
    installing it, so it never appears in the install dir, so the missing-set
    is non-empty every startup and pip is re-invoked forever.
    """
    marker_str = marker_str.strip()
    if not marker_str:
        return True
    # Preferred path: packaging.markers is part of pip's vendored stack and
    # usually importable from Metashape's Python.
    try:
        from packaging.markers import Marker  # type: ignore
        return bool(Marker(marker_str).evaluate())
    except Exception:
        pass
    # Fallback for the only marker ReefShape actually uses today.
    m = re.match(r"sys_platform\s*(==|!=)\s*['\"]([^'\"]+)['\"]\s*$", marker_str)
    if m:
        op, value = m.group(1), m.group(2)
        return (sys.platform == value) if op == "==" else (sys.platform != value)
    return True


def _parse_requirement_names(requirements_txt: str):
    """Yield the distribution name for each requirement line that applies
    on this interpreter.

    We only need names accurate enough to skip pip when nothing is missing —
    full PEP 508 parsing isn't worth pulling in `packaging` for, but we do
    need to honor the environment marker so platform-gated requirements
    don't trigger a re-install every startup on the platforms that skip them.
    """
    for raw_line in requirements_txt.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ";" in line:
            head, marker = line.split(";", 1)
            if not _marker_applies(marker):
                continue
            line = head.strip()
        if not line:
            continue
        # Distribution name is the leading run of name-chars (letters, digits,
        # underscore, dot, hyphen).
        match = re.match(r"[A-Za-z0-9_.\-]+", line)
        if match:
            yield match.group(0)


def _try_make_progress_dialog():
    """Try to construct a Qt progress dialog for the pip install.

    Returns (dialog, QtWidgets) on success, (None, None) if Qt isn't available
    (no QApplication, no PySide bindings, etc.). We deliberately use a plain
    QDialog with a QLabel + indeterminate QProgressBar rather than the stock
    QProgressDialog: QProgressDialog calls adjustSize() internally on every
    setLabelText(), which overrides setFixedSize and lets long pip lines
    stretch the dialog across the screen. The custom dialog's size is fully
    under our control. The status label is stashed at `dlg._status_label` so
    `_stream_pip` can update it.
    """
    try:
        try:
            from PySide6 import QtWidgets, QtCore
        except ImportError:
            from PySide2 import QtWidgets, QtCore
    except ImportError:
        return None, None

    app = QtWidgets.QApplication.instance()
    if app is None:
        # No event loop running (e.g. command-line Metashape). Skip the dialog.
        return None, None

    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("ReefShape: Installing Dependencies")
    dlg.setWindowModality(QtCore.Qt.WindowModal)
    dlg.setFixedSize(540, 220)
    # Hide the close button — there's no clean way to recover from a half-done
    # pip install, so we don't let the user dismiss the dialog mid-flight.
    flags = dlg.windowFlags() & ~QtCore.Qt.WindowCloseButtonHint
    # Also strip the system menu (right-click on title bar) on platforms that
    # use it for the close action.
    flags &= ~QtCore.Qt.WindowSystemMenuHint
    dlg.setWindowFlags(flags)

    layout = QtWidgets.QVBoxLayout(dlg)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    heading = QtWidgets.QLabel(
        "ReefShape first-run setup\n\n"
        "Installing Python dependencies for the ICP alignment script. "
        "This is a one-time install and may take a few minutes."
    )
    heading.setWordWrap(True)
    layout.addWidget(heading)

    status = QtWidgets.QLabel("Starting pip…")
    status.setWordWrap(True)
    # Give the status line a tight max width so even an un-wrappable URL can't
    # force the label preferred-width past the dialog's interior.
    status.setMaximumWidth(500)
    status.setStyleSheet("color: palette(mid);")
    layout.addWidget(status)

    layout.addStretch(1)

    bar = QtWidgets.QProgressBar()
    bar.setRange(0, 0)  # 0,0 = indeterminate (pulses)
    bar.setTextVisible(False)
    layout.addWidget(bar)

    dlg._status_label = status  # used by _stream_pip
    dlg.show()
    QtWidgets.QApplication.processEvents()
    return dlg, QtWidgets


def _stream_pip(cmd, dlg, QtWidgets):
    """Run `cmd` and stream stdout to the progress dialog's status label.

    Returns pip's exit code. We only surface high-signal lines ("Collecting…",
    "Downloading…", "Installing…", "Successfully installed…") because pip's
    chattier lines (DEPRECATION warnings, hash checks, the in-place progress
    bars) would just flicker past too fast to read. Lines are also stripped of
    the long " (from -r <abs path to requirements>...)" tail pip adds, which
    is just noise in the dialog.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
    )

    interesting_prefixes = (
        "Collecting ", "Downloading ", "Installing ",
        "Building ", "Preparing ", "Successfully ",
    )
    last_label = None
    status = getattr(dlg, "_status_label", None) if dlg is not None else None

    assert proc.stdout is not None
    for raw_line in iter(proc.stdout.readline, ""):
        line = raw_line.rstrip()
        if not line:
            continue
        print(line)  # also keep the full transcript in the Metashape console
        if status is not None and line.startswith(interesting_prefixes):
            # Drop pip's verbose "(from -r <requirements file>)" annotation —
            # it's the same on every line and just takes up room.
            display = line.split(" (from ", 1)[0]
            # Hard cap so even unwrappable text can't grow the dialog.
            if len(display) > 70:
                display = display[:69] + "…"
            if display != last_label:
                status.setText(display)
                last_label = display
        if dlg is not None:
            QtWidgets.QApplication.processEvents()

    return proc.wait()


def pip_install(requirements_txt: str) -> None:
    """Ensure every package in `requirements_txt` is importable.

    Fast path: if every top-level name is already importable, this is a no-op
    (no subprocess, no network). Otherwise pip is invoked with the full
    requirements file (not just the missing ones) so transitive pins take
    effect. While pip runs, a Qt progress dialog streams pip's output so the
    user doesn't see Metashape "freeze" silently for several minutes — falls
    back to console-only output if no QApplication is running.
    """
    target = user_packages_location()

    # Check what's already installed by reading dist-info in the target dir.
    # This is more reliable than find_spec(import_name) because import names
    # don't always match distribution names (Werkzeug → werkzeug, PyYAML →
    # yaml, MarkupSafe → markupsafe, …) and the previous version was re-
    # installing every startup as a result.
    installed = _distributions_installed_in(target)
    requested = list(_parse_requirement_names(requirements_txt))
    missing = [name for name in requested
               if _normalize_dist(name) not in installed]
    if not missing:
        return

    print("pip_auto_install: installing missing dependencies (first run only)")
    print("  target dir: {}".format(target))
    print("  missing:    {}".format(", ".join(missing)))

    req_file = Path(target).parent / "_reefshape_requirements.txt"
    req_file.write_text(requirements_txt, encoding="utf-8")
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", target,
        "--upgrade",
        "--disable-pip-version-check",
        "-r", str(req_file),
    ]

    dlg, QtWidgets = _try_make_progress_dialog()
    try:
        rc = _stream_pip(cmd, dlg, QtWidgets)
        if rc != 0:
            raise RuntimeError(
                "pip_auto_install: dependency install failed (pip exit {}). "
                "Install manually with:\n  \"{}\" -m pip install "
                "--target=\"{}\" -r \"{}\"".format(
                    rc, sys.executable, target, req_file
                )
            )
    finally:
        if dlg is not None:
            dlg.close()
        try:
            req_file.unlink()
        except OSError:
            pass

    # Make freshly-installed packages discoverable in this process.
    if target not in sys.path:
        sys.path.insert(0, target)
    importlib.invalidate_caches()
