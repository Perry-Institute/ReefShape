# Reefshape: A System for the Efficient Collection and Automated Processing of Time-Series Underwater Photogrammetry Data for Benthic Habitat Monitoring

<p align="center"> 
  <img src="https://www.dropbox.com/scl/fi/6r2nx382dr8nzp1sjswb6/Reefshape.png?rlkey=nwfnmovaa2g2rihrmiko9jkan&raw=1" alt="ReefShape Logo" width="400"/> 
</p>

<b>NEW (JUNE 2025): ✨Introducing ReefShape v1.2!✨ The new version contains a number of improvements to functionality and some bug fixes. It also comes with installer files for Windows and Mac to make it easier to get ReefShape up and running. [See the full changelog here.](CHANGELOG.md)</b> 

ReefShape is a methodology for underwater photogrammetry or Large-Area Imaging developed specifically for coral reef monitoring. This GitHub repository acts as a landing page for ReefShape, and includes instructional materials on the methodology, sample files, and processing scripts. The key feature or ReefShape is <b>robust, automated photogrammetry data processing</b> using our custom pipeline that's driven by python scripts for the efficient processing of georeferenced, time-series coral reef photomosaics using Agisoft Metashape Professional. ReefShape was created by Will Greene and Sam Marshall at the <a href="https://www.perryinstitute.org/">Perry Institute for Marine Science</a>. 

### ReefShape Publication
The ReefShape method has been peer-reviewed and is published open-access in the Journal of Visualized Experiments. Check out the publication <a href="https://dx.doi.org/10.3791/67343">here</a>. The article includes a comprehensive protocol section with all instructions needed to make use of ReefShape. Alongside it is a 14 minute video explaining the protocol. Reading the JoVE article is the best place to start to understand what ReefShape is and how to use it.

Additionally, we have put together a white paper designed to more fully explain the entire process, from data collection through data analysis. Please find the <a href="https://docs.google.com/document/d/e/2PACX-1vQRazKwhYxGvmL2b0kGN1Nq9on1qL7MEkPXuC5SPvZqiAmmCvN_ShBfYB7Rc4fBbpEiHhQ5WzZkLUA5/pub">ReefShape white paper</a> here. 

### ReefShape Webinar
[![Webinar Thumbnail](https://www.dropbox.com/scl/fi/fqazzf3t70hdx24sebdec/ReefShape-Webinar-Thumbnail.jpg?rlkey=1fx81abbdsc11mocxg7bu7438&raw=1)](http://www.youtube.com/watch?v=N4yzl1FFQcE "ReefShape Webinar")

## Intro
At its core, ReefShape is a method for the setup and collection of underwater photogrammetry data that when done properly, allows for full automation of the processing and time-series alignment pipelines when using our custom scripts. Looking to get started using the method? You're in the right place - all the information you need for equipment setup, data collection, and processing is embedded or linked from this page. Our collection of python scripts are designed to <b>dramatically expedite processing</b> of underwater photogrammetry data in Agisoft Metashape Professional v2.0 and above. Specifically, they allow for the automation of the entire photogrammetry process, provided that coded Metashape targets are used for corner markers and scalebars, and that GPS locations and depths of the corner markers are collected when a plot is first established, as is outlined in the ReefShape data collection protocol. 

The scripts facilitate automatic time-series alignment of plots where permanent corner markers are installed. Automatic data export is also included for analysis in standard GIS software or <a href="https://github.com/cnr-isti-vclab/TagLab">TagLab</a>. A publication describing the ReefShape method will be made available in the near future. The remainder of this readme file gives an overview of the scripts and their use, as well as information on our corner marker system and using the ReefShape GPS data collection survey. 

<img src="https://www.dropbox.com/scl/fi/uu7oh76lzm5itfyoe3nmc/SI-4_GeoRef_Example.png?rlkey=iqyvft4j68p8d85zegtk17t0m&raw=1" alt="Metashape Screenshot" width="1000"/>
<i> Screenshot of a reef plot processed with ReefShape. The reference panel at the left shows the inclusion of real-world georeferencing, depth, and scaling information. </i> <br>
<br>

### Why use ReefShape?
There are a lot of great underwater photogrammetry protocols out there, and if you look at them with a careful eye, you will realize there are far more similarities between methods than there are differences. Most recommend collecting imagery in a similar way, and yield similar results. ReefShape differs from other published protocols by making a few tweaks to plot setup and metadata collection in the field that allow the automation of the photogrammetry processing component through our custom scripts that enhance efficiency, data quality, ease of use, and general workflow optimization. Here are the key things that ReefShape does:

First, we make use of a <b>simple referencing system</b> that provides the photogrammetry software (Agisoft Metashape) with all the information it needs to properly scale, orient, and locate the models with real world coordinates. Detectable corner markers and scale bars coupled with our free Survey123 form facilitates the easy collection and integration of this data - no more bubble levels or compass bearings underwater, manually placing control points, setting scale and orienting the model on the computer, or external storage of GPS coordinates to relocate the plot. Second, our <b> automated processing scripts </b> make photogrammetry processing simple, repeatable, and quick: you input all the data needed for processing in a graphic interface (takes about 1 minute), then click OK to run the entire process start to finish, resulting in finished and exported data products. Importantly, all this happens on your local computer, giving you full control of your data and the ability to adjust and fix any errors. No more long upload times or black-box, pay-per-use processing! The automated process is optimized to be as efficient as possible, meaning that you can run it in a matter of hours on most reasonably powerful consumer computers with a dedicated graphics processor. Third, our use of permanent corner markers facilitates <b>automatic time-series alignment</b> of datasets, improving accuracy and saving lots of time. Finally, our scripts create <b>analysis-ready data exports</b> for you that are ready to go and properly formatted for general GIS software (QGIS, ArcGIS Pro, etc) and <a href="https://github.com/cnr-isti-vclab/TagLab">TagLab</a> for rapid coral colony segmentation and time-series change analysis. Overall, our goal with ReefShape is to make underwater photogrammetry faster and easier, letting researchers focus on what actually matters, which is collecting relevant ecological data from the imagery products, rather than spending time and effort on the photogrammetry processing itself. 

### Corner Markers
The ReefShape workflow relies on auto-detectable corner markers being either permenently installed (for time-series monitoring of a plot) or temporarily placed. We have developed two kinds of custom markers for this purpose: simple flexible molded PVC pucks, and experimental anti-fouling markers that resist algal growth. Both types can be nailed, epoxied, or cemented to the substrate for installation. For more information on these corner markers or to purchase sets, contact me at wgreene@perryinstitute.org. 

<p align="center"> 
  <img src="https://www.dropbox.com/scl/fi/7w4gp8xrdimjvxeoyzbol/Markers.png?rlkey=rhzm5iduonyqw2mp039w82hhn&raw=1" alt="Corner Markers" width="400"/>
</p>
<i> Set of four permanent corner tags utilizing Metashape coded targets. These markers are designed to be nailed to the substrate, demarcating the boundaries of a plot upon setup. Automatic time-series alignment of plots relies on these "ground control points", and subsequent timepoints require only marker cleaning and image acquisition, saving significant time underwater. </i> <br>
<br>

These scripts are under active development - we recommend checking periodically to download the latest version. If you are having trouble using any of the scripts, email me at wgreene@perryinstitute.org and I'll get back to you as soon as I can.

## ReefShape Software
This repo is set up with a few important resources (this readme, scalebar example, georeferencing example, & data collection cheat sheet) in the root folder, and a folder called ReefShape_scripts that contains the script collection. For installation, the repo is designed to be downloaded in its entirety with the Code --> Download ZIP button.

### Installation
First off, you need to have <b> Agisoft Metashape Professional Edition v2.0+ </b> installed on your computer. You CAN use ReefShape with the Demo version of Metashape Pro if you want to try before you purchase Metashape.

To install the ReefShape scripts, you first need to download this repo (click on the green Code button and choose Download ZIP) and unzip it into a location of your choice. There are then two files to help you install: on PC, double-click on `Install Reefshape (PC).bat` to install, or on Mac, double click on `Install ReefShape (Mac)`. Each of these is essentially just a function to copy the contents of the ReefShape_Scripts into the Metashape scripts directory. You can also totally do this manually if that feels more comfortable. When properly installed, a custom ReefShape menu bar item containing buttons for the scripts will appear automatically each time you start Metashape. To install manually, copy the contents of the ReefShape_Scripts folder (not the folder itself) into the following folder, which varies on Mac vs PC:

Windows: `C:\Users\[YOUR USERNAME]\AppData\Local\Agisoft\Metashape Pro\scripts`

Mac: `/Users/[YOUR USERNAME]/Library/Application Support/Agisoft/Metashape Pro/scripts`

NOTE: On both Mac and PC, the locations listed above are contained within folders that are hidden by default. You can locate them by manually typing the correct folder path in the path/address bar of Finder / File Explorer. Storing the scripts in other locations will not allow them to be added to Metashape automatically.

ONE MORE NOTE: On Mac, if you have Gatekeeper turned on, the installation app will be blocked. If you get this warning, it can easily be bypassed by going to System Preferences --> Privacy & Security, then scrolling to the bottom and clicking allow. 

It is also possible to run the scripts without full installation. The user can simply open Metashape and run any of the scripts by clicking on Tools --> Run Script... in Metashape, then selecting the desired script (described below). This will create a custom menu button called <b>ReefShape</b> in the Metashape GUI that has the script inside it. To run it, just click the newly created menu option. Note that the custom menu bar options will be lost upon restarting Metashape unless they are installed properly as described above.

### ReefShape Scripts 

Here's what all the scripts in the ReefShape_scripts folder do.

<b>`ui_components.py`</b> This file contains class definitions for user interface components used in the full workflow script (full_reefshape_workflow.py) and the align timepoints script (align_chunks.py). It cannot function as a standalone script, but in order for the other scripts to run they must be located in the same folder as this file. These components are in a separate file to improve code organization and make it easier for others to expand on these scripts.

<b>`01_full_reefshape_workflow.py`</b> This file is the main script that can run the entire ReefShape process start to finish. When you click on it, it will bring up a dialog box where you can add photos, name the project and chunk, input georeferencing and scaling information, and a few other basic options (if you have already set up your project by adding photos and naming it properly, you may ignore these parts of the dialog box). It has built-in checks to enable the script to be run from any point in the process, and it will not repeat any steps. This allows easy integration of manual processing or refinement at any stage in the process. If you need the script to redo any step in the process (such as model building, orthomosaic building, DEM building, etc.), simply delete the selected data product from the project and run the script again to redo that step. 

<img src="https://www.dropbox.com/scl/fi/x2ry4t0gf0z5pzd4awxyp/SI-2_Metashape-Reef-Script.png?rlkey=a6ehs0jxnaq47cb2l9pilzl0e&raw=1" alt="ReefShape Dialog Box" width="600"/>
<i> The dialog box for the full ReefShape workflow. It allows for all necessary information to be input at once for full automation of the photogrammetry process. </i> <br>
<br>


<img src="https://www.dropbox.com/scl/fi/fiiu6kzgp6liv1ysumicb/ReefShape-Workflow.png?rlkey=l8esgwdwthnh7wl0qeviqhb2p&raw=1" alt="ReefShape Process" width="800"/>
<i> Flow diagram of the process automated within the full ReefShape workflow script.  </i> <br>
<br>

<b>`02_align_chunks.py`</b> This script implements a dialog box used to align two timepoints of a photomosaic plot to one another, each of which is contained within a separate chunk of the same project. The script is meant to be used in conjunction with the underwater workflow implemented in full_reefshape_workflow.py. Once the user has collected subsequent sets of photos of a plot with permanent corner markers, this script can be run to align the subsequent sets to a previous timepoint. The data from the earlier time point must be already processed before this script is used. It functions by detecting markers in the "target chunk", creating a temporary reference file with the precise estimated locations of the corner markers from the "reference chunk", and importing them into the target chunk. After running this script, the user should verify the four corner markers were detected properly and that the reference information was imported successfully before running the full reefshape workflow to complete the photogrammetry process and generate data products. If the targets failed to be detected, the user can manually place markers (with the proper names, i.e. "target 1") and re-run the align chunks script to bring over the referencing information from the reference timepoint again.

<img src="https://www.dropbox.com/scl/fi/g5hswvnsz8ltvcrjl0izl/SI-3_Align-Timepoints-Script.png?rlkey=xjthfx7vfn3yw5o3vs145aw2a&raw=1" alt="Align Timepoints Dialog Box" width="500"/>
<i> The Align Timepoints dialog box, facilitating time-series alignment. </i> <br>
<br>

<b>`03_optimization_process.py`</b> This script employs a tie-point culling and camera calibration optimization process to improve alignment accuracy and reconstruction consistency. It first culls tie points with a reconstruction uncertainty statistic above 25, then culls remaining tie points with a projection accuracy statistic worse than 15. Finally, it optimizes the camera alignment with all parameters checked and "fit additional corrections" checked. In our experience, fitting all possible parameters helps to properly account for atypical lens distortions associated with dome ports and aspherical lens elements and improves geometric consistency between timepoints. While this process is integrated into the full workflow, it is sometimes useful to have this functionality as a standalone utility. 

<b>`04_scale_model.py`</b> This script prompts the user for a scalebar text file containing the lengths of scalebars that may be present in the currently selected chunk / timepoint. It then creates scalebars out of any scalebar marker pairs found in the chunk and inputs the correct lengths and measurement accuracies, then updates the referencing to reflect this information. While this process is integrated into the full workflow, it is sometimes useful to have this functionality as a standalone utility. 

<b>`05_create_boundary.py`</b> This script brings up a GUI box that allows the user to input a sequence of four markers in the currently selected chunk, and generates an outer boundary polygon by stringing the locations of the four markers together. This can be run to automatically define a ROI based on the locations of markers in your project (typically, the corner markers). 

<b>`06_copy_boundary.py`</b> This script brings up a GUI box that allows the user to select a source chunk and a target chunk. It functions by copying the outer boundary polygon from the source chunk into the target chunk. This is useful for copying a custom ROI between chunks to get aligned outputs for taglab.

<b>`07_calculate_area_ratio.py`</b> This script automates the process of calculating the 3D to 2D surface area ratio (a commonly-used rugosity metric) for the currently selected chunk / timepoint. It does this in full 3D (as opposed to 2.5D, as in most GIS workflows), thereby capturing a better representation of the reef that includes underhangs. It accomplishes this task by duplicating and clipping the 3D mesh by the polygon defined as the outer boundary (which is automated in the full workflow and create boundary scripts, but can be done manually as well), then calculating the 3D surface area of the clipped mesh and comparing it to the 2D planar area of the boundary to generate the ratio. The temporary clipped mesh is then deleted. A GUI box displays the calculated ratio, and it is printed in the console as well. For this script to function properly, a single outer boundary polygon and 3D mesh must be present in the chunk.

<b>`08_clean_project.py`</b> This script looks in the currently selected chunk / timepoint for unnecessary files for long-term storage (key points, depth maps, orthophotos), and deletes them. This dramatically reduces file sizes and is recommended to be run once the user is happy with the data products for a given timepoint. 

<i>Note: The scripts are prefixed with a number so that they appear in the proper order in the ReefShape menu bar dropdown. Over time, new scripts may be added, and thus the numbering on these scripts are subject to change.</i> 

### Other Files

These are the additional files in the Resources folder of this repo:

<b>`Scalebar Example.txt`</b> This file is an example text file that contains the scalebar lengths. A scalebar in this context consists of a "bar" (usually a piece of acrylic, generally ~580mm x 80mm x 3mm) with printed coded Metashape targets taped / glued to either end (see image below). The targets used for scalebars must all be unique, and when using this process, markers 1-4 are reserved for corner markers. For each scalebar, the user must measure the distance between the centerpoints of the targets, then add a line in a text file that corresponds to that scalebar. Each time this scalebar is detected in a project, its distance will be input into Metashape's reference panel. See text file for the correct formatting.

<img src="https://www.dropbox.com/scl/fi/g3oeph5ca859ie4kv7pc4/ScalebarImage.png?rlkey=b496gwbdrvssrrgcvgshk6wds&raw=1" alt="Scalebar Image" width="600"/>
<i> Example illustration of a scalebar </i> <br>
<br>

<b>`Georef Example.csv`</b> This CSV file shows the correct format for georeferencing information, matching what is exported by the ReefShape Survey123 survey designed to accompany this method (see below section for more details). We strongly recommend using the ReefShape survey to collect GPS data, however it is possible to use another collection method. If you choose to collect GPS data with a handheld receiver (GPS dive watch, Garmin eTrex, etc), you must download and format your own data for input into the ReefShape workflow script. Use this csv file as a template. In order for the script to run properly, you must specify how your georeferencing file is formatted in the ReefShape dialog box, i.e. which reference values (lat, long, depth, etc) are in which columns. The Full ReefShape Workflow script is set by default to match the formatting of this example file, where the label (target name) is column 1, latitude is column 2, longitude is column 3, depth is column 4, xy accuracy is column 5, and z accuracy is column 6. Since there is one header row, the import starts at row 2. Lat and long must be in decimal degrees, depth must be in negative meters, and the file must be saved as a simple CSV.

<b>`Data Collection Cheat Sheet.pdf`</b> This PDF has useful reminders for how to set up plots and carry out data collection correctly to work with these scripts. It is not a full explanation of anything; rather it is meant to be used as a quick reference in the field to make sure you aren't forgetting anything!


## Collecting location information for ReefShape

We have created a public Survey123 survey to facilitate easy GPS data collection to accompany this workflow. When a survey is submitted, it automatically emails pre-formatted location data to the user. The survey can be accessed by anybody for free, even without an ArcGIS Online account. The user must download the ESRI Survey123 app on their smartphone, then follow this <a href="https://arcg.is/1aOnKS0">link to the survey</a> (or scan the QR code below) and select the option to open the survey in the app. The survey is meant to be filled out in the field, using either your phone's location or the location of a bluetooth receiver such as a Garmin GLO2 or Bad Elf GPS. The user should place their smartphone in a waterproof pouch or case, and swim out over the locations of each of the four corner markers that define the plot. If GPS locations are collected using an alternative method (handheld GPS, RTK GNSS system, drone, etc), the survey can be filled in with the corresponding location data to aid in correctly formatting it for use in the ReefShape process. 

The survey contains a repeating section allowing for the input of a GPS point and depth corresponding to each marker. The first repeat corresponds by default to target 1; the user should locate that marker, hold the phone or bluetooth receiver directly above it, then press the crosshairs button to record the location, then repeat for all corners. Once the locations of the corner markers are recorded, the corresponding depths, which should be measured in meters with a dive watch or other instrument, can be input for each target. Once this process is complete, the survey can be submitted and an email will be sent to the user with the pre-formatted data, ready to be plugged into the full ReefShape workflow script. Remember: if you're using real-world coordinates, the coordinate system (CRS) in the Full ReefShape Workflow dialog box should be set to WGS84 + EGM96 (EGM96 is a global geoid model included in Metashape that approximates sea level as opposed to regular WGS84 that uses an ellipsoidal height).

<img src="https://www.dropbox.com/s/up21rqn1k8v4hr5/SI-1_Survey123.jpeg?raw=1" alt="ReefShape Survey123 Screenshot" width="600"/>
<i> Screenshot of the Survey123 ReefShape Interface. </i> <br>
<br>

![image](https://github.com/Perry-Institute/ReefShape/assets/117117153/c61a2638-63a9-44ef-8104-667496354bcf)

### What if I do not or cannot collect real-world location data?

Don't worry! We've created a parallel survey called ReefShape Local that helps the user create a georeference file for ReefShape that uses local coordinates instead of GPS coordinates. You can access the survey here: <a href="https://arcg.is/1fWWa01">ReefShape Local</a>. The way it works is simple: you just tell the survey which marker corresponds to which corner (northeast, southeast, southwest, northwest, or north, east, south, west, depending on how you lay out your plot), and input its depth, and the survey does the rest for you, creating and emailing you a reference file that allows for full automation using ReefShape. Remember: if you're using ReefShape Local and NOT using real-world location data, the coordinate system (CRS) in the Full ReefShape Workflow dialog box should be left in Local Coordinates (m).


That's all for now! I hope ReefShape helps save you time and energy on your coral photogrammetry projects and allows you to spend more time collecting important ecological data to help understand and protect our reefs.

-Will

## Citation
If you use ReefShape, please cite it:
```bibtex
@article{ReefShape,
	title = {Reefshape: A System for the Efficient Collection and Automated Processing of Time-Series Underwater Photogrammetry Data for Benthic Habitat Monitoring},
	author = {Greene, William O. and Marshall, Sam and Li, Jiwei and Dahlgren, Craig P.},
	journal = {Journal of Visualized Experiments},
	number = {220},
	year = {2025},
  pages = {67343},
	issn = {1940-087X},
	doi = {10.3791/67343}
}
