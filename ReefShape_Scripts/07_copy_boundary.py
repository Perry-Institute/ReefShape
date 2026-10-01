'''
Boundary Copy Function with Dialog Box
10/18/2024
Will Greene
PIMS/ASU

This script prompts the user for a dialog box to select a "source chunk" and a "target chunk." Upon clicking "Copy Boundary,"
it copies the outer boundary polygon from the source chunk to the target chunk and names it "Copied Boundary." This function
is useful for getting aligned chunks with custom boundaries to export pixel-aligned maps for analysis in software like TagLab.

Written with the help of AI (ChatGPT).
'''

import Metashape
from PySide2 import QtWidgets


def _outer_boundary(chunk):
    """The chunk's outer boundary shape, or None."""
    if not chunk.shapes:
        return None
    return next((shape for shape in chunk.shapes
                 if shape.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary), None)


class BoundaryCopyGUI:
    def __init__(self, parent):
        self.parent = parent
        self.dialog = None

    def initUI(self):
        doc = self.parent.document
        if len(doc.chunks) < 2:
            Metashape.app.messageBox(
                "Copying a boundary needs two chunks: one that has the boundary "
                "and one to copy it to.")
            return

        dialog = QtWidgets.QDialog(parent=None)
        dialog.setWindowTitle("Copy Boundary Polygon")
        self.dialog = dialog

        layout = QtWidgets.QVBoxLayout(dialog)

        source_label = QtWidgets.QLabel("Source Chunk")
        self.source_combo = QtWidgets.QComboBox()
        layout.addWidget(source_label)
        layout.addWidget(self.source_combo)

        target_label = QtWidgets.QLabel("Target Chunk")
        self.target_combo = QtWidgets.QComboBox()
        layout.addWidget(target_label)
        layout.addWidget(self.target_combo)

        buttons = QtWidgets.QHBoxLayout()
        copy_button = QtWidgets.QPushButton("Copy Boundary")
        copy_button.clicked.connect(self.copy_boundary)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(dialog.reject)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        buttons.addWidget(copy_button)
        layout.addLayout(buttons)

        # Populate combo boxes with chunk names
        active_chunk = doc.chunk  # Get the currently active chunk
        for chunk in doc.chunks:
            self.source_combo.addItem(chunk.label)
            self.target_combo.addItem(chunk.label)

        # Set the default source chunk to the first chunk in the document
        self.source_combo.setCurrentIndex(0)

        # Set the default target chunk to the currently active chunk
        if active_chunk:
            active_chunk_index = doc.chunks.index(active_chunk)
            self.target_combo.setCurrentIndex(active_chunk_index)

        dialog.exec_()

    def copy_boundary(self):
        """Slot for the Copy button. Failures are shown in a message box: one
        escaping a Qt slot only reaches the console, which most users never see."""
        try:
            self._copy_boundary()
        except Exception as err:
            import traceback
            traceback.print_exc()
            Metashape.app.messageBox("Could not copy the boundary:\n\n{}".format(err))

    def _copy_boundary(self):
        source_index = self.source_combo.currentIndex()
        target_index = self.target_combo.currentIndex()

        doc = self.parent.document

        if source_index < 0 or target_index < 0:
            Metashape.app.messageBox("Choose a source chunk and a target chunk.")
            return

        source_chunk = doc.chunks[source_index]
        target_chunk = doc.chunks[target_index]

        if source_chunk == target_chunk:
            Metashape.app.messageBox(
                "The source and target are the same chunk. Choose the chunk that "
                "has the boundary as the source, and the chunk to copy it to as "
                "the target.")
            return

        source_outer_boundary = _outer_boundary(source_chunk)
        if source_outer_boundary is None:
            Metashape.app.messageBox(
                "The source chunk '{}' does not have an outer boundary shape to "
                "copy.".format(source_chunk.label))
            return

        # Two outer boundaries on one chunk leave it unclear which one the
        # exports clip to, so offer to replace rather than add a second.
        existing = _outer_boundary(target_chunk)
        if existing is not None:
            reply = QtWidgets.QMessageBox.question(
                self.dialog, "Replace boundary?",
                "The target chunk '{}' already has an outer boundary ('{}'). "
                "Replace it with the one from '{}'?".format(
                    target_chunk.label, existing.label or "unnamed",
                    source_chunk.label),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                return
            target_chunk.shapes.remove([existing])

        # Ensure the target chunk has a shapes folder
        if not target_chunk.shapes:
            target_chunk.shapes = Metashape.Shapes()  # Initialize shapes if not present
            target_chunk.shapes.crs = source_chunk.shapes.crs  # Ensure CRS matches the source chunk

        target_outer_boundary = target_chunk.shapes.addShape()
        target_outer_boundary.label = "Copied Boundary"
        target_outer_boundary.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary

        # Copy geometry directly
        target_outer_boundary.geometry = source_outer_boundary.geometry

        Metashape.app.update()
        print("Boundary copied successfully")
        Metashape.app.messageBox(
            "Copied the outer boundary from '{}' to '{}'. Save the project to keep "
            "it.".format(source_chunk.label, target_chunk.label))

        self.dialog.accept()


def add_custom_menu():
    Metashape.app.removeMenuItem("ReefShape/Tools/Copy Boundary Polygon")
    Metashape.app.removeMenuItem("ReefShape/Tools/Copy Boundary")
    Metashape.app.addMenuItem("ReefShape/Tools/Copy Boundary", lambda: show_boundary_copy_tool())

def show_boundary_copy_tool():
    try:
        boundary_copy_gui = BoundaryCopyGUI(Metashape.app)
        boundary_copy_gui.initUI()
    except Exception as err:
        Metashape.app.messageBox("Copy Boundary error:\n\n{}".format(err))

def main():
    add_custom_menu()

if __name__ == "__main__":
    main()
