'''
Boundary Copy Function with Dialog Box
05/07/2024
Will Greene
PIMS/ASU

This script prompts the user for a dialog box to select a "source chunk" and a "target chunk." Upon clicking "Copy Boundary," 
it copies the outer boundary polygon from the source chunk to the target chunk and names it "Copied Boundary." This function 
is useful for getting aligned chunks with custom boundaries to export pixel-aligned maps for analysis in software like TagLab.

Written with the help of AI (ChatGPT).
'''


import Metashape
from PySide2 import QtWidgets


class BoundaryCopyGUI:
    def __init__(self, parent):
        self.parent = parent

    def initUI(self):
        dialog = QtWidgets.QDialog(parent=None)
        dialog.setWindowTitle("Copy Boundary Polygon")

        layout = QtWidgets.QVBoxLayout(dialog)

        source_label = QtWidgets.QLabel("Source Chunk")
        self.source_combo = QtWidgets.QComboBox()
        layout.addWidget(source_label)
        layout.addWidget(self.source_combo)

        target_label = QtWidgets.QLabel("Target Chunk")
        self.target_combo = QtWidgets.QComboBox()
        layout.addWidget(target_label)
        layout.addWidget(self.target_combo)

        copy_button = QtWidgets.QPushButton("Copy Boundary")
        copy_button.clicked.connect(self.copy_boundary)
        layout.addWidget(copy_button)

        # Populate combo boxes with chunk names
        doc = self.parent.document
        for chunk in doc.chunks:
            self.source_combo.addItem(chunk.label)
            self.target_combo.addItem(chunk.label)

        dialog.exec_()
    
    def copy_boundary(self):
        source_index = self.source_combo.currentIndex()
        target_index = self.target_combo.currentIndex()

        doc = self.parent.document

        if source_index < 0 or target_index < 0:
            print("Invalid source or target chunk selection.")
            return

        source_chunk = doc.chunks[source_index]
        target_chunk = doc.chunks[target_index]

        if target_chunk is None or not hasattr(target_chunk, 'shapes'):
            print("Target chunk is not properly initialized or does not exist.")
            return

        source_outer_boundary = next((shape for shape in source_chunk.shapes if shape.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary), None)
        if source_outer_boundary is None:
            print("Source chunk does not have an outer boundary shape.")
            return

        target_outer_boundary = target_chunk.shapes.addShape()
        target_outer_boundary.label = "Copied Boundary"
        target_outer_boundary.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary

        # Copy geometry directly
        target_outer_boundary.geometry = source_outer_boundary.geometry

        print("Boundary copied successfully.")
    
    
    

def add_custom_menu():
    Metashape.app.removeMenuItem("ReefShape/Tools/Copy Boundary Polygon")
    Metashape.app.removeMenuItem("ReefShape/Tools/Copy Boundary")
    Metashape.app.addMenuItem("ReefShape/Tools/Copy Boundary", lambda: show_boundary_copy_tool())

def show_boundary_copy_tool():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    boundary_copy_gui = BoundaryCopyGUI(Metashape.app)
    boundary_copy_gui.initUI()

def main():
    add_custom_menu()

if __name__ == "__main__":
    main()
