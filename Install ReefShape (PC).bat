@echo off
setlocal enabledelayedexpansion

echo Installing ReefShape scripts for Windows...

REM Get the path to this script's directory
set "SOURCE=%~dp0ReefShape_Scripts"
set "TARGET=%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts"

echo Source folder: %SOURCE%
echo Target folder: %TARGET%

REM Make sure the target directory exists
if not exist "%TARGET%" (
    mkdir "%TARGET%"
)

REM Wipe stale ReefShape state in the target dir before copying. This is the
REM key reason "reinstalling" sometimes leaves the user with old menu items
REM bound to old code: Metashape caches compiled bytecode in __pycache__, and
REM xcopy doesn't delete files that no longer exist in the source. We delete:
REM   - __pycache__/ entirely (all .pyc, all Python versions)
REM   - the modules/ subdir we ship (so removed helper files don't linger)
REM   - any ReefShape-suite .py at the top level (so a renamed script doesn't
REM     leave an orphan around — e.g. a previous version of the ICP script).
REM We do NOT touch other .py files in the scripts dir, in case the user has
REM installed unrelated Metashape scripts there.
if exist "%TARGET%\__pycache__" (
    echo Removing stale bytecode cache: %TARGET%\__pycache__
    rmdir /S /Q "%TARGET%\__pycache__"
)
if exist "%TARGET%\modules" (
    echo Removing stale modules folder: %TARGET%\modules
    rmdir /S /Q "%TARGET%\modules"
)
for %%F in (
    "01_full_reefshape_workflow.py"
    "02_align_chunks.py"
    "02a_align_chunks_ICP.py"
    "03_optimization_process.py"
    "04_scale_model.py"
    "05_create_boundary.py"
    "06_copy_boundary.py"
    "07_calculate_area_ratio.py"
    "08_clean_project.py"
    "09_create_boundary_from_photos.py"
    "ui_components.py"
) do (
    if exist "%TARGET%\%%~F" (
        del /Q "%TARGET%\%%~F"
    )
)

REM Copy everything, overwriting if needed
xcopy /E /Y /I "%SOURCE%" "%TARGET%"

echo.
echo Installation complete!
echo ReefShape scripts have been installed to:
echo %TARGET%
pause
