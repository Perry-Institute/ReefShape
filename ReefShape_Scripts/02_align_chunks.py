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
from os import path
import sys
import csv
import re
from PySide2 import QtGui, QtCore, QtWidgets # NOTE: the style enums (such as alignment) seem to be in QtCore.Qt
from ui_components import AddPhotosGroupBox, BoundaryMarkerDlg, GeoreferenceGroupBox


class AlignChunksDlg(QtWidgets.QDialog):
    def __init__(self, parent):
        # initialize main dialog window
        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Align Chunks")
        self.setMinimumWidth(500)
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
        self.project_setup.labelNamingConventions.hide()
        self.project_setup.labelProjectName.hide()
        self.project_setup.txtProjectName.hide()
        self.project_setup.btnProjectName.hide()
        self.project_setup.labelChunkName.hide()
        self.project_setup.txtChunkName.hide()
        self.project_setup.btnChunkName.hide()
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

        layout_target_type = QtWidgets.QHBoxLayout()
        layout_target_type.addWidget(self.labelTargetType)
        layout_target_type.addWidget(self.comboTargetType)
        layout_target_type.addStretch()

        # add a button to create a new chunk
        self.btnCreateChunk = QtWidgets.QPushButton("Create Chunk")
        layout_target_type.addWidget(self.btnCreateChunk)
        self.project_setup.layout().addLayout(layout_target_type)
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

        general_groupbox = QtWidgets.QGroupBox("General")
        general_layout = QtWidgets.QVBoxLayout()
        general_layout.addLayout(ref_chunk_layout)
        general_layout.addLayout(new_chunk_layout)
        general_layout.addLayout(damaged_marker_layout)
        general_layout.addLayout(add_marker_layout)
        general_groupbox.setLayout(general_layout)


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
        main_layout.addWidget(general_groupbox)
        main_layout.addLayout(ok_layout)
        self.setLayout(main_layout)

        # populate combo boxes with options
        self.updateChunkList()
        self.updateMarkerList()
        self.txtDamagedMarkers.setPlainText("No Damaged Markers")
        # connect signals and slots
        self.btnCreateChunk.clicked.connect(self.createChunk)
        self.comboRefChunk.activated.connect(self.setReferenceChunk)
        self.comboNewChunk.activated.connect(self.setActiveChunk)
        self.comboDamagedMarkers.currentIndexChanged.connect(self.addDamagedMarker)
        self.comboTargetType.currentIndexChanged.connect(self.onTargetTypeChange)
        self.btnRemoveMarker.clicked.connect(self.removeDamagedMarker)
        self.btnOk.clicked.connect(self.alignChunks)
        QtCore.QObject.connect(self.btnClose, QtCore.SIGNAL("clicked()"), self, QtCore.SLOT("reject()"))


        self.exec()

    def alignChunks(self):
        '''
        Contains main workflow
        '''
        print("Script started...")
        self.setEnabled(False)

        if(len(self.doc.chunks) < 2):
            Metashape.app.messageBox("Unable to align chunks: Please create a second chunk to align")
            self.setEnabled(True)
            return

        est_ref_path = os.path.join(self.project_folder, self.reference_chunk.label + "_est_ref.csv")
        # export estimated reference from old chunk - in decimal degrees, 9 decimal places is about 0.1mm
        self.reference_chunk.exportReference(path = est_ref_path, format = Metashape.ReferenceFormatCSV,
                            items = Metashape.ReferenceItemsMarkers, columns = 'nouvwUVW', delimiter = ",", precision = 9)

        self.correctEnabledMarkers(est_ref_path)

        # detect markers in new chunk
        if(len(self.chunk.markers) == 0): # only detect markers if there are currently no markers in selected chunk
            self.chunk.detectMarkers(target_type = self.target_type, tolerance=20, filter_mask=False, inverted=False, noparity=False, maximum_residual=5, minimum_size=0, minimum_dist=5)

        # import reference to new chunk
        self.chunk.importReference(path = est_ref_path, format = Metashape.ReferenceFormatCSV, delimiter = ',', columns = "noxyz", skip_rows = 1,
                              crs = self.reference_chunk.crs, ignore_labels=False, create_markers=False, threshold=0.1, shutter_lag=0)

        # adjust accuracy for damaged markers - accuracy is set in meters
        for marker in self.chunk.markers:
            marker.reference.accuracy = [0.0001, 0.0001, 0.0001]
            for damaged_marker in self.damaged_markers:
                if(marker.label == damaged_marker.label):
                    marker.reference.accuracy = [1, 1, 1]

        self.chunk.updateTransform()
        self.updateAndSave()
        self.reject()

    def updateAndSave(self):
        ''' saves changes to the project and updates the user interface '''
        print("Saving Project...")
        Metashape.app.update()
        self.doc.save()
        print("Project Saved")

    def createChunk(self):
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
                self.comboDamagedMarkers.addItem(marker.label)
        self.comboDamagedMarkers.addItem("Add damaged marker")
        self.comboDamagedMarkers.setCurrentIndex(len(self.reference_chunk.markers))
        self.damaged_markers.clear()
        self.txtDamagedMarkers.setPlainText("No Damaged Markers")

    def addDamagedMarker(self):
        '''
        Slot: when the user selects a marker from the dropdown list, add it to the list of
        damaged markers and update the text box displaying the list
        '''
        self.damaged_markers.append(self.reference_chunk.findMarker(self.comboDamagedMarkers.currentIndex()))
        self.txtDamagedMarkers.setPlainText(str(self.damaged_markers))

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


    def correctEnabledMarkers(self, path):
        '''
        Edits the estimated reference file so that only markers used for georeferencing in
        time point 1 are used to align time point 2.
        Currently, this functionality is broken in metashape, and all markers are marked as
        enabled.
        '''
        # read in raw georeferencing data and put it in a list
        file = open(path)
        eof = False
        header = file.readline() # skip first header containing crs info
        header = file.readline()
        ref = [header.split(sep = ",")] # save second header line containing column headers
        line = file.readline()
        index = 0 # use marker index to match file lines with markers - Metashape exports markers in the order of their indices

        while not eof:
            marker_ref = line.split(sep = ",")
            if(self.reference_chunk.markers[index].reference.enabled):
                ref.append(marker_ref)
            # marker_ref[1] = int(self.reference_chunk.markers[index].reference.enabled) # rewrite enabled flag
            line = file.readline()
            index += 1
            if (index >= len(self.reference_chunk.markers) or not len(line)):
                eof = True
                break
        file.close()
        # write edited reference info back to the file
        with open(path, 'w', newline = '') as f:
            writer = csv.writer(f)
            writer.writerows(ref)
        f.close()

    # END CLASS AlignChunksDlg
def run_script():
    app = QtWidgets.QApplication.instance()
    parent = app.activeWindow()

    dlg = AlignChunksDlg(parent)



# add function to menu
label_old = "Custom/Align Chunks"
label = "ReefShape/Align Timepoints"
Metashape.app.removeMenuItem(label_old)
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
