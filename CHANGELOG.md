# 🪸 CHANGELOG – ReefShape

## [v1.2] – June 2025

### ✨ Major Enhancements
✅ **Auto-naming of chunks**: When adding new photos in both main scripts (Full ReefShape Workflow and Align Timepoints), the chunk is automatically renamed to match the capture date in the EXIF data of the images. If this is wrong, you can still rename manually!
✅ **Manual setting of vertex coloring**: Previously, vertex colors were always generated, sometimes adding hours to the processing time. Vertex colors can now be disabled to save time. If they are enabled, a separate progress bar tracks vertex color generation much more accurately.
✅ **Custom error dialogs**: Introduced a robust `show_error_dialog()` function to cleanly report exceptions through the GUI instead of console output. Also prevents the GUI from freezing upon errors.
✅ **Generate Report Improvements**: There is now a separate button to export a processing report independently from other exports. The processing report now has a much nicer header as well.
✅ **Settings saved from last session**: The Full ReefShape Workflow script now saves user settings, so they will stay as they were the last time you ran the script, even if you close it instead of running it. Less clicks for everybody!

### 🧠 Behavior Changes
✅ **Auto-georeferencing overhaul**: If no markers are present, the workflow now pre-selects “Yes” for georeferencing input to ensure smoother initial setup. This setting used to be off by default and asked the user whether they were using auto-detectable markers. The prompt now explains what it does more clearly.
✅ **Coordinate System Improvement**: Now, instead of the script defaulting to local coordinates, it defaults to your last used settings, and only WGS84+EGM96 or local coordinates can be chosen. This mitigates confusion and saves clicking time. If you'd like your data exported in a different CRS, this can be done manually outside of ReefShape.
✅ **Project Cleanup is now automatic**: At the end of processing (if script finishes correctly with no errors), the script will automatically delete all intermediate products that are not necessary for long-term storage. This includes Key Points, Depth Maps, and Orthophotos.
✅ **Create chunk & target type placement**: The “Create Chunk” button and “Select Target Type” dropdown are now positioned at the top of the project setup panel for more intuitive access now that chunks can be named automatically.

### 🐛 Bug Fixes
✅ Fixed issue where `comboRefChunk` and `comboNewChunk` dropdowns did not reflect renamed or newly created chunks.
✅ Resolved `[None]` entries in damaged marker dropdowns by attaching actual `Marker` objects as `QComboBox` user data.
✅ Large map exports for DEMs now support the BigTiff standard and no longer error out for files larger than 4GB.
✅ Prevented chunk label resets when saving the project.
✅ Prevented crash when no chunks existed by ensuring a default chunk is always initialized.
