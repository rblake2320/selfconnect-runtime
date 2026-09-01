#!/usr/bin/env bash
# MSI build + POST-BUILD WORKER GATE.
# After `wix build`, the MSI is administrative-extracted (msiexec /a — no
# elevation needed) to a scratch prefix and the INSTALLED-layout worker is
# gated: it must physically execute a real fs_list from the installed path or
# this script fails. Guards the whole chain freeze → wxs harvest → install
# layout, not just the freeze.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$ROOT/installers/windows"
PY="$ROOT/.venv/Scripts/python.exe"
MSI="$HERE/SelfConnectRuntime-0.2.0.msi"

cd "$HERE"
wix build Package.wxs -o "$MSI" --acceptEula wix7

# ---- unelevated administrative extract into a scratch prefix ----
EXTRACT="$ROOT/build/msi-extract"
rm -rf "$EXTRACT"; mkdir -p "$EXTRACT"
MSIW="$(cygpath -w "$MSI")"; EXTW="$(cygpath -w "$EXTRACT")"
msiexec //a "$MSIW" //qn TARGETDIR="$EXTW"

INST="$EXTRACT/PFiles/SelfConnect Runtime"
[ -d "$INST" ] || INST="$(dirname "$(find "$EXTRACT" -name scr.exe | head -1)")"
echo "installed layout: $INST"

# ---- GATE the INSTALLED artifacts (CLI onefile + service onedir) ----
"$PY" "$ROOT/scripts/frozen_worker_gate.py" \
    "$INST/scr.exe" \
    "$INST/scr-service.exe"

ls -la "$MSI"
echo "MSI BUILD + INSTALLED-WORKER GATE: PASS"
