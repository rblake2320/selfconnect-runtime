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
