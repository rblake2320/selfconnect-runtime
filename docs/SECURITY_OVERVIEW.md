# SCR Security Overview (internal draft)

INTERNAL — private repo, pre-disclosure. No wire-format detail beyond what the
code already fixes.

## Trust boundaries

- **Model output is untrusted.** The model may request anything; the capability
  kernel (deny-by-default) decides what executes. No ambient authority reaches a
  worker or MCP subprocess.
- **Tools run out-of-process.** Native tools execute in sandboxed workers
  (restricted env, cwd jail, timeout, memory cap, whole-tree kill). MCP servers
  get scoped env only and manifest-scoped tool projection.
- **Packages are untrusted until verified.** Ed25519 signature over a SHA-256
  Merkle root; publisher key pinning; signed revocation lists; fail-closed
  loader with localized tamper detection.
- **Evidence is tamper-evident.** Hash-chained ledger + HMAC seals; bundles
  verify offline with stdlib only.

## Controls by threat

| Threat | Control |
|---|---|
| Malicious model action | capability kernel, HITL approval gates |
| Path escape (traversal/symlink/ADS) | resolved-path containment |
| Tampered package | Merkle + Ed25519 + pinning + revocation |
| Tampered evidence | hash chain + HMAC seals |
| Orphaned/runaway process | Job Objects / setsid + reaping, timeouts |
| Credential theft from disk | DPAPI vault; log redaction; no plaintext secrets |
| Policy escalation | intersection-only tightening; monotonic attenuation |
| Replayed approval | approval bound to the exact action id |
| Downgrade to revoked pkg | signed revocation list |
| Crash mid-side-effect | write-ahead journal; quarantine non-idempotent |

## Residual / OPEN

- DPAPI-NG (vs classic DPAPI) — Phase 9.
- Windows job-assignment micro-window (ADR-003) — Phase 9 pen review.
- Installer build + clean-box install — OPEN (STATUS).
