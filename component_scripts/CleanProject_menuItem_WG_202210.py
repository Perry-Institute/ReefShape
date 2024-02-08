# location for this file:
# Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts
# Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts

# script to clean up files that aren't needed for long-term storage
# written by Will Greene, Perry Institute for Marine Science, Oct 2022
# Only functions on the currently selected chunk
import Metashape

def clean_project():
   doc = Metashape.app.document
   chunk = doc.chunk


   #remove orthophotos without removing orthomosaic
   ortho = chunk.orthomosaic
   ortho.removeOrthophotos()

   #remove key points(if present)
   sparsecloud = chunk.tie_points
   sparsecloud.removeKeypoints()

   #remove depth maps (if present)
   depthmaps = chunk.depth_maps
   depthmaps.clear()
   
   Metashape.app.update()
   Metashape.app.document.save()

   print("Script finished")
label = "Custom/Clean Project"
Metashape.app.addMenuItem(label, clean_project)
