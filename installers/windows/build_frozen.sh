#!/usr/bin/env bash
# Reproducible frozen-artifact build + WORKER GATES.
#
# scr.exe          — onefile (portable, hand-run; pays a per-spawn extraction
#                    tax and needs the sandbox-provisioned TEMP — acceptable
#                    for the portable CLI).
# scr-service/     — onedir (MSI-installed service; workers launch from disk
#                    with NO extraction and NO TEMP dependency — the RUN-D
#                    failure surface removed by construction).
#
# Every build ends with scripts/frozen_worker_gate.py: each artifact's worker
# must physically execute a real fs_list under the sandbox's exact restricted
# env or the BUILD FAILS. (RUN D shipped a frozen exe whose workers could not
# spawn; venv tests can never catch that class.)
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
"$PY" -m PyInstaller --onedir --clean --noconfirm --distpath "$DIST" \
    --name scr-service --add-data "$ADD_DATA" \
    installers/windows/freeze/entry_scr_service.py

"$PY" scripts/build_enterprise_pkg.py "$DIST"

# ---- BUILD GATE: frozen workers must actually run (fail the build if not) --
"$PY" scripts/frozen_worker_gate.py \
    "$DIST/scr.exe" \
    "$DIST/scr-service/scr-service.exe"

ls -la "$DIST"
