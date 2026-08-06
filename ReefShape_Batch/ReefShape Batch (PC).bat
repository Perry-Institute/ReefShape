@echo off
setlocal enabledelayedexpansion

REM ---------------------------------------------------------------------
REM ReefShape Batch launcher (Windows)
REM
REM Runs the batch GUI on the Python interpreter that ships inside Metashape,
REM so the user needs no Python installation of their own. That interpreter
REM already has PySide2, and batch\qt.py adds Metashape's root to the DLL
REM search path at import time so Qt5Widgets.dll and friends resolve.
REM
REM Set REEFSHAPE_METASHAPE to an install folder to override discovery
REM (useful when several Metashape versions are installed side by side).
REM ---------------------------------------------------------------------

set "APPDIR=%~dp0"
REM Strip the trailing backslash so quoted paths below don't end in \"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"

REM --- Locate the Metashape install root ---------------------------------
set "MSROOT="

REM 1. Explicit override wins.
if defined REEFSHAPE_METASHAPE (
    if exist "%REEFSHAPE_METASHAPE%\metashape.exe" set "MSROOT=%REEFSHAPE_METASHAPE%"
)

REM 2. The registry knows about non-default install locations that guessing
REM    would miss. HKLM for a machine-wide install, HKCU for a per-user one.
if not defined MSROOT (
    for /f "tokens=2,*" %%A in (
        'reg query "HKLM\SOFTWARE\Agisoft\Metashape Pro\Install" /v InstallDir 2^>nul ^| find "InstallDir"'
    ) do set "MSROOT=%%B"
)
if not defined MSROOT (
    for /f "tokens=2,*" %%A in (
        'reg query "HKCU\SOFTWARE\Agisoft\Metashape Pro\Install" /v InstallDir 2^>nul ^| find "InstallDir"'
    ) do set "MSROOT=%%B"
)
REM The registry value carries a trailing backslash; drop it.
if defined MSROOT if "!MSROOT:~-1!"=="\" set "MSROOT=!MSROOT:~0,-1!"

REM 3. Fall back to the usual install locations.
if not defined MSROOT (
    for %%D in (
        "%PROGRAMFILES%\Agisoft\Metashape Pro"
        "%PROGRAMFILES(X86)%\Agisoft\Metashape Pro"
        "%LOCALAPPDATA%\Agisoft\Metashape Pro"
    ) do (
        if not defined MSROOT if exist "%%~D\metashape.exe" set "MSROOT=%%~D"
    )
)

REM --- Pick an interpreter ------------------------------------------------
REM Prefer Metashape's bundled Python. If Metashape wasn't found here, fall
REM back to a system Python: the app can still start, show its "Metashape not
REM found" dialog, and let the user point at the install by hand -- which is
REM a far better outcome than a batch file that dies with a cryptic message.
set "PYEXE="
if defined MSROOT (
    if exist "%MSROOT%\python\pythonw.exe" set "PYEXE=%MSROOT%\python\pythonw.exe"
)

if not defined PYEXE (
    where pythonw.exe >nul 2>&1 && set "PYEXE=pythonw.exe"
)
if not defined PYEXE (
    where python.exe >nul 2>&1 && set "PYEXE=python.exe"
)

if not defined PYEXE (
    echo.
    echo ERROR: Could not find a Python interpreter to run ReefShape Batch.
    echo.
    echo Agisoft Metashape Professional does not appear to be installed in a
    echo standard location, and no system Python was found on your PATH.
    echo.
    echo If Metashape is installed somewhere unusual, set the environment
    echo variable REEFSHAPE_METASHAPE to its folder, for example:
    echo     setx REEFSHAPE_METASHAPE "D:\Agisoft\Metashape Pro"
    echo.
    pause
    exit /b 1
)

REM Hand the discovered root to the app so it doesn't repeat the search.
if defined MSROOT set "REEFSHAPE_METASHAPE=%MSROOT%"

REM Run from the app directory so `batch` is importable as a package.
cd /d "%APPDIR%"

REM pythonw.exe detaches from the console, which is what a double-clicked GUI
REM wants -- but it also means a crash during startup would vanish silently.
REM batch\__main__.py guards its own imports and reports any startup failure
REM via a native message box plus %LOCALAPPDATA%\ReefShape\startup_error.log,
REM so there is always something to look at.
start "" "%PYEXE%" -m batch %*

endlocal
