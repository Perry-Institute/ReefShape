import Metashape
from PySide2 import QtWidgets, QtCore

class CreateBoundary:
    def __init__(self, parent):
        self.parent = parent
        self.dialog = None
        self.selected_markers = {}

    def initUI(self):
        # Reset the marker list
        self.selected_markers = {}

        self.dialog = QtWidgets.QDialog(None, QtCore.Qt.WindowFlags())
        self.dialog.setWindowTitle("Corner Marker Positions")
        
        layout = QtWidgets.QVBoxLayout()

        # Add instruction label
        instruction_label = QtWidgets.QLabel("Select the markers (in order) that define the boundary:")
        layout.addWidget(instruction_label)

        # Add slot selection widgets
        for position in ["Corner 1", "Corner 2", "Corner 3", "Corner 4"]:
            label = QtWidgets.QLabel(f"{position}:")
            combo_box = QtWidgets.QComboBox()
            combo_box.addItem("Select Marker")
            combo_box.addItems(self.get_available_markers())
            combo_box.currentIndexChanged.connect(lambda state, pos=position, box=combo_box: self.marker_selected(pos, box.currentText()))
            layout.addWidget(label)
            layout.addWidget(combo_box)

        # Add buttons
        button_layout = QtWidgets.QHBoxLayout()
        ok_button = QtWidgets.QPushButton("Ok")
        ok_button.clicked.connect(self.generate_polygon)
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.dialog.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

        self.dialog.setLayout(layout)
        self.dialog.exec_()

    def get_available_markers(self):
        doc = self.parent.document
        chunk = doc.chunk
        return [marker.label for marker in chunk.markers]

    def marker_selected(self, position, marker):
        self.selected_markers[position] = marker

    def generate_polygon(self):
        doc = self.parent.document
        chunk = doc.chunk

        # Sort selected markers
        sorted_markers = [self.selected_markers[f"Corner {i}"] for i in range(1, 5)]

        # Assuming you have a function to create a polygon from selected markers
        if len(sorted_markers) == 4:
            selected_markers_objects = [marker for marker in chunk.markers if marker.label in sorted_markers]
            success = self.create_polygon_from_markers(selected_markers_objects, chunk)
            if success:
                self.dialog.accept()
        else:
            QtWidgets.QMessageBox.warning(self.dialog, "Warning", "Please select a marker for each corner.")

    def create_polygon_from_markers(self, marker_list, chunk):
        if not chunk:
            print("Empty project, script aborted")
            return False
        if len(marker_list) < 3:
            print("At least three markers required to create a polygon. Script aborted.")
            return False

        T = chunk.transform.matrix
        crs = chunk.crs
        if not chunk.shapes:
            chunk.shapes = Metashape.Shapes()
            chunk.shapes.crs = chunk.crs
        shape_crs = chunk.shapes.crs

        # Sort markers based on the order they were selected
        sorted_marker_list = sorted(marker_list, key=lambda marker: list(self.selected_markers.values()).index(marker.label))

        coords = [shape_crs.project(T.mulp(marker.position)) for marker in sorted_marker_list]

        shape = chunk.shapes.addShape()
        shape.label = "Marker Boundary"
        shape.geometry.type = Metashape.Geometry.Type.PolygonType
        shape.boundary_type = Metashape.Shape.BoundaryType.OuterBoundary
        shape.geometry = Metashape.Geometry.Polygon(coords)

        print("Script finished.")
        return True

def main():
    # Remove existing menu item if it exists
    app.removeMenuItem("ReefShape/Tools/Create Boundary")
    
    # Add new menu button under ReefShape/Tools
    app.addMenuItem("ReefShape/Tools/Create Boundary", lambda: create_boundary.initUI())

# Entry point
if __name__ == "__main__":
    app = Metashape.app
    create_boundary = CreateBoundary(app)
    main()
