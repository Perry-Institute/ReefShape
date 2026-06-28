'''
UI Component Classes for Underwater Workflow
Sam Marshall

This file contains class definitions for UI components used in the full workflow script (FullUW_dialog.py).
It cannot function as a standalone script.

Note: if you edit this script after running a script that uses it in Metashape, your changes
will likely not be reflected until you close Metashape and reopen it. This is (I think) because
Metashape caches a bytecode version of the script the first time it is run in a given session
and then uses that version when it is run again.
'''


import Metashape
import os
import sys
import csv
import re
from datetime import datetime
from PySide2 import QtGui, QtCore, QtWidgets # NOTE: the style enums (such as alignment) seem to be in QtCore.Qt
from PySide2.QtCore import Signal


class CollapsibleGroupBox(QtWidgets.QGroupBox):
    '''
    A QGroupBox that collapses to its title bar when the user unchecks the
    box in the title. Use exactly like a QGroupBox — call setLayout(layout)
    with whatever sublayouts/widgets you want inside; the only added API is
    setCollapsed()/isCollapsed() for setting initial state from chunk
    inspection.

    Implementation: all content goes inside a single inner QWidget (`_content`)
    rather than being parented directly to the QGroupBox. To collapse, we
    hide just that inner widget — Qt's layout system then shrinks the box
    to its title strip. We tried iterating direct children with findChildren
    + setVisible first; it disables them via Qt's default checkable-groupbox
    behavior but doesn't reliably hide them across QStyles, leaving the box
    at full height with grayed-out content. The inner-container approach is
    one widget to toggle and works consistently.

    `setLayout` is overridden to install the layout on `_content` instead of
    on the QGroupBox itself, so existing call sites that do
    `self.setLayout(layout)` keep working without code changes.

    Visual: a native checkable QGroupBox title strip with a checkbox — that
    checkbox is the collapse toggle. We picked native over a custom chevron
    header because chevron headers require platform-specific mouse-hit math
    on the title strip and break subtly across macOS/Windows/Linux QStyles.

    On Metashape's Classic theme the indicator renders as a plain checkbox
    (reads as enable/disable), so we prefix the title with a chevron
    (▼ expanded / ▶ collapsed) that flips with state. Light/Dark themes
    install a large application stylesheet that already renders the
    indicator as a chevron, so the prefix is skipped there to avoid two
    chevrons. Detection uses QApplication.styleSheet() length — Classic
    in testing is ~190 chars (basic palette tweaks), Light/Dark is ~26k+
    (full custom widget styling); a 1000-char threshold sits cleanly
    between them and doesn't depend on QStyle.objectName(), which is
    empty for Metashape in all themes.

    A tooltip on the box also explains the collapse behavior in plain
    English regardless of which theme is active.
    '''
    def __init__(self, title="", parent=None):
        # Initialize with empty title; we set the real title (with chevron
        # prefix) via _refreshTitle below once self._title_text exists.
        super().__init__("", parent)
        self._title_text = title
        # "Complete" state — when set via setComplete(True), the title gets
        # " | Complete" appended and is rendered green. Used to signal that
        # a panel auto-collapsed because its work is already done in the
        # chunk (so the user knows *why* the panel collapsed itself).
        self._complete = False
        self._complete_label = "Complete"
        self.setCheckable(True)
        self.setChecked(True)
        # Inner container that holds all caller-supplied content. Hiding this
        # one widget collapses everything in one go.
        self._content = QtWidgets.QWidget(self)
        outer = QtWidgets.QVBoxLayout()
        outer.setContentsMargins(8, 4, 8, 8)
        outer.addWidget(self._content)
        # Bypass our setLayout override so the outer layout actually
        # installs on the QGroupBox itself (not on _content).
        QtWidgets.QGroupBox.setLayout(self, outer)
        self.toggled.connect(self._on_toggled)
        self._refreshTitle()
        self.setToolTip(
            "Click the checkbox to collapse or expand this section. "
            "Settings in collapsed sections are still used when you run the workflow."
        )

    def setLayout(self, layout):
        # Redirect caller layouts onto the inner content widget so existing
        # `self.setLayout(...)` call sites in subclasses keep working.
        self._content.setLayout(layout)

    def setTitle(self, title):
        # Track the unprefixed title separately so we can re-render with the
        # chevron whenever the collapse state changes. Direct callers that
        # do `groupbox.setTitle("New")` get the prefix applied automatically.
        self._title_text = title
        self._refreshTitle()

    def title(self):
        return self._title_text

    @staticmethod
    def _is_themed_mode():
        '''True when Metashape is in Light or Dark theme (which renders the
        QGroupBox indicator as a chevron via the app stylesheet). Classic
        mode applies a tiny stylesheet (~190 chars in testing); Light/Dark
        apply ~26k+ chars of custom widget styling. The 1000-char
        threshold sits cleanly between the two.'''
        app = QtWidgets.QApplication.instance()
        if app is None:
            return False
        return len(app.styleSheet()) > 1000

    def _refreshTitle(self):
        # Use the direct C++ slot to set the displayed title; calling self.
        # setTitle here would recurse through our override. Only add the
        # chevron prefix on themes that don't already render one (Classic).
        if self._is_themed_mode():
            prefix = ""
        else:
            prefix = "▼ " if self.isChecked() else "▶ "
        title = self._title_text
        if self._complete:
            title = "{} | {}".format(title, self._complete_label)
        QtWidgets.QGroupBox.setTitle(self, "{}{}".format(prefix, title))

    def setComplete(self, complete=True, label="Complete"):
        '''Mark this section as "complete" — its work is already done in the
        chunk. Appends " | <label>" to the title and colors the title text
        green. Combined with setCollapsed(True), gives the user a visual
        cue for *why* the panel auto-collapsed.

        Caveat: QGroupBox renders its title as a single styled text run, so
        the stylesheet colors the entire title (chevron + base + label),
        not just the suffix. The whole-title color still cleanly signals
        complete vs in-progress at a glance.'''
        self._complete = complete
        self._complete_label = label
        # 0x1a7f37 ≈ GitHub's "checkmark green" — distinct from the standard
        # palette text color in every theme.
        self.setStyleSheet(
            "QGroupBox::title { color: #1a7f37; font-weight: bold; }"
            if complete else ""
        )
        self._refreshTitle()

    def isComplete(self):
        return self._complete

    def _on_toggled(self, checked):
        self._content.setVisible(checked)
        self._refreshTitle()

    def setCollapsed(self, collapsed):
        self.setChecked(not collapsed)

    def isCollapsed(self):
        return not self.isChecked()

    def contentLayout(self):
        '''The layout installed on the inner content widget — i.e. the
        layout that holds caller-supplied widgets. Use this when you need
        to insert into or query the content layout after construction
        (e.g. `contentLayout().insertLayout(0, my_row)`).

        `self.layout()` returns the *outer* layout that wraps the content
        widget for collapse — inserting into it would place items outside
        the collapsible region.'''
        return self._content.layout()


class AddPhotosGroupBox(CollapsibleGroupBox):
    '''
    Groupbox holding widgets used to setup a Metashape project and add photos.

    Allows the user to save the currently active Metashape project under a different name,
    change the active chunk's name, and add photos to the active chunk.

    It cannot make a new chunk, and is NOT recommended to be used for creating a new Metashape
    project from within an already active project (one that has data in it).

    The parent widget MUST have an attribute named 'chunk' that represents the project's active
    chunk - this may be changed in a later version, but is a requirement for now
    '''
    chunkUpdated = Signal()
    def __init__(self, parent):
        # call parent constructor to initialize
        super().__init__("Project Setup")
        self.parent = parent
        self.project_path = Metashape.app.document.path
        self.photo_folder = "No folder selected"
        self.project_name = "Untitled"
        self.chunk_name = Metashape.app.document.chunk.label

        # ---- create widgets ----
        self.labelNamingConventions = QtWidgets.QLabel("Select a project and chunk name. To ensure consistency, we suggest "
                                                        "using the site name\nas the project name and the date the data "
                                                        "was collected (in YYYYMMDD format) as the chunk name.")
        self.labelNamingConventions.setAlignment(QtCore.Qt.AlignCenter)

        self.labelProjectName = QtWidgets.QLabel("Project Name:")
        self.btnProjectName = QtWidgets.QPushButton("Create New")
        self.txtProjectName = QtWidgets.QPlainTextEdit()
        if(self.parent.project_name):
            self.project_name = self.parent.project_name
        self.txtProjectName.setPlainText(self.project_name)
        self.txtProjectName.setFixedHeight(40)
        self.txtProjectName.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtProjectName.setReadOnly(True)

        self.labelAddPhotos = QtWidgets.QLabel("Add Photos:")
        self.btnAddPhotos = QtWidgets.QPushButton("Select Folder")
        self.txtAddPhotos = QtWidgets.QPlainTextEdit()
        if(len(Metashape.app.document.chunk.cameras) > 0):
            self.photo_folder = str(len(Metashape.app.document.chunk.cameras)) + " cameras found. Select a folder if you would like to add more"
        self.txtAddPhotos.setPlainText(self.photo_folder)
        self.txtAddPhotos.setFixedHeight(40)
        self.txtAddPhotos.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtAddPhotos.setReadOnly(True)

        self.labelChunkName = QtWidgets.QLabel("Chunk Name:")
        self.btnChunkName = QtWidgets.QPushButton("Rename Chunk")
        self.txtChunkName = QtWidgets.QPlainTextEdit(Metashape.app.document.chunk.label)
        self.txtChunkName.setFixedHeight(40)
        self.txtChunkName.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.txtChunkName.setReadOnly(True)

        self.btnCreateProj = QtWidgets.QPushButton("Save Project")
        # self.btnCreateProj.setFixedWidth(75)

        self.labelPhotosAdded = QtWidgets.QLabel()

        # ---- create layouts and assemble widgets ----
        main_layout = QtWidgets.QVBoxLayout()

        project_name_layout = QtWidgets.QHBoxLayout()
        project_name_layout.addWidget(self.labelProjectName)
        project_name_layout.addWidget(self.txtProjectName)
        project_name_layout.addWidget(self.btnProjectName)

        chunk_name_layout = QtWidgets.QHBoxLayout()
        chunk_name_layout.addWidget(self.labelChunkName)
        chunk_name_layout.addWidget(self.txtChunkName)
        chunk_name_layout.addWidget(self.btnChunkName)

        photos_dir_layout = QtWidgets.QHBoxLayout()
        photos_dir_layout.addWidget(self.labelAddPhotos)
        photos_dir_layout.addWidget(self.txtAddPhotos)
        photos_dir_layout.addWidget(self.btnAddPhotos)

        create_proj_layout = QtWidgets.QHBoxLayout()
        create_proj_layout.addStretch()
        create_proj_layout.addWidget(self.labelPhotosAdded)
        create_proj_layout.addWidget(self.btnCreateProj)

        main_layout.addWidget(self.labelNamingConventions)
        main_layout.addLayout(project_name_layout)
        main_layout.addLayout(photos_dir_layout)
        main_layout.addLayout(chunk_name_layout)
        main_layout.addLayout(create_proj_layout)
        # Absorb vertical slack at the bottom so collapsed sibling panels
        # don't inflate gaps between this panel's rows.
        main_layout.addStretch(1)

        self.setLayout(main_layout)

        # connect buttons to slots
        self.btnAddPhotos.clicked.connect(self.getPhotoFolder)
        self.btnProjectName.clicked.connect(self.getProjectName)
        self.btnChunkName.clicked.connect(self.getChunkName)
        self.btnCreateProj.clicked.connect(self.saveProject)

    def getPhotoFolder(self):
        '''
        Slot: gets a folder from which to add photos from the user, then adds the photos to the
        project's active chunk
        '''
        new_folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Open directory', self.parent.project_folder)

        if(new_folder):
            # add photos to active chunk
            self.photo_folder = new_folder
            try:
                image_list = os.listdir(self.photo_folder)
                photo_list = list()
                for photo in image_list:
                    if photo.rsplit(".",1)[1].lower() in  ["jpg", "jpeg", "tif", "tiff"]:
                        photo_list.append(os.path.join(self.photo_folder, photo))
                Metashape.app.document.chunk.addPhotos(photo_list)
                self.txtAddPhotos.setPlainText(self.photo_folder)
                self.labelPhotosAdded.setText(str(len(Metashape.app.document.chunk.cameras)) + " images successfully added. Select another folder if you would like to add more")
            except Exception as err:
                Metashape.app.messageBox("Error adding photos")
                return
        else:
            Metashape.app.messageBox("Unable to add photos: please select a folder to add photos from")

        # After adding photos, attempt to auto-rename chunk if default name
        if self.parent.chunk.label.startswith("Chunk"):

            # Create a deep copy of photo_list to ensure it's captured correctly inside the delayed function
            captured_photo_list = list(photo_list)

            def delayed_rename():
                try:
                    doc = Metashape.app.document
                    chunk = doc.chunk  # Always re-fetch the active chunk
                    cameras = chunk.cameras

                    if not cameras:
                        print("No cameras found in chunk after delay.")
                        return

                    photo_path = os.path.abspath(captured_photo_list[0])
                    for cam in cameras:
                        if cam.photo and os.path.abspath(cam.photo.path) == photo_path:
                            for key in ["Exif/DateTimeOriginal", "Exif/DateTime", "System/FileModifyDate"]:
                                if key in cam.photo.meta:
                                    dt = datetime.strptime(cam.photo.meta[key], "%Y:%m:%d %H:%M:%S")
                                    photo_date = dt.strftime("%Y%m%d")

                                    chunk.label = photo_date
                                    self.txtChunkName.setPlainText(photo_date)
                                    Metashape.app.document.chunk = chunk
                                    QtWidgets.QApplication.processEvents()
                                    self.chunkUpdated.emit()
                                    print(f" Chunk successfully renamed to: {photo_date}")
                                    return
                    print("Failed to match photo to camera or extract metadata.")

                except Exception as e:
                    print(f"Exception in delayed rename: {e}")

            # Delay long enough to allow internal photo/meta processing to complete
            QtCore.QTimer.singleShot(500, delayed_rename)
        


    def getProjectName(self):
        '''
        Slot: gets project name from the user
        '''
        if(self.project_path): # if there is already a valid project name and path, save any changes so they ont get lost when the new project is made
            Metashape.app.document.save(path = self.project_path)
        project_path = QtWidgets.QFileDialog.getSaveFileName(self, 'Open file', self.parent.project_folder, "Metashape Project (*.psx)")[0]
        new_name = os.path.basename(project_path)[:-4]
        if(self.checkNaming(new_name) and new_name):
            self.project_name = new_name
            self.project_path = project_path
            self.parent.doc = Metashape.Document()
            self.parent.doc.addChunk()
            self.parent.doc.save(path = self.project_path)
            Metashape.app.document.open(path = self.project_path)
            self.txtAddPhotos.setPlainText("No folder selected")
            self.labelPhotosAdded.setText("")
        elif(not self.checkNaming(new_name)):
            Metashape.app.messageBox("Unable to create project: please select a name that includes only alphanumeric characters (abcABC123) and underscore (_) or dash (-), with no special characters (e.g. @$/.)")

        self.txtProjectName.setPlainText(self.project_name)

    def getChunkName(self):
        '''
        Slot to save chunk name
        '''
        new_name, ok = QtWidgets.QInputDialog().getText(self, "Create Chunk", "Chunk name:")
        if(self.checkNaming(new_name) and new_name and ok):
            self.chunk_name = new_name
            chunk = Metashape.app.document.chunk
            Metashape.app.document.chunk.label = new_name
            Metashape.app.update()
            QtWidgets.QApplication.processEvents()
            
            # Force GUI refresh
            Metashape.app.document.chunk = chunk
            QtWidgets.QApplication.processEvents()
            
            self.chunkUpdated.emit()
        elif(not self.checkNaming(new_name) and ok):
            Metashape.app.messageBox("Unable to rename chunk: please select a name that includes only alphanumeric characters (abcABC123) and underscore (_) or dash (-), with no special characters (e.g. @$/.)")
        self.txtChunkName.setPlainText(self.chunk_name)
        
    def checkNaming(self, name):
        '''
        Checks project and chunk names to ensure there are no special characters in them
        '''
        if(re.search(r"[.\^$*+?\[\]|<>&\\]", name)):
            return False
        return True

    def saveProject(self):
        '''
        Saves project to the designated path, leaving chunk names as is
        '''
        #active_chunk_name = self.chunk.label
        #Metashape.app.document.chunk.label = self.chunk_name

        if(self.project_path):
            # save project with new name - if project already exists, the current project state will be saved as changes to it
            Metashape.app.document.save(path = self.project_path)
            self.parent.project_folder = os.path.dirname(self.project_path)
            self.parent.project_name = os.path.basename(self.project_path)[:-4]
        else:
            Metashape.app.messageBox("Unable to save project: please select a name and file path for the project")

    '''def extract_photo_date(self, photo_list):
        doc = Metashape.app.document
        chunk = doc.chunk
        photo_path = photo_list[0]
        try:
            with open(photo_path, 'rb') as f:
                tags = exifread.process_file(f, stop_tag="EXIF DateTimeOriginal")
            date_tag = tags.get("EXIF DateTimeOriginal")

            if not date_tag:
                raise ValueError("DateTimeOriginal tag not found.")

            dt = datetime.strptime(str(date_tag), "%Y:%m:%d %H:%M:%S")
            self.photo_date = dt.strftime("%Y%m%d")
            return self.photo_date

        except Exception as e:
            print(f"Failed to extract photo date: {e}")
            return None
        '''
    def extract_photo_date(self, photo_list):
        import os
        doc = Metashape.app.document
        chunk = doc.chunk

        if not photo_list or not chunk:
            print("No photos or chunk found.")
            return None

        input_path = os.path.abspath(photo_list[0])

        for cam in chunk.cameras:
            if cam.photo and cam.photo.path:
                cam_path = os.path.abspath(cam.photo.path)
                if cam_path == input_path and cam.photo.meta:
                    meta = cam.photo.meta
                    for key in ["Exif/DateTimeOriginal", "Exif/DateTime", "System/FileModifyDate"]:
                        if key in meta:
                            try:
                                dt = datetime.strptime(meta[key], "%Y:%m:%d %H:%M:%S")
                                self.photo_date = dt.strftime("%Y%m%d")
                                return self.photo_date
                            except Exception as e:
                                print(f"Error parsing {key}: {e}")
                                return None
                    print("No supported date field found in metadata.")
                    return None

        print("Photo path not found in chunk or photo missing metadata.")
        return None
    


class BoundaryMarkerDlg(QtWidgets.QDialog):
    '''
    Optional sub-dialog box in which the user can specify the arrangement of the corner
    markers for georeferencing.

    This class does not actually modify the georeferencing file or the active chunk's markers,
    it is used only to get user input as to how the boundary creation may be adjusted for different
    marker placement scenarios. See the boundaryCreation() and create_shape_from_markers() functions
    in FullUW_dialog.py to see how this functionality is implemented.

    The main output from this class is the corner_markers list, which is a list of integers
    representing the corner markers of a photomosaic plot in clockwise order, e.g.
    [top-left, top-right, bottom-right, bottom-left]
    '''
    def __init__(self, parent):
        self.corner_markers = [] # initialize return value to empty list

        # initialize main dialog window
        QtWidgets.QDialog.__init__(self, parent)
        self.setWindowTitle("Corner Marker Positions")

        # ---- create widgets ----
        self.labelCornerPositioning = QtWidgets.QLabel("Each auto-detectable marker is numbered with a " +
                                        "unique integer. When setting up a plot, it is best to place the " +
                                        "corner markers around the plot such that they are in numeric order " +
                                        "going clockwise or counterclockwise, as shown as the default below. However, if the " +
                                        "markers were placed in a different arrangement, you may specify that " +
                                        "arrangement here. This will enable the script to draw the bounding " +
                                        "box for the plot correctly.\n\n")
        self.labelCornerPositioning.setToolTip("The orientation of the plot does not matter, only the relative " +
                                        "positions of the markers around it.\nFor example, the default " +
                                        "arrangement below could also be specified by setting the top-left to target " +
                                        "4,\nthe top-right to target 3, the bottom-right to target 2, and the bottom-left " +
                                        "to target 1")
        self.labelCornerPositioning.setWordWrap(True)
        # self.labelCornerPositioning.setAlignment(QtCore.Qt.AlignCenter)

        self.labelCorner1 = QtWidgets.QLabel("NW target number: ")
        self.spinboxCorner1 = QtWidgets.QSpinBox()
        self.spinboxCorner1.setMinimum(1)
        self.spinboxCorner1.setValue(4)

        self.labelCorner2 = QtWidgets.QLabel("NE target number: ")
        self.spinboxCorner2 = QtWidgets.QSpinBox()
        self.spinboxCorner2.setMinimum(1)
        self.spinboxCorner2.setValue(1)

        self.labelCorner3 = QtWidgets.QLabel("SE target number: ")
        self.spinboxCorner3 = QtWidgets.QSpinBox()
        self.spinboxCorner3.setMinimum(1)
        self.spinboxCorner3.setValue(2)

        self.labelCorner4 = QtWidgets.QLabel("SW target number: ")
        self.spinboxCorner4 = QtWidgets.QSpinBox()
        self.spinboxCorner4.setMinimum(1)
        self.spinboxCorner4.setValue(3)

        self.btnOk = QtWidgets.QPushButton("Ok")
        self.btnOk.setFixedSize(70, 40)
        self.btnOk.setToolTip("Set Marker Positions")

        self.btnClose = QtWidgets.QPushButton("Close")
        self.btnClose.setFixedSize(70, 40)

        # ---- create layouts to hold widgets ----
        corner1_layout = QtWidgets.QHBoxLayout()
        corner1_layout.addWidget(self.labelCorner1)
        corner1_layout.addWidget(self.spinboxCorner1)

        corner2_layout = QtWidgets.QHBoxLayout()
        corner2_layout.addWidget(self.labelCorner2)
        corner2_layout.addWidget(self.spinboxCorner2)

        corner3_layout = QtWidgets.QHBoxLayout()
        corner3_layout.addWidget(self.labelCorner3)
        corner3_layout.addWidget(self.spinboxCorner3)

        corner4_layout = QtWidgets.QHBoxLayout()
        corner4_layout.addWidget(self.labelCorner4)
        corner4_layout.addWidget(self.spinboxCorner4)

        ok_layout = QtWidgets.QHBoxLayout()
        ok_layout.addWidget(self.btnOk)
        ok_layout.addWidget(self.btnClose)

        # create grid layout to arrange target inputs in a rectangle
        plot_layout = QtWidgets.QGridLayout()
        plot_layout.setColumnStretch(0, 1)
        plot_layout.setColumnStretch(1, 10)
        plot_layout.setColumnMinimumWidth(1, 150)
        plot_layout.setColumnStretch(2, 1)
        plot_layout.setRowStretch(0, 1)
        plot_layout.setRowStretch(1, 10)
        plot_layout.setRowMinimumHeight(1, 150)
        plot_layout.setRowStretch(2, 1)

        # create a groupbox to act as a plot rectangle
        plot_groupbox = QtWidgets.QGroupBox()
        plot_label = QtWidgets.QLabel("Reef Plot")
        plot_label.setAlignment(QtCore.Qt.AlignCenter)
        plot_label.setEnabled(False)

        inner_layout = QtWidgets.QHBoxLayout()
        inner_layout.addWidget(plot_label)
        plot_groupbox.setLayout(inner_layout)

        # ---- assemble layouts into main layout ----
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.addWidget(self.labelCornerPositioning)
        plot_layout.addLayout(corner1_layout, 0, 0)
        plot_layout.addLayout(corner2_layout, 0, 2)
        plot_layout.addWidget(plot_groupbox, 1, 1)
        plot_layout.addLayout(corner3_layout, 2, 2)
        plot_layout.addLayout(corner4_layout, 2, 0)
        main_layout.addLayout(plot_layout)
        main_layout.addLayout(ok_layout)

        self.setLayout(main_layout)

        # ---- connect signals and slots ----
        self.btnOk.clicked.connect(self.ok)
        self.btnClose.clicked.connect(self.reject)

    def ok(self):
        '''
        Slot for user to set corner marker values and close the dialog - ensures that there is a valid
        return value only if the user clicks ok. A more integrated Qt way to do this might be to
        reimplement the core 'accept()' slot, but this way is a bit simpler.

        For this specific use case, making sure there is a valid return isn't really necessary since there
        will be a default value in the main dialog anyway, but it's good practice and keeps the script a bit more flexible
        '''
        self.corner_markers = [self.spinboxCorner1.value(), self.spinboxCorner2.value(), self.spinboxCorner3.value(), self.spinboxCorner4.value()]
        self.reject()



class GeoreferenceGroupBox(CollapsibleGroupBox):
    '''
    Groupbox holding widgets used for importing scaling and georeferencing information into an
    existing Metashape project, as well as specifying the formatting and arrangement of the
    georeferencing file and markers.

    This class's only purpose is UI organization; it does not actually import scaling or referencing
    information. For these tasks, see importReference() and createScalebars() in FullUW_dialog.py.
    This class is best used in conjunction with those functions by accessing its member variables,
    such as georef_path or scalebars_path.
    '''
    def __init__(self, parent):
        super().__init__("Georeferencing")
        self.parent = parent
        self.autoDetectMarkers = False
        # Initialize file paths so accessing them before the user picks a
        # file doesn't AttributeError. Callers (e.g. the main workflow
        # dialog) can preload these from QSettings to restore a previously
        # used scalebar file across sessions.
        self.scalebars_path = ""
        self.georef_path = ""
        # set default corner marker arrangement
        self.corner_markers = [1, 2, 3, 4]

        # -- Build Widgets --
        # scaling/georeferencing type
        self.labelReference = QtWidgets.QLabel("Do you want to input georeferencing information now? \nSelect No if chunk is already referenced or if you'd like to do it manually.")
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
        self.target_type = Metashape.CircularTarget12bit # set default target type
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

        self.btnMarkerPosition = QtWidgets.QPushButton("Adjust Corner Markers")
        # self.btnMarkerPosition.setEnabled(False)
        # self.btnMarkerPosition.setFixedWidth(75)

        # add in spinbox widgets to define georeferencing format
        self.labelInputFormatting = QtWidgets.QLabel("Specify which columns in the georeferencing file correspond to the indicated properties")

        self.labelRefLabel = QtWidgets.QLabel("Label:")
        self.spinboxRefLabel = QtWidgets.QSpinBox()
        self.spinboxRefLabel.setMinimum(1)
        self.spinboxRefLabel.setValue(1)

        self.labelRefX = QtWidgets.QLabel("Long (X):")
        self.spinboxRefX = QtWidgets.QSpinBox()
        self.spinboxRefX.setMinimum(1)
        self.spinboxRefX.setValue(3)

        self.labelRefY = QtWidgets.QLabel("Lat (Y):")
        self.spinboxRefY = QtWidgets.QSpinBox()
        self.spinboxRefY.setMinimum(1)
        self.spinboxRefY.setValue(2)

        self.labelRefZ = QtWidgets.QLabel("Depth (Z):")
        self.spinboxRefZ = QtWidgets.QSpinBox()
        self.spinboxRefZ.setMinimum(1)
        self.spinboxRefZ.setValue(4)

        self.labelAccuracy = QtWidgets.QLabel("Accuracy")
        self.spinboxXAcc = QtWidgets.QSpinBox()
        self.spinboxXAcc.setMinimum(1)
        self.spinboxXAcc.setValue(5)
        self.spinboxYAcc = QtWidgets.QSpinBox()
        self.spinboxYAcc.setMinimum(1)
        self.spinboxYAcc.setValue(5)
        self.spinboxZAcc = QtWidgets.QSpinBox()
        self.spinboxZAcc.setMinimum(1)
        self.spinboxZAcc.setValue(6)

        self.labelSkipRows = QtWidgets.QLabel("Start import at row:")
        self.spinboxSkipRows = QtWidgets.QSpinBox()
        self.spinboxSkipRows.setMinimum(1)
        self.spinboxSkipRows.setValue(2)

        # ---- Build Sublayouts ----
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

        marker_pos_layout = QtWidgets.QHBoxLayout()
        marker_pos_layout.addStretch()
        marker_pos_layout.addWidget(self.btnMarkerPosition)

        ref_format_layout = QtWidgets.QGridLayout()
        ref_format_layout.setColumnStretch(0, 1)
        ref_format_layout.setColumnStretch(1, 10)
        ref_format_layout.setColumnStretch(2, 10)
        ref_format_layout.addWidget(self.labelInputFormatting, 0, 0, 1, 3)

        ref_format_layout.addWidget(self.labelRefLabel, 1, 0)
        ref_format_layout.addWidget(self.spinboxRefLabel, 1, 1)
        ref_format_layout.addWidget(self.labelAccuracy, 1, 2)

        ref_format_layout.addWidget(self.labelRefX, 2, 0)
        ref_format_layout.addWidget(self.spinboxRefX, 2, 1)
        ref_format_layout.addWidget(self.spinboxXAcc, 2, 2)

        ref_format_layout.addWidget(self.labelRefY, 3, 0)
        ref_format_layout.addWidget(self.spinboxRefY, 3, 1)
        ref_format_layout.addWidget(self.spinboxYAcc, 3, 2)

        ref_format_layout.addWidget(self.labelRefZ, 4, 0)
        ref_format_layout.addWidget(self.spinboxRefZ, 4, 1)
        ref_format_layout.addWidget(self.spinboxZAcc, 4, 2)

        skip_rows_layout = QtWidgets.QHBoxLayout()
        skip_rows_layout.addWidget(self.labelSkipRows)
        skip_rows_layout.addWidget(self.spinboxSkipRows)
        ref_format_layout.addLayout(skip_rows_layout, 5, 0, 1, 2)
        # Phantom row that absorbs any vertical slack so the spinbox rows
        # don't get inflated when this groupbox is taller than its content.
        ref_format_layout.setRowStretch(6, 1)

        self.ref_format_groupbox = QtWidgets.QGroupBox("Column Formatting")
        self.ref_format_groupbox.setLayout(ref_format_layout)
        self.ref_format_groupbox.setEnabled(False)

        # ---- Assemble Layouts ----
        reference_layout = QtWidgets.QVBoxLayout()
        reference_layout.addLayout(autodetect_layout)
        reference_layout.addLayout(scale_layout)
        reference_layout.addLayout(geo_layout)
        reference_layout.addLayout(marker_pos_layout)
        reference_layout.addWidget(self.ref_format_groupbox)
        # Absorb vertical slack at the bottom — without this, when the dialog
        # is taller than the natural content height the gap between the
        # "Adjust Corner Markers" button and the Column Formatting box (and
        # all the other rows in this panel) gets inflated.
        reference_layout.addStretch(1)
        self.setLayout(reference_layout)

        # ---- Connect Signals and Slots ----
        self.btnScaleFile.clicked.connect(self.getScaleFile)
        self.btnGeoFile.clicked.connect(self.getGeoFile)
        self.comboReference.currentIndexChanged.connect(self.onReferenceChanged)
        self.comboTargetType.currentIndexChanged.connect(self.onTargetTypeChange)
        self.btnMarkerPosition.clicked.connect(self.getMarkerPosition)

    def getScaleFile(self):
        # maybe change this to local variable and get text value from text box directly when workflow is run?
        # might be better to connect textchanged() signal to a custom function, but more work
        self.scalebars_path = QtWidgets.QFileDialog.getOpenFileName(self, 'Open file', self.parent.project_folder, "CSV files (*.csv *.txt)")[0]
        if(self.scalebars_path):
            self.txtScaleFile.setPlainText(self.scalebars_path)
        else:
            self.txtScaleFile.setPlainText("No File Selected")

    def getGeoFile(self):
        self.georef_path = QtWidgets.QFileDialog.getOpenFileName(self, 'Open file', self.parent.project_folder, "CSV files (*.csv *.txt)")[0]
        if(self.georef_path):
            self.txtGeoFile.setPlainText(self.georef_path)
        else:
            self.txtGeoFile.setPlainText("No File Selected")

    def onReferenceChanged(self):
        '''
        Slot: When the user switches between using auto-detectable markers and not
        using them via the comboReference combo box, this function enables or disables
        all widgets related to the automatic referencing process.
        '''
        self.autoDetectMarkers = self.comboReference.currentIndex()
        self.ref_format_groupbox.setEnabled(self.autoDetectMarkers)

        self.comboTargetType.setEnabled(self.autoDetectMarkers)

        self.labelScaleFile.setEnabled(self.autoDetectMarkers)
        self.btnScaleFile.setEnabled(self.autoDetectMarkers)
        self.txtScaleFile.setEnabled(self.autoDetectMarkers)

        self.labelGeoFile.setEnabled(self.autoDetectMarkers)
        self.btnGeoFile.setEnabled(self.autoDetectMarkers)
        self.txtGeoFile.setEnabled(self.autoDetectMarkers)

    def getMarkerPosition(self):
        '''
        Slot: Launches a sub-dialog box where the user can specify the arrangement of
        corner markers.
        '''
        marker_dlg = BoundaryMarkerDlg(self.parent)
        marker_dlg.exec()
        self.corner_markers = marker_dlg.corner_markers

    def onTargetTypeChange(self):
        '''
        Updates target type member variable when the user selects a new type
        '''
        target_type_index = self.comboTargetType.currentIndex()
        self.target_type = self.targetTypes[target_type_index][1]
