# script to update/create scalebars from a csv file.
# location for this file:
# Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts
# Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts

# csv file format:
# Marker_1_label,Marker_2_label,distance,accuracy
# ex:
# target 5,target 6,0.4965,0.00025
# target 7,target 8,0.4975,0.00025
# target 9,target 10,0.4985,0.00025

# A header row, blank lines, and scalebars naming markers that are not in this
# chunk are all tolerated (the last are skipped and listed in the console).


# This script was based on a script for creating scalebars in Photoscan available at:
# http://hairystickman.co.uk/photoscan-scale-bars/
#
# updated May 2022 by Will Greene / Perry Institute for Marine Science and
# Asif-ul Islam / Middlebury College for use with underwater photogrammetry
#
# The file handling now lives in reefshape_modules.reefshape_core.create_scalebars,
# shared with the full workflow and the batch processor, so all three read a
# scalebar file the same way.

import Metashape
from reefshape_modules import reefshape_core


def Create_Scalebars():
    try:
        doc = Metashape.app.document
        chunk = doc.chunk
        if chunk is None:
            Metashape.app.messageBox("There is no active chunk.")
            return
        if len(chunk.markers) == 0:
            Metashape.app.messageBox(
                "No markers found! Detect or place the scalebar targets first, "
                "then create the scalebars.")
            return

        path = Metashape.app.getOpenFileName("Select input text file:")
        if not path:
            return                      # cancelled

        before = len(chunk.scalebars)
        error = reefshape_core.create_scalebars(chunk, path)
        if error:
            Metashape.app.messageBox(error)
            return

        chunk.updateTransform()
        Metashape.app.update()
        added = len(chunk.scalebars) - before
        print("Script finished")
        Metashape.app.messageBox(
            "Scalebars updated: {} new, {} in total.\n\nTargets named in the "
            "file but not found in this chunk were skipped; they are listed in "
            "the console.".format(added, len(chunk.scalebars)))
    except Exception as err:
        Metashape.app.messageBox("Could not create the scalebars:\n\n{}".format(err))


label = "ReefShape/Tools/Create Scalebars from Targets"
Metashape.app.removeMenuItem(label)
Metashape.app.addMenuItem(label, Create_Scalebars)
