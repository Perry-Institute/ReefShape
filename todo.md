# Change Log and Feature List

## Full Underwater Workflow script

Desired Features:
- potentially add a table view to georef formatting input so that the user can preview the columns that they are assigning to each variable
- solve problem of model getting flipped when only using depths - set marker norm?
- incorporate project cleanup to remove depth maps and such
- (eventually) include a checkbox to keep depth maps for radiometric corrections
- add Will's method for improving alignment (keeping key points, sequential preselection, etc)

Known Problems:
- edit align chunks script so that it only uses corner markers (ie export with enabled flag or something)
- adding photos through the dialog box doesn't work if there are raw files alongside others - unsure of how extensive/problematic this is
  - seems to not be an issue with most raw file extensions, including .raw files, so idk what was going on with Will's
  - maybe just isn't reliable on mac

Code Quality:
- update readme to reflect changes to ui
- edit existing comments and add comments where needed


Testing:
- test behavior when the script fails to detect markers - does it error out? does it georeference with <4 markers? should the script try to check for having <4 markers? (can be done by filtering out scalebar markers, ie chunk.marker.reference.location)
