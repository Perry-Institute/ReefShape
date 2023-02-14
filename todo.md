# Change Log and Feature List

## Full Underwater Workflow script

Desired Features:
- potentially add a table view to georef formatting input so that the user can preview the columns that they are assigning to each variable
- solve problem of model getting flipped when only using depths - set marker norm?

Known Problems:
- adding photos through the dialog box doesn't work if there are raw files alongside others - unsure of how extensive/problematic this is
  - seems to not be an issue with most raw file extensions, including .raw files, so idk what was going on with Will's
- add photos isn't reliable on mac

Code Quality:
- update readme to reflect changes to ui
- edit existing comments and add comments where needed


Testing:
- test behavior when the script fails to detect markers - does it error out? does it georeference with <4 markers? should the script try to check for having <4 markers? (can be done by filtering out scalebar markers, ie chunk.marker.reference.location)
