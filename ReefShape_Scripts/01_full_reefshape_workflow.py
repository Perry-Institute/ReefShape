"""
Full ReefShape Workflow
Sam Marshall & Will Greene
Perry Institute for Marine Science

Version 1.3, June 2026

Implements ReefShape underwater photogrammetry workflow developed by Will Greene
Many of the component scripts were written by Will Greene and Asif-ul Islam
These were assembled into this full workflow and UI by Sam Marshall
Subsequent updates have been made by Will Greene
"""
import Metashape
import os
from os import path
import sys
import csv
import json
import re
#import exifread
from datetime import datetime
from PySide2 import QtGui, QtCore, QtWidgets
from ui_components import AddPhotosGroupBox, BoundaryMarkerDlg, CollapsibleGroupBox, GeoreferenceGroupBox
import reefshape_core
from reefshape_core import WorkflowSettings, WorkflowError


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
    

class DialogReporter(reefshape_core.Reporter):
    """
    Routes reefshape_core's progress and warnings into the Metashape GUI.

    Only two hooks need overriding. info()/warn() keep printing to the console,
    which is where users of the menu script already watch progress, while
    confirm_uncropped_taglab() restores the one genuinely interactive decision
    in the workflow. Everything else the dialog used to announce mid-run is
    now returned in the WorkflowResult and reported once, at the end.
    """

    def __init__(self, dialog):
        self.dialog = dialog

    def step(self, name, index=None, total=None):
        super().step(name, index, total)
        # Keep the dialog painting during long stages; without this the window
        # goes unresponsive-grey on Windows for the whole of a mesh build.
        QtWidgets.QApplication.processEvents()

    def confirm_uncropped_taglab(self, settings):
        reply = QtWidgets.QMessageBox.question(
            self.dialog,
            "No boundary polygon",
            "No OuterBoundary polygon exists in this chunk, so the TagLab "
            "outputs cannot be clipped to the plot.\n\n"
            "Yes - continue and export uncropped TagLab products.\n"
            "No  - stop here and return to the dialog so you can uncheck "
            "TagLab outputs and re-run, or close the dialog to create a "
            "boundary manually (see scripts 06 or 08) before re-running.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return reply == QtWidgets.QMessageBox.Yes


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
        # NOTE: georeferencing state (whether to auto-detect markers, and the
        # corner marker arrangement) lives on self.georef_groupbox, which is
        # where the widgets that drive it are. This class used to carry its
        # own self.autoDetectMarkers and self.corner_markers as well; they
        # were never kept in sync with the groupbox, and the stale
        # autoDetectMarkers made the "exit for manual referencing" check read
        # as though it consulted the user's choice when it did not.

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
        Gather settings from the dialog, then hand the actual processing to
        reefshape_core. That module is shared with the headless batch runner,
        so a change to the pipeline reaches both front ends at once instead of
        having to be made twice and inevitably diverging.
        '''
        print("Script started...")
        self.setEnabled(False)
        self.chunk = Metashape.app.document.chunk

        settings = self.buildWorkflowSettings()

        if not settings.georef_enabled and self.chunk.model is None:
            Metashape.app.messageBox(
                "You have initiated the script without specifying georeferencing information. "
                "If you ran the align timepoints script first, clicking OK will simply complete "
                "the workflow in its entirety for you (no further action needed). \n\n If this "
                "is a new project without auto-detectable markers, the script will exit after "
                "creating a mesh to allow for manual referencing, leveling, and scaling. \n\n "
                "Once this information is added, run the script again to complete the remainder "
                "of the workflow.")

        try:
            result = reefshape_core.run_workflow(
                self.doc, self.chunk, settings, DialogReporter(self))
        except WorkflowError as err:
            Metashape.app.messageBox(str(err))
            print("Script aborted")
            self.setEnabled(True)
            return

        if result.status == reefshape_core.STOPPED_FOR_MANUAL_REFERENCING:
            Metashape.app.messageBox(
                "Image alignment and mesh building complete.\n\nNow, add referencing "
                "information, then re-run the full dialog script to complete processing.")
            self.close()
            return

        print("Script finished")
        if result.warnings:
            Metashape.app.messageBox(
                "ReefShape has finished processing, but some steps were skipped or modified:\n\n"
                + "\n\n".join("- " + w for w in result.warnings)
                + "\n\nRemember to verify all data products before analysis.")
        else:
            Metashape.app.messageBox(
                "ReefShape has finished processing!\n\nRemember to verify all data products "
                "to sufficient data quality before beginning analysis.")
        self.saveSettings()
        self.close()

    def buildWorkflowSettings(self):
        '''
        Translate the dialog's widget state into a WorkflowSettings.

        This is the whole of what the dialog contributes to processing: read
        every widget once, up front, so the pipeline never has to reach back
        into Qt for a value mid-run.
        '''
        georef = self.georef_groupbox

        # Skip the "More..." sentinel if it is somehow still active -- the
        # slot normally reverts off it. A crs of None means "leave the chunk's
        # CRS alone".
        crs = self.crs_options.get(self.comboCRS.currentText())

        return WorkflowSettings(
            crs=crs,
            generic_preselection=self.checkBoxPreSelect.isChecked(),
            # The combo runs Ultra High -> Lowest and Metashape's depth-map
            # downscale doubles at each step.
            mesh_quality_downscale=2 ** self.comboMeshQuality.currentIndex(),
            vertex_colors=self.checkBoxVertexColors.isChecked(),
            ortho_resolution=self.spinboxCustomRes.value(),
            use_default_resolution=self.checkBoxDefaultRes.isChecked(),

            georef_enabled=bool(georef.autoDetectMarkers),
            target_type=georef.target_type,
            scalebar_path=getattr(georef, "scalebars_path", ""),
            georef_path=getattr(georef, "georef_path", ""),
            ref_formatting=[
                georef.spinboxRefLabel.value(), georef.spinboxRefX.value(),
                georef.spinboxRefY.value(), georef.spinboxRefZ.value(),
                georef.spinboxXAcc.value(), georef.spinboxYAcc.value(),
                georef.spinboxZAcc.value(), georef.spinboxSkipRows.value()],
            corner_markers=georef.corner_markers,

            output_dir=self.output_dir,
            export_report=self.checkBoxReport.isChecked(),
            export_gis=self.checkBoxExport.isChecked(),
            export_taglab=self.checkBoxTagLab.isChecked(),
            project_name=self.project_name,
        )

    def refreshChunkNameDisplay(self):
        try:
            chunk = self.doc.chunk
            if hasattr(self, "txtChunkName"):
                self.txtChunkName.setPlainText(chunk.label)
            if hasattr(self, "add_photos_groupbox") and hasattr(self.add_photos_groupbox, "txtChunkName"):
                self.add_photos_groupbox.txtChunkName.setPlainText(chunk.label)
        except Exception as e:
            print(f"Error updating chunk name in GUI: {e}")


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
