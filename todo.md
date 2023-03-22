# Change Log and Feature List

## Full Underwater Workflow script

Desired Features:
- potentially add a table view to georef formatting input so that the user can preview the columns that they are assigning to each variable
- solve problem of model getting flipped when only using depths - set marker norm?
- load chunk crs into dialog box as default
  - maybe save other dialog box settings and load those as defaults
- incorporate project cleanup to remove depth maps and such
- (eventually) include a checkbox to keep depth maps for radiometric corrections
- add Will's method for improving alignment (keeping key points, sequential preselection, etc)
  - probably include a target threshold for camera alignment before using this method - ie if <90% of the cameras align the first go around, do it again with another method (maybe also with lower accuracy or something) to try to align the rest
- check for existing files to avoid duplicate/redoing exports

Known Problems:
- ui scaling gets messed up, esp on mac - main window is too big, and some of the buttons get clipped too small
    - remove/edit fixed size policy on those buttons
    - look into scaling main window dynamically based on display size
- adding photos through the dialog box doesn't work if there are raw files alongside others - unsure of how extensive/problematic this is
    - seems to not be an issue with most raw file extensions, including .raw files, so idk what was going on with Will's
    - maybe just isn't reliable on mac

Code Quality:
- update readme to reflect changes to ui
- edit existing comments and add comments where needed


Testing:
- test behavior when the script fails to detect markers - does it error out? does it georeference with <4 markers? should the script try to check for having <4 markers? (can be done by filtering out scalebar markers, ie chunk.marker.reference.location)
