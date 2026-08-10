"""
The batch window: a queue of plots, and the controls to run it.

The table is the whole app. Each row is one plot; the toolbar builds the queue
and the run controls drive it. Everything else -- the editor, the log pane, the
templates -- hangs off a selected row.
"""

from __future__ import annotations

import os
import time

from ..qt import QtCore, QtGui, QtWidgets, QAction, exec_
from .. import models, store, scheduler
from ..models import Job, NEW_PLOT, REPHOTO
from ..store import Batch
from .job_editor import edit_job
from .diagnostics import show_diagnostics


COLUMNS = ["", "Type", "Job", "Chunk", "Photos", "Status", "Progress",
           "Elapsed", "Notes"]
COL_ENABLED, COL_TYPE, COL_NAME, COL_CHUNK, COL_PHOTOS = 0, 1, 2, 3, 4
COL_STATUS, COL_PROGRESS, COL_ELAPSED, COL_NOTES = 5, 6, 7, 8

STATUS_TEXT = {
    models.PENDING: "Waiting",
    models.RUNNING: "Running",
    models.DONE: "Done",
    models.FAILED: "Failed",
    models.CANCELLED: "Cancelled",
    models.SKIPPED: "Skipped",
    models.NEEDS_REFERENCING: "Needs referencing",
}

STATUS_COLOUR = {
    models.DONE: "#1a7f37",
    models.FAILED: "#c0392b",
    models.NEEDS_REFERENCING: "#b8860b",
    models.RUNNING: "#0969da",
    models.CANCELLED: "#6e7781",
}


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, install=None):
        super().__init__()
        self.install = install
        self.batch = Batch()
        self.batch.parallelism = int(store.get_setting("parallelism", 1) or 1)

        self.setWindowTitle("ReefShape Batch")
        self.resize(1180, 760)

        self.scheduler = scheduler.Scheduler(self)
        self.scheduler.jobChanged.connect(self._on_job_changed)
        self.scheduler.batchFinished.connect(self._on_batch_finished)
        self.scheduler.message.connect(self.statusBar().showMessage)

        self._build_ui()
        self._refresh_table()
        self._update_actions()

    # -- construction --

    def _build_ui(self):
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        self._build_toolbar()
        layout.addWidget(self._build_run_bar())

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(self._build_table())
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.statusBar().showMessage("Add plots to build a batch.")

    def _build_toolbar(self):
        bar = self.addToolBar("Batch")
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        bar.setMovable(False)

        def add(text, slot, tip=""):
            action = QAction(text, self)
            action.setToolTip(tip or text)
            action.triggered.connect(slot)
            bar.addAction(action)
            return action

        self.act_new_plot = add(
            "Add new plot", self._add_new_plot,
            "A plot that has not been photographed before")
        self.act_rephoto = add(
            "Add re-photography", self._add_rephoto,
            "A repeat visit to a plot that already has a Metashape project")
        self.act_scan = add(
            "Scan folder...", self._scan_folder,
            "Create one new-plot job per photo subfolder")
        bar.addSeparator()
        self.act_edit = add("Edit", self._edit_selected)
        self.act_duplicate = add("Duplicate", self._duplicate_selected)
        self.act_remove = add("Remove", self._remove_selected)
        bar.addSeparator()
        self.act_up = add("Move up", lambda: self._move_selected(-1))
        self.act_down = add("Move down", lambda: self._move_selected(1))
        bar.addSeparator()
        self.act_template = add(
            "Apply template...", self._apply_template,
            "Push one set of processing and export settings onto many jobs")

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self._action("&Open batch...", self._open_batch, "Ctrl+O"))
        file_menu.addAction(self._action("&Save batch", self._save_batch, "Ctrl+S"))
        file_menu.addAction(self._action("Save batch &as...", self._save_batch_as))
        file_menu.addSeparator()
        self.recent_menu = file_menu.addMenu("Recent batches")
        self._rebuild_recent_menu()
        file_menu.addSeparator()
        file_menu.addAction(self._action("E&xit", self.close))

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self._action(
            "Environment...", lambda: show_diagnostics(self, self.install)))

    def _action(self, text, slot, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(shortcut)
        return action

    def _build_run_bar(self):
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)

        self.run_button = QtWidgets.QPushButton("Run batch")
        self.run_button.setMinimumHeight(34)
        self.run_button.clicked.connect(self._run)
        row.addWidget(self.run_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setMinimumHeight(34)
        self.stop_button.clicked.connect(self._stop)
        row.addWidget(self.stop_button)

        row.addSpacing(20)
        row.addWidget(QtWidgets.QLabel("Run at once:"))
        self.parallel_spin = QtWidgets.QSpinBox()
        self.parallel_spin.setRange(1, 8)
        self.parallel_spin.setValue(self.batch.parallelism)
        self.parallel_spin.valueChanged.connect(self._on_parallelism_changed)
        row.addWidget(self.parallel_spin)

        self.parallel_warning = QtWidgets.QLabel("")
        self.parallel_warning.setStyleSheet("color: #b8860b;")
        row.addWidget(self.parallel_warning)

        self.stop_on_error = QtWidgets.QCheckBox("Stop on first failure")
        row.addWidget(self.stop_on_error)

        row.addStretch(1)
        self.overall = QtWidgets.QLabel("")
        row.addWidget(self.overall)
        self._on_parallelism_changed(self.batch.parallelism)
        return widget

    def _build_table(self):
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.doubleClicked.connect(self._edit_selected)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_ENABLED, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_TYPE, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_NAME, QtWidgets.QHeaderView.Stretch)
        for col in (COL_CHUNK, COL_PHOTOS, COL_STATUS, COL_PROGRESS, COL_ELAPSED):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_NOTES, QtWidgets.QHeaderView.Stretch)
        return self.table

    def _build_detail(self):
        tabs = QtWidgets.QTabWidget()

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        self.log_view.setFont(QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.FixedFont))
        tabs.addTab(self.log_view, "Log")

        self.detail_view = QtWidgets.QTextBrowser()
        tabs.addTab(self.detail_view, "Details")

        # The log file is appended to by another process, so poll it rather
        # than trying to share a handle.
        self._log_timer = QtCore.QTimer(self)
        self._log_timer.setInterval(1500)
        self._log_timer.timeout.connect(self._refresh_log)
        self._log_timer.start()
        self._log_offset = 0
        self._log_path = ""
        return tabs

    # -- job list --

    def _selected_job(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        if 0 <= index < len(self.batch.jobs):
            return self.batch.jobs[index]
        return None

    def _refresh_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.batch.jobs))
        for row, job in enumerate(self.batch.jobs):
            self._fill_row(row, job)
        self.table.blockSignals(False)
        self._update_overall()

    def _fill_row(self, row, job):
        def cell(col, text, tip=""):
            item = self.table.item(row, col)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.table.setItem(row, col, item)
            item.setText(text)
            if tip:
                item.setToolTip(tip)
            return item

        enabled = self.table.item(row, COL_ENABLED)
        if enabled is None:
            enabled = QtWidgets.QTableWidgetItem()
            enabled.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            self.table.setItem(row, COL_ENABLED, enabled)
        enabled.setCheckState(QtCore.Qt.Checked if job.enabled else QtCore.Qt.Unchecked)
        enabled.setToolTip("Untick to leave this plot out of the run")

        cell(COL_TYPE, "New" if job.kind == NEW_PLOT else "Revisit")
        cell(COL_NAME, job.display_label(), job.project_path)
        cell(COL_CHUNK, job.chunk_name or "(from photos)")
        photos = sum(models.count_images(f) for f in job.photo_folders)
        cell(COL_PHOTOS, str(photos) if photos else "-",
             "\n".join(job.photo_folders))

        status_item = cell(COL_STATUS, STATUS_TEXT.get(job.status, job.status))
        colour = STATUS_COLOUR.get(job.status)
        status_item.setForeground(QtGui.QBrush(QtGui.QColor(colour))
                                  if colour else QtGui.QBrush())

        if job.status == models.RUNNING:
            cell(COL_PROGRESS, "{} {:.0f}%".format(job.current_step, job.progress))
        elif job.status == models.PENDING:
            cell(COL_PROGRESS, "")
        else:
            cell(COL_PROGRESS, "")

        cell(COL_ELAPSED, _format_elapsed(job.elapsed()) if job.started_at else "")

        note = job.message
        if job.warnings and job.status != models.RUNNING:
            note = "{}  ({} warning{})".format(
                note or "Finished", len(job.warnings),
                "" if len(job.warnings) == 1 else "s")
        cell(COL_NOTES, note, "\n\n".join(job.warnings) if job.warnings else note)

    def _on_item_changed(self, item):
        if item.column() != COL_ENABLED:
            return
        row = item.row()
        if 0 <= row < len(self.batch.jobs):
            self.batch.jobs[row].enabled = item.checkState() == QtCore.Qt.Checked
            self.batch.mark_dirty()
            self._update_overall()

    def _on_selection_changed(self):
        job = self._selected_job()
        self._update_actions()
        self._show_details(job)
        if job and job.log_path != self._log_path:
            self._log_path = job.log_path or ""
            self._log_offset = 0
            self.log_view.clear()
            self._refresh_log()

    def _show_details(self, job):
        if job is None:
            self.detail_view.setHtml("")
            return
        issues = models.validate_job(job)
        parts = ["<h3>{}</h3>".format(job.display_label()),
                 "<p><b>Project:</b> {}<br>"
                 "<b>Photos:</b> {}<br>"
                 "<b>Chunk:</b> {}</p>".format(
                     job.project_path or "(not set)",
                     "<br>".join(job.photo_folders) or "(none)",
                     job.chunk_name or "(from photo capture date)")]
        if job.kind == REPHOTO:
            parts.append("<p><b>Aligns to:</b> {}<br><b>Damaged markers:</b> "
                         "{}</p>".format(job.reference_chunk or "(not set)",
                                         ", ".join(job.damaged_markers) or "none"))
        if job.warnings:
            parts.append("<h4>Warnings</h4><ul>{}</ul>".format(
                "".join("<li>{}</li>".format(w) for w in job.warnings)))
        if job.outputs:
            parts.append("<h4>Outputs</h4><ul>{}</ul>".format(
                "".join("<li>{}</li>".format(o) for o in job.outputs)))
        if issues:
            parts.append("<h4>Before running</h4>")
            parts.append("<ul>{}</ul>".format("".join(
                "<li>{}: {}</li>".format(i.severity, i.message) for i in issues)))
        if job.log_path:
            parts.append("<p><b>Log:</b> {}</p>".format(job.log_path))
        self.detail_view.setHtml("".join(parts))

    def _refresh_log(self):
        """Tail the selected job's log file.

        Reads from the last offset so a multi-gigabyte overnight log is not
        re-read every poll.
        """
        if not self._log_path or not os.path.isfile(self._log_path):
            return
        try:
            with open(self._log_path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._log_offset)
                new = fh.read()
                self._log_offset = fh.tell()
        except OSError:
            return
        if new:
            at_end = (self.log_view.verticalScrollBar().value()
                      >= self.log_view.verticalScrollBar().maximum() - 4)
            self.log_view.appendPlainText(new.rstrip("\n"))
            if at_end:
                self.log_view.verticalScrollBar().setValue(
                    self.log_view.verticalScrollBar().maximum())

    # -- job actions --

    def _add_job(self, kind):
        job = Job(kind=kind)
        template = store.get_template(
            store.get_setting("last_template", "Default") or "Default")
        job.apply_template(template)
        if edit_job(job, self):
            self.batch.add(job)
            self._refresh_table()
            self.table.selectRow(len(self.batch.jobs) - 1)
            self._update_actions()

    def _add_new_plot(self):
        self._add_job(NEW_PLOT)

    def _add_rephoto(self):
        self._add_job(REPHOTO)

    def _edit_selected(self):
        job = self._selected_job()
        if job is None:
            return
        if job.status == models.RUNNING:
            QtWidgets.QMessageBox.information(
                self, "Job is running",
                "Stop this job before editing it.")
            return
        if edit_job(job, self):
            if job.is_terminal():
                job.reset_for_run()
            self.batch.mark_dirty()
            self._refresh_table()

    def _duplicate_selected(self):
        job = self._selected_job()
        if job and self.batch.duplicate(job.job_id):
            self._refresh_table()

    def _remove_selected(self):
        job = self._selected_job()
        if job is None:
            return
        if job.status == models.RUNNING:
            QtWidgets.QMessageBox.information(
                self, "Job is running", "Stop this job before removing it.")
            return
        self.batch.remove(job.job_id)
        self._refresh_table()
        self._update_actions()

    def _move_selected(self, delta):
        job = self._selected_job()
        if job is None:
            return
        index = self.batch.jobs.index(job)
        self.batch.move(job.job_id, delta)
        self._refresh_table()
        self.table.selectRow(max(0, min(len(self.batch.jobs) - 1, index + delta)))

    def _apply_template(self):
        templates = store.load_templates()
        names = [t.name for t in templates]
        name, ok = QtWidgets.QInputDialog.getItem(
            self, "Apply template",
            "Apply these settings to every job in the batch.\n"
            "Per-plot georeferencing files are kept.", names, 0, False)
        if not ok:
            return
        template = next(t for t in templates if t.name == name)
        count = self.batch.apply_template_to_all(template)
        store.set_setting("last_template", name)
        self._refresh_table()
        self.statusBar().showMessage(
            "Applied '{}' to {} job(s).".format(name, count), 5000)

    def _scan_folder(self):
        from .scan_folder import scan_folder_dialog
        jobs = scan_folder_dialog(self)
        for job in jobs:
            self.batch.add(job)
        if jobs:
            self._refresh_table()
            self.statusBar().showMessage(
                "Added {} job(s) from folder scan.".format(len(jobs)), 5000)
        self._update_actions()

    # -- running --

    def _run(self):
        if self.scheduler.is_running():
            return

        issues = models.validate_batch(self.batch.jobs)
        blocking = [i for i in issues if i.severity == models.ERROR]
        if blocking:
            QtWidgets.QMessageBox.warning(
                self, "Cannot run yet",
                "Fix these before running:\n\n" + "\n\n".join(
                    "- " + i.message for i in blocking[:10]))
            return

        warnings = [i for i in issues if i.severity == models.WARNING]
        if warnings:
            reply = QtWidgets.QMessageBox.question(
                self, "Run anyway?",
                "The batch will run, but note:\n\n"
                + "\n\n".join("- " + i.message for i in warnings[:8])
                + "\n\nStart the batch?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes)
            if reply != QtWidgets.QMessageBox.Yes:
                return

        # A re-run should redo the jobs that did not finish, not the ones that
        # did -- reset only the non-successful ones.
        for job in self.batch.jobs:
            if job.status in (models.FAILED, models.CANCELLED):
                job.reset_for_run()

        self.scheduler.start(self.batch.jobs,
                             self.parallel_spin.value(),
                             self.stop_on_error.isChecked())
        self._update_actions()
        self._refresh_table()

    def _stop(self):
        if not self.scheduler.is_running():
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Stop batch",
            "Stop running jobs now, or let them finish first?\n\n"
            "Yes  - stop now (partial work is saved and can be resumed)\n"
            "No   - finish the jobs already started, queue nothing more",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel)
        if reply == QtWidgets.QMessageBox.Cancel:
            return
        self.scheduler.stop(cancel_running=(reply == QtWidgets.QMessageBox.Yes))
        self._update_actions()

    def _on_job_changed(self, job_id):
        for row, job in enumerate(self.batch.jobs):
            if job.job_id == job_id:
                self.table.blockSignals(True)
                self._fill_row(row, job)
                self.table.blockSignals(False)
                break
        self._update_overall()
        selected = self._selected_job()
        if selected and selected.job_id == job_id:
            if selected.log_path and selected.log_path != self._log_path:
                self._log_path = selected.log_path
                self._log_offset = 0
                self.log_view.clear()
            self._show_details(selected)

    def _on_batch_finished(self):
        self._update_actions()
        self._refresh_table()
        counts = {}
        for job in self.batch.jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        summary = ", ".join("{} {}".format(n, STATUS_TEXT.get(s, s).lower())
                            for s, n in sorted(counts.items()))
        self.statusBar().showMessage("Batch finished: " + summary)

        failed = counts.get(models.FAILED, 0)
        needs = counts.get(models.NEEDS_REFERENCING, 0)
        if failed or needs:
            QtWidgets.QMessageBox.information(
                self, "Batch finished",
                "{}\n\n{}{}".format(
                    summary,
                    "{} job(s) failed -- see the Notes column and each job's "
                    "log.\n".format(failed) if failed else "",
                    "{} job(s) stopped after the mesh and need referencing "
                    "information before they can be finished.".format(needs)
                    if needs else ""))

    def _on_parallelism_changed(self, value):
        self.batch.parallelism = value
        store.set_setting("parallelism", value)
        self.parallel_warning.setText(
            "Jobs will compete for the GPU" if value > scheduler.RECOMMENDED_MAX_PARALLEL
            else "")

    def _update_overall(self):
        total = sum(1 for j in self.batch.jobs if j.enabled)
        done = sum(1 for j in self.batch.jobs
                   if j.enabled and j.is_terminal())
        self.overall.setText("{} of {} finished".format(done, total)
                             if total else "")

    def _update_actions(self):
        running = self.scheduler.is_running()
        has_selection = self._selected_job() is not None
        has_jobs = bool(self.batch.jobs)

        self.run_button.setEnabled(not running and has_jobs)
        self.run_button.setText("Running..." if running else "Run batch")
        self.stop_button.setEnabled(running)
        self.parallel_spin.setEnabled(not running)

        for action in (self.act_new_plot, self.act_rephoto, self.act_scan,
                       self.act_template):
            action.setEnabled(not running)
        for action in (self.act_edit, self.act_duplicate, self.act_remove,
                       self.act_up, self.act_down):
            action.setEnabled(has_selection and not running)

    # -- files --

    def _open_batch(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open batch", "", store.BATCH_FILE_FILTER)
        if path:
            self._load_batch(path)

    def _load_batch(self, path):
        try:
            batch = Batch.load(path)
        except store.NewerSchemaWarning as warning:
            reply = QtWidgets.QMessageBox.question(
                self, "Newer batch file", str(warning) + "\n\nOpen it anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                return
            batch = warning.load_anyway()
        except store.BatchFileError as exc:
            QtWidgets.QMessageBox.warning(self, "Could not open batch", str(exc))
            return

        self.batch = batch
        self.parallel_spin.setValue(batch.parallelism)
        self.stop_on_error.setChecked(batch.stop_on_error)
        store.push_recent_batch(path)
        self._rebuild_recent_menu()
        self._refresh_table()
        self._update_actions()
        self.statusBar().showMessage("Opened {}".format(path), 5000)

    def _save_batch(self):
        if not self.batch.path:
            return self._save_batch_as()
        return self._write_batch(self.batch.path)

    def _save_batch_as(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save batch", "", store.BATCH_FILE_FILTER)
        return self._write_batch(path) if path else False

    def _write_batch(self, path):
        self.batch.parallelism = self.parallel_spin.value()
        self.batch.stop_on_error = self.stop_on_error.isChecked()
        try:
            saved = self.batch.save(path)
        except (store.BatchFileError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Could not save", str(exc))
            return False
        store.push_recent_batch(saved)
        self._rebuild_recent_menu()
        self.statusBar().showMessage("Saved {}".format(saved), 5000)
        return True

    def _rebuild_recent_menu(self):
        self.recent_menu.clear()
        paths = store.recent_batches()
        if not paths:
            action = self.recent_menu.addAction("(none)")
            action.setEnabled(False)
            return
        for path in paths:
            self.recent_menu.addAction(
                self._action(path, lambda checked=False, p=path: self._load_batch(p)))

    def closeEvent(self, event):
        if self.scheduler.is_running():
            reply = QtWidgets.QMessageBox.question(
                self, "Batch is running",
                "Jobs are still running. Stop them and close?\n\n"
                "Partial work is saved and can be resumed later.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
            self.scheduler.stop(cancel_running=True)

        if self.batch.is_dirty() and self.batch.jobs:
            reply = QtWidgets.QMessageBox.question(
                self, "Unsaved batch",
                "Save this batch before closing?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Yes)
            if reply == QtWidgets.QMessageBox.Cancel:
                event.ignore()
                return
            if reply == QtWidgets.QMessageBox.Yes and not self._save_batch():
                event.ignore()
                return
        event.accept()


def _format_elapsed(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "{}s".format(seconds)
    if seconds < 3600:
        return "{}m {:02d}s".format(seconds // 60, seconds % 60)
    return "{}h {:02d}m".format(seconds // 3600, (seconds % 3600) // 60)
