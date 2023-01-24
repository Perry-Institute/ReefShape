# Change Log and Feature List

## Full Underwater Workflow script

Desired Features:
- draw bounding box correctly when order of targets is incorrect
- refine behavior when not using automatic scaling and/or georeferencing:
    - maybe have it run through the whole workflow and then user can scale it later
    - maybe exit after aligning, have the user scale and check it, then continue
- solve problem of model getting flipped when only using depths - set marker norm?
- allow user to choose target type for scaling, or choose between a couple methods of scaling/georeferencing
- include an option to add photos and/or name the project and chunk from within the dialog box
- add feature or create a separate script to align chunks from two different time points to one another
- emulate metashape's 'import scale info' dialog box so that the user can specify which columns the relevant info is in

Known Problems:
- output sizes can be unnecessarily large, especially when producing duplicate outputs for use in TagLab
- potential 'CUDA out of memory' error, likely fixed by updating GPU drivers
