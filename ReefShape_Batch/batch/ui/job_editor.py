"""
Editor for a single batch job.

One dialog serves both job kinds. A new plot and a revisit share almost every
setting -- the same processing, the same exports, the same georeferencing
layout -- and differ only in where the project comes from and, for a revisit,
which chunk to align onto. Two separate dialogs would have been mostly
duplicate.

The re-photography fields are populated by probing the chosen project (see
batch/probe.py), so the reference chunk and damaged markers are picked from
real lists rather than typed from memory.
"""

from __future__ import annotations

import os

from ..qt import QtCore, QtWidgets, exec_
from .. import models, probe, store
from ..models import Job, NEW_PLOT, REPHOTO
from .widgets import (PathRow, CRSPicker, GeorefColumnsWidget,
                      CornerMarkersWidget, issue_summary_html)


class JobEditor(QtWidgets.QDialog):
    def __init__(self, job: Job, parent=None, templates=None):
        super().__init__(parent)
        self.job = job
        self.templates = templates or store.load_templates()
        self._project_info = None

        self.setWindowTitle("{} job".format(
            "New plot" if job.kind == NEW_PLOT else "Re-photography"))
        self.setMinimumSize(720, 640)

        outer = QtWidgets.QVBoxLayout(self)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        self.form = QtWidgets.QVBoxLayout(inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        self._build_kind_banner()
        self._build_project_section()
        self._build_rephoto_section()
        self._build_georef_section()
        self._build_processing_section()
        self._build_export_section()
        self.form.addStretch(1)

        # Validation is live: everything it checks is a filesystem stat or a
        # field comparison, so it can run on every keystroke and catch a
        # mistyped path now rather than eleven hours into a batch.
        self.issues_label = QtWidgets.QLabel()
        self.issues_label.setWordWrap(True)
        self.issues_label.setTextFormat(QtCore.Qt.RichText)
        outer.addWidget(self.issues_label)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.save_template_btn = buttons.addButton(
            "Save settings as template...", QtWidgets.QDialogButtonBox.ActionRole)
        self.save_template_btn.clicked.connect(self._save_as_template)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.ok_button = buttons.button(QtWidgets.QDialogButtonBox.Ok)

        self._load()
        self._revalidate()

    # -- construction --

    def _section(self, title):
        box = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QVBoxLayout(box)
        self.form.addWidget(box)
        return layout

    def _build_kind_banner(self):
        text = ("This plot has not been photographed before. A new Metashape "
                "project will be created from the photos."
                if self.job.kind == NEW_PLOT else
                "A repeat visit to an existing plot. The new photos are added "
                "to an existing project as a new chunk and aligned to an "
                "earlier timepoint, so the products line up.")
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: palette(mid); padding: 2px 0 6px 0;")
        self.form.addWidget(label)

    def _build_project_section(self):
        layout = self._section("Project")

        self.label_edit = QtWidgets.QLineEdit()
        self.label_edit.setPlaceholderText("Defaults to the project name")
        row = QtWidgets.QHBoxLayout()
        name_label = QtWidgets.QLabel("Job name:")
        name_label.setMinimumWidth(130)
        row.addWidget(name_label)
        row.addWidget(self.label_edit, 1)
        layout.addLayout(row)

        if self.job.kind == NEW_PLOT:
            self.project_row = PathRow(
                "Create project:", PathRow.SAVE_FILE,
                "Metashape project (*.psx)",
                "Where to create the new .psx")
        else:
            self.project_row = PathRow(
                "Existing project:", PathRow.OPEN_FILE,
                "Metashape project (*.psx)",
                "The plot's existing .psx")
        self.project_row.changed.connect(self._on_project_changed)
        layout.addWidget(self.project_row)

        self.photos_row = PathRow("Photo folder:", PathRow.FOLDER,
                                  placeholder="Folder of this visit's photos")
        self.photos_row.changed.connect(lambda _p: self._on_photos_changed())
        layout.addWidget(self.photos_row)

        self.photo_count = QtWidgets.QLabel("")
        self.photo_count.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.photo_count)

        self.chunk_edit = QtWidgets.QLineEdit()
        self.chunk_edit.setPlaceholderText(
            "Leave blank to use the photos' capture date (YYYYMMDD)")
        row = QtWidgets.QHBoxLayout()
        chunk_label = QtWidgets.QLabel("Chunk name:")
        chunk_label.setMinimumWidth(130)
        row.addWidget(chunk_label)
        row.addWidget(self.chunk_edit, 1)
        layout.addLayout(row)

    def _build_rephoto_section(self):
        self.rephoto_box = QtWidgets.QGroupBox("Align to earlier timepoint")
        layout = QtWidgets.QVBoxLayout(self.rephoto_box)
        self.form.addWidget(self.rephoto_box)
        self.rephoto_box.setVisible(self.job.kind == REPHOTO)

        row = QtWidgets.QHBoxLayout()
        ref_label = QtWidgets.QLabel("Reference chunk:")
        ref_label.setMinimumWidth(130)
        self.ref_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("Read project")
        self.refresh_btn.setToolTip(
            "Open the project in Metashape to list its chunks and markers")
        self.refresh_btn.clicked.connect(lambda: self._probe(force=True))
        row.addWidget(ref_label)
        row.addWidget(self.ref_combo, 1)
        row.addWidget(self.refresh_btn)
        layout.addLayout(row)
        self.ref_combo.currentIndexChanged.connect(self._on_reference_changed)

        self.ref_detail = QtWidgets.QLabel("")
        self.ref_detail.setStyleSheet("color: palette(mid);")
        self.ref_detail.setWordWrap(True)
        layout.addWidget(self.ref_detail)

        # What the earlier timepoint was processed at. Shown so the user can
        # match it before running: a revisit built at a different mesh quality
        # or orthomosaic resolution produces differences that are partly an
        # artifact of processing rather than of the reef.
        self.ref_settings = QtWidgets.QLabel("")
        self.ref_settings.setTextFormat(QtCore.Qt.RichText)
        self.ref_settings.setWordWrap(True)
        self.ref_settings.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 3px; padding: 6px;")
        layout.addWidget(self.ref_settings)

        layout.addWidget(QtWidgets.QLabel(
            "Damaged or moved markers (tick any target that shifted between "
            "visits -- they are kept but not trusted for alignment):"))
        self.markers_list = QtWidgets.QListWidget()
        self.markers_list.setMaximumHeight(130)
        self.markers_list.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection)
        layout.addWidget(self.markers_list)

    def _build_georef_section(self):
        # A revisit takes its georeferencing wholesale from the reference
        # chunk -- markers, scale and coordinate system all come across in the
        # alignment step. There is nothing here for the user to supply, and
        # offering the controls anyway would invite them to set something that
        # is then ignored.
        if self.job.kind == REPHOTO:
            self.georef_box = None
            self.georef_check = None
            self._georef_dependents = []
            note = QtWidgets.QLabel(
                "<b>Georeferencing</b><br>Taken from the reference chunk: its "
                "marker positions, scale and coordinate system are copied to "
                "this timepoint during alignment. Nothing to set here.")
            note.setWordWrap(True)
            note.setStyleSheet("color: palette(mid); padding: 4px 0;")
            self.form.addWidget(note)
            return

        layout = self._section("Georeferencing")

        self.georef_check = QtWidgets.QCheckBox(
            "Detect markers and apply scaling and georeferencing")
        self.georef_check.setToolTip(
            "Turn off if the chunk is already referenced, or if you intend to "
            "reference it by hand in Metashape.")
        self.georef_check.toggled.connect(self._on_georef_toggled)
        layout.addWidget(self.georef_check)

        row = QtWidgets.QHBoxLayout()
        target_label = QtWidgets.QLabel("Target type:")
        target_label.setMinimumWidth(130)
        self.target_combo = QtWidgets.QComboBox()
        for name, _value in models.TARGET_TYPES:
            self.target_combo.addItem(name)
        row.addWidget(target_label)
        row.addWidget(self.target_combo, 1)
        layout.addLayout(row)

        self.scalebar_row = PathRow("Scalebar file:", PathRow.OPEN_FILE,
                                    "Scalebar list (*.txt *.csv)")
        self.georef_file_row = PathRow("Georeferencing file:", PathRow.OPEN_FILE,
                                       "Georeferencing (*.csv *.txt)")
        self.scalebar_row.changed.connect(lambda _p: self._revalidate())
        self.georef_file_row.changed.connect(lambda _p: self._revalidate())
        layout.addWidget(self.scalebar_row)
        layout.addWidget(self.georef_file_row)

        self.columns_widget = GeorefColumnsWidget()
        self.corners_widget = CornerMarkersWidget()
        layout.addWidget(self.columns_widget)
        layout.addWidget(self.corners_widget)

        self._georef_dependents = [
            self.target_combo, self.scalebar_row, self.georef_file_row,
            self.columns_widget, self.corners_widget]

    def _build_processing_section(self):
        layout = self._section("Processing")

        # A revisit must be in the reference chunk's coordinate system --
        # anything else and the two timepoints do not overlay. So it is
        # reported, not offered.
        if self.job.kind == REPHOTO:
            self.crs_picker = None
            self.crs_note = QtWidgets.QLabel("Select a reference chunk above.")
            self.crs_note.setWordWrap(True)
            layout.addWidget(self.crs_note)
        else:
            self.crs_picker = CRSPicker()
            self.crs_note = None
            layout.addWidget(self.crs_picker)

        row = QtWidgets.QHBoxLayout()
        mesh_label = QtWidgets.QLabel("Mesh quality:")
        mesh_label.setMinimumWidth(130)
        self.mesh_combo = QtWidgets.QComboBox()
        for name, _downscale in models.MESH_QUALITIES:
            self.mesh_combo.addItem(name)
        row.addWidget(mesh_label)
        row.addWidget(self.mesh_combo)
        row.addStretch(1)
        layout.addLayout(row)

        self.preselect_check = QtWidgets.QCheckBox("Generic preselection")
        self.preselect_check.setToolTip(
            "Speeds up alignment. Turn off for photo sets with severe caustics.")
        self.colors_check = QtWidgets.QCheckBox("Calculate model colours")
        self.colors_check.setToolTip(
            "For visualisation only; does not affect the 2D exports.")
        layout.addWidget(self.preselect_check)
        layout.addWidget(self.colors_check)

        row = QtWidgets.QHBoxLayout()
        self.default_res_check = QtWidgets.QCheckBox(
            "Let Metashape choose the orthomosaic resolution")
        self.res_spin = QtWidgets.QDoubleSpinBox()
        self.res_spin.setDecimals(5)
        self.res_spin.setSingleStep(0.0001)
        self.res_spin.setMaximum(1.0)
        self.res_spin.setSuffix(" m")
        self.default_res_check.toggled.connect(
            lambda on: self.res_spin.setEnabled(not on))
        row.addWidget(self.default_res_check)
        row.addStretch(1)
        row.addWidget(QtWidgets.QLabel("Resolution:"))
        row.addWidget(self.res_spin)
        layout.addLayout(row)

    def _build_export_section(self):
        layout = self._section("Outputs")

        self.output_row = PathRow("Output folder:", PathRow.FOLDER,
                                  placeholder="Defaults to the project folder")
        layout.addWidget(self.output_row)

        self.report_check = QtWidgets.QCheckBox("Processing report (PDF)")
        self.gis_check = QtWidgets.QCheckBox(
            "GIS outputs (orthomosaic, DEM, boundary shapefile)")
        self.taglab_check = QtWidgets.QCheckBox("TagLab outputs")
        self.taglab_check.setToolTip(
            "Requires a boundary polygon. If none can be created, these are "
            "skipped with a warning and the rest of the outputs still run.")
        for box in (self.report_check, self.gis_check, self.taglab_check):
            layout.addWidget(box)

    # -- data in and out --

    def _load(self):
        job = self.job
        self.label_edit.setText(job.label)
        self.project_row.setPath(job.project_path)
        self.photos_row.setPath(job.photo_folders[0] if job.photo_folders else "")
        self.chunk_edit.setText(job.chunk_name)

        if self.georef_check is not None:
            self.georef_check.setChecked(job.georef.enabled)
            index = next((i for i, (_n, v) in enumerate(models.TARGET_TYPES)
                          if v == job.georef.target_type), 0)
            self.target_combo.setCurrentIndex(index)
            self.scalebar_row.setPath(job.georef.scalebar_path)
            self.georef_file_row.setPath(job.georef.georef_path)
            self.columns_widget.setValues(job.georef)
            self.corners_widget.setValues(job.georef.corner_markers)

        if self.crs_picker is not None:
            self.crs_picker.setWkt(job.processing.crs_wkt,
                                   job.processing.crs_label)
        self.mesh_combo.setCurrentIndex(
            max(0, [n for n, _ in models.MESH_QUALITIES].index(
                job.processing.mesh_quality)
                if job.processing.mesh_quality in
                [n for n, _ in models.MESH_QUALITIES] else 2))
        self.preselect_check.setChecked(job.processing.generic_preselection)
        self.colors_check.setChecked(job.processing.vertex_colors)
        self.default_res_check.setChecked(job.processing.use_default_resolution)
        self.res_spin.setValue(job.processing.ortho_resolution)
        self.res_spin.setEnabled(not job.processing.use_default_resolution)

        self.output_row.setPath(job.export.output_dir)
        self.report_check.setChecked(job.export.report)
        self.gis_check.setChecked(job.export.gis_outputs)
        self.taglab_check.setChecked(job.export.taglab_outputs)

        if self.georef_check is not None:
            self._on_georef_toggled(job.georef.enabled)
        else:
            # A revisit gets its markers, scale and CRS from the reference
            # chunk during alignment, so the workflow must not try to detect
            # and reference them again.
            job.georef.enabled = False
        self._on_photos_changed()

        for widget in (self.label_edit, self.chunk_edit):
            widget.textChanged.connect(lambda _t: self._revalidate())

        # Keep the reference comparison honest as the user changes settings,
        # otherwise it would show a tick against a value they have since
        # changed away from.
        if self.job.kind == REPHOTO:
            self.mesh_combo.currentIndexChanged.connect(
                lambda _i: self._refresh_reference_settings())
            self.res_spin.valueChanged.connect(
                lambda _v: self._refresh_reference_settings())
            self.default_res_check.toggled.connect(
                lambda _c: self._refresh_reference_settings())

        if self.job.kind == REPHOTO and job.project_path:
            self._probe()

    def _apply(self):
        """Write the form back into the job."""
        job = self.job
        job.label = self.label_edit.text().strip()
        job.project_path = self.project_row.path()
        job.photo_folders = [self.photos_row.path()] if self.photos_row.path() else []
        job.chunk_name = self.chunk_edit.text().strip()

        if self.georef_check is not None:
            job.georef.enabled = self.georef_check.isChecked()
            job.georef.target_type = models.TARGET_TYPES[
                self.target_combo.currentIndex()][1]
            job.georef.scalebar_path = self.scalebar_row.path()
            job.georef.georef_path = self.georef_file_row.path()
            for name, value in self.columns_widget.values().items():
                setattr(job.georef, name, value)
            job.georef.corner_markers = self.corners_widget.values()
        else:
            # Revisit: alignment supplies the referencing, so the workflow's
            # own detect-and-reference pass must stay off. The target type is
            # still needed -- marker detection runs on the new photos to find
            # the targets the reference positions are matched onto.
            job.georef.enabled = False

        if self.crs_picker is not None:
            job.processing.crs_wkt = self.crs_picker.wkt()
            job.processing.crs_label = self.crs_picker.label()
        job.processing.mesh_quality = self.mesh_combo.currentText()
        job.processing.generic_preselection = self.preselect_check.isChecked()
        job.processing.vertex_colors = self.colors_check.isChecked()
        job.processing.use_default_resolution = self.default_res_check.isChecked()
        job.processing.ortho_resolution = self.res_spin.value()

        job.export.output_dir = self.output_row.path()
        job.export.report = self.report_check.isChecked()
        job.export.gis_outputs = self.gis_check.isChecked()
        job.export.taglab_outputs = self.taglab_check.isChecked()

        if job.kind == REPHOTO:
            job.reference_chunk = self.ref_combo.currentText()
            job.damaged_markers = [
                self.markers_list.item(i).text()
                for i in range(self.markers_list.count())
                if self.markers_list.item(i).checkState() == QtCore.Qt.Checked]

    # -- slots --

    def _on_georef_toggled(self, enabled):
        for widget in self._georef_dependents:
            widget.setEnabled(enabled)
        self._revalidate()

    def _on_project_changed(self, path):
        # Offer a job name and chunk name derived from the project, but never
        # overwrite something the user typed.
        if not self.label_edit.text().strip() and path:
            self.label_edit.setText(
                os.path.splitext(os.path.basename(path))[0])
        if self.job.kind == REPHOTO:
            self._probe()
        self._revalidate()

    def _on_photos_changed(self):
        folder = self.photos_row.path()
        if not folder:
            self.photo_count.setText("")
        else:
            count = models.count_images(folder)
            self.photo_count.setText(
                "  {} image{} found".format(count, "" if count == 1 else "s")
                if count else "  No images found in this folder")
        self._revalidate()

    def _on_reference_changed(self):
        self.markers_list.clear()
        if not self._project_info:
            return
        label = self.ref_combo.currentText()
        chunk = self._project_info.chunk(label)
        if not chunk:
            return

        self.ref_detail.setText(
            "{} cameras, {} markers, {} scalebars{}".format(
                chunk.get("n_cameras", 0), chunk.get("n_markers", 0),
                chunk.get("n_scalebars", 0),
                "" if chunk.get("has_orthomosaic")
                else "  -- note: this chunk has no orthomosaic, so it may not "
                     "be fully processed"))

        self._adopt_reference_crs(chunk)
        self._show_reference_settings(chunk)

        # Scalebar targets are left out entirely. A scalebar is repositioned
        # on every visit, so its markers are never in the same place twice;
        # they play no part in alignment, and listing them as candidates for
        # "did this move?" invites a pointless decision whose answer is always
        # yes.
        previously = set(self.job.damaged_markers or [])
        listed = 0
        for marker in chunk.get("markers", []):
            if marker.get("in_scalebar"):
                continue
            item = QtWidgets.QListWidgetItem(marker.get("label", ""))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked
                               if marker.get("label") in previously
                               else QtCore.Qt.Unchecked)
            if not marker.get("reference_enabled"):
                item.setToolTip("Not enabled for referencing in this chunk, "
                                "so it will not be used for alignment anyway.")
            self.markers_list.addItem(item)
            listed += 1

        hidden = len(chunk.get("scalebar_markers", []))
        if not listed:
            item = QtWidgets.QListWidgetItem(
                "No alignment markers in this chunk")
            item.setFlags(QtCore.Qt.NoItemFlags)
            self.markers_list.addItem(item)
        self.markers_list.setToolTip(
            "{} scalebar target(s) are not listed: scalebars are moved between "
            "visits, so they are never used to align timepoints."
            .format(hidden) if hidden else "")
        self._revalidate()

    def _refresh_reference_settings(self):
        """Redraw the reference comparison after a settings change."""
        if not self._project_info:
            return
        chunk = self._project_info.chunk(self.ref_combo.currentText())
        if chunk:
            self._show_reference_settings(chunk)

    def _adopt_reference_crs(self, chunk):
        """Take the coordinate system from the reference chunk.

        Not a choice: a revisit that is not in the earlier timepoint's
        coordinate system will not overlay it, which defeats the point. So the
        CRS is reported rather than offered, and copied into the job.
        """
        if self.crs_note is None:
            return
        wkt = chunk.get("crs_wkt")
        name = chunk.get("crs_name") or "unknown"
        if wkt:
            self.job.processing.crs_wkt = wkt
            self.job.processing.crs_label = name
            self.crs_note.setText(
                "<span style='color:#1a7f37;'>&#10003; Reference chunk is in "
                "<b>{}</b> coordinates. This timepoint will use the same "
                "system.</span>".format(name))
        else:
            self.crs_note.setText(
                "<span style='color:#b8860b;'>The reference chunk has no "
                "coordinate system set. Alignment will still work, but the "
                "result will not be georeferenced.</span>")

    def _show_reference_settings(self, chunk):
        """Show how the earlier timepoint was processed, and flag mismatches.

        Comparing two timepoints only means something if they were built the
        same way, so the settings that affect the comparison are put in front
        of the user before they run, with anything that differs called out.
        """
        ref_quality = chunk.get("mesh_quality")
        ref_res = chunk.get("orthomosaic_resolution")
        rows = []

        chosen_quality = self.mesh_combo.currentText()
        if ref_quality:
            match = (ref_quality == chosen_quality)
            rows.append(self._compare_row(
                "Mesh quality", ref_quality, chosen_quality, match))
        else:
            rows.append("<tr><td>Mesh quality</td><td colspan='2'>"
                        "<i>not recorded (chunk has no mesh)</i></td></tr>")

        if ref_res:
            chosen_res = (0.0 if self.default_res_check.isChecked()
                          else self.res_spin.value())
            chosen_text = ("chosen by Metashape"
                           if self.default_res_check.isChecked()
                           else "{:.5f} m".format(chosen_res))
            # Metashape stores resolution as a float carrying accumulated
            # rounding (0.0005000000000033308), so compare with a tolerance
            # rather than for equality.
            match = (not self.default_res_check.isChecked()
                     and abs(chosen_res - ref_res) < 1e-9)
            rows.append(self._compare_row(
                "Orthomosaic resolution", "{:.5f} m".format(ref_res),
                chosen_text, match))
        else:
            rows.append("<tr><td>Orthomosaic resolution</td><td colspan='2'>"
                        "<i>not recorded (chunk has no orthomosaic)</i>"
                        "</td></tr>")

        self.ref_settings.setText(
            "<b>Reference timepoint was processed at</b>"
            "<table cellspacing='0' cellpadding='3' width='100%'>"
            "<tr><td></td><td><b>Reference</b></td><td><b>This job</b></td></tr>"
            "{}</table>".format("".join(rows)))

    @staticmethod
    def _compare_row(name, reference_value, chosen_value, match):
        colour = "#1a7f37" if match else "#b8860b"
        mark = "&#10003;" if match else "&#9888;"
        return ("<tr><td>{}</td><td>{}</td>"
                "<td style='color:{};'>{} {}</td></tr>".format(
                    name, reference_value, colour, mark, chosen_value))

    def _probe(self, force=False):
        """Read the project's chunks and markers via a headless Metashape."""
        path = self.project_row.path()
        if not path or not os.path.isfile(path):
            return

        self.ref_detail.setText("Reading project...")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtWidgets.QApplication.processEvents()
        try:
            self._project_info = probe.probe_project(path, use_cache=not force)
        except probe.ProbeError as exc:
            self._project_info = None
            self.ref_detail.setText("")
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Could not read project", str(exc))
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.ref_combo.blockSignals(True)
        self.ref_combo.clear()
        self.ref_combo.addItems(self._project_info.chunk_labels())
        self.ref_combo.blockSignals(False)

        wanted = (self.job.reference_chunk
                  or self._project_info.suggested_reference_chunk())
        if wanted:
            index = self.ref_combo.findText(wanted)
            if index >= 0:
                self.ref_combo.setCurrentIndex(index)
        self._on_reference_changed()

    def _save_as_template(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Save as template",
            "Name for this set of processing and export settings:")
        if not ok or not name.strip():
            return
        self._apply()
        store.upsert_template(store.template_from_job(self.job, name.strip()))
        QtWidgets.QMessageBox.information(
            self, "Template saved",
            "Saved '{}'. Apply it to other jobs from the batch window."
            .format(name.strip()))

    def _revalidate(self):
        self._apply()
        issues = models.validate_job(self.job)
        self.issues_label.setText(issue_summary_html(issues))
        blocking = [i for i in issues if i.severity == models.ERROR]
        self.ok_button.setEnabled(not blocking)

    def accept(self):
        self._apply()
        super().accept()


def edit_job(job: Job, parent=None) -> bool:
    """Show the editor for `job`. Returns True if the user accepted."""
    editor = JobEditor(job, parent)
    return exec_(editor) == QtWidgets.QDialog.Accepted
