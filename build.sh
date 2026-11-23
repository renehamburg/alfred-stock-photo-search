#!/bin/sh
# Package the workflow next to this folder
set -e
cd "$(dirname "$0")"
out=../Stock-Photo-Search.alfredworkflow
rm -f "$out"
zip -qr "$out" info.plist photos.py icon.png images -x "*.DS_Store"
echo "$out"
