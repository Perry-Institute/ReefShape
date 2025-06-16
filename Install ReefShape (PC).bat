@echo off
setlocal enabledelayedexpansion

echo Installing Metashape scripts for Windows...

REM Get the path to this script's directory
set "SOURCE=%~dp0ReefShape_Scripts"
set "TARGET=%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts"

echo Source folder: %SOURCE%
echo Target folder: %TARGET%

REM Make sure the target directory exists
if not exist "%TARGET%" (
    mkdir "%TARGET%"
)

REM Copy everything, overwriting if needed
xcopy /E /Y /I "%SOURCE%" "%TARGET%"

echo.
echo ✅ Installation complete!
echo Metashape scripts have been installed to:
echo %TARGET%
pause
