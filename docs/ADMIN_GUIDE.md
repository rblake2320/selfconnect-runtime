# SCR Admin Guide (internal draft)

INTERNAL — private repo. Not customer-facing copy.

## Install & first run

1. Install the service (MSI on Windows — OPEN, see STATUS; or `pip install`
   the package into a venv for now).
2. `scr init` — creates the SCR home (`%ProgramData%\SelfConnect\SCR` or
   `$SCR_HOME`), the state DB, and the packages dir.
3. `scr model add <name> --adapter <openai-compat|ollama|anthropic|bedrock|azure>
   --model <id> [--base-url ...] [--secret <cred>]` — the credential goes into
   the DPAPI vault; only a vault reference is written to config.
4. `scr package verify <pkg.scpkg> --trust trusted_keys.txt` before installing.

## Running

- Start the service; it binds `127.0.0.1` by default and refuses a non-loopback
  bind without TLS + auth.
- Create runs over REST (`POST /runs`) or the CLI. RBAC roles:
  admin / operator / auditor / viewer.
- Approval-gated actions pause; approve with `POST /jobs/{id}/approve` (operator
  or admin).

## Evidence

- `scr ledger export <session> <out.scevidence> --key <hex>` produces a
  self-verifying bundle.
- `scr ledger verify <bundle> --key <hex>` — or run the embedded `verify.py`
  on any machine with only Python stdlib.

## Backup / restore / health

- `scr doctor` — runtime, DB integrity, model count.
- Encrypted backup/restore via `scr.backup` (32-byte key from the vault).
- Metrics (Prometheus) are OFF by default; enable explicitly.

## Updates

- Staged side-by-side with a health probe and automatic rollback; offline
  update files supported. Manual only — no auto-update.
