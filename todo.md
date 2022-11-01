
To Add:

To think about:
- TODO: is there a way to draw bounding box when order of targets is messed up?
- deal with case of not using automatic scaling and/or georeferencing:
    - maybe have it run through the whole workflow and then user can scale it later
    - or maybe (probably better)[but maybe not?] exit after aligning, have the user scale and check it, then continue - ask will how well this would work with method of only collecting depths
- is there a way to avoid problem of model getting flipped when only using depths? set marker norm?
- allow user to choose target type for scaling? or choose between a couple methods of scaling/georeferencing?
- develop a full dialog box ui like for align model to model script?

Tests:
- run on small test plot with and without automatic markers enabled
- run with incorrect/mixed up scaling/georef data - prints error message and exits as it should
