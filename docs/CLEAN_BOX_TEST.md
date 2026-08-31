# Clean-Box Install Test — SelfConnect Runtime (§5)

**Audience:** someone who has never seen this product. Follow it literally.
**Goal (DoD):** complete §5 install → first agent run → evidence verify on a
fresh Windows 11 machine, from this doc alone, in **under 30 minutes**.
**Budget:** start a stopwatch at step 1; the bar is 30:00 to a VERIFIED bundle.

> Install and service registration require an **elevated (Administrator)**
> PowerShell. The MSI is **unsigned** in this build (Authenticode is a pending
> residual); Windows SmartScreen may warn — choose "More info → Run anyway".

---

## What you were handed

1. `SelfConnectRuntime-0.2.0.msi` — the installer (service + `scr` CLI + terminal stub).
2. `selfconnect-enterprise-1.0.0.scpkg` — a signed capability package.
3. `publisher_key.txt` — one line: the publisher's Ed25519 public key (hex) to pin.
4. A model endpoint you control — either **local Ollama** (`http://<host>:11434`)
   with a small model pulled (e.g. `gemma3:latest`), or an enterprise gateway.

---

## Step 1 — Install (≈2 min)

Elevated PowerShell:

```powershell
msiexec /i .\SelfConnectRuntime-0.2.0.msi /qb /l*v install.log
```

**Expected:** a progress dialog, then it closes with no error. Verify:

```powershell
Get-Service SelfConnectRuntime          # STATUS should be Running
$env:Path -split ';' | Select-String 'SelfConnect Runtime'   # CLI is on PATH
```

**Pass:** service `Running`; `scr` resolves (open a NEW PowerShell so PATH refreshes, then `scr --help` prints the command list).

## Step 2 — Initialize (≈1 min)

New (non-elevated is fine) PowerShell:

```powershell
scr init
```

**Expected:** `initialized SCR home at C:\ProgramData\SelfConnect\SCR`

## Step 3 — Add your model + live smoke test (≈3 min)

```powershell
scr model add local --adapter ollama --model gemma3:latest --base-url http://<host>:11434
scr model test local
```

**Expected:** `added model 'local' ...` then `model 'local' OK — replied '...'`.
**Pass:** `model test` prints **OK**. (If it prints FAILED, the endpoint is
unreachable or the model isn't pulled — fix that before continuing.)

## Step 4 — Install the package (≈1 min)

```powershell
scr package install .\selfconnect-enterprise-1.0.0.scpkg --trust .\publisher_key.txt
```

**Expected:** `installed selfconnect-enterprise 1.0.0`
**Pass:** that exact line. (An unsigned/tampered/untrusted package prints
`REJECTED [...]` and exits non-zero — that is correct fail-closed behavior.)

## Step 5 — Run an agent (≈2–5 min, model-dependent)

```powershell
scr run "Reply in one sentence: the target repo looks fine."
```

**Expected:** `job <id> [completed] session <session-id>` followed by the
model's reply. **Copy the `<session-id>`.**
**Pass:** stopped reason is `completed` and a session id is printed.

## Step 6 — Export + verify evidence (≈2 min)

```powershell
$key = 'ab' * 32                          # 32-byte hex key (demo)
scr session export <session-id> evidence.scevidence --key $key --seal
scr ledger verify evidence.scevidence --key $key
```

**Expected (verify):**
```
chain:        OK
session seal: OK
bundle seal:  OK
RESULT: VERIFIED
```
**Pass:** `RESULT: VERIFIED`.

### Step 6b — Offline verify with nothing installed (auditor check)

On any machine with only Python stdlib (no SCR):

```powershell
Expand-Archive evidence.scevidence -DestinationPath ev
python ev\verify.py ev\bundle.json --key $key
```

**Pass:** `RESULT: VERIFIED` — proves the ledger is auditable by a stranger.

## Step 7 — Clean uninstall (≈1 min)

Elevated PowerShell:

```powershell
msiexec /x .\SelfConnectRuntime-0.2.0.msi /qb
```

**Pass:**
- `Get-Service SelfConnectRuntime` → error "no service" (service removed).
- `C:\Program Files\SelfConnect Runtime` is gone (no orphaned binaries).
- `C:\ProgramData\SelfConnect\SCR` (your workspace/evidence) is **preserved by
  design** — data survives uninstall; delete it manually if you want a wipe.

---

## Overall pass criteria

- [ ] Steps 1–7 complete from this doc alone, no external help.
- [ ] Wall-clock from step 1 to the step-6 VERIFIED is **< 30:00**.
- [ ] `RESULT: VERIFIED` in step 6 and step 6b.
- [ ] Uninstall leaves no binaries outside the workspace; workspace preserved.

## Verified-so-far / residuals (honest)

- The frozen artifacts (`scr.exe`, `scr-service.exe`) were exercised through
  the **full §5 story live** against a real Ollama on the author's box
  (init → model add → live model test → package install → live run → sealed
  export → VERIFIED, plus offline `verify.py` VERIFIED). The MSI **builds**
  (`wix build`, WiX v7) and is a valid installer.
- **Not yet done by the author** (this is your run): the elevated
  `msiexec` install + Windows-service SCM lifecycle on a clean box — it needs
  Administrator rights the author's environment did not have.
- The MSI is **unsigned** (Authenticode pending the code-signing cert).
- The Electron terminal ships separately; the MSI installs a console launcher
  stub only.
