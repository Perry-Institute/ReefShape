"""
3D/2D Surface Area Ratio Calculation
Will Greene

Created with the help of ChatGPT

This script takes an existing model and outer boundary shape and uses them to calculate the ratio of the 3D surface area of the mesh (within the boundary shape) to the planar 2D area of the boundary shape. This ratio is a measure of the structural complexity of the reef, with higher values indicating greater complexity and habitat quality. The metric is affected by the resolution settings of the mesh, and for a given reef, higher resolution values will yield somewhat higher 3D/2D surface area ratios.

    Usage notes:
        - The script requires there to be a single model in the chunk that is activated as default
        - The script also requires there to be a single shape in the project that is set as the
        outer boundary. This is generated automatically by the Full UW Workflow script, or this
        can be done manually by deleting any existing shapes, drawing your own boundary shape on a
        completed orthomosaic, and setting it as "outer boundary" by right-clicking on it and
        setting "boundary type" to "outer boundary".
"""
import Metashape
from PySide2.QtWidgets import QMessageBox

# Function to calculate 3D surface area of a mesh
def calculate_3d_surface_area(mesh):
    sa = mesh.area()
    return sa

# Function to calculate 2D planar surface area
def calculate_2d_surface_area(outer_boundary):
    pa = outer_boundary.area()
    return pa

# Function to duplicate the 3D model and clip it by an outer boundary shape
def duplicate_and_clip_model(chunk):
    task = Metashape.Tasks.DuplicateAsset()
    task.asset_key = chunk.model.key
    task.asset_type = Metashape.DataSource.ModelData
    task.clip_to_boundary = True
    task.apply(chunk)

    chunk.model.label = "Cropped 3D Model"

# Function to divide 3D surface area by 2D planar surface area
def calculate_ratio(surface_area_3d, surface_area_2d):
    return surface_area_3d / surface_area_2d

def find_ratio_boundary(chunk):
    """The outer boundary shape, or None. Looked for by type rather than by
    position, since the boundary is not necessarily the first shape."""
    if not chunk.shapes:
        return None
    return next((shape for shape in chunk.shapes
                 if shape.boundary_type == Metashape.Shape.BoundaryType.OuterBoundary), None)

# Main function to execute the script
def main():
    try:
        _calculate()
    except Exception as err:
        import traceback
        traceback.print_exc()
        Metashape.app.messageBox("Could not calculate the ratio:\n\n{}".format(err))

def _calculate():
    # Get the active chunk
    chunk = Metashape.app.document.chunk
    if chunk is None or chunk.model is None:
        Metashape.app.messageBox("The active chunk has no 3D model.")
        return

    outer_boundary = find_ratio_boundary(chunk)
    if outer_boundary is None:
        Metashape.app.messageBox(
            "The active chunk has no outer boundary. Create one (ReefShape > Tools > "
            "Create Boundary), or set a polygon's boundary type to 'Outer Boundary'.")
        return

    # Remember the model to go back to; the clipped copy becomes the active one.
    original_model = chunk.model
    duplicated_model = None
    try:
        # Duplicate and clip the 3D model
        duplicate_and_clip_model(chunk)
        duplicated_model = chunk.model

        # Calculate 3D surface area
        surface_area_3d = calculate_3d_surface_area(duplicated_model)

        # Calculate 2D planar surface area
        surface_area_2d = calculate_2d_surface_area(outer_boundary)
    finally:
        # Always remove the temporary copy and restore the original model,
        # including when something above failed -- otherwise the project is
        # left with a stray "Cropped 3D Model" that has become the active one.
        if duplicated_model is not None and duplicated_model != original_model:
            try:
                chunk.remove(duplicated_model)
            except Exception as err:
                print("Could not remove the temporary cropped model: {}".format(err))
        try:
            chunk.model = original_model
        except Exception as err:
            print("Could not restore the original model: {}".format(err))

    if not surface_area_2d:
        Metashape.app.messageBox("The outer boundary has zero area, so no ratio can be calculated.")
        return

    # Calculate the ratio
    ratio = calculate_ratio(surface_area_3d, surface_area_2d)

    #print the ratio to make sure the value isn't lost when closing the dialog box
    print("The ratio of 3D surface area to 2D planar surface area is:")
    print(ratio)

    # Display result in a QMessageBox
    QMessageBox.information(None, "Ratio Result", f"The ratio of 3D surface area to 2D planar surface area is {ratio}")

# add function to menu
label = "ReefShape/Tools/Calculate Surface Area Ratio"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, main)
print("To execute this script press {}".format(label))
