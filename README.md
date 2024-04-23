![ReefShape Logo](https://www.dropbox.com/scl/fi/ksbvoseoqtkntk2aazmxi/ReefshapeLogo.png?rlkey=hil3gxwrfn8npppy5vgfdmfhb&raw=1)
Agisoft Metashape scripts for underwater Large Area Imaging / photogrammetry, developed specifically for the efficient processing of coral reef photomosaics by Sam Marshall and Will Greene at the <a href="https://www.perryinstitute.org/">Perry Institute for Marine Science</a>.

## Intro
This is a collection of scripts to expedite processing of underwater photogrammetry / LAI data in Agisoft Metashape. These scripts are under active development - we recommend checking the periodically to download the latest version.  If you want to learn more or are having trouble using any of the scripts, email wgreene@perryinstitute.org and I'll get back to you as soon as I can.

## Basics
This repo is set up with a root folder that contains the resources you need to get the different scripts to work.

### Installation
To install, you just need to download this repo and unzip it into a location of your choice. You simply open Metashape and run any of the scripts by clicking on Tools --> Run Script... in Metashape, then select the python script you want (described below). This will create a custom menu button called <b>ReefShape</b> in the Metashape GUI that has the script inside it. To run it, you just click the newly created menu option.

That said, it's worth properly installing the scripts (full_reefshape_workflow.py, align_chunks.py, ui_components.py) by copying them into the Metashape scripts directory. When properly installed, the custom menu options will appear automatically each time you start Metashape. The location of this folder varies on Mac vs PC:

Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts

Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts

Here's what each script in the root folder does:

<b>full_reefshape_workflow.py</b> This file is the main script that can run the entire ReefShape process start to finish. When you click on it, it will bring up a dialog box where you can add photos, name the project and chunk, input georeferencing and scaling information, and a few other basic options (if you have already set up your project by adding photos and naming it properly, you may ignore these parts of the dialog box). It has built-in checks to enable the script to be run from any point in the process, and it will not repeat any steps. This allows easy integration of manual processing or refinement at any stage in the process. If you need the script to redo any step in the process (such as model building, orthomosaic building, DEM building, etc.), simply delete the selected data product from the project and run the script again to redo that step. 

<b>align_chunks.py</b> This file is a standalone script that implements a dialog box used to align two photomosaic plots to one another, each of which is contained within a separate chunk of the same project. The script is meant to be used in conjunction with the underwater workflow implemented in full_reefShape_workflow.py. Once the user has collected two sets of photos, this script can be used to make sure the two sets line up before processing the second data set. The data from the first time point must be already processed before this script is used.

<b>ui_components.py</b> This file contains class definitions for UI components used in the full workflow script (full_reefshape_workflow.py) and the align timepoints script (align_chunks.py). It cannot function as a standalone script, but in order for the other scripts to run they must be located in the same folder as this file. These components are in a separate file to improve code organization and make it easier for others to expand on these scripts.

There are also some other items inside the root folder.

<b>Scalebar Example.txt</b> This file is an example text file that contains the scale bar lengths. The idea is that you have a scalebar with metashape targets at either end. You measure the distance between the centerpoints of the targets, then add a line in a text file that corresponds to that scalebar. Each time this scalebar is detected in a project, its distance will be input into Metashape's reference panel. See text file for the correct formatting.

<b>Georef Example.csv</b> This CSV file shows a sample format for georeferencing information. This format is based off the way that ESRI Survey123 exports location data from our custom Metashape Reef survey. The survey can be accessed by anybody for free, even without an ArcGIS Online account. You simply need to download the ESRI Survey123 app on your smartphone, then follow this <a href="https://arcg.is/1aOnKS0">link to the survey</a> and select the option to open the survey in the app. The survey is meant to be filled out in the field, using either your phone's location or the location of a bluetooth receiver such as a Garmin GLO2 or Bad Elf GPS. Once the locations of the corner markers and their depths have been entered into the survey, you simply hit submit, and an email will be sent to you with the pre-formatted data, ready to be plugged into the script. If you choose to collect GPS data with a handheld receiver (GPS dive watch, Garmin eTrex, etc), you must download and format your own data. In order for the script to run properly, you must specify how your georeferencing file is formatted in the main dialog box, i.e. which reference values (lat, long, depth, etc) are in which columns. For the example file above, the label (target x) is column 1, latitude is column 2, longitude is column 3, depth is column 4, xy accuracy is column 5, z accuracy is column 6, and since there is one header row the import starts at row 2. Lat and long must be in decimal degrees, depth must be in negative meters, and the file must be saved as a simple CSV.

<b>Data Collection Cheat Sheet.pdf</b> This PDF has useful reminders for how to carry out data collection correctly to work with these scripts. It is not a full explanation of anything; rather it is meant to be used as a quick reference in the field to make sure you aren't forgetting anything!

There are also three other folders in the repo:

<b> batch_processing </b> This contains Metashape .xml files that can be loaded into the batch processing dialog in Metashape to carry out various tasks. They are probably not useful to you, the user.

<b> batch_scripts </b> This contains a set of python scripts that can execute specific functions useful to be included in a batch processing dialog. They can be installed anywhere that you can easily find them, and in a batch process, you can use the run script... command as a batch function, and then select the file for the desired action (E.g. OptimizationProcess script is useful to run after running Align Photos...).

<b> component_scripts </b> This folder has similar python scripts to the batch_scripts folder, but formatted as menu bar items so that they can be used normally in the GUI. These scripts can be installed in the same location described for the main workflow python scripts if you wish.

That's all for now! I hope these scripts are useful to your coral photogrammetry projects!

-Will
