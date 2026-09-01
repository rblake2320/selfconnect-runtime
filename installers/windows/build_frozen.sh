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

# Data files a frozen exe has no source tree to read: the evidence verifier
# (bug caught live 2026-08-31) and the compliance control catalogs (layer #1).
# Absolute source paths + --clean.
W="$(cygpath -w "$ROOT")"
ADD_VERIFIER="$W\\scr\\_evidence_verifier.py;scr"
ADD_FRAMEWORKS="$W\\scr\\frameworks\\data;scr/frameworks/data"

"$PY" -m PyInstaller --onefile --clean --noconfirm --distpath "$DIST" \
    --name scr --add-data "$ADD_VERIFIER" --add-data "$ADD_FRAMEWORKS" \
    installers/windows/freeze/entry_scr.py
"$PY" -m PyInstaller --onedir --clean --noconfirm --distpath "$DIST" \
    --name scr-service --add-data "$ADD_VERIFIER" --add-data "$ADD_FRAMEWORKS" \
    installers/windows/freeze/entry_scr_service.py

"$PY" scripts/build_enterprise_pkg.py "$DIST"

# ---- BUILD GATE: frozen workers must actually run (fail the build if not) --
"$PY" scripts/frozen_worker_gate.py \
    "$DIST/scr.exe" \
    "$DIST/scr-service/scr-service.exe"

ls -la "$DIST"
