"""
Environment diagnostics dialog.

Shows exactly which Metashape, which interpreter, and which Qt binding the app
resolved to. This is the first thing to ask for when a user reports "it won't
start" or "it processes differently than the menu scripts do" -- both of which
almost always come down to a second Metashape install being picked up.
"""

from __future__ import annotations

import sys

from ..qt import QtWidgets, QT_API, exec_
from .. import metashape_locate


def environment_rows(install=None):
    """(label, value) pairs describing the resolved runtime.

    Never raises: this runs in failure paths where something is already wrong,
    and a diagnostics dialog that crashes is worse than useless.
    """
    rows = [
        ("Qt binding", QT_API),
        ("Python", sys.version.split()[0]),
        ("Interpreter", sys.executable),
        ("Platform", sys.platform),
    ]

    if install is None:
        try:
            install = metashape_locate.find_install()
        except metashape_locate.MetashapeNotFound as exc:
            rows.append(("Metashape", "NOT FOUND"))
            rows.append(("Details", str(exc).splitlines()[0]))
            return rows

    rows.extend([
        ("Metashape root", str(install.root)),
        ("Metashape executable", str(install.executable)),
        ("Bundled Python", str(install.python) if install.python
         else "not found (using system Python)"),
        ("Qt plugins", str(install.plugins) if install.plugins else "not found"),
        ("Config file", str(metashape_locate.config_dir() / "batch_config.json")),
    ])
    return rows


class DiagnosticsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, install=None):
        super().__init__(parent)
        self.setWindowTitle("ReefShape Batch -- Environment")
        self.setMinimumWidth(680)

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "ReefShape Batch drives Metashape in headless mode. If processing "
            "behaves differently from the ReefShape menu scripts, check that "
            "the Metashape below is the same one you normally use."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        table = QtWidgets.QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Setting", "Value"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # Values are long absolute paths; let the value column take the slack
        # so they're readable without horizontal scrolling.
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)

        for label, value in environment_rows(install):
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(label)))
            item = QtWidgets.QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            table.setItem(row, 1, item)
        table.resizeRowsToContents()
        layout.addWidget(table)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        copy_btn = buttons.addButton("Copy to Clipboard",
                                     QtWidgets.QDialogButtonBox.ActionRole)
        copy_btn.clicked.connect(lambda: self._copy(install))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _copy(self, install):
        """Put the environment on the clipboard for pasting into a bug report."""
        text = "\n".join("{}: {}".format(k, v)
                         for k, v in environment_rows(install))
        QtWidgets.QApplication.clipboard().setText(text)


def show_diagnostics(parent=None, install=None):
    exec_(DiagnosticsDialog(parent, install))
