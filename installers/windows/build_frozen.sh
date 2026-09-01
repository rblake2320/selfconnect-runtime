#!/usr/bin/env bash
# Reproducible frozen-artifact build (was tribal knowledge — RUN-B session).
# Builds scr.exe + scr-service.exe into installers/windows/dist, then rebuilds
# and re-signs the enterprise package there. Run from anywhere.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY=.venv/Scripts/python.exe
DIST=installers/windows/dist

# The evidence verifier ships as package data — a frozen exe has no source
# tree to read it from (bug caught live 2026-08-31; absolute path + --clean).
ADD_DATA="$(cygpath -w "$ROOT")\\scr\\_evidence_verifier.py;scr"

"$PY" -m PyInstaller --onefile --clean --noconfirm --distpath "$DIST" \
    --name scr --add-data "$ADD_DATA" installers/windows/freeze/entry_scr.py
"$PY" -m PyInstaller --onefile --clean --noconfirm --distpath "$DIST" \
    --name scr-service --add-data "$ADD_DATA" \
    installers/windows/freeze/entry_scr_service.py

"$PY" scripts/build_enterprise_pkg.py "$DIST"
ls -la "$DIST"
