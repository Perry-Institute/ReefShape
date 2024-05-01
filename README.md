<p align="center"> 
  <img src="https://www.dropbox.com/scl/fi/6r2nx382dr8nzp1sjswb6/Reefshape.png?rlkey=nwfnmovaa2g2rihrmiko9jkan&raw=1" alt="ReefShape Logo" width="400"/> 
</p>

Agisoft Metashape scripts for underwater Large Area Imaging / photogrammetry, developed specifically for the efficient processing of georeferenced, time-series coral reef photomosaics by Will Greene and Sam Marshall at the <a href="https://www.perryinstitute.org/">Perry Institute for Marine Science</a>.

## Intro
This is a collection of scripts to expedite processing of underwater photogrammetry data in Agisoft Metashape Professional V2.0 and above. Specifically, they allow for the automation of the entire photogrammetry process, provided that coded Metashape targets are used for corner markers and scalebars, and that GPS locations and depths of the corner markers are collected when a plot is first established. 

The scripts facilitate automatic time-series alignment of plots where permanent corner markers are installed. Automatic data export is also included for analysis in standard GIS software or <a href="https://github.com/cnr-isti-vclab/TagLab">TagLab</a>. A publication and whitepaper describing the ReefShape method will be made available in the near future. This document is not intended to provide complete instructions for the ReefShape method, but gives an overview of the scripts and their use, as well as information on using the ReefShape GPS data collection survey. 

<img src="https://www.dropbox.com/scl/fi/uu7oh76lzm5itfyoe3nmc/SI-4_GeoRef_Example.png?rlkey=iqyvft4j68p8d85zegtk17t0m&raw=1" alt="Metashape Screenshot" width="1000"/>
<i> Screenshot of a reef plot processed with ReefShape. The reference panel at the left shows the inclusion of real-world georeferencing, depth, and scaling information. </i> <br>
<br>

<p align="center"> 
  <img src="https://www.dropbox.com/scl/fi/7w4gp8xrdimjvxeoyzbol/Markers.png?rlkey=rhzm5iduonyqw2mp039w82hhn&raw=1" alt="Corner Markers" width="400"/>
</p>
<i> Set of four permanent corner tags utilizing Metashape coded targets. These markers are designed to be nailed to the substrate, demarcating the boundaries of a plot upon setup. Automatic time-series alignment of plots relies on these "ground control points", and subsequent timepoints require only marker cleaning and image acquisition, saving significant time underwater. </i> <br>
<br>

These scripts are under active development - we recommend checking periodically to download the latest version. If you want to learn more or are having trouble using any of the scripts, email wgreene@perryinstitute.org and I'll get back to you as soon as I can.

### Basics
This repo is set up with a few important resources (this readme, scalebar example, georeferencing example, & data collection cheat sheet) in the root folder, and a folder called ReefShape_scripts that contains the script collection. It is designed to be downloaded in its entirety with the Code --> Download ZIP button.

### Installation
To install the ReefShape scripts, you just need to download this repo and unzip it into a location of your choice. The contents of the ReefShape_scripts folder should then be copied into the Metashape scripts directory. When properly installed, a custom ReefShape menu bar item containing all scripts will appear automatically each time you start Metashape. The location of the scripts folder varies on Mac vs PC:

Windows: C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts

Mac: /Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts

It is also possible to run the scripts without full installation. The user can simply open Metashape and run any of the scripts by clicking on Tools --> Run Script... in Metashape, then selecting the desired script (described below). This will create a custom menu button called <b>ReefShape</b> in the Metashape GUI that has the script inside it. To run it, just click the newly created menu option. Note that the custom menu bar options will be lost upon restarting Metashape unless they are installed properly as described above.

## ReefShape Scripts 

Here's what all the scripts in the ReefShape_scripts folder do.

<b>ui_components.py</b> This file contains class definitions for user interface components used in the full workflow script (full_reefshape_workflow.py) and the align timepoints script (align_chunks.py). It cannot function as a standalone script, but in order for the other scripts to run they must be located in the same folder as this file. These components are in a separate file to improve code organization and make it easier for others to expand on these scripts.

<b>01_full_reefshape_workflow.py</b> This file is the main script that can run the entire ReefShape process start to finish. When you click on it, it will bring up a dialog box where you can add photos, name the project and chunk, input georeferencing and scaling information, and a few other basic options (if you have already set up your project by adding photos and naming it properly, you may ignore these parts of the dialog box). It has built-in checks to enable the script to be run from any point in the process, and it will not repeat any steps. This allows easy integration of manual processing or refinement at any stage in the process. If you need the script to redo any step in the process (such as model building, orthomosaic building, DEM building, etc.), simply delete the selected data product from the project and run the script again to redo that step. 

<img src="https://www.dropbox.com/scl/fi/x2ry4t0gf0z5pzd4awxyp/SI-2_Metashape-Reef-Script.png?rlkey=a6ehs0jxnaq47cb2l9pilzl0e&raw=1" alt="ReefShape Dialog Box" width="600"/>
<i> The dialog box for the full ReefShape workflow. It allows for all necessary information to be input at once for full automation of the photogrammetry process. </i> <br>
<br>

<b>02_align_chunks.py</b> This script implements a dialog box used to align two timepoints of a photomosaic plot to one another, each of which is contained within a separate chunk of the same project. The script is meant to be used in conjunction with the underwater workflow implemented in full_reefshape_workflow.py. Once the user has collected subsequent sets of photos of a plot with permanent corner markers, this script can be run to align the subsequent sets to a previous timepoint. The data from the earlier time point must be already processed before this script is used. It functions by detecting markers in the "target chunk", creating a temporary reference file with the precise estimated locations of the corner markers from the "reference chunk", and importing them into the target chunk. After running this script, the user should verify the four corner markers were detected properly and that the reference information was imported successfully before running the full reefshape workflow to complete the photogrammetry process and generate data products. If the targets failed to be detected, the user can manually place markers (with the proper names, i.e. "target 1") and re-run the align chunks script to bring over the referencing information from the reference timepoint again.

<img src="https://www.dropbox.com/scl/fi/g5hswvnsz8ltvcrjl0izl/SI-3_Align-Timepoints-Script.png?rlkey=xjthfx7vfn3yw5o3vs145aw2a&raw=1" alt="Align Timepoints Dialog Box" width="600"/>
<i> The Align Timepoints dialog box, facilitating time-series alignment. </i> <br>
<br>

<b>03_optimization_process.py</b> This script employs a tie-point culling and camera calibration optimization process to improve alignment accuracy and reconstruction consistency. It first culls tie points with a reconstruction uncertainty statistic above 25, then culls remaining tie points with a projection accuracy statistic worse than 15. Finally, it optimizes the camera alignment with all parameters checked and "fit additional corrections" checked. In our experience, fitting all possible parameters helps to properly account for atypical lens distortions associated with dome ports and aspherical lens elements and improves geometric consistency between timepoints. While this process is integrated into the full workflow, it is sometimes useful to have this functionality as a standalone utility. 

<b>04_scale_model.py</b> This script prompts the user for a scalebar text file containing the lengths of scalebars that may be present in the currently selected chunk / timepoint. It then creates scalebars out of any scalebar marker pairs found in the chunk and inputs the correct lengths and measurement accuracies, then updates the referencing to reflect this information. While this process is integrated into the full workflow, it is sometimes useful to have this functionality as a standalone utility. 

<b>05_create_boundary.py</b> This script brings up a GUI box that allows the user to input a sequence of four markers in the currently selected chunk, and generates an outer boundary polygon by stringing the locations of the four markers together. This can be run to automatically define a ROI based on the locations of markers in your project (typically, the corner markers). 

<b>06_calculate_area_ratio.py</b> This script automates the process of calculating the 3D to 2D surface area ratio (a commonly-used rugosity metric) for the currently selected chunk / timepoint. It does this in full 3D (as opposed to 2.5D, as in most GIS workflows), thereby capturing a better representation of the reef that includes underhangs. It accomplishes this task by duplicating and clipping the 3D mesh by the polygon defined as the outer boundary (which is automated in the full workflow and create boundary scripts, but can be done manually as well), then calculating the 3D surface area of the clipped mesh and comparing it to the 2D planar area of the boundary to generate the ratio. The temporary clipped mesh is then deleted. A GUI box displays the calculated ratio, and it is printed in the console as well. For this script to function properly, a single outer boundary polygon and 3D mesh must be present in the chunk.

<b>07_clean_project.py</b> This script looks in the currently selected chunk / timepoint for unnecessary files for long-term storage (key points, depth maps, orthophotos), and deletes them. This dramatically reduces file sizes and is recommended to be run once the user is happy with the data products for a given timepoint. 

<i>Note: The scripts are prefixed with a number so that they appear in the proper order in the ReefShape menu bar dropdown. Over time, new scripts may be added, and thus the numbering on these scripts are subject to change.</i> 

### Other Files

These are the additional files in the root folder of this repo:

<b>Scalebar Example.txt</b> This file is an example text file that contains the scalebar lengths. A scalebar in this context consists of a "bar" (usually a piece of acrylic, generally ~580mm x 80mm x 3mm) with printed coded Metashape targets taped / glued to either end (see image below). The targets used for scalebars must all be unique, and when using this process, markers 1-4 are reserved for corner markers. For each scalebar, the user must measure the distance between the centerpoints of the targets, then add a line in a text file that corresponds to that scalebar. Each time this scalebar is detected in a project, its distance will be input into Metashape's reference panel. See text file for the correct formatting.

<img src="https://www.dropbox.com/scl/fi/g3oeph5ca859ie4kv7pc4/ScalebarImage.png?rlkey=b496gwbdrvssrrgcvgshk6wds&raw=1" alt="Scalebar Image" width="600"/>
<i> Example illustration of a scalebar </i> <br>
<br>

<b>Georef Example.csv</b> This CSV file shows the correct format for georeferencing information, matching what is exported by the ReefShape Survey123 survey designed to accompany this method (see below section for more details). We strongly recommend using the ReefShape survey to collect GPS data, however it is possible to use another collection method. If you choose to collect GPS data with a handheld receiver (GPS dive watch, Garmin eTrex, etc), you must download and format your own data for input into the ReefShape workflow script. Use this csv file as a template. In order for the script to run properly, you must specify how your georeferencing file is formatted in the ReefShape dialog box, i.e. which reference values (lat, long, depth, etc) are in which columns. The Full ReefShape Workflow script is set by default to match the formatting of this example file, where the label (target name) is column 1, latitude is column 2, longitude is column 3, depth is column 4, xy accuracy is column 5, and z accuracy is column 6. Since there is one header row, the import starts at row 2. Lat and long must be in decimal degrees, depth must be in negative meters, and the file must be saved as a simple CSV.

<b>Data Collection Cheat Sheet.pdf</b> This PDF has useful reminders for how to set up plots and carry out data collection correctly to work with these scripts. It is not a full explanation of anything; rather it is meant to be used as a quick reference in the field to make sure you aren't forgetting anything!


## Collecting GPS information with ReefShape Survey

We have created a public Survey123 survey to facilitate easy GPS data collection to accompany this workflow. The survey can be accessed by anybody for free, even without an ArcGIS Online account. The user must download the ESRI Survey123 app on their smartphone, then follow this <a href="https://arcg.is/1aOnKS0">link to the survey</a> (or scan the QR code below) and select the option to open the survey in the app. The survey is meant to be filled out in the field, using either your phone's location or the location of a bluetooth receiver such as a Garmin GLO2 or Bad Elf GPS. The user should place their smartphone in a waterproof pouch or case, and swim out over the locations of each of the four corner markers that define the plot. 

The survey contains a repeating section allowing for the input of a GPS point and depth corresponding to each marker. The first repeat corresponds by default to target 1; the user should locate that marker, hold the phone or bluetooth receiver directly above it, then press the crosshairs button to record the location, then repeat for all corners. Once the locations of the corner markers are recorded, the corresponding depths, which should be measured in meters with a dive watch or other instrument, can be input for each target. Once this process is complete, the survey can be submitted and an email will be sent to the user with the pre-formatted data, ready to be plugged into the full ReefShape workflow script.

<img src="https://www.dropbox.com/scl/fi/rcjf9186bmonxr8utr86q/SI-1_Survey123-Screenshot.png?rlkey=fbvna507z9ziwmce6cmjam7c4&raw=1" alt="ReefShape Survey123 Screenshot" width="600"/>
<i> Screenshot of the Survey123 ReefShape Interface. </i> <br>
<br>

![image](https://github.com/Perry-Institute/ReefShape/assets/117117153/c61a2638-63a9-44ef-8104-667496354bcf)


That's all for now! I hope ReefShape helps save you time and energy on your coral photogrammetry projects and allows you to spend more time collecting important ecological data to save our reefs.

-Will
