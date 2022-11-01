# script to update/create scalebars from a csv file.
# location for this file:
# Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts
# Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts

# csv file format:
# Marker_1_label,Marker_2_label,distance,accuracy
# ex:
# point 1,point 2,9.903500,0.002000
# point 2,point 3,9.949000,0.002000
# point 3,point 4,9.913000,0.002000

# location for scalebar CSV: C:\Program Files\Agisoft\Metashape Pro\scalebars\

# PS: no additional spaces should be inserted

# tested in Metashape 1.8 (Windows 64)

# This script was based on a script for creating scalebars in Photoscan available at:
# http://hairystickman.co.uk/photoscan-scale-bars/
#
# updated May 2022 by Will Greene / Perry Institute for Marine Science and
# Asif-ul Islam / Middlebury College for use with underwater photogrammetry

import Metashape, os
from PySide2 import QtCore, QtGui

def Create_Scalebars():
   doc = Metashape.app.document
   chunk = doc.chunk

   iNumScaleBars=len(chunk.scalebars)
   iNumMarkers=len(chunk.markers)
   # Check for existing markers
   if (iNumMarkers == 0):
      raise Exception("No markers found! Unable to create scalebars.") 
   # Check for already existing scalebars
   if (iNumScaleBars > 0):
      print('There are already ',iNumScaleBars,' scalebars in this project.')
   
   cwd = "C:/Users/gsmar/AppData/Local/Agisoft/Metashape Pro/scripts" 
   file = open(cwd+"/scalebars/UWscalebars.txt", "rt")
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
   Metashape.app.update()
   print("Script finished")

#label = "Custom/Create Scalebars from Targets"
#Metashape.app.addMenuItem(label, Create_Scalebars)
Create_Scalebars()

