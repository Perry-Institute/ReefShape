#!/bin/bash

# Get folder this script is in
DIR="$(cd "$(dirname "$0")"; pwd)"

echo "Installing Metashape scripts for macOS..."

TARGET="$HOME/Library/Application Support/Agisoft/Metashape Pro/scripts"
SOURCE="$DIR/ReefShape_Scripts"

echo "Target folder: $TARGET"
mkdir -p "$TARGET"
cp -R "$SOURCE/"* "$TARGET/"

osascript -e 'display dialog "Metashape scripts installed successfully!" buttons {"OK"} default button 1'
