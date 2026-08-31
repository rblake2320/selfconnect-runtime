# Phase 5 plan — Ledger completion + evidence export (design §3.5)

## Files

| File | Purpose |
|---|---|
| `scr/_evidence_verifier.py` | **Pure stdlib** (hashlib, hmac, json only — imports nothing from `scr`). `recompute_chain(events)`, `verify_bundle_obj(bundle, bundle_hmac, key)` → structured report. This is the single source of truth for verification and is what runs "on a machine with nothing else installed." |
| `scr/evidence.py` | `export_bundle(store, session_id, key, package, out_path)` writes a `.scevidence` zip: `bundle.json` (runtime version, package name/version, session events as {seq, event-canonical, hash}, session seal), `bundle.hmac` (HMAC over canonical bundle.json), and an embedded standalone `verify.py`. `verify_bundle(path, key)` reads a bundle and verifies it via `_evidence_verifier`. `seal_on_close(ledger, session_id, key)` per-session seal. |

## Why an embedded verifier

The finish line requires an offline, self-verifiable ledger. The bundle
carries its own `verify.py` (the exact `_evidence_verifier` source), so a
customer/auditor with only Python stdlib — no SCR, no DB, no network — can
run `python verify.py bundle.json --key <hex>` and get a yes/no with reasons.
One source of truth: `evidence.py` embeds the verifier module's own source,
so the standalone and the SCR-side path can never diverge.

## Verification steps (fail-closed)

1. Recompute the SHA-256 hash chain over the events; every event's stored
   hash must match, producing head + count.
2. Session seal: `HMAC(key, "<head>:<count>")` must equal the stored seal.
3. Bundle seal: `HMAC(key, canonical(bundle.json))` must equal `bundle.hmac`
   — detects any post-export mutation of the bundle itself.

## Tests (`test_evidence.py`)

- Round-trip: export a real session's ledger → verify OK; report names count
  and head.
- Tamper: flip a byte in one event → chain break detected (localized to seq).
- Reorder / delete an event → detected.
- Wrong key → session-seal + bundle-seal fail.
- Bundle mutation (edit bundle.json after export) → bundle-seal fail.
- **Offline proof**: extract the embedded `verify.py` and run it in a
  subprocess with a cleaned `sys.path` (no `scr` importable) against the
  bundle — passes for a good bundle, fails for a tampered one. This is the
  "nothing else installed" guarantee, tested.

## Decisions (ADR-006)

- Events are stored in the bundle as their exact canonical strings (as the
  ledger persisted them), so verification never re-serializes and cannot
  drift across json library versions.
- Zero new dependencies (stdlib hashlib/hmac/json/zipfile).
