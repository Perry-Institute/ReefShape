# ReefShape: A System for the Efficient Collection and Automated Processing of Time-Series Underwater Photogrammetry Data for Benthic Habitat Monitoring

<p align="center">
  <img src="https://www.dropbox.com/scl/fi/6r2nx382dr8nzp1sjswb6/Reefshape.png?rlkey=nwfnmovaa2g2rihrmiko9jkan&raw=1" alt="ReefShape Logo" width="400"/>
</p>

ReefShape is a method for collecting and processing underwater photogrammetry (Large-Area Imaging) data, developed for coral reef monitoring at the <a href="https://www.perryinstitute.org/">Perry Institute for Marine Science</a> by Will Greene and Sam Marshall. Detectable corner markers and scalebars are placed on a plot when it is photographed; this repository holds the software that turns those photos into scaled, georeferenced, time-series-aligned data products in <b>Agisoft Metashape Professional</b>, with almost no manual work:

* **A set of Metashape scripts** (a **ReefShape** menu) that runs the whole pipeline from one dialog: align, scale, georeference, mesh, orthomosaic, DEM, boundary and exports, plus tools for time-series alignment, rugosity and housekeeping.
* **ReefShape Batch**, a separate program that queues many plots and processes them unattended, several at a time.
* **Field resources**: a data collection cheat sheet and example scalebar and georeferencing files.

> **What's new.** ReefShape v1.3 added Metashape 2.3 support. Since then: the **ReefShape Batch** processor, a shared processing core, boundaries from the convex hull of the corner markers, TagLab project files and a **legacy TagLab export**, smarter DEM/orthomosaic resolution handling, protection against **scalebar targets that move during a dive**, resumable exports, and a broad reliability pass over every dialog and script. See the [full changelog](CHANGELOG.md).

**Contents**
[Publication and video](#publication-and-video) ·
[Quick start](#quick-start) ·
[Installation](#installation) ·
[Processing one plot](#processing-one-plot) ·
[Repeat visits (time series)](#repeat-visits-time-series) ·
[ReefShape Batch](#reefshape-batch) ·
[Processing choices](#processing-choices-worth-knowing) ·
[Menu reference](#menu-reference) ·
[Input files](#input-files) ·
[Field method](#field-method) ·
[Troubleshooting](#troubleshooting) ·
[Citation](#citation)

---

## Publication and video

The ReefShape method is peer-reviewed and published open-access in the Journal of Visualized Experiments (<a href="https://dx.doi.org/10.3791/67343">article</a>). It includes a full protocol and a 14-minute video, and is the best place to start. A longer <a href="https://docs.google.com/document/d/e/2PACX-1vQRazKwhYxGvmL2b0kGN1Nq9on1qL7MEkPXuC5SPvZqiAmmCvN_ShBfYB7Rc4fBbpEiHhQ5WzZkLUA5/pub">white paper</a> covers the whole process from data collection to analysis.

[![Webinar Thumbnail](https://www.dropbox.com/scl/fi/fqazzf3t70hdx24sebdec/ReefShape-Webinar-Thumbnail.jpg?rlkey=1fx81abbdsc11mocxg7bu7438&raw=1)](http://www.youtube.com/watch?v=N4yzl1FFQcE "ReefShape Webinar")

## Why ReefShape

Most underwater photogrammetry protocols collect imagery in a similar way and give similar results. ReefShape differs in two small field tweaks that make the processing fully automatic:

1. **A simple referencing system.** Auto-detectable corner markers and scalebars, plus a GPS point and depth for each corner (collected with the free Survey123 form), give Metashape everything it needs to scale, orient and locate a model in real-world coordinates. No bubble levels, compass bearings, or manual control points.
2. **Permanent corner markers** let later visits be aligned to the first one automatically, so a repeat visit needs only marker cleaning and photography.

On top of that the scripts provide **one-dialog processing** (about a minute of setup, then hours of unattended work on an ordinary GPU workstation), **analysis-ready exports** for GIS software and <a href="https://github.com/cnr-isti-vclab/TagLab">TagLab</a>, and everything runs **on your own computer**: no uploads, no pay-per-use processing, and every step stays available for you to inspect and correct.

<img src="https://www.dropbox.com/scl/fi/uu7oh76lzm5itfyoe3nmc/SI-4_GeoRef_Example.png?rlkey=iqyvft4j68p8d85zegtk17t0m&raw=1" alt="Metashape Screenshot" width="1000"/>
<i>A reef plot processed with ReefShape. The reference panel at the left shows the real-world georeferencing, depth and scale information.</i>

## Quick start

1. **Install** Metashape Professional 2.0 or newer, then run `Install ReefShape (PC).bat` or `Install ReefShape (Mac).command` ([details](#installation)). Restart Metashape.
2. **Prepare two small files**: a [scalebar list](#scalebar-file) (once per survey, or build it with *Tools > Build Scalebar File*) and a [georeferencing file](#georeferencing-file) for the plot.
3. **One plot:** open Metashape, choose **ReefShape > Full ReefShape Workflow**, add your photos, point it at the two files, and click **Ok**.
   **Many plots:** open **ReefShape Batch**, use *Scan folder* to build a queue, and click **Run batch**.
4. **Repeat visit:** use **ReefShape > Align Timepoints** (or the batch's re-photography job) so the new visit lines up with the first, then run the full workflow on it.

## Installation

You need **Agisoft Metashape Professional 2.0 or newer** (the demo version works for trying it out). ReefShape works on Metashape 2.0 through 2.3 from a single codebase.

### The Metashape scripts

Download this repository (green **Code** button, then **Download ZIP**) and unzip it somewhere. Then:

* **Windows:** double-click `Install ReefShape (PC).bat`.
* **Mac:** double-click `Install ReefShape (Mac).command`. The first time, Gatekeeper may block it because it was downloaded from the internet: right-click the file, choose **Open**, then **Open** again. If macOS still refuses, use **System Settings > Privacy & Security > Open Anyway**.

The installer copies the contents of `ReefShape_Scripts` into Metashape's scripts folder, checks that the copy worked, and only then removes files it installed earlier (it keeps a list, so it never touches other scripts you have there). Restart Metashape and a **ReefShape** menu appears.

To install by hand, copy the *contents* of `ReefShape_Scripts` (not the folder itself, and including the `reefshape_modules` folder) into:

* Windows: `C:\Users\[you]\AppData\Local\Agisoft\Metashape Pro\scripts`
* Mac: `/Users/[you]/Library/Application Support/Agisoft/Metashape Pro/scripts`

Both locations are hidden by default; type the path into File Explorer or Finder. You can also try a script without installing it via **Tools > Run Script...**, but its menu entry then lasts only until you restart Metashape.

**Dependencies.** A few tools need extra Python packages (ICP alignment: open3d, scipy, numpy; boundary from photos: numpy, shapely; rugosity tools: numpy, rasterio). They are installed automatically, once, the first time a tool needs them, after asking you. No administrator rights are needed, and installs survive Metashape updates.

### ReefShape Batch

Nothing to install: run `ReefShape_Batch/ReefShape Batch (PC).bat` or `ReefShape Batch (Mac).command`. It uses the Python that ships inside Metashape (on a Mac it finds a Python that can draw windows and tells you if none is available). Metashape Professional must be installed and licensed on the same computer. If it is in an unusual location the app asks you to locate it, or set the `REEFSHAPE_METASHAPE` environment variable. Running the launcher with `--check` prints what it found, which is the first thing to try if it will not start.

## Processing one plot

Choose **ReefShape > Full ReefShape Workflow**. The dialog is divided into collapsible panels, and any panel whose work is already done in the chunk collapses itself and is marked *Complete*.

<img src="https://www.dropbox.com/scl/fi/x2ry4t0gf0z5pzd4awxyp/SI-2_Metashape-Reef-Script.png?rlkey=a6ehs0jxnaq47cb2l9pilzl0e&raw=1" alt="ReefShape Dialog Box" width="600"/>
<i>The full workflow dialog. Everything needed for the whole pipeline is entered here at once.</i>

| Panel | What you set |
|---|---|
| **Project Setup** | Create or name the project, add the photo folder, name the chunk (the capture date, `YYYYMMDD`, is filled in for you). Skip it if the project already exists. |
| **General** | Generic preselection, mesh quality, optional vertex colors, orthomosaic resolution (a custom value or Metashape's default), and where the plot boundary comes from. |
| **Georeferencing** | Coordinate system, marker target type, the scalebar file, the georeferencing file and its column layout. Choose *No* if the chunk is already referenced or you will reference it by hand. |
| **Export** | Output folder, processing report, GIS outputs, TagLab outputs, and the legacy TagLab outputs. |

### What the workflow does

<img src="https://www.dropbox.com/scl/fi/fiiu6kzgp6liv1ysumicb/ReefShape-Workflow.png?rlkey=l8esgwdwthnh7wl0qeviqhb2p&raw=1" alt="ReefShape Process" width="800"/>

1. **Align** the photos (two passes, since the second often picks up photos the first missed). Markers are switched off during alignment so a target that shifted between visits cannot distort it, and restored afterwards.
2. **Detect markers, import their coordinates, and build the scalebars** from your two files.
3. **Optimize** the cameras: remove high-error tie points, then fit all lens parameters, which suits dome ports and aspherical lenses.
4. **Check the scalebar targets for movement** and remove the photos in which a target appears to have shifted ([details](#scalebar-targets-that-move)).
5. **Build depth maps and the mesh.**
6. **Build the orthomosaic and DEM** ([resolution rules](#resolution)).
7. **Create the plot boundary** ([options](#plot-boundary)).
8. **Export** the report, GIS products, boundary shapefile and TagLab products.
9. **Clean the project** of data not needed for long-term storage and save.

### Stopping, resuming and redoing steps

Every stage is skipped if its product already exists and the project is saved after each one, so you can run the workflow from any point, interrupt it, or do a step by hand and carry on. To redo a stage, delete its product in Metashape (the mesh, orthomosaic, DEM, an export file...) and run the workflow again.

The workflow also **stops early, cleanly, in two situations** where continuing would waste hours or fail:

* With no georeferencing and no markers it stops after the mesh so you can reference, level and scale the model by hand, then re-run to finish.
* If the chunk has **no scale** (for instance the scalebar targets were not detected), or the scale implies an absurd plot size, it stops before building the DEM and explains what to check. Alignment and the mesh are kept.

Problems that can be known up front are reported before any processing starts: an unsaved project, no photos, or georeferencing turned on without its files.

## Repeat visits (time series)

Plots with **permanent corner markers** are aligned to an earlier visit through those markers.

1. Create a second chunk in the same project and add the new photos.
2. Run **ReefShape > Align Timepoints**. Choose the earlier chunk as the reference and the new chunk as the active one. If any marker moved between visits (a corner tag knocked loose, say), add it under *Damaged markers*: it is kept but trusted far less. The script detects the markers in the new chunk, imports the reference chunk's precise estimated marker positions, weights them, recomputes the transform, and **copies the plot boundary across** so both timepoints cover exactly the same ground.
3. Check that the markers were detected and the reference information imported, then run the **Full ReefShape Workflow** on the new chunk.

<img src="https://www.dropbox.com/scl/fi/g5hswvnsz8ltvcrjl0izl/SI-3_Align-Timepoints-Script.png?rlkey=xjthfx7vfn3yw5o3vs145aw2a&raw=1" alt="Align Timepoints Dialog Box" width="500"/>

If a target was not detected, place the marker by hand with the right name (for example `target 1`) and run Align Timepoints again.

**No permanent markers?** Use **ReefShape > Tools > Align Timepoints (ICP)**. It matches the two surveys by their surfaces (Iterative Closest Point) with no shared markers needed. For best results pre-align the chunks roughly. In ReefShape Batch, ICP is an option on a re-photography job: the new timepoint is first georeferenced and scaled from its own temporary targets, then matched to the reference chunk after its mesh is built, and the fit quality is recorded with a warning if it looks poor.

## ReefShape Batch

`ReefShape_Batch` processes many plots in one queue without you sitting in front of Metashape. It drives Metashape in the background, one separate process per job.

* **Two kinds of job.** A *new plot* creates a project from the photos. A *re-photography* job opens an existing project, adds the new photos as a new chunk, and aligns it to a reference chunk (by shared markers, or ICP), choosing from lists read from the project. You see how the earlier timepoint was processed (mesh quality, orthomosaic resolution) so you can match it.
* **Build the queue quickly.** *Scan folder* creates one job per photo folder under a season's directory (dated `YYYYMMDD` folders name the chunk). *Duplicate* copies a job, and **settings templates** apply one set of processing, export and georeferencing settings to any or all jobs. A job keeps its own georeferencing file, and a re-photography job keeps its own alignment choices.
* **Run it.** Choose how many jobs run at once (more than two compete for the GPU) and whether to stop on the first failure. A crashing job fails only itself, and jobs on the same project run one after another. Progress, a live log and warnings are shown per job. A full log is also written beside each project as `<project>_<chunk>_batch.log`.
* **Pick up where it stopped.** Failed, cancelled or interrupted jobs resume from their last completed stage when you run again, and jobs that were running when the app closed unexpectedly come back as waiting. A job whose referencing did not work stops after the mesh as **Needs referencing** without failing the rest of the batch; add the referencing and run again to finish it.
* **Batches are files.** Save a queue as a `.rsbatch` file to keep a record of what was processed, or to prepare it on a laptop and run it on the workstation.
* **No questions mid-run.** The two questions the interactive workflow can ask (replace a leftover high-resolution DEM, and continue with a photo-coverage boundary) are answered with their OK option in a batch: the DEM is resampled, and a photo-coverage fallback is recorded as a warning on the job.

## Processing choices worth knowing

### Resolution

* **Custom orthomosaic resolution (the default is 0.5 mm).** The DEM is built at that resolution so the orthomosaic is not resampled from a coarser surface, the orthomosaic is built over it, and the stored DEM is then rebuilt at **four times the orthomosaic's cell size** and the full-resolution DEM is deleted (a 0.5 mm orthomosaic keeps a 2 mm DEM). If a project already holds a finer DEM than that, you are asked before it is replaced.
* **Default resolution.** Metashape picks both, from different things: the orthomosaic's from the flight height and image resolution, and the DEM's from the density of the mesh. The orthomosaic is built straight from the mesh, then the DEM, so each gets its own default. This is slower than building the orthomosaic over a DEM, but needs no resolution to be guessed.

### Plot boundary

By default the boundary is the **convex hull of every georeferenced marker**, which needs no particular marker numbering or order, ignores a control point inside the plot, and works for plots with more than four corners. Alternatives:

* **Photo coverage:** follows the area actually photographed, for plots with no corner markers. It shifts from visit to visit.
* **None:** no boundary shapefile, and the TagLab outputs are skipped (they must be clipped to the plot).
* A boundary **already in the chunk** (for example copied across by Align Timepoints) is always kept, and the dialog tells you so.

*Tools > Create Boundary from Markers* is for a custom outline: pick markers from a list shown on a plan view, put them in order, and it warns if the outline crosses itself.

### TagLab outputs

* **TagLab outputs** are for the TagLab version that accepts large, JPEG-compressed images: the orthomosaic and DEM clipped to the boundary as single files, plus a TagLab project file (named from the chunk, with the capture date and the orthomosaic's resolution filled in).
* **TagLab outputs (legacy)** are for the standard, unmodified TagLab: LZW-compressed, split into blocks of at most 32767 px, with the DEM resampled to the orthomosaic's grid so the two line up, written to a `taglab_outputs_legacy` folder. They are much larger. If a legacy export has to fall back to photo coverage because no marker boundary exists, the dialog asks whether to continue, since a boundary that differs between visits will not line up in TagLab. Cancel, fix the boundary (check the orthomosaic against the earlier visit, then use *Copy Boundary*), and run again.

### Scalebar targets that move

A scalebar target nudged part-way through a dive is seen in two places, which bends the scale. Before the mesh is built the workflow removes the photos in which a scalebar target's pixel error is above 10 px, so the target is located from the photos where it stayed put. A bar is kept only if each end still has at least three good photos and at least three photos show both ends together. Otherwise its accuracy is loosened to 10 mm so the scale follows the other bars (a bar is never removed outright, so there is always some scale). If no bar can be cleaned reliably you are warned and the scale is left as it was. A bar that still measures more than 0.5% off its nominal length is also reported.

### Reliability

Exports are written under a temporary name and renamed when finished, so a run that is killed part-way cannot leave a half-written file that a later run would take for a finished one. File names are made safe if a chunk label contains characters such as `/` or `:`.

## Menu reference

All scripts install into the **ReefShape** menu in Metashape. The numeric prefixes only keep them in order.

### Main workflow

| Menu item | Script | What it does |
|---|---|---|
| **Full ReefShape Workflow** | `01_full_reefshape_workflow.py` | The whole pipeline from one dialog; see [Processing one plot](#processing-one-plot). |
| **Align Timepoints** | `02_align_chunks.py` | Aligns a new chunk to an earlier one through shared corner markers, and copies the boundary across. |

### Tools menu

| Menu item | Script | What it does |
|---|---|---|
| **Align Timepoints (ICP)** | `03_align_chunks_ICP.py` | Registers one chunk onto another by surface matching (tie points or mesh vertices, with optional crop-to-overlap and generalized ICP), for plots without shared markers. Adapted from Agisoft's `align_model_to_model.py`. |
| **Optimize Cameras and Model** | `04_optimization_process.py` | Removes tie points with reconstruction uncertainty above 25 and projection accuracy above 15, then fits all camera parameters. Already part of the full workflow; useful stand-alone. |
| **Create Scalebars from Targets** | `05_scale_model.py` | Reads a scalebar file and creates the scalebars for the marker pairs found in the chunk. Tolerates a header row and targets missing from the chunk. |
| **Build Scalebar File** | `06_build_scalebar_file.py` | Builds or extends a scalebar file. Enter lengths in centimetres and accuracies in millimetres; it writes the metre units the workflow expects, and can set the file as the workflow's default. |
| **Create Boundary from Markers** | `07_create_boundary.py` | Builds a boundary from markers you pick and order, shown on a plan view. Asks before replacing an existing boundary. |
| **Create Boundary from Photos** | `08_create_boundary_from_photos.py` | Builds a boundary from the aligned camera positions and the photo footprint (estimated from camera height and field of view, adjustable). Holes are filled and stray cameras ignored. |
| **Copy Boundary** | `09_copy_boundary.py` | Copies the outer boundary from one chunk to another, asking before replacing one. Keeps outputs pixel-aligned for TagLab. |
| **Calculate Surface Area Ratio** | `10_calculate_area_ratio.py` | The 3D to 2D surface area ratio (a rugosity measure) inside the outer boundary, in full 3D so underhangs count. Needs a mesh and an outer boundary. |
| **Gridded Rugosity** | `11_gridded_rugosity.py` | Rugosity on a grid (0.25 m to 5 m) inside the boundary, added to the chunk as a labeled DEM and optionally saved as a GeoTIFF. Fast: it measures each triangle once. |
| **Gridded Rugosity (Exact)** | `12_gridded_rugosity_clip.py` | The same product, but each cell is measured by clipping a copy of the mesh to it, which matches a manual clip-and-measure check exactly. Slower (minutes to tens of minutes). |
| **Compare Rugosity** | `13_compare_rugosity.py` | The fractional change in rugosity between two timepoints, cell by cell. Both chunks need rugosity rasters on the same grid (same cell size and boundary). |
| **Export Compressed DEM** | `14_export_compressed_dem.py` | Metashape's Export DEM dialog with LZW compression switched on, for far smaller files. |
| **Clean Project** | `15_clean_project.py` | Deletes key points, depth maps and orthophotos from the current chunk to shrink the project for long-term storage. Run it once you are happy with the products. |

`ui_components.py` and the `reefshape_modules` folder are shared code, not tools. They must stay in the same folder as the scripts. `reefshape_modules` holds the processing core used by both the menu scripts and ReefShape Batch, so the two cannot drift apart.

## Input files

Example files are in the `Resources` folder.

### Scalebar file

A scalebar is a bar (usually acrylic, about 580 x 80 x 3 mm) with a coded Metashape target at each end. Measure the distance between target centres and add a line per bar to a text file:

```
target 19,target 20,0.49650,0.00025
```

That is the two marker labels, the distance in metres, and the accuracy in metres. Each bar's targets must be unique. One master list serves every plot in a survey: bars that are not found in a given plot are skipped. See `Scalebar Example.txt`, or use **Tools > Build Scalebar File**.

<img src="https://www.dropbox.com/scl/fi/g3oeph5ca859ie4kv7pc4/ScalebarImage.png?rlkey=b496gwbdrvssrrgcvgshk6wds&raw=1" alt="Scalebar Image" width="600"/>
<i>A scalebar.</i>

### Georeferencing file

A simple CSV giving each corner marker's location. The format matches the ReefShape Survey123 export (`Georef Example.csv`). By default the dialog expects label in column 1, latitude in column 2, longitude in column 3, depth in column 4, horizontal accuracy in column 5 and vertical accuracy in column 6, with one header row so the import starts at row 2. Latitude and longitude are decimal degrees, and depth is in **negative metres**. If your file is laid out differently, set the column numbers in the dialog's Column Formatting box (or in the batch job's georeferencing section). Save it as a plain CSV.

### Data collection cheat sheet

`Data Collection Cheat Sheet.pdf` is a field reminder for plot setup and data collection. It is a checklist, not a full explanation.

## Field method

### Corner markers

ReefShape needs auto-detectable corner markers, permanently installed for time-series monitoring or placed temporarily. We make two kinds: flexible molded PVC pucks, and experimental anti-fouling markers that resist algal growth. Both can be nailed, epoxied or cemented to the substrate. To ask about them or to buy sets, email wgreene@perryinstitute.org.

<p align="center">
  <img src="https://www.dropbox.com/scl/fi/7w4gp8xrdimjvxeoyzbol/Markers.png?rlkey=rhzm5iduonyqw2mp039w82hhn&raw=1" alt="Corner Markers" width="400"/>
</p>
<i>A set of four permanent corner tags using Metashape coded targets, designed to be nailed to the substrate. Time-series alignment depends on them as ground control points, so later visits need only marker cleaning and photography.</i>

### Collecting location information

A public Survey123 survey makes GPS collection easy and emails you pre-formatted location data. Anyone can use it free, with no ArcGIS Online account. Install the ESRI Survey123 app on a smartphone, open the <a href="https://arcg.is/1aOnKS0">survey link</a> (or scan the QR code below) in the app, and fill it in on the plot, using the phone's location or a Bluetooth receiver such as a Garmin GLO2 or Bad Elf. Keep the phone in a waterproof pouch and swim over each corner marker. The repeating section takes a GPS point for each marker (the first repeat is target 1 by default) and then each marker's depth in metres from a dive watch or similar. On submission you are emailed the file, ready to use in the workflow. Other collection methods (handheld GPS, RTK GNSS, a drone) work too: enter their locations in the survey so the file is formatted correctly.

<img src="https://www.dropbox.com/s/up21rqn1k8v4hr5/SI-1_Survey123.jpeg?raw=1" alt="ReefShape Survey123 Screenshot" width="600"/>
<i>The ReefShape Survey123 form.</i>

![image](https://github.com/Perry-Institute/ReefShape/assets/117117153/c61a2638-63a9-44ef-8104-667496354bcf)

With real-world coordinates, set the coordinate system in the dialog to **WGS84 + EGM96** (EGM96 is a global geoid model that approximates sea level, as opposed to plain WGS84 with its ellipsoidal heights).

**No real-world location data?** Use the parallel survey, <a href="https://arcg.is/1fWWa01">ReefShape Local</a>. You tell it which marker is at which corner (northeast, southeast, southwest, northwest, or north, east, south, west) and enter the depths, and it emails a reference file in local coordinates. In that case leave the coordinate system at **Local Coordinates (m)**.

## Troubleshooting

| Symptom | What to try |
|---|---|
| No **ReefShape** menu in Metashape | Run the installer and restart Metashape. If you installed by hand, the scripts must sit directly in the scripts folder, not in a subfolder, together with `ui_components.py` and `reefshape_modules`. |
| The workflow stopped with "referencing needed" | The model had no georeferencing, or no scale. Check that the markers were detected and the scalebar and georeferencing files match the targets in the plot, add or fix the referencing, and run the workflow again; it resumes from the DEM. |
| "Georeferencing is turned on, but the file was not found" | Choose both files in the Georeferencing panel, or set it to *No* if you will reference the model by hand. |
| A dialog does nothing, or an error appears | Errors are shown in a message box. The full details are in Metashape's Console (View > Console). |
| Dependency install on first use | Expected, once: ICP, boundary-from-photos and rugosity tools install their packages after asking. Without internet access they cannot, so run them once while connected. |
| ReefShape Batch will not start | Run its launcher with `--check` in a terminal to see what it found. Check that Metashape Professional is installed and licensed, or set `REEFSHAPE_METASHAPE` to its install folder. |
| A batch job failed | The **Notes** column shows the reason, and each job's log (`<project>_<chunk>_batch.log`, beside the project) has the full transcript. Fix the cause and press **Run batch** again: finished stages are skipped. Unexpected errors in the batch window itself are logged to `errors.log` in the `ReefShape` folder of your user settings. |
| Running two batch jobs slows everything | They share one GPU and the memory. One at a time is usually fastest unless the computer has several GPUs. |

If you are stuck, email wgreene@perryinstitute.org. The scripts are under active development, so check back periodically for the latest version.

---

That's all for now. I hope ReefShape saves you time and energy on your coral photogrammetry projects and lets you spend more of it collecting the ecological data that helps us understand and protect reefs.

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
```

ReefShape is released under the MIT License (see `LICENSE`).
