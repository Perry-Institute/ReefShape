# location for this file:
# Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts
# Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts


import Metashape

# script slightly modified August 2022 by Will Greene
# Originally by Alexey Pasumansky, published at
# https://www.agisoft.com/forum/index.php?topic=14183.0

def boundary_creation():
        def create_shape_from_markers(marker_list, chunk):

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
	
                print("Script finished.")
                return 1


        chunk = Metashape.app.document.chunk

        m_list = []
        for marker in chunk.markers:
                m_list.append(marker)
        m_list_short = m_list[:4]
        create_shape_from_markers(m_list_short, chunk)

label = "Custom/Create Boundary from Corner Markers"
Metashape.app.addMenuItem(label, boundary_creation)
