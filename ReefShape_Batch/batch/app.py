"""
ReefShape Batch entry point.

Launched by the platform scripts in the parent directory:

    ReefShape Batch (PC).bat
    ReefShape Batch (Mac).command

Both resolve Metashape's bundled Python and run `python -m batch.app`, so the
user needs no Python installation of their own. Running under a system Python
with PySide installed also works -- see `batch.qt`.
"""

from __future__ import annotations

import os
import sys
import traceback

from .qt import QtWidgets, exec_, enable_high_dpi
from . import metashape_locate
from .ui.diagnostics import show_diagnostics, environment_rows


APP_NAME = "ReefShape Batch"


def ensure_install(parent=None):
    """Resolve the Metashape installation, prompting the user if need be.

    Returns an `Install`, or None if the user declined to locate one (in which
    case the caller should exit -- there is nothing the app can do without
    Metashape).

    The chosen path is persisted, so this is a once-per-machine annoyance for
    the minority with a non-standard install rather than a per-launch one.
    """
    while True:
        try:
            return metashape_locate.find_install()
        except metashape_locate.MetashapeNotFound as exc:
            box = QtWidgets.QMessageBox(parent)
            box.setIcon(QtWidgets.QMessageBox.Warning)
            box.setWindowTitle(APP_NAME + " -- Metashape not found")
            box.setText("ReefShape Batch could not find Agisoft Metashape Pro.")
            box.setInformativeText(
                "Metashape Professional must be installed and licensed on this "
                "computer.\n\nIf it is installed somewhere unusual, choose "
                "Locate to point at it directly."
            )
            box.setDetailedText(str(exc))
            locate = box.addButton("Locate...", QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Quit", QtWidgets.QMessageBox.RejectRole)
            exec_(box)
            if box.clickedButton() is not locate:
                return None

            path = _ask_for_metashape(parent)
            if not path:
                return None
            metashape_locate.save_metashape_path(path)


def _ask_for_metashape(parent=None):
    """File dialog tuned to each platform's idea of "the application".

    macOS shows an .app bundle as a single item, so we ask for the bundle and
    let `metashape_locate` dig out the binary; Windows and Linux ask for the
    executable itself.
    """
    if sys.platform == "darwin":
        path = QtWidgets.QFileDialog.getExistingDirectory(
            parent, "Select MetashapePro.app", "/Applications")
        return path or None

    if sys.platform == "win32":
        caption, filt, start = ("Select metashape.exe",
                                "Metashape (metashape.exe)",
                                "C:/Program Files/Agisoft")
    else:
        caption, filt, start = ("Select the metashape.sh launcher",
                                "Metashape (metashape.sh)", "/opt")
    path, _ = QtWidgets.QFileDialog.getOpenFileName(parent, caption, start, filt)
    return path or None


def _fatal(message, detail=""):
    """Report a startup failure through a dialog, falling back to stderr.

    Startup failures are the ones most likely to happen with no console
    attached -- the user double-clicked a launcher -- so a silent exit would
    leave them with nothing to report.
    """
    try:
        box = QtWidgets.QMessageBox()
        box.setIcon(QtWidgets.QMessageBox.Critical)
        box.setWindowTitle(APP_NAME)
        box.setText(message)
        if detail:
            box.setDetailedText(detail)
        exec_(box)
    except Exception:
        sys.stderr.write("{}\n{}\n".format(message, detail))


def _run_check():
    """Print the resolved environment and exit, without showing a GUI.

    `ReefShape Batch (PC).bat --check` from a terminal is the fastest way to
    answer "is this machine set up correctly?" -- and the right thing to ask a
    user for when a launcher misbehaves, since it exercises the same discovery
    and Qt bootstrap the GUI does but prints instead of drawing.
    """
    ok = True
    try:
        install = metashape_locate.find_install()
    except metashape_locate.MetashapeNotFound as exc:
        install, ok = None, False
        print("Metashape: NOT FOUND\n")
        print(exc)

    for label, value in environment_rows(install):
        print("{:22} {}".format(label + ":", value))

    if ok:
        # Constructing a QApplication is the real test: it's where Qt loads
        # the platform plugin, which is the step most likely to fail on a
        # misconfigured install.
        try:
            QtWidgets.QApplication([])
            print("\nQt startup:           OK")
        except Exception as exc:
            ok = False
            print("\nQt startup:           FAILED -- {}".format(exc))

    print("\nResult: {}".format("OK" if ok else "PROBLEMS FOUND"))
    return 0 if ok else 1


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)

    if "--check" in argv:
        return _run_check()

    enable_high_dpi()
    app = QtWidgets.QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("ReefShape")

    install = ensure_install()
    if install is None:
        return 1

    for label, value in environment_rows(install):
        print("{:22} {}".format(label + ":", value))

    from .ui.main_window import MainWindow
    window = MainWindow(install=install)
    window.show()

    # Open a batch passed on the command line, so a .rsbatch can be associated
    # with the app and double-clicked.
    for argument in argv[1:]:
        if argument.lower().endswith(".rsbatch") and os.path.isfile(argument):
            window._load_batch(argument)
            break

    return app.exec_() if hasattr(app, "exec_") else app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        _fatal("ReefShape Batch failed to start.", traceback.format_exc())
        sys.exit(1)
