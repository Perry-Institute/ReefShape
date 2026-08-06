"""
Qt binding shim for ReefShape Batch.

Import Qt through this module, never directly:

    from batch.qt import QtCore, QtGui, QtWidgets, Signal, QAction

It does two jobs.

**Bootstrapping.** Importing this module runs `metashape_locate.bootstrap_qt()`
*before* touching PySide, which on Windows is the difference between working
and `ImportError: DLL load failed while importing QtWidgets`. Metashape keeps
Qt5Widgets.dll in its install root rather than beside the PySide2 package, so
the root has to join the DLL search path first. Routing every Qt import through
here means no call site has to remember that.

**Binding portability.** Metashape 2.3 bundles PySide2 (Qt 5.15), but a user
running the GUI under their own Python will more likely have PySide6, and
Agisoft will move eventually. PySide6 is preferred when both are available.
Only the handful of differences this app actually hits are papered over --
this is not a general-purpose qtpy replacement, and adding to it is cheaper
than debugging a leaky abstraction.
"""

from __future__ import annotations

# Must precede any PySide import. See module docstring.
from . import metashape_locate

metashape_locate.bootstrap_qt()

QT_API = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
    QT_API = "PySide6"
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
    QT_API = "PySide2"


Signal = QtCore.Signal
Slot = QtCore.Slot
Property = QtCore.Property

# QAction moved packages in Qt 6 (QtWidgets -> QtGui). Re-export the one that
# exists so toolbar/menu code reads identically on both bindings.
if QT_API == "PySide6":
    QAction = QtGui.QAction
else:
    QAction = QtWidgets.QAction


def exec_(obj):
    """Run a dialog or application event loop on either binding.

    PySide2 spells this `exec_` (because `exec` was a keyword in Python 2);
    PySide6 spells it `exec`. Both names exist on some versions and not
    others, so prefer the binding-native one and fall back.
    """
    if QT_API == "PySide6" and hasattr(obj, "exec"):
        return obj.exec()
    return obj.exec_()


def enable_high_dpi():
    """Opt into high-DPI scaling, before the QApplication is constructed.

    Qt 6 scales by default and removed the attributes entirely -- referencing
    them there raises AttributeError -- so this is a no-op on PySide6. Under
    Qt 5 the batch window's tables and progress bars render at half size on a
    4K display without it.
    """
    if QT_API == "PySide6":
        return
    for attr in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        flag = getattr(QtCore.Qt, attr, None)
        if flag is not None:
            QtWidgets.QApplication.setAttribute(flag, True)
