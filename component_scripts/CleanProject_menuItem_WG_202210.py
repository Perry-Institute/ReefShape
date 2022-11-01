# script to clean up files that aren't needed for long-term storage
# written by Will Greene, Perry Institute for Marine Science

import Metashape

def clean_project():
   doc = Metashape.app.document
   chunk = doc.chunk
   ortho = chunk.orthomosaic
   depthmaps = chunk.depth_maps
   sparsecloud = chunk.point_cloud
   
   #remove key points(if present)
   sparsecloud.removeKeypoints()
   #remove depth maps (if present)
   depthmaps.clear()
   #remove orthophotos without removing orthomosaic
   ortho.removeOrthophotos()
   
   Metashape.app.update()
   
   print("Script finished")
label = "Custom/Clean Project"
Metashape.app.addMenuItem(label, clean_project)
