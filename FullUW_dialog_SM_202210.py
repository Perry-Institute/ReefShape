"""
Full Underwater Imagery Workflow
Sam Marshall

Implements underwater photomosaic workflow developed by Will Greene
Many of the component scripts were written by Will Greene and Asif-ul Islam
These were assembled into this full workflow and UI by Sam Marshall

    Usage notes:
        - The script will only try to import scaling and georeferencing data if there are zero scalebars and will only try to
        detect markers if there are zero markers, regardless of whether the user has enabled auto-detect markers in the dialog box.
        This means that if markers and scalebars are added by hand or it takes multiple tries to get it to read the scaling and
        georeferencing data properly, the remainder of the workflow should execute whether or not the user selects auto-detectable markers.
        There are ways around this that will still break the script (eg georeferencing by hand without adding scalebars, then running the
        workflow with auto-detect markers enabled), but in general it should work regardless of how or in what order the user detects markers or
        scales the model.
            One notable exception is if the user wishes to have a mix of automatic and hand-placed markers: the script does not support this,
        but if these steps are done outside of the script it should build the rest of the outputs without issue.

        - The boundary creation is dependent on the order of the marker placement being correct. For example, if markers 1 - 4 are placed as
        shown below:
                    1 ---- 3
                    |      |
                    2 ---- 4
        then the boundary polygon will be in an hourglass shape instead of a box as it should be. If this occurs, you will need to draw the bounding
        box by hand and run the script again to recreate the outputs.
"""


import Metashape
from os import path
import sys
import csv
import re
from PySide2 import QtGui, QtCore, QtWidgets # NOTE: the style enums (such as alignment) seem to be in QtCore.Qt


# Checking compatibility
compatible_major_version = "2.0"
found_major_version = ".".join(Metashape.app.version.split('.')[:2])
if found_major_version != compatible_major_version:
    raise Exception("Incompatible Metashape version: {} != {}".format(found_major_version, compatible_major_version))


class AddPhotosGroupBox(QtWidgets.QGroupBox):
    def __init__(self, parent):
        # call parent constructor to initialize
        super().__init__("Project Setup")
        self.parent = parent
        self.chunk_name = parent.chunk.label
        self.project_path = ""

        self.labelAddPhotos = QtWidgets.QLabel("Add photos:")
        self.btnAddPhotos = QtWidgets.QPushButton("Select Folder")
        self.txtAddPhotos = QtWidgets.QPlainTextEdit("No file selected")
        self.txtAddPhotos.setFixedHeight(40)
        self.txtAddPhotos.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtAddPhotos.setReadOnly(True)

        # text input for file name and chunk name
        self.labelNamingConventions = QtWidgets.QLabel("Select a project and chunk name. To ensure consistency, we suggest "
                                                        "using the AGRRA site code\nas the project name and the date the data "
                                                        "was collected (in YYYYMMDD format) as the chunk name.")
        self.labelNamingConventions.setAlignment(QtCore.Qt.AlignCenter)
        self.labelProjectName = QtWidgets.QLabel("Project Name:")
        self.btnProjectName = QtWidgets.QPushButton("Select File")
        self.txtProjectName = QtWidgets.QPlainTextEdit("No file selected")
        self.txtProjectName.setFixedHeight(40)
        self.txtProjectName.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtProjectName.setReadOnly(True)

        self.labelChunkName = QtWidgets.QLabel("Chunk Name:")
        self.btnChunkName = QtWidgets.QPushButton("Create Chunk")
        self.txtChunkName = QtWidgets.QPlainTextEdit("Chunk 1")
        self.txtChunkName.setFixedHeight(40)
        self.txtChunkName.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        self.btnCreateProj = QtWidgets.QPushButton("Create Project")
        self.btnCreateProj.setFixedWidth(90)

        # -- create layouts and assemble widgets --
        main_layout = QtWidgets.QVBoxLayout()

        photos_dir_layout = QtWidgets.QHBoxLayout()
        photos_dir_layout.addWidget(self.labelAddPhotos)
        photos_dir_layout.addWidget(self.txtAddPhotos)
        photos_dir_layout.addWidget(self.btnAddPhotos)

        project_name_layout = QtWidgets.QHBoxLayout()
        project_name_layout.addWidget(self.labelProjectName)
        project_name_layout.addWidget(self.txtProjectName)
        project_name_layout.addWidget(self.btnProjectName)

        chunk_name_layout = QtWidgets.QHBoxLayout()
        chunk_name_layout.addWidget(self.labelChunkName)
        chunk_name_layout.addWidget(self.txtChunkName)
        chunk_name_layout.addWidget(self.btnChunkName)

        main_layout.addLayout(photos_dir_layout)
        main_layout.addWidget(self.labelNamingConventions)
        main_layout.addLayout(project_name_layout)
        main_layout.addLayout(chunk_name_layout)
        main_layout.addWidget(self.btnCreateProj)

        self.setLayout(main_layout)

        # connect buttons to slots
        self.btnAddPhotos.clicked.connect(self.getPhotoFolder)
        self.btnProjectName.clicked.connect(self.getProjectName)
        self.btnChunkName.clicked.connect(self.getChunkName)
        self.btnCreateProj.clicked.connect(self.createProject)

    def getPhotoFolder(self):
        self.photo_folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Open directory', self.parent.project_folder)
        if(self.photo_folder):
            self.txtAddPhotos.setPlainText(self.photo_folder)
        else:
            self.txtAddPhotos.setPlainText("No File Selected")

    def getProjectName(self):
        '''
        Slot to save project name
        '''
        self.project_path = QtWidgets.QFileDialog.getSaveFileName(self, 'Open file', self.parent.project_folder, "Metashape Project (*.psx)")[0]
        self.project_name = path.basename(self.project_path)[:-4]
        if(self.checkNaming(self.project_name) and self.project_name):
            self.txtProjectName.setPlainText(self.project_name)
        elif(not self.checkNaming(self.project_name)):
            Metashape.app.messageBox("Unable to save project: please select a name that includes only alphanumeric characters (abcABC123) and underscore (_) or dash (-), with no special characters (e.g. @$/.)")
        else:
            self.txtProjectName.setPlainText("No File Selected")

    def getChunkName(self):
        '''
        Slot to save chunk name
        '''
        self.chunk_name, ok = QtWidgets.QInputDialog().getText(self, "Create Chunk", "Chunk name:")
        if(self.checkNaming(self.chunk_name) and self.chunk_name and ok):
            self.txtChunkName.setPlainText(self.chunk_name)
        elif(not self.checkNaming(self.chunk_name) and ok):
            Metashape.app.messageBox("Unable to create chunk: please select a name that includes only alphanumeric characters (abcABC123) and underscore (_) or dash (-), with no special characters (e.g. @$/.)")
        else:
            self.txtProjectName.setPlainText("No File Selected")


    def checkNaming(self, name):
        '''
        Checks project and chunk names to ensure there are no special characters in them
        '''
        if(re.search("[\.\^\$\*\+\?\[\]\|\<\>&\\\]", name)):
            return False
        return True

    def createProject(self):
        '''
        Saves project to the designated path and renames the active chunk
        '''
        self.parent.chunk.label = self.chunk_name

        if(self.photo_folder and self.project_path):
            self.parent.doc.save(path = self.project_path) # save project with new name
            # add photos to active chunk
            try:
                image_list = os.listdir(self.photo_folder)
                photo_list = list()
                for photo in image_list:
                    if photo.rsplit(".",1)[1].lower() in  ["jpg", "jpeg", "tif", "tiff"]:
                        photo_list.append("/".join([self.photo_folder, photo]))
                self.parent.chunk.addPhotos(photo_list)
            except:
                Metashape.app.messageBox("Error adding photos")
                return

        elif(self.project_path):
            Metashape.app.messageBox("Unable to add photos: please select a folder to add photos from")
        elif(self.photo_folder):
            Metashape.app.messageBox("Unable to save project: please select a name and file path for the project")



class ReferenceFormatDlg(QtWidgets.QDialog):
    # dummy class to demonstrate creating a sub-dialog box
    # call like this: ref_dlg = ReferenceFormatDlg(self)
    def __init__(self, parent):
        self.chunk = parent.chunk
        self.crs = parent.CRS

        # initialize main dialog window
        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Import CSV")

        self.labelCRS = QtWidgets.QLabel("Coordinate System:")
        self.txtCRS = QtWidgets.QPlainTextEdit(self.crs.name)

        mainLayout = QtWidgets.QHBoxLayout()
        mainLayout.addWidget(self.labelCRS)
        mainLayout.addWidget(self.txtCRS)
        self.setLayout(mainLayout)

        self.exec()



class FullWorkflowDlg(QtWidgets.QDialog):

    def __init__(self, parent):

        # set document info
        self.doc = Metashape.app.document
        self.chunk = self.doc.chunk
        self.project_folder = path.dirname(self.doc.path)
        self.project_name = path.basename(self.doc.path)[:-4] # extracts project name from file path
        self.output_dir = self.project_folder

        # add crs options
        self.localCRS = Metashape.CoordinateSystem('LOCAL_CS["Local Coordinates (m)",LOCAL_DATUM["Local Datum",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]]]')
        self.defaultCRS = Metashape.CoordinateSystem('COMPD_CS["WGS 84 + EGM96 height",GEOGCS["WGS 84",DATUM["World Geodetic System 1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],TOWGS84[0,0,0,0,0,0,0],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.01745329251994328,AUTHORITY["EPSG","9102"]],AUTHORITY["EPSG","4326"]],VERT_CS["EGM96 height",VERT_DATUM["EGM96 geoid",2005,AUTHORITY["EPSG","5171"]],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AUTHORITY["EPSG","5773"]]]')
        self.CRS = self.chunk.crs
        self.autoDetectMarkers = False

        # initialize main dialog window
        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Run Underwater Workflow")

        # --- Build Widgets ---
        # these are declared as member variables so that they can be referenced and modified by slots that are outside of the constructor
        # -- General --
        # Coordinate System input
        self.labelCRS = QtWidgets.QLabel("Coordinate System:")
        self.btnCRS = QtWidgets.QPushButton("Select CRS")
        self.txtCRS = QtWidgets.QPlainTextEdit("Local Coordinates (m)")
        self.txtCRS.setFixedHeight(40)
        self.txtCRS.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtCRS.setReadOnly(True)

        # generic preselection
        self.checkBoxPreSelect = QtWidgets.QCheckBox("Enable Generic Preselection")
        self.checkBoxPreSelect.setChecked(True)
        self.checkBoxPreSelect.setToolTip("Generic preselection speeds up photo alignment, but for photo sets with severe caustics disabling it can make alignment more effective")

        # taglab outputs
        self.checkBoxTagLab = QtWidgets.QCheckBox("Create tiled outputs for use in TagLab")
        self.checkBoxTagLab.setChecked(True)
        self.checkBoxTagLab.setToolTip("TagLab requires image inputs to have certain size and compression parameters"
                                       "\n\nIf this option is checked, a second set of outputs will be created that are broken into blocks that can be used in TagLab")

        # directory input for exports
        self.labelOutputDir = QtWidgets.QLabel("Folder for outputs: ")
        self.btnOutputDir = QtWidgets.QPushButton("Select Folder")
        self.txtOutputDir = QtWidgets.QPlainTextEdit("No file selected")
        self.txtOutputDir.setFixedHeight(40)
        self.txtOutputDir.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtOutputDir.setReadOnly(True)

        # -- Georeferencing --

        # scaling/georeferencing type
        self.labelReference = QtWidgets.QLabel("Are you using auto-detectable markers for scaling and georeferencing?")
        self.comboReference = QtWidgets.QComboBox()
        self.comboReference.addItems(["No", "Yes"])
        self.comboReference.setCurrentIndex(0)

        # add target types
        self.targetTypes = [
            ("Circular Target 12 Bit", Metashape.CircularTarget12bit),
            ("Circular Target 14 Bit", Metashape.CircularTarget14bit),
            ("Circular Target 16 Bit", Metashape.CircularTarget16bit),
            ("Circular Target 20 Bit", Metashape.CircularTarget20bit),
            ("Circular Target", Metashape.CircularTarget),
            ("Cross Target", Metashape.CrossTarget)
        ]

        self.labelTargetType = QtWidgets.QLabel("Select Target Type:")
        self.comboTargetType = QtWidgets.QComboBox()
        self.comboTargetType.setEnabled(False)
        for target_type in self.targetTypes:
            self.comboTargetType.addItem(target_type[0])


        # file input for scalebars
        self.labelScaleFile = QtWidgets.QLabel("Scalebar File:")
        self.labelScaleFile.setEnabled(False)
        self.btnScaleFile = QtWidgets.QPushButton("Select File")
        self.btnScaleFile.setEnabled(False)
        self.txtScaleFile = QtWidgets.QPlainTextEdit("No file selected")
        self.txtScaleFile.setEnabled(False)
        self.txtScaleFile.setFixedHeight(40)
        self.txtScaleFile.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtScaleFile.setReadOnly(True)

        # file input for georeferencing
        self.labelGeoFile = QtWidgets.QLabel("Georeferencing File:")
        self.labelGeoFile.setEnabled(False)
        self.btnGeoFile = QtWidgets.QPushButton("Select File")
        self.btnGeoFile.setEnabled(False)
        self.txtGeoFile = QtWidgets.QPlainTextEdit("No file selected")
        self.txtGeoFile.setEnabled(False)
        self.txtGeoFile.setFixedHeight(40)
        self.txtGeoFile.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtGeoFile.setReadOnly(True)

        # add in spinbox widgets to define georeferencing format
        self.labelRefLabel = QtWidgets.QLabel("Label:")
        self.spinboxRefLabel = QtWidgets.QSpinBox()
        self.spinboxRefLabel.setMinimum(1)

        self.labelRefX = QtWidgets.QLabel("X:")
        self.spinboxRefX = QtWidgets.QSpinBox()
        self.spinboxRefX.setMinimum(1)
        self.spinboxRefX.setValue(2)

        self.labelRefY = QtWidgets.QLabel("Y:")
        self.spinboxRefY = QtWidgets.QSpinBox()
        self.spinboxRefY.setMinimum(1)
        self.spinboxRefY.setValue(3)

        self.labelRefZ = QtWidgets.QLabel("Z:")
        self.spinboxRefZ = QtWidgets.QSpinBox()
        self.spinboxRefZ.setMinimum(1)
        self.spinboxRefZ.setValue(4)

        self.labelAccuracy = QtWidgets.QLabel("Accuracy")
        self.spinboxXAcc = QtWidgets.QSpinBox()
        self.spinboxXAcc.setMinimum(1)
        self.spinboxXAcc.setValue(5)
        self.spinboxYAcc = QtWidgets.QSpinBox()
        self.spinboxYAcc.setMinimum(1)
        self.spinboxYAcc.setValue(6)
        self.spinboxZAcc = QtWidgets.QSpinBox()
        self.spinboxZAcc.setMinimum(1)
        self.spinboxZAcc.setValue(7)

        self.labelSkipRows = QtWidgets.QLabel("Start import at row:")
        self.spinboxSkipRows = QtWidgets.QSpinBox()
        self.spinboxSkipRows.setMinimum(1)

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
        main_layout = QtWidgets.QVBoxLayout()  # create main layout - this will hold sublayouts containing individual widgets

        # -- Create sublayouts --
        crs_layout = QtWidgets.QHBoxLayout()
        crs_layout.addWidget(self.labelCRS)
        crs_layout.addWidget(self.txtCRS)
        crs_layout.addWidget(self.btnCRS)

        checkbox_layout = QtWidgets.QHBoxLayout()
        checkbox_layout.addWidget(self.checkBoxPreSelect)
        checkbox_layout.addWidget(self.checkBoxTagLab)

        output_layout = QtWidgets.QHBoxLayout()
        output_layout.addWidget(self.labelOutputDir)
        output_layout.addWidget(self.txtOutputDir)
        output_layout.addWidget(self.btnOutputDir)

        autodetect_layout = QtWidgets.QHBoxLayout()
        autodetect_layout.addWidget(self.labelReference)
        autodetect_layout.addWidget(self.comboReference)
        autodetect_layout.addWidget(self.comboTargetType)

        scale_layout = QtWidgets.QHBoxLayout()
        scale_layout.addWidget(self.labelScaleFile)
        scale_layout.addWidget(self.txtScaleFile)
        scale_layout.addWidget(self.btnScaleFile)

        geo_layout = QtWidgets.QHBoxLayout()
        geo_layout.addWidget(self.labelGeoFile)
        geo_layout.addWidget(self.txtGeoFile)
        geo_layout.addWidget(self.btnGeoFile)

        ref_format_layout = QtWidgets.QGridLayout()
        ref_format_layout.setColumnStretch(0, 1)
        ref_format_layout.setColumnStretch(1, 10)
        ref_format_layout.setColumnStretch(2, 10)
        ref_format_layout.addWidget(self.labelRefLabel, 0, 0)
        ref_format_layout.addWidget(self.spinboxRefLabel, 0, 1)
        ref_format_layout.addWidget(self.labelAccuracy, 0, 2)

        ref_format_layout.addWidget(self.labelRefX, 1, 0)
        ref_format_layout.addWidget(self.spinboxRefX, 1, 1)
        ref_format_layout.addWidget(self.spinboxXAcc, 1, 2)

        ref_format_layout.addWidget(self.labelRefY, 2, 0)
        ref_format_layout.addWidget(self.spinboxRefY, 2, 1)
        ref_format_layout.addWidget(self.spinboxYAcc, 2, 2)

        ref_format_layout.addWidget(self.labelRefZ, 3, 0)
        ref_format_layout.addWidget(self.spinboxRefZ, 3, 1)
        ref_format_layout.addWidget(self.spinboxZAcc, 3, 2)

        skip_rows_layout = QtWidgets.QHBoxLayout()
        skip_rows_layout.addWidget(self.labelSkipRows)
        skip_rows_layout.addWidget(self.spinboxSkipRows)
        ref_format_layout.addLayout(skip_rows_layout, 4, 0, 1, 2)

        self.ref_format_groupbox = QtWidgets.QGroupBox("Column Formatting")
        self.ref_format_groupbox.setLayout(ref_format_layout)
        self.ref_format_groupbox.setEnabled(False)

        ok_layout = QtWidgets.QHBoxLayout()
        ok_layout.addWidget(self.btnOk)
        ok_layout.addWidget(self.btnQuit)


        # -- Assemble sublayouts into groupboxes --
        general_groupbox = QtWidgets.QGroupBox("General")
        general_layout = QtWidgets.QVBoxLayout()
        general_layout.addLayout(crs_layout)
        general_layout.addLayout(checkbox_layout)
        general_layout.addLayout(output_layout)
        general_groupbox.setLayout(general_layout)

        reference_groupbox = QtWidgets.QGroupBox("Georeferencing")
        reference_layout = QtWidgets.QVBoxLayout()
        reference_layout.addLayout(autodetect_layout)
        reference_layout.addLayout(scale_layout)
        reference_layout.addLayout(geo_layout)
        reference_layout.addWidget(self.ref_format_groupbox)
        reference_groupbox.setLayout(reference_layout)

        # -- Assemble groupboxes into main layout --
        addphotos_groupbox = AddPhotosGroupBox(self)
        main_layout.addWidget(addphotos_groupbox)
        main_layout.addWidget(general_groupbox)
        main_layout.addWidget(reference_groupbox)
        main_layout.addLayout(ok_layout)

        # old format
        # main_layout.addLayout(reference_layout)
        # main_layout.addLayout(scale_layout)
        # main_layout.addLayout(geo_layout)
        # main_layout.addWidget(ref_format_groupbox)
        # main_layout.addLayout(crs_layout)
        # main_layout.addLayout(checkbox_layout)
        # main_layout.addLayout(output_layout)
        # main_layout.addLayout(ok_layout)

        self.setLayout(main_layout)

        # --- Connect signals and slots ---
        # these two syntaxes for connecting signals to slots should be equivalent, but the second method (dot notation) may make it easier
        # to use widget-specific signals (such as currentIndexChanged) instead of core signals
        QtCore.QObject.connect(self.btnScaleFile, QtCore.SIGNAL("clicked()"), self.getScaleFile)
        QtCore.QObject.connect(self.btnGeoFile, QtCore.SIGNAL("clicked()"), self.getGeoFile)
        self.btnOutputDir.clicked.connect(self.getOutputDir)
        self.btnCRS.clicked.connect(self.getCRS)
        self.comboReference.currentIndexChanged.connect(self.onReferenceChanged)

        QtCore.QObject.connect(self.btnOk, QtCore.SIGNAL("clicked()"), self.runWorkFlow)
        QtCore.QObject.connect(self.btnQuit, QtCore.SIGNAL("clicked()"), self, QtCore.SLOT("reject()"))

        self.exec()


    def runWorkFlow(self):
        '''
        Contains the main workflow structure
        '''
        print("Script started...")
        self.setEnabled(False)

        if(not self.autoDetectMarkers):
            Metashape.app.messageBox("Since you are not using auto-detectable markers for scaling and georeferencing, the script will exit after creating a mesh "
                                     "so that you may add georeferencing information by hand.\n\nOnce you have added scaling and georeferencing information, you may "
                                     "run the script again in order to complete the ramainder of the workflow\n\nIf you run the script again without scaling "
                                     "or georeferencing, the script will still create an orthomosaic and DEM, but these may be of lower quality.")

        # set constants
        DOC = self.doc
        CHUNK = self.chunk
        ALIGN_QUALITY = 1 # quality setting for camera alignment; corresponds to high accuracy in GUI
        DM_QUALITY = 4 # quality setting for depth maps; corresponds to medium in GUI
        ORTHO_RES = 0.0005 # cell size in meters for orthomosaic
        INTERPOLATION = Metashape.DisabledInterpolation # interpolation setting for DEM creation

        # set arguments from dialog box
        project_folder = self.project_folder
        project_name = self.project_name
        try:
            if(self.autoDetectMarkers):
                scalebars_path = self.scalebars_path
                georef_path = self.georef_path
        except:
            print("No files selected. If you would like to automatically detect markers, please select files containing scaling and georeferencing information" +
                    "\nScript aborted")
            self.reject()
            return

        output_dir = self.output_dir
        generic_preselect = self.checkBoxPreSelect.isChecked()
        taglab_outputs = self.checkBoxTagLab.isChecked()

        target_type_index = self.comboTargetType.currentIndex()
        target_type = self.targetTypes[target_type_index][1]

        ref_formatting = [self.spinboxRefLabel.value(), self.spinboxRefX.value(), self.spinboxRefY.value(), self.spinboxRefZ.value(),
                            self.spinboxXAcc.value(), self.spinboxYAcc.value(), self.spinboxZAcc.value(), self.spinboxSkipRows.value()]

        print(ref_formatting)
        CRS = self.CRS
        CHUNK.crs = CRS

#         print(project_folder)
#         print(project_name)
#         print(scalebars_path)
#         print(georef_path)
#         print(output_dir)
#         print(generic_preselect)
#         print(CRS)

        ###### 1. Align & Scale ######

        # a. Align photos
        if(CHUNK.tie_points == None): # check if photos are aligned - assumes they are aligned if there is a point cloud, could change to threshold # of cameras
            CHUNK.matchPhotos(downscale = ALIGN_QUALITY, keypoint_limit_per_mpx = 300, generic_preselection = generic_preselect,
                              reference_preselection=True, filter_mask=False, mask_tiepoints=True,
                              filter_stationary_points=True, keypoint_limit=40000, tiepoint_limit=4000, keep_keypoints=False, guided_matching=False,
                              reset_matches=False, subdivide_task=True, workitem_size_cameras=20, workitem_size_pairs=80, max_workgroup_size=100)
            CHUNK.alignCameras(adaptive_fitting = True, min_image=2, reset_alignment=False, subdivide_task=True)
            print(" --- Cameras are aligned and sparse point cloud generated --- ")
            self.updateAndSave(DOC)


        # b. detect markers
        if(len(CHUNK.markers) == 0 and self.autoDetectMarkers): # detects markers only if there are none to start with - could change to threshold # of markers there should be, but i think makes most sense to leave as-is
            CHUNK.detectMarkers(target_type = target_type, tolerance=20, filter_mask=False, inverted=False, noparity=False, maximum_residual=5, minimum_size=0, minimum_dist=5)
            print(" --- Markers Detected --- ")

        # c. scale model
        if(len(CHUNK.scalebars) == 0 and self.autoDetectMarkers): # creates scalebars only if there are none already - ask Will if this makes sense
            scale_except = self.createScalebars(CHUNK, scalebars_path)
            ref_except = self.referenceModel(CHUNK, georef_path, CRS, ref_formatting)
            # this structure is really clunky but I'm not sure what the best way to differentiate between sub-exceptions is without defining whole exception classes, which seems excessiv
            error = ""
            if(scale_except or ref_except):
                if(scale_except):
                    print(scale_except)
                    error = error + scale_except
                if(ref_except):
                    print(ref_except)
                    error = error + ref_except
                Metashape.app.messageBox("Unable to scale and reference model:\n" + error + "Check that the files are formatted correctly and try again, or add markers and scalebars through the Metashape GUI.")
                if(not go):
                    self.reject()
                    return
            else:
                CHUNK.updateTransform()



        if(CHUNK.model == None):
            # d. optimize camera alignment - only optimize if there isn't already a model
            self.gradSelectsOptimization(CHUNK)
            print( " --- Camera Optimization Complete --- ")
            self.updateAndSave(DOC)

            ###### 2. Generate products ######

            # a. build mesh
            CHUNK.buildDepthMaps(downscale = DM_QUALITY, filter_mode = Metashape.MildFiltering, reuse_depth = True, max_neighbors=16, subdivide_task=True, workitem_size_cameras=20, max_workgroup_size=100)
            CHUNK.buildModel(surface_type = Metashape.Arbitrary, interpolation = Metashape.EnabledInterpolation, face_count=Metashape.HighFaceCount,
                             face_count_custom = 1000000, source_data = Metashape.DepthMapsData, keep_depth = False) # change this to false to avoid wasted space?
            print(" --- Mesh Generated --- ")
            self.updateAndSave(DOC)

        # if not usin automatic referencing, exit script after mesh creation
        if(not self.autoDetectMarkers and len(CHUNK.markers) == 0):
            print("Exiting script for manual referencing")
            self.reject()
            return

        # b. build orthomosaic and DEM
        if(CHUNK.orthomosaic == None):
            CHUNK.buildOrthomosaic(resolution = ORTHO_RES, surface_data=Metashape.ModelData, blending_mode=Metashape.MosaicBlending, fill_holes=True, ghosting_filter=False,
                                   cull_faces=False, refine_seamlines=False, flip_x=False, flip_y=False, flip_z=False, subdivide_task=True,
                                   workitem_size_cameras=20, workitem_size_tiles=10, max_workgroup_size=100)

        if(CHUNK.elevation == None):
            CHUNK.buildDem(source_data = Metashape.ModelData, interpolation = INTERPOLATION, flip_x=False, flip_y=False, flip_z=False,
                           resolution=0, subdivide_task=True, workitem_size_tiles=10, max_workgroup_size=100)
            print(" --- Orthomosaic and DEM Built --- ")
            self.updateAndSave(DOC)

        # c. create boundary
        if(not CHUNK.shapes):
            self.boundaryCreation(CHUNK)
            print(" --- Boundary Polygon Created ---")


        ###### 3. Export products ######

        # a. generate report
        CHUNK.exportReport(path = output_dir + "/" + project_name + "_" + CHUNK.label + ".pdf", title = project_name + " " + CHUNK.label,
                           description = "Processing report for " + project_name + " on chunk " + CHUNK.label, font_size=12, page_numbers=True, include_system_info=True)

        # set up compression parameters
        jpg = Metashape.ImageCompression()
        jpg.tiff_compression = Metashape.ImageCompression.TiffCompressionJPEG
        jpg.jpeg_quality = 90
        jpg.tiff_big = True
        jpg.tiff_compression = True

        lzw = jpg
        lzw.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW

        # b. export orthomosaic and DEM in full format
        # WG: changed these to NOT clip the full outputs to bounding box. Future: add a shapefile export in here that defines boundary
        CHUNK.exportRaster(path = output_dir + "/" + project_name + "_" + CHUNK.label + ".tif", resolution = ORTHO_RES,
                           source_data = Metashape.OrthomosaicData, split_in_blocks = False, image_compression = jpg,
                           save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                           min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=False,title='Orthomosaic', description='Generated by Agisoft Metashape')

        CHUNK.exportRaster(path = output_dir + "/" + project_name + "_" + CHUNK.label + "_DEM.tif", resolution = ORTHO_RES, nodata_value = -5,
                           source_data = Metashape.ElevationData, split_in_blocks = False, image_compression = jpg,
                           save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                           min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=False,title='Orthomosaic', description='Generated by Agisoft Metashape')

        # export ortho and dem in blockwise format for Taglab
        if(taglab_outputs):
            CHUNK.exportRaster(path = output_dir + "/taglab_outputs/" + project_name + "_" + CHUNK.label + ".tif", resolution = ORTHO_RES,
                               source_data = Metashape.OrthomosaicData, block_width = 32767, block_height = 32767, split_in_blocks = True, image_compression = lzw, # remainder of parameters are defaults specified to ensure any alternate settings get oerridden
                               save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                               min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=True,title='Orthomosaic', description='Generated by Agisoft Metashape')
            CHUNK.exportRaster(path = output_dir + "/taglab_outputs/" + project_name + "_" + CHUNK.label + "_DEM.tif", nodata_value = -5,
                               source_data = Metashape.ElevationData, block_width = 32767, block_height = 32767, split_in_blocks = True, image_compression = lzw, # remainder of parameters are defaults specified to ensure any alternate settings get oerridden
                               save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                               min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=True,title='Orthomosaic', description='Generated by Agisoft Metashape')


        ###### 4. Clean up project ######
        self.cleanProject(CHUNK)
        self.updateAndSave(DOC)
        print("Script finished")

        self.reject()




    ######### Workflow Functions #########

    def updateAndSave(self, doc):
        print("Saving Project...")
        Metashape.app.update()
        doc.save()
        print("Project Saved")


    def createScalebars(self, chunk, path):

       iNumScaleBars=len(chunk.scalebars)
       iNumMarkers=len(chunk.markers)
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
              #split the line and load into variables
              point1, point2, dist, acc = line.split(",")
              #find the corresponding scalebar, if there is any
              scalebarfound=0
              if (iNumScaleBars > 0):
                 for sbScaleBar in chunk.scalebars:
                    strScaleBarLabel_1=point1+"_"+point2
                    strScaleBarLabel_2=point2+"_"+point1
                    if sbScaleBar.label==strScaleBarLabel_1 or sbScaleBar.label==strScaleBarLabel_2:
                       # scalebar found
                       scalebarfound=1
                       # update it
                       sbScaleBar.reference.distance=float(dist)
                       sbScaleBar.reference.accuracy=float(acc)
              # Check if scalebar was found
              if (scalebarfound==0):
                 # Scalebar was not found: add a new one
                 # Find Marker 1 with label described by "point1"
                 bMarker1Found=0
                 for marker in chunk.markers:
                    if (marker.label == point1):
                       marker1 = marker
                       bMarker1Found=1
                       break
                 # Find Marker 2 with label described by "point2"
                 bMarker2Found=0
                 for marker in chunk.markers:
                    if (marker.label == point2):
                       marker2 = marker
                       bMarker2Found=1
                       break
                 # Check if both markers were detected
                 if bMarker1Found==1 and bMarker2Found==1:
                    # Markers were detected. Create new scalebar.
                    sbScaleBar = chunk.addScalebar(marker1,marker2)
                    # update it:
                    sbScaleBar.reference.distance=float(dist)
                    sbScaleBar.reference.accuracy=float(acc)
                 else:
                    # Marker not found. Raise exception and print, but do not stop process.
                    if (bMarker1Found == 0):
                       print("Marker "+point1+" was not found!")
                    if (bMarker2Found == 0):
                       print("Marker "+point2+" was not found!")
              #All done.
              #reading the next line in input file
              line = file.readline()
              if not len(line):
                 eof = True
                 break
           file.close()
           print(" --- Scalebars Created --- ")

       except:
           return "There was a problem reading scalebar data\n"



    def referenceModel(self, chunk, path, _crs, formatting):
        '''
        Imports marker georeferencing data from a csv
        The file must be formatted in one of two ways, either with marker coordinate and accuracy info located in the column numbers specified below
        or with all extemporaneous information (such as objectid) removed.

        If the file is not already in the correct format, this function creates a new file that is formatted correctly and imports data from that file.
        IMPORTANT: this function will raise an exception if the file has the wrong number of columns, but it cannot guarantee that the file is
            formatted perfectly, ie that all the right information is in the right columns. The georeferencing should be inspected after this script is finished,
            any errors will likely be obvious

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
            line = file.readline() # read second line in file, first line is column headers

            # if(len(line.split(sep = ",")) == 6):
            #     print("File is formatted correctly")
            #     new_path = path

            # elif(len(line.split(sep = ",")) >= 8):
            while not eof:
                marker_ref = line.split(sep = ",")
                ref_line = [marker_ref[n], marker_ref[x], marker_ref[y], marker_ref[z], marker_ref[X], marker_ref[Y], marker_ref[Z]]
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

            # import new georeferencing data
            chunk.importReference(path = new_path, format = Metashape.ReferenceFormatCSV, delimiter = ',', columns = "nxyzXYZ", skip_rows = skip,
                                  crs = _crs, ignore_labels=False, create_markers=False, threshold=0.1, shutter_lag=0)
            print(" --- Georeferencing Updated --- ")
        except:
            return "There was a problem reading georeferencing data\n"



    def gradSelectsOptimization(self, chunk):

    # define thresholds for reconstruction uncertainty and projection accuracy
        reconun = float(25)
        projecac = float(15)

    # initiate filters, remote points above thresholds
        f = Metashape.TiePoints.Filter()
        f.init(chunk, Metashape.TiePoints.Filter.ReconstructionUncertainty)
        f.removePoints(reconun)

        f = Metashape.TiePoints.Filter()
        f.init(chunk, Metashape.TiePoints.Filter.ProjectionAccuracy)
        f.removePoints(projecac)

    # optimize camera locations based on all distortion parameters
        chunk.optimizeCameras(fit_f=True, fit_cx=True, fit_cy=True,
                              fit_b1=True, fit_b2=True, fit_k1=True,
                              fit_k2=True, fit_k3=True, fit_k4=True,
                              fit_p1=True, fit_p2=True, fit_corrections=True,
                              adaptive_fitting=False, tiepoint_covariance=False)


    def create_shape_from_markers(self, marker_list, chunk):
        if not chunk:
                print("Empty project, script aborted")
                return 0
        if len(marker_list) < 3:
                print("At least three markers required to create a polygon. Script aborted.")
                return 0

        T = chunk.transform.matrix
        crs = chunk.crs
        if not chunk.shapes:
                chunk.shapes = Metashape.Shapes()
                chunk.shapes.crs = chunk.crs
        shape_crs = chunk.shapes.crs


        coords = [shape_crs.project(T.mulp(marker.position)) for marker in marker_list]

        shape = chunk.shapes.addShape()
        shape.label = "Marker Boundary"
        shape.geometry.type = Metashape.Geometry.Type.PolygonType
        shape.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
        shape.geometry = Metashape.Geometry.Polygon(coords)

        return 1

    def boundaryCreation(self, chunk):
        m_list = []
        for marker in chunk.markers:
            m_list.append(marker)
        m_list_short = m_list[:4]
        self.create_shape_from_markers(m_list_short, chunk)


    def cleanProject(self, chunk):
       ortho = chunk.orthomosaic
       depthmaps = chunk.depth_maps
       sparsecloud = chunk.tie_points

       #remove key points(if present)
       sparsecloud.removeKeypoints()
       #remove depth maps (if present)
       depthmaps.clear()
       #remove orthophotos without removing orthomosaic
       ortho.removeOrthophotos()


    # ----- Slots for Dialog Box -----
    def getScaleFile(self):
        # maybe change this to local variable and get text value from text box directly when workflow is run?
        # might be better to connect textchanged() signal to a custom function, but more work
        self.scalebars_path = QtWidgets.QFileDialog.getOpenFileName(self, 'Open file', self.project_folder, "CSV files (*.csv *.txt)")[0]
        if(self.scalebars_path):
            self.txtScaleFile.setPlainText(self.scalebars_path)
        else:
            self.txtScaleFile.setPlainText("No File Selected")

    def getGeoFile(self):
        self.georef_path = QtWidgets.QFileDialog.getOpenFileName(self, 'Open file', self.project_folder, "CSV files (*.csv *.txt)")[0]
        if(self.georef_path):
            self.txtGeoFile.setPlainText(self.georef_path)
        else:
            self.txtGeoFile.setPlainText("No File Selected")

    def getOutputDir(self):
        self.output_dir = QtWidgets.QFileDialog.getExistingDirectory(self, 'Open directory', self.project_folder)
        if(self.output_dir):
            self.txtOutputDir.setPlainText(self.output_dir)
        else:
            self.txtOutputDir.setPlainText("No File Selected")

    def getCRS(self):
        crs = Metashape.app.getCoordinateSystem("Select Coordinate System")
        if(crs):
            self.CRS = crs
            self.txtCRS.setPlainText(crs.name)

    def onReferenceChanged(self):
        self.autoDetectMarkers = self.comboReference.currentIndex()
        self.ref_format_groupbox.setEnabled(self.autoDetectMarkers)

        self.comboTargetType.setEnabled(self.autoDetectMarkers)

        self.labelScaleFile.setEnabled(self.autoDetectMarkers)
        self.btnScaleFile.setEnabled(self.autoDetectMarkers)
        self.txtScaleFile.setEnabled(self.autoDetectMarkers)

        self.labelGeoFile.setEnabled(self.autoDetectMarkers)
        self.btnGeoFile.setEnabled(self.autoDetectMarkers)
        self.txtGeoFile.setEnabled(self.autoDetectMarkers)

        if (self.autoDetectMarkers):
            # self.CRS = self.defaultCRS
            self.txtCRS.setPlainText(self.CRS.name)
        else:
            # self.CRS = self.localCRS
            # self.txtCRS.setPlainText("Local Coordinates")
            self.txtCRS.setPlainText(self.CRS.name)

#         if (self.comboReference.currentIndex() == 0):
#             self.comboTargetType.setEnabled(False)
#         else:
#             self.comboTargetType.setEnabled(True)


def run_script():
    app = QtWidgets.QApplication.instance()
    parent = app.activeWindow()

    dlg = FullWorkflowDlg(parent)



# add function to menu
# label = "Custom/Full Underwater Workflow"
# Metashape.app.addMenuItem(label, run_script)
# print("To execute this script press {}".format(label))

run_script()
