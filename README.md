# Metashape_Reef
Agisoft Metashape scripts for underwater Large Area Imaging. Note: all scripts are tested to work with Metashape 1.8.4, and have not been updated to work with the latest Metashape 2.0 release. I know there is some incompatibility so don't bother trying yet!

## Intro
This is a collection of scripts for use in Agisoft Metashape. Please note that this is still very much a work in progress - if you're accessing this currently, <b> please do not share it widely quite yet! </b> If you want to learn more or are having trouble using any of the scripts, email wgreene@perryinstitute.org and I'll get back to you as soon as I can. 

## Basics
This repo is set up with a root folder that contains the resources you need to get the different scripts to work. 

<b>Full_UW_dialog... .py</b> This file is the main script that can run the entire process start to finish. You simply open Metashape, import your photos, save the project as the site name, and then rename the chunk containing the photos to the current date in numeric yyyymmdd format (20230119 would be Jan 19, 2023). This script can be run by clicking on Tools --> Run Script... in Metashape, which will create a custom menu button that has the script inside it. When you click on it, it will bring up a dialog box where you can input a georeference file, scalebar file, and a few other basic options. This will be expanded and made more comprehensive as we go. A better way to run the script is to install it where Metashape will execute / add the custom menu button every time you open the program. The correct location for the file varies on Mac vs PC:

Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts

Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts

<b>Scalebar Example.txt</b> This file is an example text file that contains the scale bar lengths. The idea is that you have a scalebar with metashape targets at either end. You measure the distance between the centerpoints of the targets, then add a line in a text file that corresponds to that scalebar. Each time this scalebar is detected in a project, its distance will be input into Metashape's reference panel. See text file for the correct formatting.

<b>Georef Example.csv</b> This CSV file shows the correct format for georeferencing information. The correct format is based off the way that ESRI Survey123 exports location data from the PIMS Photomosaic Georeferencing survey. Email me at wgreene@perryinstitute.org with your ArcGIS Online username and I can add you to our survey group. The key thing to note is if you're creating a georeferencing file manually, you need to put the target name (target x) that you collected a GPS point for in column 4, then lat, lon, depth, xyAccuracy, and zAccuracy in the following columns. It takes only decimal degrees for lat and lon and negative meters for depth. The file needs to be saved as a simple CSV, and you then input that csv into the full workflow dialog box.

There are also three other folders in the repo:

<b> batch_processing </b> This contains Metashape .xml files that can be loaded into the batch processing dialog in Metashape to carry out various tasks. They are probably not useful to you, the user.

<b> batch_scripts </b> This contains a set of python scripts that can execute specific functions useful to be included in a batch processing dialog. They can be installed anywhere that you can easily find them, and in a batch process, you can use the run script... command as a batch function, and then select the file for the desired action (E.g. OptimizationProcess script is useful to run after running Align Photos...). 

<b> component_scripts </b> This folder has similar python scripts to the batch_scripts folder, but formatted as menu bar items so that they can be used normally in the GUI. These scripts should be installed in the same location as the Full_UW_Dialog script:

Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts

Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts

That's all for now! I hope these scripts are useful to your coral photogrammetry projects!

-Will


