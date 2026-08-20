"""
Build Scalebar File
Will Greene, Perry Institute for Marine Science

Writes the scalebar list the ReefShape workflow reads: one line per scalebar,

    target 19,target 20,0.49650,0.00025

with the distance and accuracy both in metres. Getting that by hand means
typing marker labels consistently and converting millimetres to metres for
every bar, which is a tedious way to introduce a typo that only shows up later
as a badly scaled model.

Entry here is in the units the bars are actually measured in -- centimetres for
length, millimetres for accuracy -- and the conversion happens on save.

An existing file can be loaded and added to, which is the usual case: a survey
keeps one master list and appends new bars as they are made.
"""

import os
import re

import Metashape
from PySide2 import QtCore, QtWidgets


# Written into the file as the marker label prefix. Metashape's detected
# markers are named "target 1", "target 2" and so on, and the workflow matches
# these labels literally, so the default has to match exactly.
DEFAULT_PREFIX = "target "

# Millimetres. Typical for a bar measured carefully with a steel rule; the
# master list this was built against uses 0.25 to 1.0 mm.
DEFAULT_ACCURACY_MM = 0.5

# QSettings scope used by 01_full_reefshape_workflow.py, so "set as default"
# means the workflow dialog opens with this file already selected.
SETTINGS_ORG = "ReefShape"
SETTINGS_APP = "UnderwaterWorkflow"
SETTINGS_KEY = "scalebars_path"

COL_A, COL_B, COL_DISTANCE, COL_ACCURACY = range(4)


class BuildScalebarFileDlg(QtWidgets.QDialog):

    def __init__(self, parent):
        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Build Scalebar File")
        self.resize(620, 500)
        self.loaded_path = ""

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Each scalebar is a pair of targets a known distance apart. Enter "
            "the distance in centimetres and the accuracy in millimetres; both "
            "are written to the file in metres."))

        layout.addLayout(self._build_source_row())
        layout.addWidget(self._build_table(), 1)
        layout.addLayout(self._build_row_buttons())

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.set_default_check = QtWidgets.QCheckBox(
            "Set as the default scalebar file for the ReefShape workflow")
        self.set_default_check.setChecked(True)
        self.set_default_check.setToolTip(
            "The Full ReefShape Workflow dialog will open with this file "
            "already selected.")
        layout.addWidget(self.set_default_check)

        layout.addLayout(self._build_buttons())

        self._add_row()
        self._refresh()

    # -- construction --

    def _build_source_row(self):
        row = QtWidgets.QHBoxLayout()

        row.addWidget(QtWidgets.QLabel("Marker label prefix:"))
        self.prefix_edit = QtWidgets.QLineEdit(DEFAULT_PREFIX)
        self.prefix_edit.setFixedWidth(110)
        self.prefix_edit.setToolTip(
            "Written before each marker number. Must match the marker labels "
            "in Metashape exactly -- detected targets are named \"target 1\", "
            "\"target 2\" and so on.")
        self.prefix_edit.textChanged.connect(lambda _t: self._refresh())
        row.addWidget(self.prefix_edit)

        row.addStretch(1)
        load = QtWidgets.QPushButton("Load existing file...")
        load.setToolTip("Add to a scalebar list you already have.")
        load.clicked.connect(self._load)
        row.addWidget(load)
        return row

    def _build_table(self):
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Marker A", "Marker B", "Distance (cm)", "Accuracy (mm)"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in range(4):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Stretch)
        return self.table

    def _build_row_buttons(self):
        row = QtWidgets.QHBoxLayout()
        for text, slot, tip in (
            ("Add scalebar", self._add_row,
             "Marker numbers continue from the last row."),
            ("Duplicate", self._duplicate_row,
             "Copy the selected row, for a second bar of the same length."),
            ("Remove", self._remove_row, ""),
            ("Sort", self._sort_rows, "Order by first marker number."),
        ):
            button = QtWidgets.QPushButton(text)
            if tip:
                button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_buttons(self):
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        self.save_button = QtWidgets.QPushButton("Save file...")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        row.addWidget(close)
        row.addWidget(self.save_button)
        return row

    # -- rows --

    def _spin(self, integer, value, minimum, maximum, decimals=3):
        widget = QtWidgets.QSpinBox() if integer else QtWidgets.QDoubleSpinBox()
        if not integer:
            # Three decimals in centimetres resolves 10 micrometres, which is
            # what it takes to carry a bar measured to 49.675 cm without
            # rounding. Two decimals silently turned that into 49.68 -- a
            # 50 micrometre error on every such bar, on data whose whole
            # purpose is sub-millimetre scale.
            widget.setDecimals(decimals)
            widget.setSingleStep(0.05)
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.valueChanged.connect(lambda _v: self._refresh())
        return widget

    def _add_row(self, marker_a=None, marker_b=None, distance_cm=50.0,
                 accuracy_mm=DEFAULT_ACCURACY_MM):
        # Continue the numbering from the last row: bars are made in pairs and
        # numbered consecutively, so this is nearly always what comes next.
        if marker_a is None:
            last = self.table.rowCount() - 1
            if last >= 0:
                previous = self.table.cellWidget(last, COL_B).value()
                marker_a, marker_b = previous + 1, previous + 2
            else:
                marker_a, marker_b = 1, 2

        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setCellWidget(row, COL_A, self._spin(True, marker_a, 1, 99999))
        self.table.setCellWidget(row, COL_B, self._spin(True, marker_b, 1, 99999))
        self.table.setCellWidget(
            row, COL_DISTANCE, self._spin(False, distance_cm, 0.1, 100000.0))
        self.table.setCellWidget(
            row, COL_ACCURACY, self._spin(False, accuracy_mm, 0.01, 1000.0))
        self.table.selectRow(row)
        self._refresh()

    def _duplicate_row(self):
        row = self.table.currentRow()
        if row < 0:
            return
        values = self._row_values(row)
        self._add_row(values[1] + 1, values[1] + 2, values[2], values[3])

    def _remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._refresh()

    def _sort_rows(self):
        rows = sorted((self._row_values(r) for r in range(self.table.rowCount())),
                      key=lambda v: (v[0], v[1]))
        self.table.setRowCount(0)
        for marker_a, marker_b, distance, accuracy in rows:
            self._add_row(marker_a, marker_b, distance, accuracy)

    def _row_values(self, row):
        return (self.table.cellWidget(row, COL_A).value(),
                self.table.cellWidget(row, COL_B).value(),
                self.table.cellWidget(row, COL_DISTANCE).value(),
                self.table.cellWidget(row, COL_ACCURACY).value())

    # -- validation --

    def _problems(self):
        problems = []
        seen = {}
        for row in range(self.table.rowCount()):
            marker_a, marker_b, distance, _accuracy = self._row_values(row)
            if marker_a == marker_b:
                problems.append(
                    "Row {}: a scalebar needs two different markers."
                    .format(row + 1))
            if distance <= 0:
                problems.append("Row {}: distance must be greater than zero."
                                .format(row + 1))
            # Order-independent: a bar between 19 and 20 is the same bar as
            # one between 20 and 19, and the workflow matches either way.
            key = tuple(sorted((marker_a, marker_b)))
            if key in seen:
                problems.append(
                    "Rows {} and {} are both between markers {} and {}."
                    .format(seen[key] + 1, row + 1, key[0], key[1]))
            else:
                seen[key] = row
        if not self.prefix_edit.text():
            problems.append("A marker label prefix is required.")
        return problems

    def _refresh(self):
        count = self.table.rowCount()
        problems = self._problems()
        if count == 0:
            self.status.setText("Add at least one scalebar.")
            self.status.setStyleSheet("color: palette(mid);")
            self.save_button.setEnabled(False)
        elif problems:
            self.status.setText("\n".join(problems[:3]))
            self.status.setStyleSheet("color: #b8860b;")
            self.save_button.setEnabled(False)
        else:
            example = self._format_line(*self._row_values(0))
            self.status.setText(
                "{} scalebar{} ready. First line: {}".format(
                    count, "" if count == 1 else "s", example))
            self.status.setStyleSheet("color: #1a7f37;")
            self.save_button.setEnabled(True)

    # -- file --

    def _format_line(self, marker_a, marker_b, distance_cm, accuracy_mm):
        prefix = self.prefix_edit.text()
        # Six decimals in metres matches the precision the entry fields
        # carry (10 micrometres of distance, 1 micrometre of accuracy).
        return "{}{},{}{},{:.6f},{:.6f}".format(
            prefix, marker_a, prefix, marker_b,
            distance_cm / 100.0, accuracy_mm / 1000.0)

    def _load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open scalebar file", self._start_dir(),
            "Scalebar list (*.txt *.csv);;All files (*)")
        if not path:
            return

        rows, skipped, prefix = [], 0, None
        try:
            with open(path) as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(",")
                    if len(parts) < 4:
                        skipped += 1
                        continue
                    try:
                        numbers = [int(re.search(r"(\d+)", parts[i]).group(1))
                                   for i in (0, 1)]
                        # Back to the units shown in the table.
                        distance_cm = float(parts[2]) * 100.0
                        accuracy_mm = float(parts[3]) * 1000.0
                    except (AttributeError, ValueError):
                        skipped += 1
                        continue
                    if prefix is None:
                        match = re.match(r"^(.*?)(\d+)\s*$", parts[0])
                        prefix = match.group(1) if match else DEFAULT_PREFIX
                    rows.append((numbers[0], numbers[1], distance_cm, accuracy_mm))
        except OSError as err:
            Metashape.app.messageBox("Could not read the file:\n\n{}".format(err))
            return

        if not rows:
            Metashape.app.messageBox(
                "No scalebars could be read from that file. Each line should "
                "look like:\n\n    target 19,target 20,0.49650,0.00025")
            return

        self.table.setRowCount(0)
        if prefix:
            self.prefix_edit.setText(prefix)
        for marker_a, marker_b, distance, accuracy in rows:
            self._add_row(marker_a, marker_b, distance, accuracy)

        self.loaded_path = path
        message = "Loaded {} scalebar{} from {}".format(
            len(rows), "" if len(rows) == 1 else "s", os.path.basename(path))
        if skipped:
            message += " ({} line{} skipped)".format(
                skipped, "" if skipped == 1 else "s")
        print(message)
        self._refresh()

    def _start_dir(self):
        if self.loaded_path:
            return os.path.dirname(self.loaded_path)
        doc = Metashape.app.document
        if doc and doc.path:
            return os.path.dirname(doc.path)
        return ""

    def _save(self):
        default_name = (self.loaded_path
                        or os.path.join(self._start_dir(), "scalebars.txt"))
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save scalebar file", default_name,
            "Scalebar list (*.txt);;All files (*)")
        if not path:
            return

        lines = [self._format_line(*self._row_values(row))
                 for row in range(self.table.rowCount())]
        try:
            # newline="" keeps Python from translating the line endings a
            # second time on Windows; the workflow reads the file line by line
            # and is indifferent, but doubled endings look like corruption to
            # anyone opening it in an editor.
            with open(path, "w", newline="") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as err:
            Metashape.app.messageBox("Could not write the file:\n\n{}".format(err))
            return

        note = ""
        if self.set_default_check.isChecked():
            settings = QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)
            settings.setValue(SETTINGS_KEY, path)
            note = ("\n\nThe Full ReefShape Workflow dialog will now open with "
                    "this file selected.")

        print("Wrote {} scalebars to {}".format(len(lines), path))
        Metashape.app.messageBox(
            "Saved {} scalebar{} to:\n{}{}".format(
                len(lines), "" if len(lines) == 1 else "s", path, note))
        self.accept()


def run_script():
    app = QtWidgets.QApplication.instance()
    parent = app.activeWindow()
    try:
        dlg = BuildScalebarFileDlg(parent)
        dlg.exec()
    except Exception as err:
        Metashape.app.messageBox("Build Scalebar File error:\n\n{}".format(err))


label = "ReefShape/Tools/Build Scalebar File"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
