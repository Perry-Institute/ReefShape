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
import re
#import exifread
from datetime import datetime
from PySide2 import QtGui, QtCore, QtWidgets
from ui_components import AddPhotosGroupBox, BoundaryMarkerDlg, GeoreferenceGroupBox

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
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setWindowTitle("Full ReefShape Workflow")

        # ----- Build Widgets -----
        # these are declared as member variables so that they can be referenced
        # and modified by slots that are outside of the constructor
        # -- General --
        
        # Coordinate system input
        self.labelCRS = QtWidgets.QLabel("Coordinate System:")
        self.comboCRS = QtWidgets.QComboBox()
        self.comboCRS.addItems(self.crs_options.keys())

        # Set default selection based on saved value or default to second entry
        saved_wkt = self.settings.value("coordinate_system", self.defaultCRS.wkt)
        default_index = list(self.crs_options.values()).index(next((crs for crs in self.crs_options.values() if crs.wkt == saved_wkt), self.defaultCRS))
        self.comboCRS.setCurrentIndex(default_index)
        
        
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

        export_layout = QtWidgets.QHBoxLayout()
        export_layout.addWidget(self.checkBoxReport)
        export_layout.addWidget(self.checkBoxExport)
        export_layout.addWidget(self.checkBoxTagLab)

        ok_layout = QtWidgets.QHBoxLayout()
        ok_layout.addWidget(self.btnOk)
        ok_layout.addWidget(self.btnQuit)


        # -- Assemble sublayouts into groupboxes --
        general_groupbox = QtWidgets.QGroupBox("General")
        general_layout = QtWidgets.QVBoxLayout()
        general_layout.addLayout(crs_layout)
        general_layout.addLayout(checkbox_layout)
        general_layout.addLayout(resolution_layout)
        general_layout.addLayout(output_layout)
        general_layout.addLayout(export_layout)
        general_groupbox.setLayout(general_layout)


        # -- Assemble groupboxes into main layout --
        self.addphotos_groupbox = AddPhotosGroupBox(self)
        self.addphotos_groupbox.chunkUpdated.connect(self.refreshChunkNameDisplay)
        self.georef_groupbox = GeoreferenceGroupBox(self)
        if self.chunk and len(self.chunk.markers) == 0:
            QtCore.QTimer.singleShot(0, lambda: self.georef_groupbox.comboReference.setCurrentIndex(1))
                #self.georef_groupbox.autoDetectMarkers = True
        main_layout.addWidget(self.addphotos_groupbox)
        main_layout.addWidget(general_groupbox)
        main_layout.addWidget(self.georef_groupbox)
        # main_layout.addLayout(ok_layout)

        # a somewhat complicated system of wrapper widgets is needed to accomodate the scroll layout
        # here is a summary of the structure:
        # main dialog(self) > scroll_layout > scroll_area > main_widget > main_layout

        main_widget = QtWidgets.QWidget() # wrapper widget for scroll area
        main_widget.setLayout(main_layout)
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_area.setWidget(main_widget)
        scroll_area.setAlignment(QtCore.Qt.AlignHCenter)
        scroll_layout = QtWidgets.QVBoxLayout()
        scroll_layout.addWidget(scroll_area)
        scroll_layout.addLayout(ok_layout) # place run and close buttons outside of scroll area so theyre always visible
        ok_layout.setEnabled(True)
        self.setLayout(scroll_layout) # set wrapper layout for scroll area as main dialog layout

        # adjust size and position of main widget
        self.setMinimumSize(main_widget.frameGeometry().width(), 0.6*parent.frameGeometry().height())
        # determining the actual width needed to display the widget without cutting it off or leaving extra space is
        # surprisingly difficult because of the padding and margin space between nested widgets - this workaround was found by trial and error
        width = (scroll_area.frameGeometry().width() + main_widget.frameGeometry().width()) / 2
        x = parent.frameGeometry().width()/2.0 - width/2 # set starting position so that widget is roughly centered on screen
        if(x<0): x = 0
        y = parent.frameGeometry().height()/2.0 - 0.4*parent.frameGeometry().height()
        self.setGeometry(parent.frameGeometry().x() + x, parent.frameGeometry().y() + y, width, 0.85*parent.frameGeometry().height())

        # --- Connect signals and slots ---
        # these two syntaxes for connecting signals to slots should be equivalent, but the first method (dot notation) may make it easier
        # to use widget-specific signals (such as currentIndexChanged) instead of core signals
        self.checkBoxDefaultRes.stateChanged.connect(self.onResolutionChange)
        self.btnOutputDir.clicked.connect(self.getOutputDir)
        #self.btnCRS.clicked.connect(self.getCRS)

        QtCore.QObject.connect(self.btnOk, QtCore.SIGNAL("clicked()"), self.runWorkFlow)
        QtCore.QObject.connect(self.btnQuit, QtCore.SIGNAL("clicked()"), self, QtCore.SLOT("reject()"))
        self.loadSettings()
        self.exec()
        
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
        crs_wkt = self.settings.value("coordinate_system", None, type=str)
        if crs_wkt:
            self.chunk.crs = Metashape.CoordinateSystem(crs_wkt)

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
        selected_label = self.comboCRS.currentText()
        self.settings.setValue("coordinate_system", self.crs_options[selected_label].wkt)
    
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
        Contains the main workflow structure
        '''
        print("Script started...")
        self.setEnabled(False)
        self.chunk = Metashape.app.document.chunk
        
        selected_crs_label = self.comboCRS.currentText()
        selected_crs = self.crs_options[selected_crs_label]
        self.chunk.crs = selected_crs
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
        if(self.chunk.tie_points and not self.chunk.meta['init_tie_points']):
            self.chunk.meta['init_tie_points'] = str(len(self.chunk.tie_points.points))
        
        
        ###### 1. Align & Scale ######
        # a. Align photos
        if(self.chunk.tie_points == None): # check if photos are aligned - assumes they are aligned if there is a point cloud, could change to threshold # of cameras
            self.chunk.matchPhotos(downscale = ALIGN_QUALITY, keypoint_limit_per_mpx = 300, generic_preselection = generic_preselect,
                              reference_preselection=True, filter_mask=False, mask_tiepoints=True,
                              filter_stationary_points=True, keypoint_limit=40000, tiepoint_limit=4000, keep_keypoints=True, guided_matching=False,
                              reset_matches=False, subdivide_task=True, workitem_size_cameras=20, workitem_size_pairs=80, max_workgroup_size=100)
            self.chunk.alignCameras(adaptive_fitting = True, min_image=2, reset_alignment=True, subdivide_task=True)
            #second alignment step sometimes adds extra photos to the alignment that were missed on the first pass
            self.chunk.alignCameras(adaptive_fitting = True, min_image=2, reset_alignment=False, subdivide_task=True)
            self.chunk.meta['init_tie_points'] = str(len(self.chunk.tie_points.points))
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
            # d. optimize camera alignment - only optimize if there isn't already a model, and if the current
            # number of tie points is not less than the inital number - prevents optimizing twice
            if(not len(self.chunk.tie_points.points) < int(self.chunk.meta['init_tie_points'])):
                self.gradSelectsOptimization()
                print( " --- Camera Optimization Complete --- ")
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
            self.chunk.buildDem(source_data = Metashape.ModelData, interpolation = Metashape.EnabledInterpolation, flip_x=False, flip_y=False, flip_z=False,
                           resolution=ORTHO_RES, subdivide_task=True, workitem_size_tiles=10, max_workgroup_size=100)
            print(" --- Hi-Res DEM Built --- ")
            
            
        if(self.chunk.orthomosaic == None):
            self.chunk.buildOrthomosaic(resolution = ORTHO_RES, surface_data=Metashape.ElevationData, blending_mode=Metashape.MosaicBlending, fill_holes=True, ghosting_filter=False,
                                   cull_faces=False, refine_seamlines=False, flip_x=False, flip_y=False, flip_z=False, subdivide_task=True,
                                   workitem_size_cameras=20, workitem_size_tiles=10, max_workgroup_size=100)
            print(" --- Orthomosaic Built --- ")

            self.updateAndSave()
            
#        if self.chunk.elevation:
#            # Delete the DEM and then rebuild at normal resolution
#            self.chunk.elevation = None
#            self.chunk.buildDem(source_data = Metashape.ModelData, interpolation = INTERPOLATION, flip_x=False, flip_y=False, flip_z=False,
#                           resolution=DEM_RES, subdivide_task=True, workitem_size_tiles=10, max_workgroup_size=100)
#            print(" --- DEM Built --- ")


        # c. create boundary
        if(not self.chunk.shapes):
            self.boundaryCreation()
            print(" --- Boundary Polygon Created ---")

        ###### 3. Export products ######

        # set up compression parameters
        #first, for regular orthomosaic
        jpg = Metashape.ImageCompression()
        jpg.tiff_compression = Metashape.ImageCompression.TiffCompressionJPEG
        jpg.jpeg_quality = 90
        jpg.tiff_big = True
        jpg.tiff_overviews = True
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
                self.chunk.exportReport(
                    path=report_path,
                    title=self.project_name,
                    description="\nProcessing report for " + self.project_name + " photographed on " + human_date + "\nCreated with ReefShape v1.2\nProcessed on:",
                    font_size=12,
                    page_numbers=True,
                    include_system_info=True 
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

            # build output path for boundary shapefile - this is necessary since the files will be placed in their own new folder within the output folder that the user created/selected
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
                               min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=True,title='Orthomosaic', description='Generated by Agisoft Metashape')
            self.chunk.exportRaster(path = self.output_dir + "/taglab_outputs/" + self.project_name + "_" + self.chunk.label + "_DEM.tif", resolution = ORTHO_RES, nodata_value = -5,
                               source_data = Metashape.ElevationData, block_width = 32767, block_height = 32767, split_in_blocks = True, image_compression = lzw, # remainder of parameters are defaults specified to ensure any alternate settings get overridden
                               save_kml=False, save_world=False, save_scheme=False, save_alpha=True, image_description='', network_links=True, global_profile=False,
                               min_zoom_level=-1, max_zoom_level=-1, white_background=True, clip_to_boundary=True,title='DEM', description='Generated by Agisoft Metashape')

        
        ###### 4. Clean up project ######
        self.cleanProject()
        self.updateAndSave()
        
        ###### 5. Finish Script ######      
        print("Script finished")
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
        if self.doc.chunk:
            self.txtChunkName.setPlainText(self.doc.chunk.label)
            self.addphotos_groupbox.txtChunkName.setPlainText(self.doc.chunk.label)
    
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
        Wrapper function to create a boundary shape. Based on input from the user,
        this function restricts the marker list provided to create_shape_from_markers()
        such that the resulting shape will not be crossed into an hourglass shape if the
        corner markers are positioned incorrectly.
        '''
        m_list = []
        for corner_num in self.corner_markers:
            for marker in self.chunk.markers:
                if(str(corner_num) == re.search('(\d+)', marker.label).group(0)):
                    m_list.append(marker)
        m_list_short = m_list[:4]
        self.create_shape_from_markers(m_list_short)



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
    except Exception as e:
        show_error_dialog("Workflow Error", str(e))    
            
    

# add function to menu
label = "ReefShape/Full ReefShape Workflow"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, run_script)
print("To execute this script press {}".format(label))
