# Change Log and Feature List

## Full Underwater Workflow script

Desired Features:
- draw bounding box correctly when order of targets is incorrect
- refine behavior when not using automatic scaling and/or georeferencing:
    - maybe have it run through the whole workflow and then user can scale it later
    - maybe exit after aligning, have the user scale and check it, then continue
- solve problem of model getting flipped when only using depths - set marker norm?
- allow user to choose target type for scaling, or choose between a couple methods of scaling/georeferencing
- add feature or create a separate script to align chunks from two different time points to one another
- emulate metashape's 'import scale info' dialog box so that the user can specify which columns the relevant info is in
  - potentially add a table view so that the user can preview the columns that they are assigning to each variable

Known Problems:
- adding photos through the dialog box doesn't work if there are raw files alongside others - unsure of how extensive/problematic this is
  - seems to not be an issue with most raw file extensions, including .raw files, so idk what was going on with Will's
- potential 'CUDA out of memory' error, likely fixed by updating GPU drivers

Code Quality:
- add documentation comments to each function
- edit existing comments and add comments where needed
- potentially refactor dialog box - break component groupboxes into separate classes

Testing:
- test georeferencing formatting
- test custom vs default resolution
