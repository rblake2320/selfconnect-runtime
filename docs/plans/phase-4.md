# Phase 4 plan — Package format, signing, loader (design §3.4)

## Files

| File | Purpose |
|---|---|
| `scr/merkle.py` | Deterministic SHA-256 Merkle tree over `{path: filehash}`: domain-separated leaf/node hashing, sorted by path, odd-node promotion. `merkle_root(files)`. |
| `scr/signing.py` | Ed25519 keypair generation, detached sign/verify (over the Merkle root bytes), `key_id = sha256(pubkey)[:16]`. `Keystore` of trusted keys (publisher-pinned + customer-added). Signed `RevocationList` (revoked (name,version) pairs, verified against a trusted key before it is honored). |
| `scr/package.py` | `.scpkg` = zip with `manifest.json` + payload dirs + `SIGNATURE`. `build_manifest(src)`, `write_package(src, out, signature)`, `read_member`, member listing. Manifest carries `name`, `version`, and `files: {path: sha256}`. |
| `scr/signer.py` | Publisher-side signer entry (`python -m scr.signer`): builds the manifest, computes the root, signs it, writes the `.scpkg`. NEVER shipped in the customer installer (separate module, not imported by loader/service). |
| `scr/loader.py` | `verify_package(path, keystore, revocations)` → structured result. Checks, in order and fail-closed: every packaged file's hash vs manifest (localizes the mismatching file); no extra/missing members (manifest/content mismatch); recomputed Merkle root vs SIGNATURE; Ed25519 signature over the root; publisher key pinned in the keystore; (name,version) not revoked. `run_selftests(path, adapter)` executes `tests/*.yaml` scenarios through the kernel against the configured model. |

## Test vectors / scenarios

- `test_merkle.py`: determinism (order-independent input, same root); a single
  changed file hash changes the root; single vs multi-leaf; empty.
- `test_signing.py`: sign/verify round-trip; wrong key fails; tampered root
  fails; key_id derivation; keystore trust decisions; revocation-list
  signature must verify before it is honored.
- `test_package.py`: build→read round-trip; manifest lists every payload file
  with correct hashes; SIGNATURE excluded from its own hashing.
- `test_loader.py` (adversarial): valid signed package loads; **unsigned**
  rejected; **untrusted/wrong key** rejected; **single-leaf byte tamper**
  rejected AND the error names the tampered file; **manifest/content
  mismatch** (extra member, missing member, wrong manifest hash) rejected;
  **downgrade to a revoked version** rejected; revocation list signed by an
  untrusted key is not honored (fail closed); self-test runner passes a good
  scenario and fails a bad one.

## Decisions (ADR-005)

- New dependency `cryptography==50.0.1` — Ed25519 + constant-time verify;
  design §3.4 mandates Ed25519. No hand-rolled crypto.
- Loader verifies at install AND at session start (same `verify_package`
  entry called from both; service wiring lands in Phase 6, self-test now).
- Fail-closed everywhere: any parse/format/trust error is a rejection, never
  a pass.

## Risks

- Zip path traversal on extraction (a malicious member path like `..\evil`):
  the loader never extracts to disk during verify — it reads members
  in-memory by exact name. The self-test runner extracts only `tests/*.yaml`
  to a temp dir with sanitized basenames.
