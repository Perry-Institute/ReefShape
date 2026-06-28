"""
Full ReefShape Workflow
Sam Marshall & Will Greene
Perry Institute for Marine Science

Version 1.2, June 2025

Implements ReefShape underwater photogrammetry workflow developed by Will Greene
Many of the component scripts were written by Will Greene and Asif-ul Islam
These were assembled into this full workflow and UI by Sam Marshall
Subsequent updates have been made by Will Greene
"""
import Metashape
from os import path
import sys
import csv
import json
import re
#import exifread
from datetime import datetime
from PySide2 import QtGui, QtCore, QtWidgets
from ui_components import AddPhotosGroupBox, BoundaryMarkerDlg, CollapsibleGroupBox, GeoreferenceGroupBox


# Sentinel value used as a dropdown entry that, when selected, opens
# Metashape's native coordinate-system picker. Selecting it doesn't itself
# set a CRS — the picker either returns one (added to the dropdown) or the
# selection reverts to whatever was selected before.
MORE_CRS_SENTINEL = "More…"  # "More..." with a single-char ellipsis

#function to display message boxes for errors
def show_error_dialog(title, exception):
    msg_box = QtWidgets.QMessageBox()
    msg_box.setIcon(QtWidgets.QMessageBox.Critical)
    msg_box.setWindowTitle("Error")
    msg_box.setText(str(title))
    msg_box.setInformativeText(str(exception))
    msg_box.exec_()
    
class FullWorkflowDlg(QtWidgets.QDialog):

    def __init__(self, parent):
        # set document info
        self.doc = Metashape.app.document

        if len(self.doc.chunks) == 0:
            self.doc.addChunk()

        self.chunk = self.doc.chunk
        self.project_folder = path.dirname(Metashape.app.document.path)
        self.project_name = path.basename(Metashape.app.document.path)[:-4] # extracts project name from file path
        self.output_dir = self.project_folder

        # Create QSettings object to store persistent app state across launches
        # On Windows this goes to registry, on macOS to plist, on Linux to .conf in ~/.config
        self.settings = QtCore.QSettings("ReefShape", "UnderwaterWorkflow")

        # set crs options
        self.defaultCRS = Metashape.CoordinateSystem('COMPD_CS["WGS 84 + EGM96 height",GEOGCS["WGS 84",DATUM["World Geodetic System 1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],TOWGS84[0,0,0,0,0,0,0],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.01745329251994328,AUTHORITY["EPSG","9102"]],AUTHORITY["EPSG","4326"]],VERT_CS["EGM96 height",VERT_DATUM["EGM96 geoid",2005,AUTHORITY["EPSG","5171"]],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","5773"]]]')
        self.crs_options = {
            "WGS84 + EGM96": Metashape.CoordinateSystem('COMPD_CS["WGS 84 + EGM96 height",GEOGCS["WGS 84",DATUM["World Geodetic System 1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],TOWGS84[0,0,0,0,0,0,0],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.01745329251994328,AUTHORITY["EPSG","9102"]],AUTHORITY["EPSG","4326"]],VERT_CS["EGM96 height",VERT_DATUM["EGM96 geoid",2005,AUTHORITY["EPSG","5171"]],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","5773"]]]'),
            "Local Coordinates": Metashape.CoordinateSystem('LOCAL_CS["Local Coordinates (m)",LOCAL_DATUM["Local Datum",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]]]')
        }
        self.autoDetectMarkers = False
        # set default corner marker arrangement
        self.corner_markers = [1, 2, 3, 4]

        # initialize main dialog window
        QtWidgets.QDialog.__init__(self, parent)
        # QDialog.exec() already provides modal behavior. Setting ApplicationModal
        # on top deadlocks Metashape's Light/Dark themes (custom QStyles install
        # application-level event filters that conflict with ApplicationModal).
        self.setWindowTitle("Full ReefShape Workflow")

        # ----- Build Widgets -----
        # these are declared as member variables so that they can be referenced
        # and modified by slots that are outside of the constructor
        # -- General --
        
        # Coordinate system input. The label includes the chunk's current CRS
        # name so the active value stays visible even when the panel is
        # collapsed; the dropdown next to it still lets the user switch
        # between the two built-in options, any previously-picked custom
        # options, or "More…" to open Metashape's native CRS picker.
        self.labelCRS = QtWidgets.QLabel(self._crsLabelText())
        self.comboCRS = QtWidgets.QComboBox()
        self.comboCRS.setToolTip(
            "This chunk's CRS will be changed to the currently selected "
            "value upon initiating the workflow")

        # Load any user-added CRSes from QSettings so they persist across
        # sessions. Stored as a JSON list of WKT strings keyed by display
        # name. Invalid entries are silently skipped — a bad save shouldn't
        # break the dialog.
        for label, crs in self._loadUserCRSes().items():
            if crs.wkt not in {c.wkt for c in self.crs_options.values()}:
                self.crs_options[label] = crs

        # If the chunk's current CRS isn't represented in the dropdown,
        # add it as a session-only option so the dropdown can reflect the
        # chunk's actual state. We do *not* persist this one — it might be a
        # one-off CRS the user inherited with the project and never wants
        # to see again on other projects.
        if (self.chunk and self.chunk.crs
                and not any(crs.wkt == self.chunk.crs.wkt for crs in self.crs_options.values())):
            chunk_label = self._uniqueCRSLabel(self.chunk.crs.name or "Project CRS")
            self.crs_options[chunk_label] = self.chunk.crs

        # Build the dropdown: every known CRS, followed by the sentinel.
        self.comboCRS.addItems(list(self.crs_options.keys()))
        self.comboCRS.addItem(MORE_CRS_SENTINEL)

        # Pick the initial dropdown selection: prefer the chunk's current CRS
        # if it matches a known option (so the dropdown reflects what's
        # actually applied), then fall back to the user's last saved
        # preference, then to the default. The chunk's CRS is *not* modified
        # here — applying the dropdown to the chunk happens only when the
        # user clicks OK (see runWorkFlow).
        chunk_wkt = self.chunk.crs.wkt if (self.chunk and self.chunk.crs) else None
        saved_wkt = self.settings.value("coordinate_system", self.defaultCRS.wkt)
        if chunk_wkt and any(crs.wkt == chunk_wkt for crs in self.crs_options.values()):
            initial_wkt = chunk_wkt
        else:
            initial_wkt = saved_wkt
        default_index = list(self.crs_options.values()).index(
            next((crs for crs in self.crs_options.values() if crs.wkt == initial_wkt), self.defaultCRS)
        )
        self.comboCRS.setCurrentIndex(default_index)
        # Track the last valid (non-sentinel) index so we can revert when
        # the user opens the "More…" picker and cancels.
        self._lastValidCRSIndex = default_index
        self.comboCRS.currentIndexChanged.connect(self._onCRSChanged)
        # Color the label green/red based on whether the dropdown matches
        # the chunk's actual CRS (this is the "is a change pending?" hint).
        self._refreshCRSColor()
        
        
        """old
        self.labelCRS = QtWidgets.QLabel("Coordinate System:")
        self.btnCRS = QtWidgets.QPushButton("Select CRS")
        self.txtCRS = QtWidgets.QPlainTextEdit("Local Coordinates (m)")
        self.txtCRS.setFixedHeight(40)
        self.txtCRS.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtCRS.setReadOnly(True)
        """
        # generic preselection
        self.checkBoxPreSelect = QtWidgets.QCheckBox("Enable Generic Preselection")
        self.checkBoxPreSelect.setChecked(True)
        self.checkBoxPreSelect.setToolTip("Generic preselection speeds up photo alignment, but for photo sets with severe caustics disabling it can make alignment more effective")

        # set orthomosaic resolution
        self.checkBoxDefaultRes = QtWidgets.QCheckBox("Use default resolution")
        self.checkBoxDefaultRes.setToolTip("If this option is enabled, Metashape will calculate the orthomosaic resolution based on the ")
        self.labelCustomRes = QtWidgets.QLabel("Custom Resolution (m): ")
        self.spinboxCustomRes = QtWidgets.QDoubleSpinBox()
        self.spinboxCustomRes.setDecimals(5)
        self.spinboxCustomRes.setValue(0.0005)

        # set mesh quality
        self.labelMeshQuality = QtWidgets.QLabel("Mesh Quality")
        self.comboMeshQuality = QtWidgets.QComboBox()
        self.comboMeshQuality.addItems(["Ultra High", "High", "Medium", "Low", "Lowest"])
        self.comboMeshQuality.setCurrentIndex(2)
        
        # set vertex colors option
        self.checkBoxVertexColors = QtWidgets.QCheckBox("Calculate Model Colors")
        self.checkBoxVertexColors.setToolTip("If checked, Metashape will calculate vertex colors for the mesh. This is useful for visualization but does not affect standard 2D exports. Defaults to false to save time.")
        self.checkBoxVertexColors.setChecked(False)

        # directory input for exports
        self.labelOutputDir = QtWidgets.QLabel("Folder for outputs: ")
        self.btnOutputDir = QtWidgets.QPushButton("Select Folder")
        self.txtOutputDir = QtWidgets.QPlainTextEdit("Defaults to project location")
        self.txtOutputDir.setFixedHeight(40)
        self.txtOutputDir.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtOutputDir.setReadOnly(True)
        
        # generate report
        self.checkBoxReport = QtWidgets.QCheckBox("Generate Processing Report")
        self.checkBoxReport.setChecked(True)
        self.checkBoxReport.setToolTip("If this option is checked, a processing report for the currently-selected chunk will be generated")
        
        # standard outputs for gis
        self.checkBoxExport = QtWidgets.QCheckBox("Export Uncropped GIS Outputs")
        self.checkBoxExport.setChecked(True)
        self.checkBoxExport.setToolTip("If this option is checked, the data products will be exported at full size and resolution."
                                       "\n\nIf neither this box nor the Taglab exports box are selected, the data products will not be exported")

        # taglab outputs
        self.checkBoxTagLab = QtWidgets.QCheckBox("Create TagLab Outputs")
        self.checkBoxTagLab.setChecked(True)
        self.checkBoxTagLab.setToolTip("TagLab requires image inputs to have certain size and compression parameters"
                                       "\n\nIf this option is checked, a second set of outputs will be created for TagLab analysis that are cropped to the boundary polygon and broken into blocks if needed")
        self.checkBoxTagLab.setChecked(True)

        # run script button
        self.btnOk = QtWidgets.QPushButton("Ok")
        self.btnOk.setFixedSize(90, 50)
        self.btnOk.setToolTip("Run workflow")

        # cancel and exit dialog
        self.btnQuit = QtWidgets.QPushButton("Close")
        self.btnQuit.setFixedSize(90, 50)

        # --- Assemble widgets into layouts ---
        # these are declared as local variables because they exist only within the scope of the dialog box, and
        # the layout structure remains unchanged by user actions/slots (with the exception of the reference format box)
        # additionally, ownership of layouts gets passed to their parent when they are added to a widget or
        # another layout, so they cannot be accessed via self.
        main_layout = QtWidgets.QVBoxLayout()  # create main layout - this will hold sublayouts containing individual widgets

        # -- Create sublayouts --
        """
        crs_layout.addWidget(self.labelCRS)
        crs_layout.addWidget(self.txtCRS)
        crs_layout.addWidget(self.btnCRS)
        """
        crs_layout = QtWidgets.QHBoxLayout()
        crs_layout.addWidget(self.labelCRS)
        crs_layout.addWidget(self.comboCRS)
        
        checkbox_layout = QtWidgets.QHBoxLayout()

        checkbox_layout.addWidget(self.checkBoxPreSelect)
        checkbox_layout.addStretch()
        checkbox_layout.addWidget(self.checkBoxDefaultRes)
        resolution_layout = QtWidgets.QHBoxLayout()
        resolution_layout.addWidget(self.labelMeshQuality)
        resolution_layout.addWidget(self.comboMeshQuality)
        resolution_layout.addWidget(self.checkBoxVertexColors)
        resolution_layout.addStretch()
        resolution_layout.addWidget(self.labelCustomRes)
        resolution_layout.addWidget(self.spinboxCustomRes)
        # checkbox_layout.addWidget(self.checkBoxTagLab)

        output_layout = QtWidgets.QHBoxLayout()
        output_layout.addWidget(self.labelOutputDir)
        output_layout.addWidget(self.txtOutputDir)
        output_layout.addWidget(self.btnOutputDir)

        # The three checkboxes that control export outputs used to live in the
        # General panel; they're now in their own Export panel below.
        export_layout = QtWidgets.QHBoxLayout()
        export_layout.addWidget(self.checkBoxReport)
        export_layout.addWidget(self.checkBoxExport)
        export_layout.addWidget(self.checkBoxTagLab)

        ok_layout = QtWidgets.QHBoxLayout()
        ok_layout.addWidget(self.btnOk)
        ok_layout.addWidget(self.btnQuit)


        # -- Assemble sublayouts into groupboxes --
        # NOTE: crs_layout is intentionally not added to General — it's
        # inserted at the top of the Georeferencing panel further down
        # (the CRS is conceptually a georeferencing setting).
        self.general_groupbox = CollapsibleGroupBox("General")
        general_layout = QtWidgets.QVBoxLayout()
        general_layout.addLayout(checkbox_layout)
        general_layout.addLayout(resolution_layout)
        self.general_groupbox.setLayout(general_layout)

        # Export panel: output folder selector + the three export checkboxes.
        # Output folder is grouped here because it's only meaningful when at
        # least one of the export options is enabled.
        self.export_groupbox = CollapsibleGroupBox("Export")
        export_outer = QtWidgets.QVBoxLayout()
        export_outer.addLayout(output_layout)
        export_outer.addLayout(export_layout)
        self.export_groupbox.setLayout(export_outer)


        # -- Assemble groupboxes into main layout --
        self.addphotos_groupbox = AddPhotosGroupBox(self)
        self.addphotos_groupbox.chunkUpdated.connect(self.refreshChunkNameDisplay)
        self.georef_groupbox = GeoreferenceGroupBox(self)
        # Insert the CRS row at the top of the Georeferencing panel. The CRS
        # is conceptually a georeferencing setting (it tells Metashape how to
        # interpret coordinates in the georef file) and grouping it here
        # keeps related controls together. contentLayout() is the inner
        # layout that holds the panel's actual rows.
        self.georef_groupbox.contentLayout().insertLayout(0, crs_layout)
        if self.chunk and len(self.chunk.markers) == 0:
            QtCore.QTimer.singleShot(0, lambda: self.georef_groupbox.comboReference.setCurrentIndex(1))
                #self.georef_groupbox.autoDetectMarkers = True
        main_layout.addWidget(self.addphotos_groupbox)
        main_layout.addWidget(self.general_groupbox)
        main_layout.addWidget(self.georef_groupbox)
        main_layout.addWidget(self.export_groupbox)
        # Absorb vertical slack at the bottom so collapsed panels don't
        # inflate gaps in the panels that are still expanded. Without this,
        # the dialog's minimum height (set below) forces the QVBoxLayout to
        # distribute extra space across the four groupboxes, and each
        # groupbox in turn distributes it across its own rows.
        main_layout.addStretch(1)
        # main_layout.addLayout(ok_layout)

        # Now that every panel is built, set initial collapsed/grayed state
        # based on what the chunk already contains. This is the "open by
        # default unless the section is already done" UX. Done as a 0-delay
        # singleShot so it runs after the dialog finishes painting; collapsing
        # before the layout settles can leave residual whitespace.
        QtCore.QTimer.singleShot(0, self._applyChunkStateDefaults)

        # Resize the dialog to fit content whenever a panel collapses or
        # expands. Deferred via singleShot so the layout has a chance to
        # settle before adjustSize() reads the new sizeHint.
        for panel in (self.addphotos_groupbox, self.general_groupbox,
                      self.georef_groupbox, self.export_groupbox):
            panel.toggled.connect(
                lambda _checked: QtCore.QTimer.singleShot(0, self._fitToContent))

        # a somewhat complicated system of wrapper widgets is needed to accomodate the scroll layout
        # here is a summary of the structure:
        # main dialog(self) > scroll_layout > scroll_area > main_widget > main_layout

        self._main_widget = QtWidgets.QWidget() # wrapper widget for scroll area
        self._main_widget.setLayout(main_layout)
        self._scroll_area = QtWidgets.QScrollArea()
        self._scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll_area.setWidget(self._main_widget)
        self._scroll_area.setAlignment(QtCore.Qt.AlignHCenter)
        # Let the scroll area resize its inner widget horizontally to match
        # the viewport. Without this, the inner widget keeps its initial
        # natural width and the scroll area shows a horizontal scrollbar
        # whenever the dialog is even one pixel narrower than that natural
        # width (e.g. when macOS's window chrome accounting shaves a few
        # pixels off the viewport).
        self._scroll_area.setWidgetResizable(True)
        scroll_layout = QtWidgets.QVBoxLayout()
        scroll_layout.addWidget(self._scroll_area)
        scroll_layout.addLayout(ok_layout) # place run and close buttons outside of scroll area so theyre always visible
        ok_layout.setEnabled(True)
        self.setLayout(scroll_layout) # set wrapper layout for scroll area as main dialog layout

        # Pin the minimum width so the form stays readable; leave minimum
        # height unconstrained so the dialog can shrink when panels collapse.
        # Auto-resize on collapse/expand is wired further down via each
        # panel's toggled signal.
        #
        # Width sizing: sizeHint() gives the form's natural width based on
        # the layout system; frameGeometry() before show() is unreliable
        # and systematically too small on macOS (window chrome and the
        # vertical scrollbar gutter aren't accounted for). On macOS the
        # scrollbar can overlay or take real space depending on user
        # settings, so we ask QStyle for the actual extent rather than
        # guessing.
        sb_extent = QtWidgets.QApplication.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent)
        natural_width = self._main_widget.sizeHint().width() + sb_extent + 20
        self.setMinimumWidth(natural_width)

        # Initial dialog geometry: position roughly centered on the Metashape
        # main window, with the dialog height sized to the form's content.
        # _fitToContent handles all later resize-on-collapse adjustments.
        screen_h = (QtWidgets.QApplication.primaryScreen().availableGeometry().height()
                    if QtWidgets.QApplication.primaryScreen() else parent.frameGeometry().height())
        max_height = int(0.85 * screen_h)
        content_height = self._main_widget.sizeHint().height() + 80
        height = min(content_height, max_height)
        width = natural_width
        x = parent.frameGeometry().width()/2.0 - width/2 # set starting position so that widget is roughly centered on screen
        if(x<0): x = 0
        y = parent.frameGeometry().height()/2.0 - height/2
        if(y<0): y = 0
        self.setGeometry(parent.frameGeometry().x() + x, parent.frameGeometry().y() + y, width, height)

        # --- Connect signals and slots ---
        # these two syntaxes for connecting signals to slots should be equivalent, but the first method (dot notation) may make it easier
        # to use widget-specific signals (such as currentIndexChanged) instead of core signals
        self.checkBoxDefaultRes.stateChanged.connect(self.onResolutionChange)
        self.btnOutputDir.clicked.connect(self.getOutputDir)
        #self.btnCRS.clicked.connect(self.getCRS)

        self.btnOk.clicked.connect(self.runWorkFlow)
        self.btnQuit.clicked.connect(self.reject)
        self.loadSettings()
        
    # ----- Dialog-state helpers -----

    def _crsLabelText(self):
        '''Label shown beside the CRS dropdown — includes the chunk's current
        CRS name so the active value is visible at a glance, even when the
        General panel is collapsed.'''
        try:
            current = self.chunk.crs.name if (self.chunk and self.chunk.crs) else None
        except Exception:
            current = None
        if current:
            return "Coordinate System (current: {}):".format(current)
        return "Coordinate System:"

    def _refreshCRSColor(self):
        '''Color the CRS label green if the dropdown selection matches the
        chunk's actual CRS (no change pending), red if they differ (the
        chunk's CRS *will* change on OK).

        Skips when the sentinel is somehow active — there's no real CRS
        to compare against in that intermediate state.
        '''
        selected_label = self.comboCRS.currentText()
        if selected_label not in self.crs_options:
            return
        chunk_wkt = self.chunk.crs.wkt if (self.chunk and self.chunk.crs) else ""
        dropdown_wkt = self.crs_options[selected_label].wkt
        color = "green" if dropdown_wkt == chunk_wkt else "red"
        self.labelCRS.setStyleSheet("color: {};".format(color))

    def _loadUserCRSes(self):
        '''Read previously user-added CRSes from QSettings. Returns an ordered
        dict of {display_name: CoordinateSystem}. Stored on disk as a JSON
        list of [name, wkt] pairs — JSON survives QSettings cross-platform
        type quirks (the bare `setValue(list)` path returns QVariant-wrapped
        items on some platforms).'''
        raw = self.settings.value("user_crs_list", "[]", type=str)
        try:
            entries = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        out = {}
        for entry in entries:
            try:
                name, wkt = entry
                crs = Metashape.CoordinateSystem(wkt)
                out[name] = crs
            except Exception:
                continue
        return out

    def _saveUserCRSes(self):
        '''Persist the user-added CRSes (everything in self.crs_options that
        isn't one of the two built-ins) as a JSON list of [name, wkt]
        pairs.'''
        builtin_wkts = {self.defaultCRS.wkt,
                        self.crs_options["Local Coordinates"].wkt}
        entries = [[name, crs.wkt]
                   for name, crs in self.crs_options.items()
                   if crs.wkt not in builtin_wkts]
        self.settings.setValue("user_crs_list", json.dumps(entries))

    def _uniqueCRSLabel(self, base):
        '''Return a display name that doesn't collide with an existing key in
        self.crs_options. Used both when surfacing the chunk's current CRS
        on dialog open and when adding a user-picked CRS — `getCoordinateSystem`
        sometimes returns systems whose `.name` collides with one already in
        the dropdown.'''
        if base not in self.crs_options:
            return base
        suffix = 2
        while "{} ({})".format(base, suffix) in self.crs_options:
            suffix += 1
        return "{} ({})".format(base, suffix)

    def _onCRSChanged(self, index):
        '''Slot: handle a change in the CRS dropdown. Three paths:
          - User picked the "More…" sentinel: open Metashape's CRS picker.
            If they pick one, add it to the dropdown (or reuse an existing
            matching entry) and persist. If they cancel, revert to the
            previously valid selection.
          - User picked any other entry: just update the label and remember
            the index as "last valid" for future revert.
        '''
        text = self.comboCRS.itemText(index)
        if text == MORE_CRS_SENTINEL:
            chosen = Metashape.app.getCoordinateSystem("Select Coordinate System")
            if not chosen:
                # Cancelled — revert. setCurrentIndex re-enters this slot
                # with the previous index, which falls through to the label
                # refresh below.
                self.comboCRS.setCurrentIndex(self._lastValidCRSIndex)
                return
            # Did the user pick something we already have? If so, just
            # select it instead of duplicating.
            existing_index = next(
                (i for i in range(self.comboCRS.count() - 1)  # exclude sentinel
                 if self.comboCRS.itemText(i) in self.crs_options
                 and self.crs_options[self.comboCRS.itemText(i)].wkt == chosen.wkt),
                None,
            )
            if existing_index is not None:
                self.comboCRS.setCurrentIndex(existing_index)
                return
            # Add as a new option just before the "More…" sentinel.
            label = self._uniqueCRSLabel(chosen.name or "Custom CRS")
            self.crs_options[label] = chosen
            insert_at = self.comboCRS.count() - 1
            self.comboCRS.insertItem(insert_at, label)
            self.comboCRS.setCurrentIndex(insert_at)
            self._saveUserCRSes()
            return
        # Plain selection change. Remember the index so a future "More…"
        # cancel can revert here. Deliberately do *not* touch the label
        # *text* — that shows the chunk's actual current CRS, which doesn't
        # change until the user clicks OK. We do update the label *color*
        # to signal whether the dropdown matches the chunk (green) or
        # represents a pending change (red).
        self._lastValidCRSIndex = index
        self._refreshCRSColor()

    def _applyChunkStateDefaults(self):
        '''Collapse panels and gray inputs whose work is already done in the
        current chunk. Per-panel rules:
          - Project Setup: complete if project is saved AND chunk has photos
          - General: complete if tie points + mesh + DEM + ortho all exist
          - Georeferencing: complete if any marker has reference info set
            (i.e. the chunk is georeferenced — scalebars are independent)
          - Export: never collapsed by default
        When a panel is complete we both collapse it *and* mark it complete
        (suffixes " | Complete" to the title and colors it green) so the
        user knows *why* it auto-collapsed.

        Gray-out (input still visible, just disabled because changing it
        wouldn't do anything on this re-run):
          - Generic Preselection: tie points already exist
          - Mesh Quality + Calculate Model Colors: mesh already exists
        Everything stays user-toggleable — clicking the title checkbox
        re-expands a collapsed panel, and grayed inputs would be ignored
        downstream anyway.'''
        if not self.chunk:
            return

        has_cameras = len(self.chunk.cameras) > 0
        has_tie_points = self.chunk.tie_points is not None
        has_mesh = self.chunk.model is not None
        has_dem = self.chunk.elevation is not None
        has_ortho = self.chunk.orthomosaic is not None
        project_saved = bool(Metashape.app.document.path)
        # "Referenced" means at least one marker has a reference location
        # set (lat/long/depth from the georef file, or set manually in the
        # Reference panel). Scalebars are tracked separately by Metashape
        # and aren't a prerequisite for the georef step being "done" — a
        # chunk can be georeferenced without scalebars, especially when
        # the user set up referencing manually.
        try:
            georef_done = any(
                m.reference.location is not None
                for m in self.chunk.markers if m.reference
            )
        except Exception:
            georef_done = False

        # Compute the three "complete" flags up front so each panel gets
        # the same setComplete + setCollapsed treatment.
        project_complete = project_saved and has_cameras
        general_complete = has_tie_points and has_mesh and has_dem and has_ortho

        self.addphotos_groupbox.setComplete(project_complete)
        self.addphotos_groupbox.setCollapsed(project_complete)

        self.general_groupbox.setComplete(general_complete)
        self.general_groupbox.setCollapsed(general_complete)

        self.georef_groupbox.setComplete(georef_done)
        self.georef_groupbox.setCollapsed(georef_done)

        # Gray rules
        if has_tie_points:
            self.checkBoxPreSelect.setEnabled(False)
            self.checkBoxPreSelect.setToolTip(
                "Tie points already exist in this chunk; preselection only "
                "affects the initial alignment pass.")
        if has_mesh:
            self.comboMeshQuality.setEnabled(False)
            self.labelMeshQuality.setEnabled(False)
            self.checkBoxVertexColors.setEnabled(False)
            mesh_tip = ("A mesh already exists in this chunk; mesh quality "
                        "and color settings only affect mesh building.")
            self.comboMeshQuality.setToolTip(mesh_tip)
            self.checkBoxVertexColors.setToolTip(mesh_tip)

        # Shrink the dialog to the post-collapse content height. The initial
        # geometry was sized for everything-expanded; without this call the
        # dialog stays tall and shows whitespace under the visible panels.
        self._fitToContent()

    def _fitToContent(self):
        '''Resize the dialog vertically to fit current content.

        We only touch height — width is pinned to the form's natural width
        via setMinimumWidth in the constructor, and we want the dialog to
        stay that wide whether panels are open or not.

        adjustSize() doesn't reliably shrink here: with a QScrollArea in
        the layout, the dialog's own sizeHint reflects the scroll area's
        (small) sizeHint rather than the actual content, so adjustSize
        either no-ops or shrinks to something useless. Instead we read the
        inner form's sizeHint directly and resize() to it. The form's
        sizeHint correctly reflects hidden children, so collapsing a
        panel shrinks the dialog and expanding it grows the dialog.
        '''
        if not hasattr(self, '_main_widget'):
            return
        # Force the inner layout to recompute before we ask for its sizeHint;
        # the panel toggle we're responding to may have invalidated layouts
        # that haven't been re-laid out yet in this event loop iteration.
        self._main_widget.layout().activate()
        content_h = self._main_widget.sizeHint().height() + 80
        screen = QtWidgets.QApplication.primaryScreen()
        screen_h = screen.availableGeometry().height() if screen else content_h
        target_h = min(content_h, int(0.85 * screen_h))
        self.resize(self.width(), target_h)

    def loadSettings(self):
        """Load saved settings using QSettings."""
        self.checkBoxPreSelect.setChecked(self.settings.value("checkBoxPreSelect", True, type=bool))
        self.checkBoxDefaultRes.setChecked(self.settings.value("checkBoxDefaultRes", False, type=bool))
        self.spinboxCustomRes.setValue(self.settings.value("spinboxCustomRes", 0.0005, type=float))
        self.comboMeshQuality.setCurrentIndex(self.settings.value("comboMeshQuality", 2, type=int))
        self.checkBoxExport.setChecked(self.settings.value("checkBoxExport", True, type=bool))
        self.checkBoxTagLab.setChecked(self.settings.value("checkBoxTagLab", False, type=bool))
        self.checkBoxReport.setChecked(self.settings.value("checkBoxExportReport", True, type=bool))
        self.checkBoxVertexColors.setChecked(self.settings.value("checkBoxVertexColors", False, type=bool))
        # We deliberately do NOT reapply the saved CRS to the chunk here.
        # The dropdown initial selection already honors the saved value as a
        # fallback (see __init__), and the chunk's CRS only changes when the
        # user clicks OK. Previously, loading the saved CRS into chunk.crs on
        # every dialog open silently overwrote the chunk's actual CRS — a
        # destructive surprise when reopening a project that used a different
        # CRS than the user's last saved preference.

        # Restore previously used scalebar file path so the user doesn't have
        # to re-pick it every session. We only restore if the file still
        # exists — pointing at a deleted file would mislead the user. The
        # path is only *used* during the workflow when the Georeferencing
        # dropdown is set to "Yes" (gated by autoDetectMarkers), so this
        # restore is harmless when the user chooses "No".
        saved_scalebar = self.settings.value("scalebars_path", "", type=str)
        if saved_scalebar and path.isfile(saved_scalebar):
            self.georef_groupbox.scalebars_path = saved_scalebar
            self.georef_groupbox.txtScaleFile.setPlainText(saved_scalebar)

    def saveSettings(self):
        """Save current settings using QSettings."""
        self.settings.setValue("checkBoxPreSelect", self.checkBoxPreSelect.isChecked())
        self.settings.setValue("checkBoxDefaultRes", self.checkBoxDefaultRes.isChecked())
        self.settings.setValue("spinboxCustomRes", self.spinboxCustomRes.value())
        self.settings.setValue("comboMeshQuality", self.comboMeshQuality.currentIndex())
        self.settings.setValue("checkBoxExport", self.checkBoxExport.isChecked())
        self.settings.setValue("checkBoxTagLab", self.checkBoxTagLab.isChecked())
        self.settings.setValue("checkBoxExportReport", self.checkBoxReport.isChecked())
        self.settings.setValue("checkBoxVertexColors", self.checkBoxVertexColors.isChecked())
        # Guard against the "More…" sentinel — it isn't a real CRS option.
        # In normal use the dropdown reverts off the sentinel before this
        # runs, but this defends against any race.
        selected_label = self.comboCRS.currentText()
        if selected_label in self.crs_options:
            self.settings.setValue("coordinate_system", self.crs_options[selected_label].wkt)
        # Persist the scalebar path so the next launch can restore it (only
        # if a path was actually picked — don't overwrite a saved path with
        # an empty string when the dropdown is set to "No" and no file was
        # selected this session).
        if getattr(self.georef_groupbox, "scalebars_path", ""):
            self.settings.setValue("scalebars_path", self.georef_groupbox.scalebars_path)
    
    def reject(self):
        self.saveSettings()
        super().reject()
        
    def closeEvent(self, event):
        print("Close event triggered")
        self.saveSettings()
        event.accept()  # allow the window to close
        self.reject() #explicitly deal with this for non-classic systems
         
    def runWorkFlow(self):
        '''
        OK-button slot: run the workflow body, but make sure any exception
        re-enables the dialog so the user can adjust their inputs and try
        again (or close the dialog). Without this wrapper, an exception
        leaves the dialog in a setEnabled(False) state with no way to
        recover — and on macOS in the Light/Dark theme even the title-bar
        close button is a disabled Qt child widget, so the only recourse
        is restarting Metashape.
        '''
        try:
            self._runWorkFlowImpl()
        except Exception as e:
            import traceback
            traceback.print_exc()
            show_error_dialog("Workflow Error", str(e))
            self.setEnabled(True)

    def _runWorkFlowImpl(self):
        '''
        Contains the main workflow structure
        '''
        print("Script started...")
        self.setEnabled(False)
        self.chunk = Metashape.app.document.chunk
        
        # Skip the "More…" sentinel if somehow it's the active item (defensive
        # — the slot reverts off it). When skipping, leave chunk.crs alone.
        selected_crs_label = self.comboCRS.currentText()
        if selected_crs_label in self.crs_options:
            self.chunk.crs = self.crs_options[selected_crs_label]
        ###### 0. Setting Parameters ######
        if(not self.georef_groupbox.autoDetectMarkers and self.chunk.model == None):
            Metashape.app.messageBox("You have initiated the script without specifying georeferencing information. If you ran the align timepoints script first, "
                                    "clicking OK will simply complete the workflow in its entirety for you (no further action needed). \n\n If this is a new project "
                                    "without auto-detectable markers, the script will exit after creating a mesh to allow for manual referencing, leveling, "
                                    "and scaling. \n\n Once this information is added, run the script again to complete the remainder of the workflow.")

        # set constants
        ALIGN_QUALITY = 1 # quality setting for camera alignment; corresponds to high accuracy in GUI
        DM_QUALITY = 2 ** self.comboMeshQuality.currentIndex() # quality setting for depth maps; corresponds to medium in GUI
        INTERPOLATION = Metashape.DisabledInterpolation # interpolation setting for DEM creation

        # set arguments from dialog box
        try:
            if(self.georef_groupbox.autoDetectMarkers):
                scalebars_path = self.georef_groupbox.scalebars_path
                georef_path = self.georef_groupbox.georef_path
        except:
            Metashape.app.messageBox("No files selected. If you would like to automatically detect markers, please select files containing scaling and georeferencing information")
            print("Script aborted")
            self.setEnabled(True)
            return

        # taglab_outputs = self.checkBoxTagLab.isChecked()
        generic_preselect = self.checkBoxPreSelect.isChecked()

        if(self.checkBoxDefaultRes.isChecked()):
            ORTHO_RES = 0
        else:
            ORTHO_RES = self.spinboxCustomRes.value()

        DEM_RES = 0 # allow metashape to choose dem resolution by default, since we arent exporting for taglab

        target_type = self.georef_groupbox.target_type

        ref_formatting = [self.georef_groupbox.spinboxRefLabel.value(), self.georef_groupbox.spinboxRefX.value(),
                            self.georef_groupbox.spinboxRefY.value(), self.georef_groupbox.spinboxRefZ.value(),
                            self.georef_groupbox.spinboxXAcc.value(), self.georef_groupbox.spinboxYAcc.value(),
                            self.georef_groupbox.spinboxZAcc.value(), self.georef_groupbox.spinboxSkipRows.value()]
        self.corner_markers = self.georef_groupbox.corner_markers
        
        
        ###### 1. Align & Scale ######
        # a. Align photos
        # Markers may have been imported from a prior timepoint (via the Align
        # Timepoints script) before alignment runs. If any of those markers
        # physically shifted between timepoints, their image-space projections
        # are inconsistent across photos and can break alignment / pull camera
        # optimization toward a worse solution. To avoid this, we disable every
        # existing marker before alignment and restore each marker's original
        # enabled state once camera optimization is complete.
        suppressed_markers = []  # list of (marker, original_enabled) tuples
        if(self.chunk.tie_points == None):
            for m in self.chunk.markers:
                suppressed_markers.append((m, m.enabled))
                m.enabled = False
        if(self.chunk.tie_points == None): # check if photos are aligned - assumes they are aligned if there is a point cloud, could change to threshold # of cameras
            self.chunk.matchPhotos(downscale = ALIGN_QUALITY, keypoint_limit_per_mpx = 300, generic_preselection = generic_preselect,
                              reference_preselection=True, filter_mask=False, mask_tiepoints=True,
                              filter_stationary_points=True, keypoint_limit=40000, tiepoint_limit=4000, keep_keypoints=True, guided_matching=False,
                              reset_matches=False, subdivide_task=True, workitem_size_cameras=20, workitem_size_pairs=80, max_workgroup_size=100)
            self.chunk.alignCameras(adaptive_fitting = True, min_image=2, reset_alignment=True, subdivide_task=True)
            #second alignment step sometimes adds extra photos to the alignment that were missed on the first pass
            self.chunk.alignCameras(adaptive_fitting = True, min_image=2, reset_alignment=False, subdivide_task=True)
            self.updateAndSave()
            print(" --- Initial alignment completed -- Refining alignment --- ")

            # remove and re-add unaligned photos to try to align them
            unaligned_photo_paths = []
            for camera in self.chunk.cameras:
                if not camera.transform: # Check if the camera is not aligned
                    unaligned_photo_paths.append(camera.photo.path)
                    self.chunk.remove([camera]) # Remove unaligned cameras from the chunk

            if unaligned_photo_paths: # only try to add photos if list of paths is not empty
                self.chunk.addPhotos(unaligned_photo_paths)
                
            # rerun alignment without generic preselection
            self.chunk.matchPhotos(downscale = ALIGN_QUALITY, keypoint_limit_per_mpx = 300, generic_preselection = False,
                              reference_preselection=True, filter_mask=False, mask_tiepoints=True,
                              filter_stationary_points=True, keypoint_limit=40000, tiepoint_limit=4000, keep_keypoints=True, guided_matching=False,
                              reset_matches=False, subdivide_task=True, workitem_size_cameras=20, workitem_size_pairs=80, max_workgroup_size=100)
            self.chunk.alignCameras(adaptive_fitting = True, min_image=2, reset_alignment=False, subdivide_task=True)

            print(" --- Cameras are aligned and sparse point cloud generated --- ")
            self.updateAndSave()

        # b. detect markers
        if(len(self.chunk.markers) == 0 and self.georef_groupbox.autoDetectMarkers): # detects markers only if there are none to start with - could change to threshold # of markers there should be, but i think makes most sense to leave as-is
            self.chunk.detectMarkers(target_type = target_type, tolerance=20, filter_mask=False, inverted=False, noparity=False, maximum_residual=5, minimum_size=0, minimum_dist=5)
            print(" --- Markers Detected --- ")

        # c. scale model
        if(len(self.chunk.scalebars) == 0 and self.georef_groupbox.autoDetectMarkers): # creates scalebars only if there are none already
            ref_except = self.referenceModel(georef_path, ref_formatting)
            scale_except = ""
            if(not ref_except):
                scale_except = self.createScalebars(scalebars_path)
            # this structure is really clunky but I'm not sure what the best way to differentiate between sub-exceptions is without defining whole exception classes, which seems excessive
            error = ""
            if(scale_except or ref_except):
                if(scale_except):
                    print(scale_except)
                    error = error + scale_except
                if(ref_except):
                    print(ref_except)
                    error = error + ref_except
                Metashape.app.messageBox("Unable to scale and reference model:\n" + error + "Check that the files are formatted correctly and try again, or add markers and scalebars through the Metashape GUI.")
                self.reject()
                return
            else:
                self.chunk.updateTransform()


        if(self.chunk.model == None):
            # d. optimize camera alignment - skip if optimization has already been run
            # (manually or by a previous script run). Metashape writes 'optimize/fit_*'
            # keys to chunk.tie_points.meta whenever optimizeCameras() executes, so
            # presence of any 'optimize/' key is a reliable signal.
            if not self.isOptimized():
                self.gradSelectsOptimization()
                print( " --- Camera Optimization Complete --- ")
                self.updateAndSave()

            # Restore enabled state for any markers we suppressed before alignment.
            # Now that alignment and optimization are done, marker georef is needed
            # for the chunk transform, boundary creation, and downstream exports.
            if suppressed_markers:
                for m, original_enabled in suppressed_markers:
                    m.enabled = original_enabled
                suppressed_markers.clear()
                self.updateAndSave()

            ###### 2. Generate products ######
            # a. build mesh
            # reset reconstruction region to make sure the mesh gets built for the full plot
            self.chunk.resetRegion()
            self.updateAndSave()
            # try 'task' syntax to enable hidden preferences (ie pm_enable) to be changed
            task = Metashape.Tasks.BuildDepthMaps()
            task.downscale = DM_QUALITY
            task.filter_mode = Metashape.FilterMode.MildFiltering
            task.reuse_depth = True
            task.max_neighbors = 16
            task.subdivide_task = True
            task.workitem_size_cameras = 20
            task.max_workgroup_size = 100
            task["pm_enable"] = "1"
            task.apply(self.chunk)
            self.updateAndSave()
            
            self.chunk.buildModel(
                surface_type = Metashape.Arbitrary, 
                interpolation = Metashape.EnabledInterpolation, 
                face_count=Metashape.HighFaceCount,
                face_count_custom = 1000000, 
                source_data = Metashape.DepthMapsData, 
                keep_depth = True,
                vertex_colors=False
            )
            print(" --- Mesh Generated --- ")
            self.updateAndSave()
            
            if self.checkBoxVertexColors.isChecked():
                self.chunk.colorizeModel()
                self.updateAndSave()
                
        # if not using automatic referencing, exit script after mesh creation
        if(not self.autoDetectMarkers and len(self.chunk.markers) == 0):
            print("Exiting script for manual referencing")
            Metashape.app.messageBox("Image alignment and mesh building complete.\n\nNow, add referencing information, then re-run the full dialog script to complete processing.")
            self.close()
            return

        # b. build orthomosaic and DEM
        
        if(self.chunk.elevation == None):
            self.chunk.buildDem(source_data = Metashape.ModelData, interpolation = Metashape.EnabledInterpolation,
                           resolution=ORTHO_RES, subdivide_task=True, workitem_size_tiles=10, max_workgroup_size=100)
            print(" --- Hi-Res DEM Built --- ")
            
            
        if(self.chunk.orthomosaic == None):
            self.chunk.buildOrthomosaic(resolution = ORTHO_RES, surface_data=Metashape.ElevationData, blending_mode=Metashape.MosaicBlending, fill_holes=True, ghosting_filter=False,
                                   cull_faces=False, refine_seamlines=False, subdivide_task=True,
                                   workitem_size_cameras=20, workitem_size_tiles=10, max_workgroup_size=100)
            print(" --- Orthomosaic Built --- ")

            self.updateAndSave()

            # Replace the high-res DEM with one at 1/4 the ortho resolution.
            # The ortho-resolution DEM was needed only to avoid resampling artifacts
            # while building the ortho; for storage and downstream use, a coarser
            # DEM is fine and saves significant disk space. Rebuilds from the mesh
            # (rather than resampling the existing DEM) so the result is clean.
            ortho_cell = self.chunk.orthomosaic.resolution
            dem_resample_res = ortho_cell * 4
            self.chunk.elevation = None
            self.chunk.buildDem(source_data=Metashape.ModelData, interpolation=Metashape.EnabledInterpolation,
                                resolution=dem_resample_res, subdivide_task=True,
                                workitem_size_tiles=10, max_workgroup_size=100)
            print(" --- DEM resampled to {:.4f} m for storage --- ".format(dem_resample_res))
            self.updateAndSave()


        # c. create boundary (skip if an outer boundary already exists, e.g.
        # copied from a reference chunk by the Align Timepoints script)
        has_boundary = False
        if self.chunk.shapes:
            for shape in self.chunk.shapes:
                if shape.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary:
                    has_boundary = True
                    break
        if not has_boundary:
            has_boundary = self.boundaryCreation()
            if has_boundary:
                print(" --- Boundary Polygon Created ---")
            else:
                print(" --- Boundary polygon was NOT created; downstream steps that depend on it will be skipped ---")

        # Track any steps that get skipped or modified during the export phase
        # so we can surface them in the completion dialog at the very end.
        warnings = []

        ###### 3. Export products ######

        # If no boundary exists and the user wants TagLab outputs (which normally
        # clip to the boundary), ask how to proceed before running any exports.
        # The two other boundary-dependent steps — the boundary shapefile export
        # and the report's temporary-disable loop — handle a missing boundary
        # gracefully below, so we only need to gate on TagLab here.
        taglab_clip = True
        if not has_boundary and self.checkBoxTagLab.isChecked():
            reply = QtWidgets.QMessageBox.question(
                self,
                "No boundary polygon",
                "No OuterBoundary polygon exists in this chunk, so the TagLab "
                "outputs cannot be clipped to the plot.\n\n"
                "Yes — continue and export uncropped TagLab products.\n"
                "No  — stop here and return to the dialog so you can uncheck "
                "TagLab outputs and re-run, or close the dialog to create a "
                "boundary manually (see scripts 06 or 08) before re-running.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply == QtWidgets.QMessageBox.No:
                print("Export aborted by user — no boundary polygon available for TagLab clipping")
                self.setEnabled(True)
                return
            taglab_clip = False
            warnings.append("TagLab outputs were exported uncropped because no boundary polygon was available.")
        if not has_boundary:
            warnings.append(
                "Boundary polygon could not be created automatically — corner markers {} were not all found in the chunk. "
                "The boundary shapefile export was skipped. "
                "To produce a boundary, either run script 06 (corner markers) or script 08 (from camera footprints) and re-run this workflow."
                .format(self.corner_markers)
            )

        # set up compression parameters
        #first, for regular orthomosaic
        jpg = Metashape.ImageCompression()
        jpg.tiff_compression = Metashape.ImageCompression.TiffCompressionJPEG
        jpg.jpeg_quality = 90
        jpg.tiff_big = True
        jpg.tiff_overviews = True
        # Write the TIFF as tiled rather than stripped. JPEG-compressed TIFF
        # strips are capped at 65500 px per libtiff, which orthomosaics of
        # high-res reef plots routinely exceed. Tiled JPEG compresses each
        # internal tile independently (typically 256/512 px) so the limit
        # doesn't apply. GIS software reads tiled and stripped TIFFs the same.
        jpg.tiff_tiled = True
        #lzw for DEM and TagLab products
        lzw = Metashape.ImageCompression()
        lzw.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW
        lzw.tiff_big = True
        lzw.tiff_overviews = True
        
        # generate report              
        if self.checkBoxReport.isChecked():
            report_path = self.output_dir + "/" + self.project_name + "_" + self.chunk.label + ".pdf"
            if not os.path.exists(report_path):
                # --- Temporarily disable boundary polygon for uncropped report ---
                original_boundaries = []
                if self.chunk.shapes:
                    for shape in self.chunk.shapes:
                        if shape.geometry and shape.geometry.type == Metashape.Geometry.Type.PolygonType:
                            if shape.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary:
                                original_boundaries.append((shape, shape.boundary_type))
                                shape.boundary_type = Metashape.Shape.BoundaryType.NoBoundary

                # Export report
                human_date = self.format_date_label(self.chunk.label)
                # Metashape 2.3 renamed include_system_info -> save_system_info
                ms_version = tuple(int(p) for p in Metashape.app.version.split(".")[:2])
                system_info_kwarg = "save_system_info" if ms_version >= (2, 3) else "include_system_info"
                self.chunk.exportReport(
                    path=report_path,
                    title=self.project_name,
                    description="\nProcessing report for " + self.project_name + " photographed on " + human_date + "\nCreated with ReefShape v1.3\nProcessed on:",
                    font_size=12,
                    page_numbers=True,
                    **{system_info_kwarg: True}
                )

                # --- Restore original boundary types ---
                for shape, original_type in original_boundaries:
                    shape.boundary_type = original_type
                     

        # generate main orthomosaic and DEM for GIS
        if(self.checkBoxExport.isChecked()):
            # export orthomosaic and DEM in full format
            ortho_path = self.output_dir + "/" + self.project_name + "_" + self.chunk.label + ".tif"
            dem_path = self.output_dir + "/" + self.project_name + "_" + self.chunk.label + "_DEM.tif"
            if(not os.path.exists(ortho_path)):
                self.chunk.exportRaster(path = ortho_path, resolution = ORTHO_RES,
                                   source_data = Metashape.OrthomosaicData, split_in_blocks = False, image_compression = jpg,
                                   save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                                   min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=False,title='Orthomosaic', description='Generated by Agisoft Metashape with ReefShape')
            
            if(not os.path.exists(dem_path)):
                self.chunk.exportRaster(path = dem_path, resolution = DEM_RES, nodata_value = -5,
                                   source_data = Metashape.ElevationData, split_in_blocks = False, image_compression = lzw,
                                   save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                                   min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=False,title='DEM', description='Generated by Agisoft Metashape with ReefShape')

            # build output path for boundary shapefile - this is necessary since the files will be placed in their own new folder within the output folder that the user created/selected.
            # Skip when no boundary exists; otherwise we'd create an empty folder
            # and write a polygon-less shapefile.
            if has_boundary:
                shape_dir = os.path.join(self.output_dir, self.project_name + "_" + self.chunk.label + "_boundary")
                if(not os.path.exists(shape_dir)):
                    os.mkdir(shape_dir)
                self.chunk.exportShapes(path = os.path.join(shape_dir, self.project_name + "_" + self.chunk.label + "_boundary.shp"), save_points=False, save_polylines=False, save_polygons=True,
                                   format = Metashape.ShapesFormatSHP, polygons_as_polylines=False, save_labels=True, save_attributes=True)


        # export ortho and dem in blockwise format for Taglab
        if(self.checkBoxTagLab.isChecked()):
            self.chunk.exportRaster(path = self.output_dir + "/taglab_outputs/" + self.project_name + "_" + self.chunk.label + ".tif", resolution = ORTHO_RES,
                               source_data = Metashape.OrthomosaicData, block_width = 32767, block_height = 32767, split_in_blocks = True, image_compression = lzw, # remainder of parameters are defaults specified to ensure any alternate settings get oerridden
                               save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                               min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=taglab_clip,title='Orthomosaic', description='Generated by Agisoft Metashape')
            self.chunk.exportRaster(path = self.output_dir + "/taglab_outputs/" + self.project_name + "_" + self.chunk.label + "_DEM.tif", resolution = ORTHO_RES, nodata_value = -5,
                               source_data = Metashape.ElevationData, block_width = 32767, block_height = 32767, split_in_blocks = True, image_compression = lzw, # remainder of parameters are defaults specified to ensure any alternate settings get overridden
                               save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                               min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=taglab_clip,title='DEM', description='Generated by Agisoft Metashape')

        
        ###### 4. Clean up project ######
        self.cleanProject()
        self.updateAndSave()
        
        ###### 5. Finish Script ######
        print("Script finished")
        if warnings:
            warning_text = "\n\n".join("- " + w for w in warnings)
            Metashape.app.messageBox(
                "ReefShape has finished processing, but some steps were skipped or modified:\n\n"
                + warning_text
                + "\n\nRemember to verify all data products before analysis."
            )
        else:
            Metashape.app.messageBox("ReefShape has finished processing!\n\nRemember to verify all data products to sufficient data quality before beginning analysis.")
        self.saveSettings()
        self.close()


    ############# Workflow Functions #############

    def updateAndSave(self):
        print("Saving Project...")
        Metashape.app.update()
        Metashape.app.document.save()
        print("Project Saved")

    def refreshChunkNameDisplay(self):
        try:
            chunk = self.doc.chunk
            if hasattr(self, "txtChunkName"):
                self.txtChunkName.setPlainText(chunk.label)
            if hasattr(self, "add_photos_groupbox") and hasattr(self.add_photos_groupbox, "txtChunkName"):
                self.add_photos_groupbox.txtChunkName.setPlainText(chunk.label)
        except Exception as e:
            print(f"Error updating chunk name in GUI: {e}")
    
    
    def createScalebars(self, path):
        '''
        Creates scalebars in the project's active chunk based on information from
        a user-provided text file
        '''
        iNumScaleBars=len(self.chunk.scalebars)
        iNumMarkers=len(self.chunk.markers)
        # Check for existing markers
        if (iNumMarkers == 0):
            raise Exception("No markers found! Unable to create scalebars.")
        # Check for already existing scalebars
        if (iNumScaleBars > 0):
            print('There are already ',iNumScaleBars,' scalebars in this project.')

        try:
            file = open(path)
            eof = False
            line = file.readline()
            while not eof:
              # split the line and load into variables
              point1, point2, dist, acc = line.split(",")
              # find the corresponding scalebar, if there is any
              scalebarfound = 0
              if (iNumScaleBars > 0):
                 for sbScaleBar in self.chunk.scalebars:
                    strScaleBarLabel_1 = point1 + "_" + point2
                    strScaleBarLabel_2 = point2 + "_" + point1
                    if sbScaleBar.label == strScaleBarLabel_1 or sbScaleBar.label == strScaleBarLabel_2:
                       # scalebar found
                       scalebarfound = 1
                       # update it
                       sbScaleBar.reference.distance = float(dist)
                       sbScaleBar.reference.accuracy = float(acc)
              # Check if scalebar was found
              if (scalebarfound == 0):
                 # Scalebar was not found: add a new one
                 # Find Marker 1 with label described by "point1"
                 bMarker1Found = 0
                 for marker in self.chunk.markers:
                    if (marker.label == point1):
                       marker1 = marker
                       bMarker1Found = 1
                       break
                 # Find Marker 2 with label described by "point2"
                 bMarker2Found = 0
                 for marker in self.chunk.markers:
                    if (marker.label == point2):
                       marker2 = marker
                       bMarker2Found = 1
                       break
                 # Check if both markers were detected
                 if bMarker1Found == 1 and bMarker2Found == 1:
                    # Markers were detected. Create new scalebar.
                    sbScaleBar = self.chunk.addScalebar(marker1,marker2)
                    # update it:
                    sbScaleBar.reference.distance = float(dist)
                    sbScaleBar.reference.accuracy = float(acc)
                 else:
                    # Marker not found. Raise exception and print, but do not stop process.
                    if (bMarker1Found == 0):
                       print("Marker " + point1 + " was not found!")
                    if (bMarker2Found == 0):
                       print("Marker " + point2 + " was not found!")
              #All done.
              #reading the next line in input file
              line = file.readline()
              if not len(line):
                 eof = True
                 break
            file.close()
            print(" --- Scalebars Created --- ")

        except:
           return "Script error: There was a problem reading scalebar data\n"


    def referenceModel(self, path, formatting):
        '''
        Imports marker georeferencing data from a user-provided csv, for which
        the user may specify the correct column arrangement. The function will raise
        an exception if the referencing information is not numeric

        If the project already has georeferencing information, this information will be overwritten.
        '''
        # set indices for which columns lat/long data is in
        n = formatting[0] - 1
        x = formatting[1] - 1
        y = formatting[2] - 1
        z = formatting[3] - 1
        X = formatting[4] - 1
        Y = formatting[5] - 1
        Z = formatting[6] - 1
        skip = formatting[7] - 1

        try:
            # create file path for reformatted georeferencing data
            new_path = path[:-4] + "_reformat.csv"

            # read in raw georeferencing data and put it in a list
            ref = []
            file = open(path)
            eof = False
            line = file.readline()
            # skip the specified number of rows when reading in data prior to reformatting
            for i in range(0, skip):
                line = file.readline()

            while not eof:
                marker_ref = line.strip().split(sep = ",")
                ref_line = [marker_ref[n], marker_ref[x], marker_ref[y], marker_ref[z], marker_ref[X], marker_ref[Y], marker_ref[Z]]
                for item in ref_line[1:]:
                    try:
                        item_float = float(item)
                    except Exception as err:
                        print("Script error: '" + item + "'" + " cannot be read as a coordinate value. Your column assignments may be incorrect.")
                        raise
                #print(ref_line)
                if(not len(ref)):
                    ref = [ref_line]
                elif(len(ref) > 0):
                    ref.append(ref_line)

                line = file.readline()
                if not len(line):
                     eof = True
                     break

            file.close()

            header = ["label", "x", "y", "z", "X_acc", "Y_acc", "Z_acc"]
            # write cleaned georeferencing data to a new file
            with open(new_path, 'w', newline = '') as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(ref)
            f.close()

            # import new georeferencing data
            self.chunk.importReference(path = new_path, format = Metashape.ReferenceFormatCSV, delimiter = ',', columns = "nxyzXYZ", skip_rows = skip,
                                  crs = self.chunk.crs, ignore_labels=False, create_markers=False, threshold=0.1, shutter_lag=0)

            os.remove(new_path)
            print(" --- Georeferencing Updated --- ")
        except:
            return "Script error: There was a problem reading georeferencing data\n"



    def isOptimized(self):
        '''
        Returns True if camera optimization has already been performed on the
        current chunk (manually via the Metashape GUI or by an earlier run of
        this script). Detection works by checking for 'optimize/' keys in
        chunk.tie_points.meta — Metashape writes these whenever optimizeCameras
        runs, so the signal is independent of how optimization was triggered.
        '''
        tp = self.chunk.tie_points
        if not tp:
            return False
        try:
            return any(k.startswith('optimize/') for k in tp.meta.keys())
        except AttributeError:
            return False


    def gradSelectsOptimization(self):
        '''
        Refines camera alignment by filtering out tie points with high error
        '''
        # define thresholds for reconstruction uncertainty and projection accuracy
        reconun = float(25)
        projecac = float(15)

        # initiate filters, remove points above thresholds
        f = Metashape.TiePoints.Filter()
        f.init(self.chunk, Metashape.TiePoints.Filter.ReconstructionUncertainty)
        f.removePoints(reconun)

        f = Metashape.TiePoints.Filter()
        f.init(self.chunk, Metashape.TiePoints.Filter.ProjectionAccuracy)
        f.removePoints(projecac)

        # optimize camera locations based on all distortion parameters
        self.chunk.optimizeCameras(fit_f=True, fit_cx=True, fit_cy=True,
                              fit_b1=True, fit_b2=True, fit_k1=True,
                              fit_k2=True, fit_k3=True, fit_k4=True,
                              fit_p1=True, fit_p2=True, fit_corrections=True,
                              adaptive_fitting=False, tiepoint_covariance=False)


    def create_shape_from_markers(self, marker_list):
        '''
        Creates a boundary shape from a given set of markers
        '''
        if not self.chunk:
                print("Empty project, script aborted")
                return 0
        if len(marker_list) < 4:
                print("At least four markers required to create a plot. Boundary creation aborted.")
                return 0

        T = self.chunk.transform.matrix
        crs = self.chunk.crs
        if not self.chunk.shapes:
                self.chunk.shapes = Metashape.Shapes()
                self.chunk.shapes.crs = self.chunk.crs
        shape_crs = self.chunk.shapes.crs


        coords = [shape_crs.project(T.mulp(marker.position)) for marker in marker_list]

        shape = self.chunk.shapes.addShape()
        shape.label = "Marker Boundary"
        shape.geometry.type = Metashape.Geometry.Type.PolygonType
        shape.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
        shape.geometry = Metashape.Geometry.Polygon(coords)

        return 1

    def boundaryCreation(self):
        '''
        Wrapper function to create a boundary shape. Iterates self.corner_markers
        in the order the user specified so the resulting polygon follows that
        cyclic order (and won't self-intersect as long as the user listed the
        corners in cyclic order). Returns True if a boundary polygon was created,
        False if it could not be (e.g. fewer than 4 matching corner markers, or
        labels with no digits to match against).
        '''
        m_list = []
        for corner_num in self.corner_markers:
            for marker in self.chunk.markers:
                # Skip markers we can't safely use: no estimated position
                # (not placed/converged) or no digits in label to compare.
                if marker.position is None:
                    continue
                digits = re.search(r'(\d+)', marker.label)
                if digits is None:
                    continue
                if str(corner_num) == digits.group(0):
                    m_list.append(marker)
                    break  # only one marker per corner_num
        if len(m_list) < 4:
            print("Could not find all 4 corner markers {} in the chunk. "
                  "Boundary creation skipped.".format(self.corner_markers))
            return False
        return bool(self.create_shape_from_markers(m_list[:4]))



    def cleanProject(self):

        # Remove orthophotos without removing orthomosaic
        ortho = self.chunk.orthomosaic
        if ortho:
            ortho.removeOrthophotos()

        # Remove key points (if present)
        sparsecloud = self.chunk.tie_points
        if sparsecloud:
            sparsecloud.removeKeypoints()

        # Remove depth maps (if present)
        depthmaps = self.chunk.depth_maps
        if depthmaps:
            depthmaps.clear()
            
    def format_date_label(self, date_str):
        """
        Tries to convert YYYYMMDD string to human-readable format, e.g. "20250612" -> "June 12, 2025"
        If chunk label is not in this format, just uses chunk label
        """
        try:
            date_obj = datetime.strptime(date_str, "%Y%m%d")
            return date_obj.strftime("%B %d, %Y")
        except ValueError:
            return date_str

    # ----- Slots for Dialog Box -----
    def getOutputDir(self):
        self.output_dir = QtWidgets.QFileDialog.getExistingDirectory(self, 'Open directory', self.project_folder)
        if(self.output_dir):
            self.txtOutputDir.setPlainText(self.output_dir)
        else:
            self.txtOutputDir.setPlainText("No Folder Selected")

    def getCRS(self):
        crs = Metashape.app.getCoordinateSystem("Select Coordinate System",'LOCAL_CS["Local Coordinates (m)",LOCAL_DATUM["Local Datum",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]]]')
        if(crs):
            Metashape.app.document.chunk.crs = crs
            self.txtCRS.setPlainText(crs.name)

    def onResolutionChange(self):
        '''
        Slot: enables/disables the custom ortho resolution input box
        '''
        use_default_res = self.checkBoxDefaultRes.isChecked()
        self.labelCustomRes.setEnabled(not use_default_res)
        self.spinboxCustomRes.setEnabled(not use_default_res)

    # END CLASS FullWorkflowDlg

def run_script():
    try:
        app = QtWidgets.QApplication.instance()
        parent = app.activeWindow()
        dlg = FullWorkflowDlg(parent)
        dlg.exec()
    except Exception as e:
        show_error_dialog("Workflow Error", str(e))
            
    

# add function to menu
label = "ReefShape/Full ReefShape Workflow"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
