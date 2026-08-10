"""
Build a batch from a folder of plots in one go.

Point at a season's parent directory and get one new-plot job per photo
subfolder. This is the difference between setting up twenty plots and setting
up one twenty times, which is the whole reason the batch app exists.

Two layouts are recognised, because both are common in the field:

    Season/                     Season/
      SiteA/  *.JPG               SiteA/
      SiteB/  *.JPG                 20260522/  *.JPG
                                  SiteB/
                                    20260523/  *.JPG

The first names the chunk from EXIF; the second takes the subfolder's name as
the chunk, since a dated folder is already the answer.
"""

from __future__ import annotations

import os
import re

from ..qt import QtCore, QtWidgets, exec_
from .. import models, store
from ..models import Job, NEW_PLOT


DATE_FOLDER = re.compile(r"^\d{8}$")

# Folders that are outputs, not inputs. Scanning these in would create jobs
# pointed at a previous run's exports.
IGNORED = {"taglab_outputs", "exports", "outputs", "reports"}


def find_plots(root):
    """Discover plots under `root`.

    Returns a list of dicts: name, photo_folder, chunk_name, image_count.
    Only the two layouts above are considered -- guessing more deeply risks
    sweeping in an export folder and creating a job that processes an
    orthomosaic back into a project.
    """
    plots = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return plots

    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name.lower() in IGNORED:
            continue

        direct = models.count_images(entry.path)
        if direct:
            plots.append({"name": entry.name, "photo_folder": entry.path,
                          "chunk_name": "", "image_count": direct})
            continue

        # No images directly inside: look one level down for dated folders.
        try:
            children = sorted(os.scandir(entry.path), key=lambda e: e.name)
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.name.lower() in IGNORED:
                continue
            count = models.count_images(child.path)
            if not count:
                continue
            plots.append({
                "name": entry.name,
                "photo_folder": child.path,
                # A folder already named YYYYMMDD is the chunk name; anything
                # else is left blank so EXIF decides.
                "chunk_name": child.name if DATE_FOLDER.match(child.name) else "",
                "image_count": count,
            })
    return plots


class ScanFolderDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scan folder for plots")
        self.setMinimumSize(860, 560)
        self.plots = []

        layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Choose a folder containing one subfolder per plot. Each plot "
            "gets a new-plot job, with settings taken from the template below. "
            "You can edit any of them afterwards.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QtWidgets.QHBoxLayout()
        self.folder_edit = QtWidgets.QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText("No folder chosen")
        browse = QtWidgets.QPushButton("Choose folder...")
        browse.clicked.connect(self._choose)
        row.addWidget(QtWidgets.QLabel("Scan:"))
        row.addWidget(self.folder_edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Settings template:"))
        self.template_combo = QtWidgets.QComboBox()
        self.templates = store.load_templates()
        self.template_combo.addItems([t.name for t in self.templates])
        last = store.get_setting("last_template", "Default")
        index = self.template_combo.findText(last or "Default")
        if index >= 0:
            self.template_combo.setCurrentIndex(index)
        row.addWidget(self.template_combo)
        row.addStretch(1)
        layout.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Create projects in:"))
        self.dest_combo = QtWidgets.QComboBox()
        self.dest_combo.addItems([
            "Alongside each plot's photos",
            "All in one folder...",
        ])
        self.dest_combo.currentIndexChanged.connect(self._on_dest_changed)
        self.dest_edit = QtWidgets.QLineEdit()
        self.dest_edit.setReadOnly(True)
        self.dest_edit.setVisible(False)
        row.addWidget(self.dest_combo)
        row.addWidget(self.dest_edit, 1)
        layout.addLayout(row)

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["", "Plot", "Photos", "Chunk", "Project will be created at"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        self.summary = QtWidgets.QLabel("")
        layout.addWidget(self.summary)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Add jobs")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_ok()

    def _choose(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Folder containing plot subfolders",
            store.get_setting("last_scan_folder", "") or "")
        if not folder:
            return
        self.folder_edit.setText(folder)
        store.set_setting("last_scan_folder", folder)
        self._rescan()

    def _on_dest_changed(self, index):
        self.dest_edit.setVisible(index == 1)
        if index == 1:
            folder = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Folder to create all projects in")
            if folder:
                self.dest_edit.setText(folder)
            else:
                self.dest_combo.setCurrentIndex(0)
        self._rescan()

    def _rescan(self):
        root = self.folder_edit.text()
        self.plots = find_plots(root) if root else []
        self.table.setRowCount(len(self.plots))
        for row, plot in enumerate(self.plots):
            check = QtWidgets.QTableWidgetItem()
            check.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            check.setCheckState(QtCore.Qt.Checked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(plot["name"]))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(
                str(plot["image_count"])))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(
                plot["chunk_name"] or "(from photos)"))
            path = self._project_path(plot)
            item = QtWidgets.QTableWidgetItem(path)
            item.setToolTip(path)
            if os.path.exists(path):
                item.setText(path + "   (already exists -- will be continued)")
            self.table.setItem(row, 4, item)

        if not root:
            self.summary.setText("")
        elif not self.plots:
            self.summary.setText(
                "No photo folders found. Expected subfolders containing .jpg "
                "or .tif images, either directly or one level down.")
        else:
            total = sum(p["image_count"] for p in self.plots)
            self.summary.setText(
                "{} plot(s), {} images total.".format(len(self.plots), total))
        self._update_ok()

    def _project_path(self, plot):
        if self.dest_combo.currentIndex() == 1 and self.dest_edit.text():
            folder = self.dest_edit.text()
        else:
            # Beside the photos, one level up from a dated subfolder so the
            # project sits with the plot rather than inside one visit.
            folder = os.path.dirname(plot["photo_folder"])
        return os.path.join(folder, plot["name"] + ".psx")

    def _checked(self):
        return [plot for row, plot in enumerate(self.plots)
                if self.table.item(row, 0)
                and self.table.item(row, 0).checkState() == QtCore.Qt.Checked]

    def _update_ok(self):
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(
            bool(self.plots))

    def jobs(self):
        template = next(
            (t for t in self.templates
             if t.name == self.template_combo.currentText()), None)
        store.set_setting("last_template", self.template_combo.currentText())

        jobs = []
        for plot in self._checked():
            job = Job(kind=NEW_PLOT,
                      label=plot["name"],
                      project_path=self._project_path(plot),
                      photo_folders=[plot["photo_folder"]],
                      chunk_name=plot["chunk_name"])
            if template:
                job.apply_template(template)
            jobs.append(job)
        return jobs


def scan_folder_dialog(parent=None):
    """Show the scanner. Returns the jobs to add (empty if cancelled)."""
    dialog = ScanFolderDialog(parent)
    if exec_(dialog) != QtWidgets.QDialog.Accepted:
        return []
    return dialog.jobs()
