# Clean-Box Install Test (§5 Installation Story)

INTERNAL. The procedure a stranger follows on a fresh Windows VM to satisfy the
DoD bar: "§5 install on a clean machine from docs alone, under 30 minutes."

Status of the automated parts:
- **Steps 2–5 are automated** in `tests/test_e2e_install_story.py` (offline via
  a local Ollama-shaped stub; live via `SCR_OLLAMA_URL`). That test is green.
- **Step 1 (MSI install)** is NOT yet automatable here — the WiX toolset is not
  usable in the build environment (`wix` absent; `dotnet --version` errors).
  This doc carries the manual MSI build + install steps so the clean-box run is
  reproducible the moment WiX is available.

---

## A. Build the MSI (publisher machine, once WiX works)

```powershell
# one-time toolchain (per the owner's repair block)
winget install Microsoft.DotNet.SDK.8 -h
dotnet tool install --global wix          # WiX v5/6
wix --version

# freeze the runtime to dist\scr.exe / dist\scr-service.exe (PyInstaller)
py -3.12 -m pip install pyinstaller
pyinstaller --onefile -n scr        --collect-all scr  scr\cli.py
pyinstaller --onefile -n scr-service --collect-all scr scr\service_main.py   # see NOTE
Move-Item dist\scr.exe, dist\scr-service.exe installers\windows\dist\

# build + Authenticode-sign the MSI
cd installers\windows
wix build scr.wxs -o SelfConnectRuntime-Setup.msi
signtool sign /fd SHA256 /a SelfConnectRuntime-Setup.msi   # publisher cert
```

> NOTE: `scr-service.exe` needs a small `service_main.py` entry that runs the
> FastAPI app via uvicorn bound to loopback (uses `scr.service.create_app` +
> `scr.service.check_bind`). Tracked as an OPEN item — it is a thin wrapper,
> not yet written, because it is only exercisable once the MSI/service story is
> testable on a VM.

## B. Clean Windows VM run (the 30-minute bar)

1. **Install** — double-click `SelfConnectRuntime-Setup.msi` (or
   `winget install SelfConnect.Runtime`). Installs the service + `scr` CLI.
2. **Init** — `scr init`
3. **Model** — `scr model add spark --adapter ollama --model llama3.1 --base-url http://<SPARK_IP>:11434`
   then `scr model test spark` → expect `OK`.
4. **Package** — `scr package install selfconnect-enterprise-1.0.0.scpkg --trust publisher_key.txt`
   → expect `installed selfconnect-enterprise 1.0.0`.
5. **Run** — `scr run "Run the security review on C:\target\repo"` → expect a
   `completed` line with a session id.
6. **Evidence** — `scr session export <session> evidence.scevidence --key <hex> --seal`
   then `scr ledger verify evidence.scevidence --key <hex>` → expect `VERIFIED`.

Record wall-clock time from step 1 to step 6; the bar is < 30 minutes.

## C. Automated equivalent (steps 2–6, runnable now)

```powershell
# offline (stub model)
py -3.12 -m pytest tests\test_e2e_install_story.py -q

# live against the DGX Spark
$env:SCR_OLLAMA_URL  = "http://<SPARK_IP>:11434"
$env:SCR_OLLAMA_MODEL = "llama3.1"
py -3.12 -m pytest tests\test_e2e_install_story.py -q -s
```

## OPEN items gating a full green clean-box run

- MSI build + Authenticode signing (WiX/dotnet not usable in this environment).
- `scr-service.exe` service entry wrapper (thin, not yet written).
- Live model step depends on the DGX Spark Ollama being reachable
  (`OLLAMA_HOST=0.0.0.0`, LAN IP handed in via `SCR_OLLAMA_URL`).
