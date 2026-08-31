# Phase 7 plan — Vault, config, CLI, updater, licensing, installers (design §3.2, §5, §6, §7)

## Files (testable core)

| File | Purpose |
|---|---|
| `scr/vault.py` | Credential vault. Windows: DPAPI (`CryptProtectData`/`CryptUnprotectData` via ctypes) — ciphertext at rest under the SCR home, decrypted on read; nothing secret ever plaintext on disk. POSIX: `keyring`/libsecret backend (skip-marked when unavailable). `store_secret`/`get_secret`/`delete_secret`. |
| `scr/redaction.py` | `logging.Filter` that redacts registered secret values AND high-entropy key patterns from every log record (message + args). Tested to leak nothing. |
| `scr/license.py` | Offline Ed25519 license: `{subject, seats, features, not_after}` + signature. `verify(text, pubkey, now)` → `valid` / `grace` (expired → read-only evidence access, never brick) / `invalid`. |
| `scr/config.py` | Layered config (defaults → file → overrides) under `SCR_HOME`. Model endpoints store a **vault reference**, never an inline secret. |
| `scr/updater.py` | Staged side-by-side install → health probe → switch, with automatic rollback to the previous version on probe failure; offline update files. |
| `scr/cli.py` | `scr` argparse dispatch: `init`, `model add/list`, `package verify`, `run`, `ledger export/verify`, `license install/status`, `doctor`. Routes into existing modules. |

## Installer scaffolds (authored, build/install OPEN — not verifiable here)

`installers/windows/scr.wxs` (WiX), `installers/windows/winget.yaml`,
`installers/linux/scr.service` (systemd), `installers/linux/debian/control`.
These are real artifacts; the design's "MSI installs on a clean box in <30 min"
Definition-of-Done item is marked **OPEN** in STATUS until built and run on a
clean Windows box with the WiX toolchain (not present in this environment).

## Tests

- `test_vault.py`: Windows DPAPI round-trip; on-disk blob != plaintext; delete;
  missing secret returns None. (POSIX keyring path skip-marked.)
- `test_redaction.py`: a registered secret and an API-key-shaped token are
  both scrubbed from formatted log output; non-secret text passes through.
- `test_license.py`: valid license verifies; tampered field / wrong key
  rejected; expired license → `grace` (evidence read-only allowed, runs
  denied); malformed license rejected.
- `test_config.py`: layering precedence; a model endpoint persists a vault
  reference, never the secret; round-trip load/save.
- `test_updater.py`: successful staged update switches `current`; a failing
  health probe rolls back and `current` still points at the old version;
  offline update file applied.
- `test_cli.py`: `init` creates the home; `model add` records a vault ref;
  `package verify` on a signed package prints VERIFIED; `run` via a mock
  adapter; `ledger verify`; `license status`.

## Decisions (ADR-008)

- Windows vault = classic DPAPI (user scope) — real and testable on this box.
  DPAPI-NG (NCrypt, machine/protection-descriptor) is the stricter Phase 9
  target; recorded as a deferral, not a silent gap.
- New dependency: none required for the tested core (DPAPI via ctypes,
  license via existing `cryptography`). `keyring` is an OPTIONAL extra used
  only on POSIX and skip-marked in tests.
- Installers are scaffolds; their build/run is OPEN and labeled as such.
