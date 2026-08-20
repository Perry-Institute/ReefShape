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
                      issue_summary_html)


class JobEditor(QtWidgets.QDialog):
    def __init__(self, job: Job, parent=None, templates=None):
        super().__init__(parent)
        self.job = job
        self.templates = templates or store.load_templates()
        self._project_info = None
        # Set from the probe when a reference chunk is chosen: a revisit
        # inherits the earlier timepoint's boundary during alignment, which
        # overrides whatever boundary source is selected.
        self._reference_has_boundary = False

        # Set before any widget exists. Populating the form fires the same
        # signals a user's clicks would, and those handlers write the form
        # back into the job -- which, mid-load, would write the *unpopulated*
        # widgets over the settings being loaded. Guarding on this is what
        # keeps loading a one-way operation. See _load and _apply.
        self._loading = False

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

        self._loading = True
        try:
            self._load()
        finally:
            self._loading = False
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

        self.icp_check = QtWidgets.QCheckBox(
            "Try alignment without permanent markers (ICP)")
        self.icp_check.setToolTip(
            "For plots with no permanent corner targets. Each visit is "
            "georeferenced and scaled from its own temporary markers, which "
            "usually leaves the two timepoints within 0.1-2 m of each other; "
            "ICP then matches the reef surfaces themselves to close that gap.\n\n"
            "Leave off wherever permanent markers exist -- matching them is "
            "exact, much faster, and can be checked independently.")
        self.icp_check.toggled.connect(self._on_icp_toggled)
        layout.addWidget(self.icp_check)

        self.icp_box = self._build_icp_settings()
        layout.addWidget(self.icp_box)

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
            "<b>Reference</b> anchors the new timepoint on that target. "
            "<b>Damaged</b> keeps the target but stops trusting it, for "
            "anything that shifted between visits."))

        self.show_all_markers = QtWidgets.QCheckBox("List all markers")
        self.show_all_markers.setToolTip(
            "By default, targets that only carry scale (part of a scalebar, "
            "with no georeference information) are hidden -- scalebars are "
            "repositioned every visit and are never used to align timepoints.\n\n"
            "Tick this to see every marker in the chunk regardless.")
        self.show_all_markers.toggled.connect(
            lambda _on: self._on_reference_changed())
        layout.addWidget(self.show_all_markers)

        self.markers_table = QtWidgets.QTableWidget(0, 3)
        self.markers_table.setHorizontalHeaderLabels(
            ["Marker", "Reference", "Damaged"])
        self.markers_table.verticalHeader().setVisible(False)
        self.markers_table.setMaximumHeight(180)
        self.markers_table.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection)
        self.markers_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.markers_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.markers_table.itemChanged.connect(self._on_marker_toggled)
        layout.addWidget(self.markers_table)

        self.marker_summary = QtWidgets.QLabel("")
        self.marker_summary.setWordWrap(True)
        layout.addWidget(self.marker_summary)

    def _build_icp_settings(self):
        """ICP tuning. Defaults match 03_align_chunks_ICP.py."""
        box = QtWidgets.QGroupBox("ICP settings")
        grid = QtWidgets.QGridLayout(box)

        note = QtWidgets.QLabel(
            "ICP runs after this timepoint's mesh is built and before the DEM, "
            "so the mesh can be used for matching and the rasters are then "
            "generated in the corrected position.")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        grid.addWidget(note, 0, 0, 1, 4)

        self.icp_moving_combo = QtWidgets.QComboBox()
        self.icp_master_combo = QtWidgets.QComboBox()
        for combo in (self.icp_moving_combo, self.icp_master_combo):
            for label, _value in models.ICP_SOURCES:
                combo.addItem(label)
        grid.addWidget(QtWidgets.QLabel("This timepoint:"), 1, 0)
        grid.addWidget(self.icp_moving_combo, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Reference:"), 1, 2)
        grid.addWidget(self.icp_master_combo, 1, 3)

        self.icp_scale_spin = QtWidgets.QDoubleSpinBox()
        self.icp_scale_spin.setDecimals(4)
        self.icp_scale_spin.setRange(0.0001, 10000.0)
        self.icp_scale_spin.setToolTip(
            "Size of the reference relative to this timepoint. Both visits "
            "are scaled in metres, so this is 1.0 unless something is wrong.")
        grid.addWidget(QtWidgets.QLabel("Scale ratio:"), 2, 0)
        grid.addWidget(self.icp_scale_spin, 2, 1)

        self.icp_res_spin = QtWidgets.QDoubleSpinBox()
        self.icp_res_spin.setDecimals(4)
        self.icp_res_spin.setRange(0.0001, 10.0)
        self.icp_res_spin.setSingleStep(0.005)
        self.icp_res_spin.setSuffix(" m")
        self.icp_res_spin.setToolTip(
            "Approximate spacing between points in the reference. 0.01 suits "
            "mesh-based reef alignment; lower for sub-centimetre work once "
            "roughly aligned, higher for a sparse tie-point first pass.")
        grid.addWidget(QtWidgets.QLabel("Target resolution:"), 2, 2)
        grid.addWidget(self.icp_res_spin, 2, 3)

        self.icp_initial_check = QtWidgets.QCheckBox(
            "Use initial alignment (skip global registration)")
        self.icp_initial_check.setToolTip(
            "Recommended. Starts ICP from where the two timepoints already "
            "sit. Untick only if they are wildly misaligned -- global feature "
            "matching is unreliable on this kind of data.")
        self.icp_crop_check = QtWidgets.QCheckBox(
            "Crop to overlap after coarse pass")
        self.icp_crop_check.setToolTip(
            "Drops areas only one survey covered, which would otherwise be "
            "matched to whatever is nearest and skew the fit. Recommended.")
        self.icp_gicp_check = QtWidgets.QCheckBox(
            "Add Generalized ICP refinement")
        self.icp_gicp_check.setToolTip(
            "A further pass using plane-to-plane matching. Slower, but "
            "typically the tightest final precision on noisy surfaces.")
        grid.addWidget(self.icp_initial_check, 3, 0, 1, 2)
        grid.addWidget(self.icp_crop_check, 3, 2, 1, 2)
        grid.addWidget(self.icp_gicp_check, 4, 0, 1, 2)

        warn_label = QtWidgets.QLabel("Warn if fit is worse than:")
        self.icp_fitness_spin = QtWidgets.QDoubleSpinBox()
        self.icp_fitness_spin.setDecimals(2)
        self.icp_fitness_spin.setRange(0.0, 1.0)
        self.icp_fitness_spin.setSingleStep(0.05)
        self.icp_fitness_spin.setPrefix("fitness ")
        self.icp_rmse_spin = QtWidgets.QDoubleSpinBox()
        self.icp_rmse_spin.setDecimals(3)
        self.icp_rmse_spin.setRange(0.001, 10.0)
        self.icp_rmse_spin.setSingleStep(0.01)
        self.icp_rmse_spin.setPrefix("RMSE ")
        self.icp_rmse_spin.setSuffix(" m")
        for widget in (self.icp_fitness_spin, self.icp_rmse_spin):
            widget.setToolTip(
                "ICP always returns a transform, even a bad one, and with no "
                "permanent markers there is nothing independent to check it "
                "against. Outside these bounds the job still completes, but "
                "is flagged so you know to look at the result.")
        grid.addWidget(warn_label, 5, 0)
        grid.addWidget(self.icp_fitness_spin, 5, 1)
        grid.addWidget(self.icp_rmse_spin, 5, 2)

        self.icp_deps_label = QtWidgets.QLabel("")
        self.icp_deps_label.setWordWrap(True)
        grid.addWidget(self.icp_deps_label, 6, 0, 1, 4)

        return box

    def _on_icp_toggled(self, enabled):
        """Show the georeferencing panel only when ICP needs it."""
        self.icp_box.setVisible(enabled)
        if self.georef_box is not None:
            self.georef_box.setVisible(enabled)
        if self.georef_note is not None:
            self.georef_note.setVisible(not enabled)
        if enabled and self.georef_check is not None:
            # ICP needs this timepoint referenced from its own targets, which
            # is the whole reason the panel is back.
            self.georef_check.setChecked(True)
        if enabled:
            self._check_icp_dependencies()
        self._on_boundary_changed()
        self._revalidate()

    def _check_icp_dependencies(self):
        """Warn up front if ICP's third-party packages are not installed yet.

        pip runs on first use and can take several minutes. Finding that out
        when the queue is half way through an overnight run is worse than
        being told now.
        """
        import importlib.util
        missing = [name for name in ("open3d", "scipy", "numpy")
                   if importlib.util.find_spec(name) is None]
        if missing:
            self.icp_deps_label.setText(
                "<span style='color:#b8860b;'>ICP needs {} which "
                "{} not installed yet. The first ICP job will install {} "
                "automatically -- expect it to sit for a few minutes before "
                "processing starts.</span>".format(
                    ", ".join(missing), "is" if len(missing) == 1 else "are",
                    "it" if len(missing) == 1 else "them"))
        else:
            self.icp_deps_label.setText(
                "<span style='color:#1a7f37;'>&#10003; ICP dependencies are "
                "installed.</span>")

    def _build_georef_section(self):
        # For a revisit the panel is built but hidden: marker alignment takes
        # its georeferencing wholesale from the reference chunk, so there is
        # nothing to supply. ICP is the exception -- with only temporary
        # targets, each visit is referenced and scaled from its own, so the
        # panel comes back when ICP is selected. See _on_icp_toggled.
        if self.job.kind == REPHOTO:
            self.georef_note = QtWidgets.QLabel(
                "<b>Georeferencing</b><br>Taken from the reference chunk: its "
                "marker positions, scale and coordinate system are applied to "
                "this timepoint during alignment. Nothing to set here.")
            self.georef_note.setWordWrap(True)
            self.georef_note.setStyleSheet("color: palette(mid); padding: 4px 0;")
            self.form.addWidget(self.georef_note)
        else:
            self.georef_note = None

        self.georef_box = QtWidgets.QGroupBox("Georeferencing")
        layout = QtWidgets.QVBoxLayout(self.georef_box)
        self.form.addWidget(self.georef_box)

        if self.job.kind == REPHOTO:
            hint = QtWidgets.QLabel(
                "ICP matches surfaces, not markers, so this timepoint needs "
                "its own georeferencing and scale from the temporary targets "
                "used on this visit.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: palette(mid);")
            layout.addWidget(hint)

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
        layout.addWidget(self.columns_widget)

        # The plot outline is derived from the georeferenced markers
        # themselves (convex hull), so there is no corner arrangement to
        # configure any more.
        self._georef_dependents = [
            self.target_combo, self.scalebar_row, self.georef_file_row,
            self.columns_widget]

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

        row = QtWidgets.QHBoxLayout()
        boundary_label = QtWidgets.QLabel("Plot boundary:")
        boundary_label.setMinimumWidth(130)
        self.boundary_combo = QtWidgets.QComboBox()
        for name, _value in models.BOUNDARY_SOURCES:
            self.boundary_combo.addItem(name)
        self.boundary_combo.setToolTip(
            "Corner markers is the best option wherever permanent targets "
            "exist: the boundary is tied to the plot, so it is identical "
            "every visit and timepoints stay comparable.\n\n"
            "Photo coverage follows where the photographer swam, so it "
            "shifts between visits.\n\n"
            "Choosing no boundary also disables TagLab outputs, which must "
            "be clipped to the plot.")
        self.boundary_combo.currentIndexChanged.connect(
            lambda _i: self._on_boundary_changed())
        row.addWidget(boundary_label)
        row.addWidget(self.boundary_combo, 1)
        layout.addLayout(row)

        self.boundary_note = QtWidgets.QLabel("")
        self.boundary_note.setWordWrap(True)
        layout.addWidget(self.boundary_note)

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

        if self.crs_picker is not None:
            self.crs_picker.setWkt(job.processing.crs_wkt,
                                   job.processing.crs_label)
        self.mesh_combo.setCurrentIndex(
            max(0, [n for n, _ in models.MESH_QUALITIES].index(
                job.processing.mesh_quality)
                if job.processing.mesh_quality in
                [n for n, _ in models.MESH_QUALITIES] else 2))
        self.boundary_combo.setCurrentIndex(_boundary_index(
            job.processing.boundary_source))
        self.preselect_check.setChecked(job.processing.generic_preselection)
        self.colors_check.setChecked(job.processing.vertex_colors)
        self.default_res_check.setChecked(job.processing.use_default_resolution)
        self.res_spin.setValue(job.processing.ortho_resolution)
        self.res_spin.setEnabled(not job.processing.use_default_resolution)

        self.output_row.setPath(job.export.output_dir)
        self.report_check.setChecked(job.export.report)
        self.gis_check.setChecked(job.export.gis_outputs)
        self.taglab_check.setChecked(job.export.taglab_outputs)

        if job.kind == REPHOTO:
            self.icp_check.setChecked(job.icp.enabled)
            self.icp_moving_combo.setCurrentIndex(_source_index(job.icp.moving_source))
            self.icp_master_combo.setCurrentIndex(_source_index(job.icp.master_source))
            self.icp_scale_spin.setValue(job.icp.scale_ratio)
            self.icp_res_spin.setValue(job.icp.target_resolution)
            self.icp_initial_check.setChecked(job.icp.use_initial_alignment)
            self.icp_crop_check.setChecked(job.icp.crop_to_overlap)
            self.icp_gicp_check.setChecked(job.icp.use_generalized_icp)
            self.icp_fitness_spin.setValue(job.icp.min_fitness)
            self.icp_rmse_spin.setValue(job.icp.max_rmse)
            # Not connected via toggled, since setChecked above is a no-op when
            # the value already matches the default.
            self._on_icp_toggled(job.icp.enabled)

        self._on_georef_toggled(job.georef.enabled)
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
        """Write the form back into the job.

        Does nothing while the form is being populated: the signals that fire
        during loading would otherwise write half-filled widgets over the
        settings being loaded, silently resetting the job to defaults.
        """
        if self._loading:
            return
        job = self.job
        job.label = self.label_edit.text().strip()
        job.project_path = self.project_row.path()
        job.photo_folders = [self.photos_row.path()] if self.photos_row.path() else []
        job.chunk_name = self.chunk_edit.text().strip()

        job.georef.target_type = models.TARGET_TYPES[
            self.target_combo.currentIndex()][1]
        job.georef.scalebar_path = self.scalebar_row.path()
        job.georef.georef_path = self.georef_file_row.path()
        for name, value in self.columns_widget.values().items():
            setattr(job.georef, name, value)

        if job.kind == REPHOTO:
            job.icp.enabled = self.icp_check.isChecked()
            job.icp.moving_source = models.ICP_SOURCES[
                self.icp_moving_combo.currentIndex()][1]
            job.icp.master_source = models.ICP_SOURCES[
                self.icp_master_combo.currentIndex()][1]
            job.icp.scale_ratio = self.icp_scale_spin.value()
            job.icp.target_resolution = self.icp_res_spin.value()
            job.icp.use_initial_alignment = self.icp_initial_check.isChecked()
            job.icp.crop_to_overlap = self.icp_crop_check.isChecked()
            job.icp.use_generalized_icp = self.icp_gicp_check.isChecked()
            job.icp.min_fitness = self.icp_fitness_spin.value()
            job.icp.max_rmse = self.icp_rmse_spin.value()

            # Marker alignment supplies the referencing wholesale, so the
            # workflow's own detect-and-reference pass must stay off. ICP is
            # the exception: it needs this timepoint referenced from its own
            # temporary targets before the surfaces can be matched.
            job.georef.enabled = (self.icp_check.isChecked()
                                  and self.georef_check.isChecked())
        else:
            job.georef.enabled = self.georef_check.isChecked()

        if self.crs_picker is not None:
            job.processing.crs_wkt = self.crs_picker.wkt()
            job.processing.crs_label = self.crs_picker.label()
        job.processing.mesh_quality = self.mesh_combo.currentText()
        job.processing.boundary_source = models.BOUNDARY_SOURCES[
            self.boundary_combo.currentIndex()][1]
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
            # Labels come from item data, not the visible text -- the text
            # carries a "(scalebar only)" style annotation.
            #
            # Selections for markers currently filtered out of the table are
            # preserved, so toggling "List all markers" cannot silently change
            # the job.
            visible = set()
            reference, damaged = [], []
            for row in range(self.markers_table.rowCount()):
                name_item = self.markers_table.item(row, 0)
                label = name_item.data(QtCore.Qt.UserRole) if name_item else None
                if not label:
                    continue
                visible.add(label)
                ref_item = self.markers_table.item(row, 1)
                dmg_item = self.markers_table.item(row, 2)
                if ref_item and ref_item.checkState() == QtCore.Qt.Checked:
                    reference.append(label)
                    if dmg_item and dmg_item.checkState() == QtCore.Qt.Checked:
                        damaged.append(label)

            if self.markers_table.rowCount():
                job.reference_markers = reference + [
                    m for m in (job.reference_markers or []) if m not in visible]
                job.damaged_markers = damaged + [
                    m for m in (job.damaged_markers or []) if m not in visible]

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

    def _on_boundary_changed(self):
        """Spell out the consequence of turning the boundary off."""
        # A revisit copies the earlier timepoint's boundary during alignment,
        # and the workflow keeps a boundary that already exists rather than
        # replacing it -- so the selection below would have no effect.
        if self._reference_has_boundary and not self.job.icp.enabled:
            self.boundary_combo.setEnabled(False)
            self.boundary_note.setText(
                "<span style='color:#1a7f37;'>The reference chunk's boundary "
                "will be copied to this timepoint during alignment and used "
                "for the exports, so the two plots cover the same ground. "
                "This setting will not be used.</span>")
            self._revalidate()
            return

        self.boundary_combo.setEnabled(True)
        source = models.BOUNDARY_SOURCES[
            self.boundary_combo.currentIndex()][1]
        if source == models.BOUNDARY_NONE:
            self.boundary_note.setText(
                "<span style='color:#b8860b;'>No boundary shapefile will be "
                "exported, and TagLab outputs will be skipped -- they must be "
                "clipped to the plot boundary.</span>")
        elif source == models.BOUNDARY_PHOTOS:
            self.boundary_note.setText(
                "<span style='color:palette(mid);'>The boundary will follow "
                "the area photographed, which shifts between visits.</span>")
        else:
            self.boundary_note.setText("")
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
        self.markers_table.setRowCount(0)
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
        self._reference_has_boundary = bool(chunk.get("has_outer_boundary"))
        self._on_boundary_changed()

        # Hide targets that carry scale but no georeference -- a scalebar is
        # repositioned every visit, so asking whether one moved has only one
        # possible answer.
        #
        # Crucially this is not "hide scalebar markers". Where no permanent
        # corner markers are installed, people place temporary targets at the
        # corners and use them for both scaling and georeferencing. Those are
        # double-duty: part of a scalebar *and* carrying real reference
        # information, and they are exactly the control points alignment
        # depends on. reference_enabled is what tells the two apart, and it is
        # the same flag the alignment itself filters on.
        show_all = self.show_all_markers.isChecked()
        damaged = set(self.job.damaged_markers or [])
        # An empty reference_markers list means "use the chunk's own enabled
        # flags", so the first time a chunk is shown the ticks reflect how
        # alignment would behave with no intervention.
        chosen = set(self.job.reference_markers or [])
        use_defaults = not chosen

        rows = []
        hidden = 0
        for marker in chunk.get("markers", []):
            scaling_only = (marker.get("in_scalebar")
                            and not marker.get("reference_enabled"))
            if scaling_only and not show_all:
                hidden += 1
                continue
            rows.append((marker, scaling_only))

        self.markers_table.blockSignals(True)
        self.markers_table.setRowCount(len(rows))
        for row, (marker, scaling_only) in enumerate(rows):
            label = marker.get("label", "")

            name_item = QtWidgets.QTableWidgetItem(label)
            name_item.setData(QtCore.Qt.UserRole, label)
            if scaling_only:
                name_item.setText(label + "   (scalebar only)")
                name_item.setToolTip(
                    "Part of a scalebar with no georeference information. "
                    "Tick Reference to anchor on it anyway.")
            elif marker.get("in_scalebar"):
                name_item.setText(label + "   (georeferenced + scalebar)")
                name_item.setToolTip(
                    "A temporary target doing double duty: it carries scale "
                    "and georeference information, so it does anchor the "
                    "alignment.")
            self.markers_table.setItem(row, 0, name_item)

            is_reference = (marker.get("reference_enabled", False)
                            if use_defaults else label in chosen)

            ref_item = QtWidgets.QTableWidgetItem()
            ref_item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            ref_item.setCheckState(QtCore.Qt.Checked if is_reference
                                   else QtCore.Qt.Unchecked)
            ref_item.setToolTip(
                "Use this target's position from the earlier timepoint to "
                "anchor the new one.")
            self.markers_table.setItem(row, 1, ref_item)

            dmg_item = QtWidgets.QTableWidgetItem()
            dmg_item.setCheckState(QtCore.Qt.Checked if label in damaged
                                   else QtCore.Qt.Unchecked)
            # Damage only means anything for a marker that is anchoring the
            # alignment; on one that is not, the tick would have no effect and
            # would only suggest it did.
            dmg_item.setFlags(QtCore.Qt.ItemIsUserCheckable
                              | (QtCore.Qt.ItemIsEnabled if is_reference
                                 else QtCore.Qt.NoItemFlags))
            dmg_item.setToolTip(
                "This target moved between visits: keep it, but do not let it "
                "pull the alignment." if is_reference else
                "Only applies to markers used as a reference.")
            self.markers_table.setItem(row, 2, dmg_item)

        self.markers_table.blockSignals(False)

        self.show_all_markers.setText(
            "List all markers ({} scalebar-only target(s) hidden)".format(hidden)
            if hidden else "List all markers")
        self._update_marker_summary()
        self._revalidate()

    def _on_marker_toggled(self, item):
        """Keep the Damaged column in step with the Reference column."""
        if item.column() != 1:
            self._update_marker_summary()
            self._revalidate()
            return
        damaged_item = self.markers_table.item(item.row(), 2)
        if damaged_item is not None:
            is_reference = item.checkState() == QtCore.Qt.Checked
            self.markers_table.blockSignals(True)
            damaged_item.setFlags(
                QtCore.Qt.ItemIsUserCheckable
                | (QtCore.Qt.ItemIsEnabled if is_reference
                   else QtCore.Qt.NoItemFlags))
            if not is_reference:
                damaged_item.setCheckState(QtCore.Qt.Unchecked)
            self.markers_table.blockSignals(False)
        self._update_marker_summary()
        self._revalidate()

    def _update_marker_summary(self):
        reference = damaged = 0
        for row in range(self.markers_table.rowCount()):
            ref_item = self.markers_table.item(row, 1)
            dmg_item = self.markers_table.item(row, 2)
            if ref_item and ref_item.checkState() == QtCore.Qt.Checked:
                reference += 1
                if dmg_item and dmg_item.checkState() == QtCore.Qt.Checked:
                    damaged += 1
        anchors = reference - damaged
        colour = "#1a7f37" if anchors >= 3 else "#b8860b"
        self.marker_summary.setText(
            "<span style='color:{};'>{} marker(s) will anchor this "
            "timepoint{}.</span>".format(
                colour, anchors,
                " ({} more marked damaged)".format(damaged) if damaged else ""))

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
        if self._loading:
            return
        self._apply()
        issues = models.validate_job(self.job)
        self.issues_label.setText(issue_summary_html(issues))
        blocking = [i for i in issues if i.severity == models.ERROR]
        self.ok_button.setEnabled(not blocking)

    def accept(self):
        self._apply()
        super().accept()


def _boundary_index(value):
    """Index of a boundary source in BOUNDARY_SOURCES, defaulting to markers."""
    return next((i for i, (_label, v) in enumerate(models.BOUNDARY_SOURCES)
                 if v == value), 0)


def _source_index(value):
    """Index of an ICP source value in ICP_SOURCES, defaulting to the first."""
    return next((i for i, (_label, v) in enumerate(models.ICP_SOURCES)
                 if v == value), 0)


def edit_job(job: Job, parent=None) -> bool:
    """Show the editor for `job`. Returns True if the user accepted."""
    editor = JobEditor(job, parent)
    return exec_(editor) == QtWidgets.QDialog.Accepted
