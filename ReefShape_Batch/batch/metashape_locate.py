"""
Locate the installed Agisoft Metashape Pro and make its bundled Qt importable.

ReefShape Batch runs *outside* Metashape: the GUI process never imports the
`Metashape` module (it isn't importable from the bundled interpreter anyway --
verified: `ModuleNotFoundError` when you try). Instead the GUI drives Metashape
by spawning short-lived headless workers:

    metashape.exe -r worker/run_job.py job.json

So this module answers two questions, and deliberately answers them with
nothing but the standard library:

  1. Where is Metashape?  -> `find_install()` / `Install`
  2. How do we get a Qt event loop?  -> `bootstrap_qt()`

The stdlib-only constraint is not stylistic. `batch/qt.py` imports PySide
*after* calling `bootstrap_qt()`, because on Windows the PySide2 that ships
inside Metashape cannot be imported at all until Metashape's root directory is
on the DLL search path (Qt5Widgets.dll and friends live there, not next to the
PySide2 package). If this module imported Qt to read a saved setting via
QSettings, that import would fail before it could fix itself. Hence the manual
override is persisted as plain JSON instead.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path
from typing import List, Optional


# Set this to an install root or an executable path to bypass discovery
# entirely. Useful for testing against a second Metashape version.
ENV_OVERRIDE = "REEFSHAPE_METASHAPE"


class MetashapeNotFound(Exception):
    """Raised when no usable Metashape installation could be located."""


# --------------------------------------------------------------------------
# Config file (stdlib-only; see module docstring for why this isn't QSettings)
# --------------------------------------------------------------------------

def config_dir() -> Path:
    """Per-user directory for ReefShape Batch's own state.

    Deliberately *not* under Metashape's data directory: this config has to be
    readable before we know where Metashape is, and it should survive a
    Metashape uninstall/reinstall cycle so a manual override isn't lost.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA",
                                   Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME",
                                   Path.home() / ".config"))
    target = base / "ReefShape"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _config_path() -> Path:
    return config_dir() / "batch_config.json"


def load_config() -> dict:
    """Read the config file, returning {} for missing/corrupt files.

    A corrupt config must never be fatal -- the app can always fall back to
    discovery, and a hard failure here would leave the user with an app that
    won't start and no obvious way to fix it.
    """
    try:
        with open(_config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(data: dict) -> None:
    """Write the config file, ignoring write failures.

    Losing a saved preference is an annoyance; crashing the app over a
    read-only home directory is not acceptable.
    """
    try:
        with open(_config_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


def save_metashape_path(path: str) -> None:
    """Persist a user-chosen Metashape location (from a Browse... dialog)."""
    cfg = load_config()
    cfg["metashape_path"] = str(path)
    save_config(cfg)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def _exe_name() -> str:
    if sys.platform == "win32":
        return "metashape.exe"
    if sys.platform == "darwin":
        return "MetashapePro"
    return "metashape.sh"


def _root_from_any(path: Path) -> Optional[Path]:
    """Normalize a user-supplied path to an install root.

    Accepts whatever a Browse dialog is likely to hand back: the install
    directory itself, the executable inside it, or -- on macOS, where the
    file picker shows a bundle as a single item -- the `.app` bundle.
    """
    if not path.exists():
        return None
    if path.is_file():
        # .../MetashapePro.app/Contents/MacOS/MetashapePro -> the .app
        if sys.platform == "darwin" and path.parent.name == "MacOS":
            return path.parent.parent.parent
        return path.parent
    if sys.platform == "darwin" and path.suffix == ".app":
        return path
    return path


def _candidate_roots() -> List[Path]:
    """Plausible install roots, best guess first.

    Ordering matters: the first candidate that passes `Install._validate` wins,
    so explicit user intent (env var, saved config) has to precede anything
    discovered automatically.
    """
    out: List[Path] = []

    def add(p) -> None:
        if not p:
            return
        root = _root_from_any(Path(p))
        if root and root not in out:
            out.append(root)

    add(os.environ.get(ENV_OVERRIDE))
    add(load_config().get("metashape_path"))

    if sys.platform == "win32":
        # The registry is the authoritative answer on Windows: it tracks
        # non-default install locations that path guessing would miss.
        add(_registry_install_dir())
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                add(Path(base) / "Agisoft" / "Metashape Pro")
    elif sys.platform == "darwin":
        # Glob rather than hardcode: Agisoft has shipped the bundle as both
        # "MetashapePro.app" and "Metashape Pro.app" across versions, and
        # users routinely keep a versioned copy alongside the current one.
        for base in ("/Applications", str(Path.home() / "Applications")):
            for match in sorted(glob.glob(os.path.join(base, "*etashape*.app")),
                                reverse=True):
                add(match)
    else:
        for base in ("/opt/metashape-pro", "/usr/local/metashape-pro",
                     str(Path.home() / "metashape-pro")):
            add(base)

    return out


def _registry_install_dir() -> Optional[str]:
    """Read InstallDir from HKLM\\SOFTWARE\\Agisoft\\Metashape Pro\\Install.

    Verified present on this machine pointing at
    "C:\\Program Files\\Agisoft\\Metashape Pro\\". Checks HKCU too, since a
    per-user install writes there instead.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Agisoft\Metashape Pro\Install") as key:
                value, _ = winreg.QueryValueEx(key, "InstallDir")
                if value:
                    return value
        except OSError:
            continue
    return None


class Install:
    """A validated Metashape installation.

    Attributes:
        root:       install root (on macOS, the .app bundle)
        executable: the binary we hand to the process pool
        python:     bundled interpreter, or None if it couldn't be found
        plugins:    Qt plugin directory, or None
        libraries:  directory holding the Qt shared libraries
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.executable = self._find_executable()
        self.libraries = self._find_libraries()
        self.python = self._find_python()
        self.plugins = self._find_plugins()

    # -- layout resolution --

    def _find_executable(self) -> Optional[Path]:
        name = _exe_name()
        candidates = [self.root / name]
        if sys.platform == "darwin":
            candidates.insert(0, self.root / "Contents" / "MacOS" / name)
        for c in candidates:
            if c.is_file():
                return c
        return None

    def _find_libraries(self) -> Path:
        """Directory containing Qt5Core/Qt5Widgets/etc.

        Windows and Linux keep these beside the executable; macOS puts them in
        the bundle's Frameworks directory.
        """
        if sys.platform == "darwin":
            fw = self.root / "Contents" / "Frameworks"
            if fw.is_dir():
                return fw
        return self.root

    def _find_python(self) -> Optional[Path]:
        """The bundled CPython that ships with Metashape.

        This is what the GUI runs on, so the user needs no Python install of
        their own. Verified on Windows: <root>/python/python.exe is CPython
        3.12 with PySide2 5.15.18 in site-packages.

        The macOS layout is globbed rather than hardcoded because the exact
        location has moved between Metashape versions and is unverified here;
        see the "macOS support" task.
        """
        if sys.platform == "win32":
            exe = self.root / "python" / "python.exe"
            return exe if exe.is_file() else None

        if sys.platform == "darwin":
            # Several layouts, because Agisoft has used more than one and the
            # cost of guessing wrong is the app falling back to a system
            # interpreter that may have no Qt at all. Versioned names sort
            # first so we bind to a real interpreter rather than the bare
            # `python3` symlink.
            patterns = [
                self.root / "Contents" / "Frameworks" / "python" / "bin" / "python3*",
                self.root / "Contents" / "MacOS" / "python" / "bin" / "python3*",
                self.root / "Contents" / "Resources" / "python" / "bin" / "python3*",
                self.root / "Contents" / "Frameworks" / "Python.framework"
                / "Versions" / "*" / "bin" / "python3*",
                self.root / "Contents" / "Resources" / "Python.framework"
                / "Versions" / "*" / "bin" / "python3*",
            ]
        else:
            patterns = [self.root / "python" / "bin" / "python3*"]

        for pattern in patterns:
            for match in sorted(glob.glob(str(pattern)), reverse=True):
                if os.path.isfile(match) and os.access(match, os.X_OK):
                    return Path(match)

        # Last resort: walk the install looking for any `bin/python3*`. Only
        # reached when every known layout misses, which is precisely the case
        # where guessing has already failed and a slow answer beats none.
        return self._search_for_python()

    def _search_for_python(self) -> Optional[Path]:
        """Walk the installation for a bundled interpreter.

        Bounded by depth so an unexpected symlink cannot turn this into a walk
        of the whole filesystem, and it stops at the first hit.
        """
        root_depth = len(self.root.parts)
        for dirpath, dirnames, filenames in os.walk(str(self.root)):
            if len(Path(dirpath).parts) - root_depth > 6:
                dirnames[:] = []
                continue
            if os.path.basename(dirpath) != "bin":
                continue
            for name in sorted(filenames, reverse=True):
                if not name.startswith("python3"):
                    continue
                candidate = os.path.join(dirpath, name)
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return Path(candidate)
        return None

    def _find_plugins(self) -> Optional[Path]:
        for rel in (("plugins",), ("Contents", "PlugIns")):
            p = self.root.joinpath(*rel)
            if p.is_dir():
                return p
        return None

    # -- validation --

    def _validate(self) -> bool:
        """True if this looks like a real, usable installation.

        Only the executable is required. A missing bundled interpreter is
        survivable -- the GUI can run under any Python that has PySide
        installed -- but without the executable we cannot process anything at
        all, which is the whole point of the app.
        """
        return self.executable is not None

    def __repr__(self) -> str:
        return "<Install root={} exe={} python={}>".format(
            self.root, self.executable, self.python)


def find_install() -> Install:
    """Return the first usable Metashape installation.

    Raises MetashapeNotFound if none of the candidates pan out; callers should
    catch this and offer a "Browse for Metashape..." dialog, then persist the
    result with `save_metashape_path`.
    """
    tried = []
    for root in _candidate_roots():
        install = Install(root)
        if install._validate():
            return install
        tried.append(str(root))
    raise MetashapeNotFound(
        "Could not find Agisoft Metashape Pro.\n\nLooked in:\n  "
        + ("\n  ".join(tried) if tried else "(no candidate locations)")
        + "\n\nSet the {} environment variable to the install folder, or "
          "choose the location manually.".format(ENV_OVERRIDE)
    )


# --------------------------------------------------------------------------
# Qt bootstrap
# --------------------------------------------------------------------------

_bootstrapped = False


def bootstrap_qt(install: Optional[Install] = None) -> Optional[Install]:
    """Make Metashape's bundled Qt importable in *this* process.

    Must run before the first `import PySide2`. `batch/qt.py` calls it at
    module import time so that any module doing `from batch.qt import
    QtWidgets` gets a working Qt without having to think about ordering.

    Returns the Install used, or None if we are not running under Metashape's
    interpreter and don't need to do anything (a system Python with its own
    PySide wheel works as-is).

    Platform notes:

    Windows -- Qt5*.dll sit in the install root, not beside the PySide2
    package, so importing PySide2.QtWidgets fails with "DLL load failed" until
    the root is added to the DLL search path. `os.add_dll_directory` fixes this
    in-process, which is why the Windows path needs no launcher cooperation.

    macOS/Linux -- the dynamic loader reads DYLD_/LD_LIBRARY_PATH only at
    process start, so setting them here would be too late. The launcher scripts
    export them before exec'ing Python; all that's left for us is the Qt plugin
    path, which Qt reads at QApplication construction.
    """
    global _bootstrapped
    if _bootstrapped:
        return install

    # A Python that can already import Qt needs no help. Checking the spec
    # rather than importing avoids paying for a full Qt import (and avoids
    # importing the wrong Qt) just to answer the question.
    if _qt_already_importable():
        _bootstrapped = True
        return None

    if install is None:
        try:
            install = find_install()
        except MetashapeNotFound:
            # Let the caller's PySide import raise instead: an ImportError
            # naming the missing module is a clearer diagnostic than a
            # MetashapeNotFound raised from a module the user never called.
            return None

    if sys.platform == "win32":
        for d in {install.root, install.libraries}:
            if d.is_dir():
                try:
                    os.add_dll_directory(str(d))
                except OSError:
                    pass
        # Belt and braces: some loaders in the PySide2 stack shell out to
        # things that consult PATH rather than the DLL directory list.
        os.environ["PATH"] = str(install.root) + os.pathsep + os.environ.get("PATH", "")

    if install.plugins:
        os.environ.setdefault("QT_PLUGIN_PATH", str(install.plugins))
        # Qt looks for the platform plugin (windows/cocoa/xcb) under this
        # specific variable, and won't fall back to QT_PLUGIN_PATH for it.
        platforms = install.plugins / "platforms"
        if platforms.is_dir():
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(platforms))

    _bootstrapped = True
    return install


def _qt_already_importable() -> bool:
    """True if some PySide is importable without our intervention.

    Uses find_spec on the *submodule* that actually pulls in the Qt shared
    libraries. `import PySide2` alone succeeds even on a broken install --
    verified: the top-level package imports fine while PySide2.QtWidgets
    raises "DLL load failed" -- so checking the top-level package would give a
    false positive and skip the bootstrap we need.
    """
    import importlib.util
    for binding in ("PySide6", "PySide2"):
        try:
            if importlib.util.find_spec(binding) is None:
                continue
            spec = importlib.util.find_spec(binding + ".QtWidgets")
        except (ImportError, ValueError):
            continue
        if spec is not None:
            try:
                __import__(binding + ".QtWidgets")
                return True
            except ImportError:
                # Package present, shared libraries not loadable -- exactly
                # the pre-bootstrap Windows state. Keep going.
                continue
    return False
