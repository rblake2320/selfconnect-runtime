# Architecture Decision Records

One paragraph each. Newest at the bottom.

## ADR-001 — Design doc reconstructed (2026-08-31)

The original `SELFCONNECT_RUNTIME_DESIGN.md` was authored in a claude.ai
conversation and was not among the files present on this machine (searched
Downloads, Desktop, Documents, Owner's Inbox, and all rblake2320 GitHub
repos; only `scr-phase1.zip` was delivered). Per rule 10 (choose the
stricter option and continue), the design doc was reconstructed from the
master prompt's phase plan, the Phase 1 README's module→section mapping, and
the Phase 1 code, whose interfaces are treated as normative. The
reconstruction is labeled as such in the doc header. If the original
document is provided later, it supersedes the reconstruction and any
divergence found is treated as P0.

## ADR-002 — POSIX memory/isolation via rlimits + setsid, not cgroups-v2 (2026-08-31)

Design §3.6 says "cgroups-v2 where available else rlimits + setsid." cgroups-v2
delegation requires either root or a pre-delegated user slice, which a
customer install cannot assume. Phase 2 implements the stated fallback only:
`RLIMIT_AS` for the memory cap and `start_new_session` + `killpg` for
process-tree reaping. cgroups-v2 (when a delegated slice exists) is deferred
to Phase 9's hardening matrix. On Windows the Job Object provides both the
memory cap and tree kill natively, so there is no equivalent gap there.

## ADR-003 — Job Object assigned immediately after spawn, not via CREATE_SUSPENDED (2026-08-31)

On Windows the worker is assigned to its Job Object in the few milliseconds
after `Popen` returns, rather than spawning suspended and assigning before
the first instruction. The stricter CREATE_SUSPENDED approach needs raw
CreateProcess handling that subprocess.Popen does not expose without
reimplementing process creation. The residual window is not attacker-
controllable in Phase 2 scope: the worker is our own code (`scr.worker`) and
`proc_exec` only launches operator-allowlisted binaries, so nothing hostile
runs in that window. Revisit with a CREATE_SUSPENDED spawn path in the
Phase 9 pen-review of the sandbox.

## ADR-004 — Policy tightening scope: tools + net_hosts in Phase 3 (2026-08-31)

Design §3.3 requires admin policy to tighten a manifest by intersection only,
never widening. Phase 3 implements tightening for `tools` and `net_hosts`,
which is sufficient to prove the two behaviors the design and tests demand:
intersection narrows authority, and any attempt to name a tool/host absent
from the base manifest is a `PolicyError` (rejected, not silently granted).
Root and exec-rule tightening follow the identical containment logic already
present in `capability._roots_contained` / `attenuate`; wiring them through
the policy layer is deferred to Phase 9 hardening to keep the Phase 3 surface
small. Recorded as an explicit deferral, not a silent gap. New dependency
`PyYAML==6.0.2` (safe_load only) because policy files are YAML per the design.

## ADR-005 — Ed25519 via `cryptography`; loader fail-closed (2026-08-31)

Design §3.4 mandates Ed25519 detached signatures over the package Merkle
root. We use `cryptography==50.0.1` (constant-time verify) rather than any
hand-rolled implementation. Trust is by key pinning in a deny-by-default
`Keystore`; a revocation list is honored only when its own signature verifies
against a trusted key, so a forged revocation list cannot deny-of-service a
good package. The loader is fail-closed throughout: a malformed zip,
unparseable manifest, missing signature, hash mismatch, root mismatch, bad
signature, untrusted key, or valid revocation each returns a rejection with a
localized reason — there is no code path where an error yields a pass. The
`scr/signer.py` module is the only place private-key signing lives and is
never imported by the loader or (future) service, keeping the publisher
capability out of the customer installer.

## ADR-006 — Evidence verifier is stdlib-only and embedded in the bundle (2026-08-31)

The finish line requires an offline, self-verifiable ledger. Rather than make
verification depend on an installed SCR, the verifier (`_evidence_verifier`)
imports nothing beyond hashlib/hmac/json and is embedded verbatim into every
exported `.scevidence` bundle as `verify.py`. An auditor with only Python
stdlib runs `python verify.py bundle.json --key <hex>` and gets a yes/no. To
avoid claim/code divergence between the standalone and SCR-side paths, both
call the same module and the export reads that module's own source to embed —
there is exactly one verifier. Events are stored in the bundle as their exact
canonical strings (as the ledger persisted them), so verification never
re-serializes and cannot drift across json versions. The subprocess test runs
the embedded verifier in an isolated cwd with `-S` (no site) to prove the
"nothing else installed" guarantee rather than asserting it.

## ADR-007 — FastAPI service; SQLite connection shared across threads (2026-08-31)

Design §3.1 mandates a FastAPI REST + WebSocket service. FastAPI dispatches
synchronous routes on a threadpool, so the SQLite connection is opened with
`check_same_thread=False`; WAL mode + `synchronous=FULL` + `busy_timeout`
keep this safe for the single-tenant self-hosted service (SQLite serializes
access internally). The service binds loopback-only by default and a
bind-guard refuses any non-loopback address unless both TLS and auth are
configured. New dependencies fastapi/uvicorn/httpx/websockets are all
mandated by the service surface. Tests use Starlette's in-process TestClient
(no bound port) for hermetic, Windows-safe coverage; the loopback bind
refusal is asserted directly against the guard function. Resume after an
approval gate re-enters the model loop for the next turn — correct for a real
adapter that responds to the tool result in the conversation; the mock in the
service test is made conversation-aware to model that faithfully rather than
replaying its script.

## ADR-008 — Vault uses classic DPAPI now; installers scaffolded, build OPEN (2026-08-31)

Design §3.2/§7 name DPAPI-NG for the Windows vault. Classic DPAPI
(`CryptProtectData`/`CryptUnprotectData`, user scope) is implemented and
tested for real on this Windows box — the round-trip works and the on-disk
blob provably excludes the plaintext. DPAPI-NG (NCrypt with protection
descriptors, machine-scoped sharing) is the stricter target and is deferred to
Phase 9 hardening; recorded as a deferral, not a silent gap. The installers
(WiX MSI, winget, systemd, deb) are authored as real scaffolds but their BUILD
and the "install on a clean box in <30 minutes" Definition-of-Done item are
marked OPEN in STATUS — the WiX toolset is not present in this environment and
claiming a working MSI without building and running it on a clean box would
violate rule 3 (no claim/code divergence). The tested Phase 7 core adds no new
runtime dependency; `keyring` is an optional POSIX-only extra.

## ADR-009 — SigV4 implemented directly, not via boto3; metrics off by default (2026-08-31)

The Bedrock adapter implements AWS SigV4 signing with stdlib hmac/hashlib
rather than pulling in `boto3`/`botocore` (a large transitive dependency
tree). Correctness is anchored to AWS's published SigV4 example vector: the
`derive_signing_key` output is asserted byte-for-byte against the documented
value, so the signing core is proven without a live AWS call. Backup
encryption reuses the already-present `cryptography` (AES-256-GCM), so Phase 8
adds zero dependencies. Prometheus metrics are off by default per design §3.8;
the registry is inert until explicitly enabled, so nothing is exposed on a
default install. The NIST 800-171 mapping is an internal indicative draft, not
an assessment, and lists its gaps rather than overclaiming coverage.

## ADR-010 — Phase 9 hardening: tests + review, no new code paths (2026-08-31)

Hardening adds coverage, not surface: Windows twins (TerminateProcess chaos,
directory-junction escape) of the POSIX-skipped tests; disk-full, clock-jump,
lock-contention, and upgrade-path chaos; and a written pen-style review
(`docs/PEN_REVIEW.md`) of the capability kernel and sandbox. The review found
no exploitable gap lacking a control; two by-design limitations
(allowlisted-exec trust, the Windows job-assignment micro-window) are
documented with compensating controls rather than papered over. The MSI/
clean-box install remains OPEN in STATUS because it cannot be honestly
verified in this environment — labeling it OPEN is the rule-3 obligation, not
a failure to hide.

## ADR-011 — Original design doc placed; supersedes the reconstruction (2026-08-31)

The authoritative original design document ("SelfConnect Runtime — Production
Design Document", author Ron Blake) was delivered and now lives at
`docs/SELFCONNECT_RUNTIME_DESIGN.md`, replacing the ADR-001 reconstruction.
ADR-001 is retained above as history. The original is richer than the
reconstruction: it carries goals G1–G8 with acceptance bars, a §9 test table
summing to ~845 (targets labeled "approx", tests-per-claim justification rule
applies), and named features the reconstruction lacked (parallel-safe tool
execution, summarization-on-overflow, deterministic replay, classification
ceilings, parent-revocation chain invalidation, stale-lock detection via
PID+boot-id+heartbeat, manifest semver/deps/min-runtime, hot reload, schema
migrations, SBOM/signed artifacts). `docs/DESIGN_GAP_ANALYSIS.md` is
re-reconciled against it and drives the closure plan, sequenced by goal impact
(G3/G5 first) per the owner's directive. No prior claim was invalidated by the
original — the implemented core matches it; the gaps are unbuilt features and
under-target coverage, each labeled.
