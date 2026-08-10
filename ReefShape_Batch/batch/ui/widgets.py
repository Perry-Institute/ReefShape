"""
Small reusable widgets for the batch UI.

Nothing here knows about Metashape -- the GUI process cannot import it. Where
the in-Metashape dialogs call `Metashape.app.getCoordinateSystem()`, this
offers a list of known coordinate systems plus a WKT box instead. In practice
ReefShape plots use one of the two built-ins, so the escape hatch only has to
be workable, not elegant.
"""

from __future__ import annotations

import os

from ..qt import QtCore, QtWidgets, Signal, exec_
from .. import models


class PathRow(QtWidgets.QWidget):
    """Label, read-only path box, and a Browse button.

    Read-only because a hand-typed path that does not quite exist is a
    frustrating way to lose an overnight batch; everything is picked through a
    file dialog and validated.
    """

    changed = Signal(str)

    FOLDER, OPEN_FILE, SAVE_FILE = "folder", "open", "save"

    def __init__(self, label, mode=FOLDER, file_filter="", placeholder="",
                 parent=None):
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter
        self._path = ""

        self.label = QtWidgets.QLabel(label)
        self.edit = QtWidgets.QLineEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText(placeholder or "Not set")
        self.button = QtWidgets.QPushButton("Browse...")
        self.button.clicked.connect(self._browse)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label.setMinimumWidth(130)
        layout.addWidget(self.label)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def path(self):
        return self._path

    def setPath(self, path):
        self._path = path or ""
        self.edit.setText(self._path)
        self.edit.setToolTip(self._path)
        # Keep the visible end of a long path useful: the filename matters
        # more than the drive letter when scanning a list of jobs.
        self.edit.setCursorPosition(len(self._path))

    def _browse(self):
        start = os.path.dirname(self._path) if self._path else ""
        if self.mode == self.FOLDER:
            path = QtWidgets.QFileDialog.getExistingDirectory(
                self, self.label.text(), start)
        elif self.mode == self.SAVE_FILE:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, self.label.text(), start, self.file_filter)
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, self.label.text(), start, self.file_filter)
        if path:
            self.setPath(path)
            self.changed.emit(path)


class CRSPicker(QtWidgets.QWidget):
    """Coordinate system chooser.

    Offers the two systems ReefShape actually uses, plus any custom WKT the
    user has entered before. `Metashape.app.getCoordinateSystem()` is not
    available out here, so "Custom..." opens a plain WKT text box -- rarely
    needed, but the only way to reach an arbitrary CRS from outside Metashape.
    """

    CUSTOM = "Custom WKT..."

    def __init__(self, parent=None):
        super().__init__(parent)
        self._custom = {}   # label -> wkt

        self.combo = QtWidgets.QComboBox()
        self._reload()
        self.combo.currentIndexChanged.connect(self._on_changed)
        self._last_index = 0

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QtWidgets.QLabel("Coordinate system:")
        label.setMinimumWidth(130)
        layout.addWidget(label)
        layout.addWidget(self.combo, 1)

    def _reload(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        for name, _wkt in models.BUILTIN_CRS:
            self.combo.addItem(name)
        for name in self._custom:
            self.combo.addItem(name)
        self.combo.addItem(self.CUSTOM)
        self.combo.blockSignals(False)

    def _on_changed(self, index):
        if self.combo.itemText(index) != self.CUSTOM:
            self._last_index = index
            return

        wkt, ok = QtWidgets.QInputDialog.getMultiLineText(
            self, "Custom coordinate system",
            "Paste the coordinate system's WKT definition.\n"
            "You can copy this from Metashape: Reference pane settings, or "
            "any CRS shown in a project.", "")
        if not ok or not wkt.strip():
            self.combo.setCurrentIndex(self._last_index)
            return

        name = self._name_for(wkt.strip())
        self._custom[name] = wkt.strip()
        self._reload()
        self.combo.setCurrentIndex(self.combo.findText(name))
        self._last_index = self.combo.currentIndex()

    @staticmethod
    def _name_for(wkt):
        """Pull a display name out of the WKT's leading quoted string."""
        try:
            start = wkt.index('"') + 1
            return wkt[start:wkt.index('"', start)][:60] or "Custom CRS"
        except ValueError:
            return "Custom CRS"

    def wkt(self):
        name = self.combo.currentText()
        for builtin, wkt in models.BUILTIN_CRS:
            if builtin == name:
                return wkt
        return self._custom.get(name, models.WGS84_EGM96_WKT)

    def label(self):
        return self.combo.currentText()

    def setWkt(self, wkt, label=""):
        for name, builtin in models.BUILTIN_CRS:
            if builtin == wkt:
                self.combo.setCurrentIndex(self.combo.findText(name))
                return
        if wkt:
            name = label or self._name_for(wkt)
            self._custom[name] = wkt
            self._reload()
            self.combo.setCurrentIndex(self.combo.findText(name))
        self._last_index = self.combo.currentIndex()


class GeorefColumnsWidget(QtWidgets.QGroupBox):
    """Which column of the georeferencing CSV holds what.

    Mirrors the Column Formatting box in the Metashape dialog, including its
    1-based numbering -- the numbers are meant to be read straight off a
    spreadsheet's column headers.
    """

    def __init__(self, parent=None):
        super().__init__("Column formatting", parent)
        grid = QtWidgets.QGridLayout(self)

        hint = QtWidgets.QLabel(
            "Which column of the georeferencing file holds each value. "
            "Column numbers start at 1.")
        hint.setWordWrap(True)
        grid.addWidget(hint, 0, 0, 1, 4)

        self.spins = {}
        fields = [
            ("col_label", "Label:", 1, 1, 0),
            ("col_y", "Latitude (Y):", 2, 1, 2),
            ("col_x", "Longitude (X):", 3, 2, 0),
            ("col_z", "Depth (Z):", 4, 2, 2),
            ("col_x_accuracy", "X accuracy:", 5, 3, 0),
            ("col_y_accuracy", "Y accuracy:", 5, 3, 2),
            ("col_z_accuracy", "Z accuracy:", 6, 4, 0),
            ("skip_rows", "Start at row:", 2, 4, 2),
        ]
        for name, text, default, row, col in fields:
            spin = QtWidgets.QSpinBox()
            spin.setMinimum(1)
            spin.setMaximum(99)
            spin.setValue(default)
            self.spins[name] = spin
            grid.addWidget(QtWidgets.QLabel(text), row, col)
            grid.addWidget(spin, row, col + 1)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

    def values(self):
        return {name: spin.value() for name, spin in self.spins.items()}

    def setValues(self, georef):
        for name, spin in self.spins.items():
            spin.setValue(getattr(georef, name, spin.value()))


class CornerMarkersWidget(QtWidgets.QGroupBox):
    """The four corner targets, in cyclic order around the plot.

    Order decides the boundary polygon's winding, so listing them out of
    cyclic order produces a self-intersecting boundary. Laid out as a rectangle
    for the same reason the Metashape dialog does it: it makes the intended
    order obvious without having to explain it.
    """

    def __init__(self, parent=None):
        super().__init__("Corner markers", parent)
        grid = QtWidgets.QGridLayout(self)

        hint = QtWidgets.QLabel(
            "Target numbers at each corner of the plot. Any rotation is fine "
            "as long as they go around the plot in order.")
        hint.setWordWrap(True)
        grid.addWidget(hint, 0, 0, 1, 3)

        self.nw, self.ne, self.se, self.sw = (
            QtWidgets.QSpinBox() for _ in range(4))
        for spin, value in ((self.nw, 1), (self.ne, 2),
                            (self.se, 3), (self.sw, 4)):
            spin.setMinimum(1)
            spin.setMaximum(999)
            spin.setValue(value)

        for spin, label, row, col in ((self.nw, "NW", 1, 0), (self.ne, "NE", 1, 2),
                                      (self.sw, "SW", 3, 0), (self.se, "SE", 3, 2)):
            cell = QtWidgets.QHBoxLayout()
            cell.addWidget(QtWidgets.QLabel(label + ":"))
            cell.addWidget(spin)
            grid.addLayout(cell, row, col)

        plot = QtWidgets.QLabel("Reef plot")
        plot.setAlignment(QtCore.Qt.AlignCenter)
        plot.setFrameShape(QtWidgets.QFrame.Box)
        plot.setMinimumSize(110, 60)
        plot.setEnabled(False)
        grid.addWidget(plot, 2, 1)
        grid.setColumnStretch(1, 1)

    def values(self):
        # Clockwise from NW, which is the cyclic order the boundary builder
        # walks.
        return [self.nw.value(), self.ne.value(),
                self.se.value(), self.sw.value()]

    def setValues(self, corners):
        if len(corners) == 4:
            for spin, value in zip((self.nw, self.ne, self.se, self.sw), corners):
                spin.setValue(int(value))


def issue_summary_html(issues):
    """Format validation issues as HTML for a label."""
    if not issues:
        return "<span style='color:#1a7f37;'>&#10003; Ready to run.</span>"
    parts = []
    for issue in issues:
        colour = "#c0392b" if issue.severity == models.ERROR else "#b8860b"
        word = "Error" if issue.severity == models.ERROR else "Warning"
        parts.append("<div style='color:{};margin-bottom:4px;'>"
                     "<b>{}:</b> {}</div>".format(colour, word, issue.message))
    return "".join(parts)
