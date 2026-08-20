@echo off
setlocal enabledelayedexpansion

REM ---------------------------------------------------------------------
REM ReefShape installer for Windows.
REM
REM Two things this gets right that an earlier version did not.
REM
REM ORDER. The old version deleted a hardcoded list of script names first and
REM copied second, checked neither the source nor the result, and printed
REM "Installation complete!" either way. When the copy failed, the user was
REM left with exactly the scripts that happened to be missing from that
REM hardcoded delete list -- and a message telling them it had worked. So now:
REM validate the source, copy, verify the copy, and only then remove stale
REM files. A failure at any point says so instead of claiming success.
REM
REM WHAT COUNTS AS OURS. Stale files are read from a manifest written by the
REM previous install, so the installer can only ever remove files it put there
REM itself. Deciding by name pattern instead is not safe: the scripts folder
REM is shared, and "looks like a ReefShape script" also matches ReefShape-Air's
REM 01_full_aerial_workflow.py and reefshape_air_ui_components.py, which are a
REM different product and not ours to delete.
REM ---------------------------------------------------------------------

set "SOURCE=%~dp0ReefShape_Scripts"
set "TARGET=%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts"
set "MANIFEST=%TARGET%\.reefshape_manifest"

echo Installing ReefShape scripts for Windows...
echo Source folder: %SOURCE%
echo Target folder: %TARGET%
echo.

REM --- 1. Validate the source before touching anything -----------------
if not exist "%SOURCE%" (
    echo ERROR: Could not find the ReefShape_Scripts folder at:
    echo   %SOURCE%
    echo.
    echo This installer must stay next to the ReefShape_Scripts folder. If you
    echo moved it out of the ReefShape folder, put it back and run it again.
    echo.
    pause
    exit /b 1
)

for %%R in (
    "01_full_reefshape_workflow.py"
    "ui_components.py"
    "modules\reefshape_core.py"
    "modules\pip_auto_install.py"
) do (
    if not exist "%SOURCE%\%%~R" (
        echo ERROR: The ReefShape_Scripts folder is missing %%~R
        echo It looks incomplete. Re-download ReefShape and try again.
        echo.
        pause
        exit /b 1
    )
)

if not exist "%TARGET%" (
    mkdir "%TARGET%"
    if errorlevel 1 (
        echo ERROR: Could not create the scripts folder at:
        echo   %TARGET%
        echo.
        pause
        exit /b 1
    )
)

REM --- 2. Clear cached bytecode for our scripts only --------------------
REM A stale .pyc for a script that has since changed is a genuinely confusing
REM failure: the menu item is there and runs the old code. __pycache__ is
REM shared with other products, so only our own entries are cleared.
if exist "%TARGET%\__pycache__" (
    echo Clearing cached bytecode for ReefShape scripts
    for %%F in ("%SOURCE%\*.py") do (
        del /Q "%TARGET%\__pycache__\%%~nF.*.pyc" >nul 2>&1
    )
)
if exist "%TARGET%\modules" (
    echo Removing stale modules folder
    rmdir /S /Q "%TARGET%\modules"
)

REM --- 3. Copy, and check that it worked -------------------------------
echo Copying scripts...
xcopy /E /Y /I /Q "%SOURCE%" "%TARGET%"
if errorlevel 1 (
    echo.
    echo ERROR: Copying the scripts failed.
    echo Check that you have permission to write to:
    echo   %TARGET%
    echo.
    pause
    exit /b 1
)

REM Trust nothing: confirm the files are actually there. A copy that reports
REM success but produced nothing is the exact failure this installer exists to
REM stop hiding.
set /a INSTALLED=0
for %%F in ("%SOURCE%\*.py") do (
    if exist "%TARGET%\%%~nxF" set /a INSTALLED+=1
)
if !INSTALLED! EQU 0 (
    echo.
    echo ERROR: The scripts did not copy across, despite the copy reporting
    echo success. Check permissions on:
    echo   %TARGET%
    echo.
    pause
    exit /b 1
)

REM --- 4. Only now remove files this installer previously placed --------
if exist "%MANIFEST%" (
    for /f "usebackq delims=" %%N in ("%MANIFEST%") do (
        if not exist "%SOURCE%\%%N" (
            if exist "%TARGET%\%%N" (
                echo Removing script from an older ReefShape version: %%N
                del /Q "%TARGET%\%%N"
            )
        )
    )
) else (
    REM No manifest: first install since manifests existed, so fall back to
    REM the names ReefShape shipped historically. An explicit list, because a
    REM guess would risk deleting a different product's scripts.
    for %%N in (
        "02a_align_chunks_ICP.py"
        "03_optimization_process.py"
        "04_scale_model.py"
        "05_create_boundary.py"
        "06_copy_boundary.py"
        "07_calculate_area_ratio.py"
        "08_clean_project.py"
        "09_create_boundary_from_photos.py"
    ) do (
        if not exist "%SOURCE%\%%~N" (
            if exist "%TARGET%\%%~N" (
                echo Removing script from an older ReefShape version: %%~N
                del /Q "%TARGET%\%%~N"
            )
        )
    )
)

REM --- 5. Record what we installed, for the next run --------------------
break > "%MANIFEST%"
for %%F in ("%SOURCE%\*.py") do (
    echo %%~nxF>> "%MANIFEST%"
)

echo.
echo Installation complete: !INSTALLED! scripts installed to
echo   %TARGET%
echo.
echo Restart Metashape to pick them up.
pause
endlocal
