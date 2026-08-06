"""
Guarded entry point: `python -m batch`.

The launchers start the GUI with `pythonw` (Windows) or a detached process
(macOS), neither of which shows a console. Anything that goes wrong before Qt
is up -- a missing PySide, a Metashape install too old to have the DLLs we
need, a half-copied install directory -- would otherwise be a window that
never appears and no explanation anywhere.

So this module imports nothing at module scope, wraps the real startup, and on
failure reports through whatever channel still works: a log file always, and a
native OS message box that needs no Qt.
"""

from __future__ import annotations

import sys


def _log_path():
    """Where to write a startup failure.

    Mirrors `metashape_locate.config_dir()` by hand rather than importing it,
    because the import that failed may well have been that module.
    """
    import os
    from pathlib import Path

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA",
                                   Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    target = base / "ReefShape"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path.home() / "reefshape_batch_startup_error.log"
    return target / "startup_error.log"


def _native_message_box(title, text):
    """Show an error without Qt, since Qt is the usual suspect.

    Windows gets a real MessageBox via user32; macOS gets an AppleScript
    dialog via osascript. Anywhere else -- and anywhere those fail -- falls
    through to stderr, which at least helps someone running from a terminal.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            # 0x10 = MB_ICONERROR
            ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x10)
            return
        if sys.platform == "darwin":
            import subprocess
            script = ('display dialog {} with title {} buttons {{"OK"}} '
                      'default button "OK" with icon stop').format(
                          _as_applescript_string(text),
                          _as_applescript_string(title))
            subprocess.run(["osascript", "-e", script], check=False)
            return
    except Exception:
        pass
    sys.stderr.write("{}\n{}\n".format(title, text))


def _as_applescript_string(value):
    """Quote a Python string for embedding in an AppleScript literal."""
    return '"{}"'.format(str(value).replace("\\", "\\\\").replace('"', '\\"'))


def _report_startup_failure(exc_text):
    log = _log_path()
    try:
        import datetime
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("\n=== {} ===\n".format(datetime.datetime.now().isoformat()))
            fh.write("python: {}\n".format(sys.executable))
            fh.write("version: {}\n".format(sys.version))
            fh.write(exc_text)
    except OSError:
        pass

    # Lead with the most common cause and its fix. The traceback goes in the
    # log; a wall of it in a message box helps nobody.
    _native_message_box(
        "ReefShape Batch failed to start",
        "ReefShape Batch could not start.\n\n"
        "This usually means Agisoft Metashape Professional is not installed, "
        "or is installed somewhere ReefShape Batch could not find.\n\n"
        "Details were written to:\n{}\n\n{}".format(
            log, exc_text.strip().splitlines()[-1] if exc_text.strip() else "")
    )


def main():
    try:
        from .app import main as app_main
    except Exception:
        import traceback
        _report_startup_failure(traceback.format_exc())
        return 1

    try:
        return app_main()
    except Exception:
        import traceback
        _report_startup_failure(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
