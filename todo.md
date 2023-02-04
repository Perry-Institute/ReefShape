# Change Log and Feature List

## Full Underwater Workflow script

Desired Features:
- solve problem of model getting flipped when only using depths - set marker norm?
- allow user to choose target type for scaling, or choose between a couple methods of scaling/georeferencing
- add feature or create a separate script to align chunks from two different time points to one another
- potentially add a table view to georef formatting input so that the user can preview the columns that they are assigning to each variable

Known Problems:
- adding photos through the dialog box doesn't work if there are raw files alongside others - unsure of how extensive/problematic this is
  - seems to not be an issue with most raw file extensions, including .raw files, so idk what was going on with Will's

Code Quality:
- update readme to reflect changes to ui
- edit existing comments and add comments where needed
- potentially refactor dialog box - break component groupboxes into separate classes
- clean up/refactor main workflow to utilize member variables rather than creating unnecessary local vars
- clean up member functions in the full workflow class

Testing:
