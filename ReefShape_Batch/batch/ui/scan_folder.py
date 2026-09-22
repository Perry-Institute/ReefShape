"""
Build a batch from a folder of plots in one go.

Point at a season's parent directory and get one new-plot job per photo folder
found under each plot folder. This is the difference between setting up twenty
plots and setting up one twenty times, which is the whole reason the batch app
exists.

Photos may sit at any depth below a plot folder (see MAX_DEPTH). Common shapes:

    Season/              Season/                Season/
      SiteA/  *.JPG        SiteA/                 SiteA/
      SiteB/  *.JPG          20260522/  *.JPG       20260522/
                           SiteB/                     JPEG/  *.JPG
                             20260523/  *.JPG         RAW/   *.ARW

Each plot folder is one plot, and the project sits with it. A folder named
YYYYMMDD anywhere between the plot and its photos names the chunk, since a
dated folder is already the answer; otherwise EXIF decides. RAW folders are
passed over naturally: only .jpg and .tif files count as photos.
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

# How many folders deep to look for photos, counting the plot folder as one.
# The usual layout is Plot/YYYYMMDD/JPEG (three); the rest is headroom. The cap
# also keeps a symlink loop finite.
MAX_DEPTH = 6


def _subfolders(folder):
    """Subfolders of `folder` that could hold photos, in name order."""
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name)
    except OSError:
        return []
    return [e for e in entries
            if e.is_dir()
            and not e.name.startswith(".")
            and e.name.lower() not in IGNORED
            # Metashape's own project data; never source photos, and large
            # enough to be worth not walking now that the scan goes deep.
            and not e.name.lower().endswith(".files")]


def _find_photo_folders(folder, trail, depth):
    """Yield (path, trail, image_count) for each photo folder at or below `folder`.

    `trail` is the folder names from the plot folder down to `folder`. A folder
    holding images is a photo folder and the search stops there: the workflow
    reads only the top level of one, so anything nested inside it is not a
    separate set of photos. Folders without images are searched further.
    """
    count = models.count_images(folder)
    if count:
        yield folder, trail, count
    elif depth < MAX_DEPTH:
        for child in _subfolders(folder):
            yield from _find_photo_folders(
                child.path, trail + [child.name], depth + 1)


def _chunk_name(trail):
    """The nearest YYYYMMDD folder name in `trail`, or "" to let EXIF decide."""
    for name in reversed(trail):
        if DATE_FOLDER.match(name):
            return name
    return ""


def find_plots(root):
    """Discover plots under `root`.

    Returns a list of dicts: name, photo_folder, chunk_name, image_count,
    project_folder. A plot folder with several photo folders (one per survey)
    yields one entry for each.

    Only folders holding photo files are picked up, and IGNORED folders are
    skipped at every depth, so a previous run's exports are not swept in and
    turned into a job that processes an orthomosaic back into a project.
    """
    plots = []
    for entry in _subfolders(root):
        for path, trail, count in _find_photo_folders(entry.path, [], 1):
            plots.append({
                "name": entry.name,
                "photo_folder": path,
                "chunk_name": _chunk_name(trail),
                "image_count": count,
                # Photos straight in the plot folder put the project beside it;
                # anything nested puts it in the plot folder, so it sits with
                # the plot rather than inside one visit.
                "project_folder": (entry.path if trail
                                   else os.path.dirname(entry.path)),
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
            "Choose a folder containing one subfolder per plot. Photos can be "
            "directly in a plot folder or in folders nested below it (for "
            "example a dated survey folder holding a JPEG folder). Each photo "
            "folder gets a new-plot job, with settings taken from the "
            "template below. You can edit any of them afterwards.")
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

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["", "Plot", "Photos", "Chunk", "Photo folder",
             "Project will be created at"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.Stretch)
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
            folder = QtWidgets.QTableWidgetItem(
                os.path.relpath(plot["photo_folder"], root))
            folder.setToolTip(plot["photo_folder"])
            self.table.setItem(row, 4, folder)
            path = self._project_path(plot)
            item = QtWidgets.QTableWidgetItem(path)
            item.setToolTip(path)
            if os.path.exists(path):
                item.setText(path + "   (already exists -- will be continued)")
            self.table.setItem(row, 5, item)

        if not root:
            self.summary.setText("")
        elif not self.plots:
            self.summary.setText(
                "No photo folders found. Expected .jpg or .tif images in "
                "folders up to {} levels below the chosen folder."
                .format(MAX_DEPTH))
        else:
            total = sum(p["image_count"] for p in self.plots)
            self.summary.setText(
                "{} plot(s), {} images total.".format(len(self.plots), total))
        self._update_ok()

    def _project_path(self, plot):
        if self.dest_combo.currentIndex() == 1 and self.dest_edit.text():
            folder = self.dest_edit.text()
        else:
            folder = plot["project_folder"]
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
