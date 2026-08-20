'''
Align Chunks from Different Time Points
Sam Marshall

This file implements a dialog box used to align two photomosaic plots to one another.

It is a standalone script that is meant to be used in conjunction with the underwater workflow
implemented in FullUW_dialog.py. Once the user has collected two sets of photos, this script
can be used to make sure the two sets line up before processing the second data set. The
data from the first time point must be already processed before this script is used.

The script works by exporting Metashape's estimated reference information for markers in the first
time point at sub-millimeter precision, then using this information to georeference markers in the
second time point (and subsequent data sets). Using high-precision estimated coordinates rather
than the source coordinates enables Metashape to warp the data products from the second time point
so that they align pixel-to-pixel with those from the first, even though the actual georeferencing
(ie where on earth the reef is located) can never be that precise.
'''

import Metashape
import os
from os import path
import sys
import csv
import re
from PySide2 import QtGui, QtCore, QtWidgets # NOTE: the style enums (such as alignment) seem to be in QtCore.Qt
from ui_components import AddPhotosGroupBox, BoundaryMarkerDlg, CollapsibleGroupBox, GeoreferenceGroupBox
from modules import reefshape_align
from modules.reefshape_core import Reporter, WorkflowError


class AlignChunksDlg(QtWidgets.QDialog):
    def __init__(self, parent):
        # initialize main dialog window
        QtWidgets.QDialog.__init__(self, parent)
        # QDialog.exec() already provides modal behavior. Setting ApplicationModal
        # on top deadlocks Metashape's Light/Dark themes (custom QStyles install
        # application-level event filters that conflict with ApplicationModal).
        self.setWindowTitle("Align Chunks")
        # Minimum width is set after the layout is in place so it reflects
        # the form's actual natural width plus chrome (see end of __init__).
        # set document info
        self.doc = Metashape.app.document
        self.project_folder = path.dirname(self.doc.path)
        self.project_name = path.basename(self.doc.path)[:-4] # extracts project name from file path
        self.reference_chunk = self.doc.chunk # set default reference chunk to current active chunk
        self.chunk = self.doc.chunk
        self.chunk_keys = []
        self.output_dir = self.project_folder
        self.damaged_markers = []

        # set default corner marker arrangement - is this needed for timepoint two?
        self.corner_markers = [1, 2, 3, 4]

        # ---- Project Setup Groupbox ----
        # create project setup groupbox - this is a modified AddPhotosGroupBox
        self.project_setup = AddPhotosGroupBox(self)
        self.project_setup.chunkUpdated.connect(self.updateChunkList)
        self.project_setup.labelNamingConventions.hide()
        self.project_setup.labelProjectName.hide()
        self.project_setup.txtProjectName.hide()
        self.project_setup.btnProjectName.hide()
        #self.project_setup.labelChunkName.hide()
        #self.project_setup.txtChunkName.hide()
        #self.project_setup.btnChunkName.hide()
        self.project_setup.btnCreateProj.hide()

        # add target types
        self.targetTypes = [
            ("Circular Target 12 Bit", Metashape.CircularTarget12bit),
            ("Circular Target 14 Bit", Metashape.CircularTarget14bit),
            ("Circular Target 16 Bit", Metashape.CircularTarget16bit),
            ("Circular Target 20 Bit", Metashape.CircularTarget20bit),
            ("Circular Target", Metashape.CircularTarget),
            ("Cross Target", Metashape.CrossTarget)
        ]
        self.target_type = Metashape.CircularTarget12bit # set default target type
        self.labelTargetType = QtWidgets.QLabel("Select Target Type:")
        self.comboTargetType = QtWidgets.QComboBox()
        for target_type in self.targetTypes:
            self.comboTargetType.addItem(target_type[0])

        #widget group for create chunk button and target type selector
        layout_target_type = QtWidgets.QHBoxLayout()
        # add a button to create a new chunk 
        self.btnCreateChunk = QtWidgets.QPushButton("Create Chunk")
        layout_target_type.addWidget(self.btnCreateChunk)
        layout_target_type.addStretch()
        layout_target_type.addWidget(self.labelTargetType)
        layout_target_type.addWidget(self.comboTargetType)
        
        #add this widget group to the top of the project setup layout.
        # Use contentLayout() (not layout()) so the row goes inside the
        # collapsible content rather than between the title bar and the
        # collapsible region.
        self.project_setup.contentLayout().insertLayout(0, layout_target_type)
        # add the button to create_proj_layout - because of the way Qt passes ownership of layouts
        # around, create_proj_layout must be accessed via the main layout's itemAt() function
        # self.project_setup.layout().itemAt(4).addWidget(self.project_setup.btnCreateChunk)

        # ---- General Groupbox ----
        self.labelRefChunk = QtWidgets.QLabel("Select Reference Chunk:")
        self.comboRefChunk = QtWidgets.QComboBox()
        ref_chunk_layout = QtWidgets.QHBoxLayout()
        ref_chunk_layout.addWidget(self.labelRefChunk)
        ref_chunk_layout.addWidget(self.comboRefChunk)

        self.labelNewChunk = QtWidgets.QLabel("Select Active Chunk:")
        self.comboNewChunk = QtWidgets.QComboBox()
        new_chunk_layout = QtWidgets.QHBoxLayout()
        new_chunk_layout.addWidget(self.labelNewChunk)
        new_chunk_layout.addWidget(self.comboNewChunk)

        self.labelDamagedMarkers = QtWidgets.QLabel("Select Damaged Markers:")
        self.txtDamagedMarkers = QtWidgets.QPlainTextEdit("No damaged markers")
        self.txtDamagedMarkers.setFixedHeight(40)
        self.txtDamagedMarkers.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtDamagedMarkers.setReadOnly(True)
        self.comboDamagedMarkers = QtWidgets.QComboBox()
        self.btnRemoveMarker = QtWidgets.QPushButton("Undo Add Marker")
        damaged_marker_layout = QtWidgets.QHBoxLayout()
        damaged_marker_layout.addWidget(self.labelDamagedMarkers)
        damaged_marker_layout.addWidget(self.txtDamagedMarkers)
        add_marker_layout = QtWidgets.QHBoxLayout()
        add_marker_layout.addStretch()
        add_marker_layout.addWidget(self.comboDamagedMarkers)
        add_marker_layout.addWidget(self.btnRemoveMarker)

        self.general_groupbox = CollapsibleGroupBox("General")
        general_layout = QtWidgets.QVBoxLayout()
        general_layout.addLayout(ref_chunk_layout)
        general_layout.addLayout(new_chunk_layout)
        general_layout.addLayout(damaged_marker_layout)
        general_layout.addLayout(add_marker_layout)
        # Absorb vertical slack so collapsing the sibling Project Setup panel
        # doesn't inflate gaps between this panel's rows.
        general_layout.addStretch(1)
        self.general_groupbox.setLayout(general_layout)


        self.btnOk = QtWidgets.QPushButton("Ok")
        self.btnOk.setFixedSize(70, 40)
        self.btnOk.setToolTip("Align chunks")

        self.btnClose = QtWidgets.QPushButton("Close")
        self.btnClose.setFixedSize(70, 40)

        ok_layout = QtWidgets.QHBoxLayout()
        ok_layout.addWidget(self.btnOk)
        ok_layout.addWidget(self.btnClose)

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(self.project_setup)
        main_layout.addWidget(self.general_groupbox)
        # Stretch between the panels and the OK/Close row so a collapsed
        # panel just shrinks the dialog instead of inflating the gap to the
        # buttons.
        main_layout.addStretch(1)
        main_layout.addLayout(ok_layout)
        self.setLayout(main_layout)

        # Width: pin to the form's natural width plus the platform scrollbar
        # extent + a little chrome. Without this, macOS's window chrome
        # accounting can leave the dialog one or two pixels too narrow and
        # force a horizontal scrollbar.
        sb_extent = QtWidgets.QApplication.style().pixelMetric(
            QtWidgets.QStyle.PM_ScrollBarExtent)
        self.setMinimumWidth(main_layout.sizeHint().width() + sb_extent + 20)

        # Resize the dialog to fit content whenever a panel collapses or
        # expands. Deferred via singleShot so the layout settles before
        # adjustSize() reads the new sizeHint.
        for panel in (self.project_setup, self.general_groupbox):
            panel.toggled.connect(
                lambda _checked: QtCore.QTimer.singleShot(0, self._fitToContent))

        # populate combo boxes with options
        self.updateChunkList()
        self.comboRefChunk.popupAboutToBeShown = self.updateChunkList
        self.comboNewChunk.popupAboutToBeShown = self.updateChunkList
        self.updateMarkerList()
        self.comboDamagedMarkers.popupAboutToBeShown = self.updateMarkerList
        self.txtDamagedMarkers.setPlainText("No Damaged Markers")
        # connect signals and slots
        self.btnCreateChunk.clicked.connect(self.createChunk)
        self.comboRefChunk.activated.connect(self.setReferenceChunk)
        self.comboNewChunk.activated.connect(self.setActiveChunk)
        self.comboDamagedMarkers.currentIndexChanged.connect(self.addDamagedMarker)
        self.comboTargetType.currentIndexChanged.connect(self.onTargetTypeChange)
        self.btnRemoveMarker.clicked.connect(self.removeDamagedMarker)
        self.btnOk.clicked.connect(self.alignChunks)
        self.btnClose.clicked.connect(self.reject)

        # Apply chunk-state defaults (collapse Project Setup if photos are
        # already in the active chunk, mark it Complete). Deferred via
        # singleShot so the dialog has finished its initial layout pass
        # before we ask it to recalc geometry.
        QtCore.QTimer.singleShot(0, self._applyChunkStateDefaults)

    def _applyChunkStateDefaults(self):
        '''Collapse Project Setup when the active chunk already has photos
        — Align Timepoints is typically run on a chunk that already has its
        second-timepoint photos loaded, so showing the Project Setup panel
        open by default is just visual noise. Marks it "Complete" so the
        user knows why it auto-collapsed.'''
        if not self.chunk:
            return
        has_cameras = len(self.chunk.cameras) > 0
        self.project_setup.setComplete(has_cameras)
        self.project_setup.setCollapsed(has_cameras)
        self._fitToContent()

    def _fitToContent(self):
        '''Resize the dialog vertically to fit current content. Called
        whenever a collapsible panel toggles. Width stays pinned via the
        minimum-width set at construction.'''
        self.layout().activate()
        self.adjustSize()

    def alignChunks(self):
        '''
        OK-button slot: run the alignment, but make sure any exception
        re-enables the dialog so the user can adjust their inputs and try
        again (or close the dialog). Without this wrapper an error during
        alignment freezes the dialog in setEnabled(False), and on macOS in
        the Light/Dark theme even the title-bar close button is a Qt child
        widget that becomes unclickable.
        '''
        try:
            self._alignChunksImpl()
        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Alignment Error", str(e))
            self.setEnabled(True)

    def _alignChunksImpl(self):
        '''
        Gather the user's choices, then hand the alignment to reefshape_align.
        That module is shared with the headless batch runner so the menu
        script and a batched re-photography job align timepoints identically.
        '''
        print("Script started...")
        self.setEnabled(False)

        if len(self.doc.chunks) < 2:
            Metashape.app.messageBox(
                "Unable to align chunks: Please create a second chunk to align")
            self.setEnabled(True)
            return

        try:
            reefshape_align.align_timepoints(
                doc=self.doc,
                reference_chunk=self.reference_chunk,
                chunk=self.chunk,
                target_type=self.target_type,
                damaged_markers=[m.label for m in self.damaged_markers if m],
                reporter=Reporter(),
            )
        except WorkflowError as err:
            Metashape.app.messageBox(str(err))
            print("Script aborted")
            self.setEnabled(True)
            return

        self.updateAndSave()
        self.reject()


    def updateAndSave(self):
        ''' saves changes to the project and updates the user interface '''
        print("Saving Project...")
        Metashape.app.update()
        self.doc.save()
        print("Project Saved")

    """def createChunk(self):
        '''
        Slot: creates a new chunk in the project and prompts the user for a name
        '''
        new_name, ok = QtWidgets.QInputDialog().getText(self, "Create Chunk", "Chunk name:")
        if(new_name and ok):
            self.chunk = self.doc.addChunk()
            self.doc.chunk = self.chunk # set the projects active chunk to be the new chunk
            self.project_setup.txtAddPhotos.setPlainText("Select Folder")
            self.chunk.label = new_name
        self.updateChunkList()
        """
    def createChunk(self):
        '''
        Slot: creates a new chunk in the project with default naming
        '''
        self.chunk = self.doc.addChunk()
        self.doc.chunk = self.chunk # set the projects active chunk to be the new chunk
        self.project_setup.txtAddPhotos.setPlainText("Select Folder")
        self.project_setup.txtChunkName.setPlainText(self.doc.chunk.label)
        self.updateChunkList()
        self.updateMarkerList()
        
    def updateChunkList(self):
        '''
        Slot: populates/updates the list of chunks to choose from in each combo box
        '''
        # chunk keys do not necessarily correspond to the number of chunks in the project,
        # so we need a separate list in order to link the combo boxes with the actual chunk list
        self.chunk_keys.clear()
        self.comboRefChunk.clear()
        self.comboNewChunk.clear()
        for chunk in self.doc.chunks:
            self.comboRefChunk.addItem(chunk.label)
            self.comboNewChunk.addItem(chunk.label)
            self.chunk_keys.append(chunk.key)
        self.comboRefChunk.setCurrentIndex(self.chunk_keys.index(self.reference_chunk.key))
        self.comboNewChunk.setCurrentIndex(self.chunk_keys.index(self.chunk.key))
        self.updateMarkerList()
        
    def setReferenceChunk(self):
        '''
        Slot: when the user selects a new chunk to be the reference chunk, updates the corresponding
        member variable and adds the chunk's markers to the markers combo box
        '''
        self.reference_chunk = self.doc.findChunk(self.chunk_keys[self.comboRefChunk.currentIndex()])
        self.updateMarkerList()

    def setActiveChunk(self):
        '''
        Slot: when the user selects a new chunk to be the active chunk or creates a new chunk,
        updates the corresponding member variable and adds the chunk's markers to the markers combo box
        '''
        self.chunk = self.doc.findChunk(self.chunk_keys[self.comboNewChunk.currentIndex()])
        self.doc.chunk = self.chunk
        self.updateMarkerList()

    def updateMarkerList(self):
        '''
        Slot: populates/updates the list of markers to choose as damaged when the user selects
        a new chunk to be the reference chunk
        '''
        self.comboDamagedMarkers.clear()
        if(len(self.reference_chunk.markers) > 0):
            for marker in self.reference_chunk.markers:
                self.comboDamagedMarkers.addItem(marker.label, marker)
        self.comboDamagedMarkers.addItem("Add damaged marker")
        self.comboDamagedMarkers.setCurrentIndex(len(self.reference_chunk.markers))
        self.damaged_markers.clear()
        self.txtDamagedMarkers.setPlainText("No Damaged Markers")

    """def addDamagedMarker(self):
        '''
        Slot: when the user selects a marker from the dropdown list, add it to the list of
        damaged markers and update the text box displaying the list
        '''
        self.damaged_markers.append(self.reference_chunk.findMarker(self.comboDamagedMarkers.currentIndex()))
        self.txtDamagedMarkers.setPlainText(str(self.damaged_markers))
        """
    def addDamagedMarker(self):
        """
        Slot: when the user selects a marker from the dropdown list,
        add it to the damaged markers list and update the text box.
        """
        marker = self.comboDamagedMarkers.currentData()
        if marker is not None:
            if marker not in self.damaged_markers:
                self.damaged_markers.append(marker)
            self.updateDamagedMarkerDisplay()
        else:
            print("Warning: selected marker is None")
    def updateDamagedMarkerDisplay(self):
        if not self.damaged_markers:
            self.txtDamagedMarkers.setPlainText("No damaged markers")
        else:
            labels = [marker.label for marker in self.damaged_markers if marker is not None]
            self.txtDamagedMarkers.setPlainText(", ".join(labels))    
    
    def removeDamagedMarker(self):
        '''
        Slot: removes the most recently added marker from the list of added markers
        '''
        if(not len(self.damaged_markers) == 0):
            self.damaged_markers.pop()
            self.txtDamagedMarkers.setPlainText(str(self.damaged_markers))
            if(len(self.damaged_markers) == 0):
                self.comboDamagedMarkers.setCurrentIndex(len(self.reference_chunk.markers))
                self.txtDamagedMarkers.setPlainText("No Damaged Markers")

    def onTargetTypeChange(self):
        '''
        Updates target type member variable when the user selects a new type
        '''
        target_type_index = self.comboTargetType.currentIndex()
        self.target_type = self.targetTypes[target_type_index][1]


    def closeEvent(self, event):
        self.reject()
        event.accept()
        # END CLASS AlignChunksDlg
def run_script():
    try:
        app = QtWidgets.QApplication.instance()
        parent = app.activeWindow()
        dlg = AlignChunksDlg(parent)
        dlg.exec()
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "Error", str(e))
        


# add function to menu
label_old = "Custom/Align Chunks"
label = "ReefShape/Align Timepoints"
Metashape.app.removeMenuItem(label_old)
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
