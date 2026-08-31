# Design Gap Analysis — reconciled against the ORIGINAL

**Status:** the authoritative original design doc is now on disk
(`docs/SELFCONNECT_RUNTIME_DESIGN.md`, "SelfConnect Runtime — Production Design
Document", author Ron Blake). This document reconciles the implementation
against it. The earlier ADR-001 reconstruction is retained as history in
`docs/DECISIONS.md`; ADR-011 records the supersession.

Two things the original makes authoritative that the reconstruction lacked:
1. **Goals G1–G8 with acceptance bars** (§1) — closure is sequenced by these.
2. **§9 test table summing to ~845** (targets are labeled "approx"; the
   tests-per-claim justification rule applies — meet the target with real
   adversarial tests OR justify why fewer fully cover the claim).

Legend: **[MISSING]** · **[PARTIAL]** · **[UNTESTED]** · **[DIVERGENT]** · **OK** · **[CLOSED]** (done since this reconciliation).

**Closed since reconciliation (2026-08-31):** G3 verify-at-execution (C4),
G5 real process-tree cancel (C5), G1 CLI verbs (C19), §5 install-story E2E
(offline + **live gemma3 on the DGX Spark**), G2 live-Ollama proof + package
self-tests, shared conformance corpus, newline-exec capability regression,
`scr doctor` full check set (C17), OllamaAdapter server-error surfacing.
Then the prioritized §9 backlog, goal-impact order, one gated commit each:
**P0.1** schema migrations + snapshot/auto-restore (C12), **P0.2** stale-lock
detection PID+boot-id+heartbeat (C9, incl. a researched `GetTickCount64`
restype fix), **P0.3** parallel-safe tool exec (C1, real concurrent HTTP),
**P1.4** parent-revocation chain invalidation + classification ceilings
(C7+C8, MELD-gated — semantics only, no encoding in docs), **P1.5**
summarization-on-overflow (C2), **P2.6** seat enforcement (C14) + /metrics
off-by-default (C18) + log rotation (C20). Suite 230 → **285**.

**Tail now CLOSED** (goal-impact order): C11 worker privilege reduction
(privilege-drop; OS read-isolation → AppContainer residual C11b), C10 DPAPI-
wrapped backup key, C15 SBOM + signed release artifacts, C13 package
shadow-install updates, C6 manifest semver/deps/min-runtime + hot-reload,
C3 deterministic replay, C16 named-pipe transport. Suite → **318**.

**Still OPEN** (only these): the MSI build + clean-box step 1 (blocked on the
WiX/dotnet toolchain, owner-side); C11b OS-level read isolation (AppContainer/
Landlock — documented residual, capability kernel is the enforced read jail);
Authenticode signing of the MSI (pending the code-signing cert); reproducible-
build attestation (SBOM + artifact signing shipped; bit-reproducibility is a
build-infra task).

---

## A. Goal-impact map (drives closure order)

| Goal | Bar | State | Gaps blocking the bar |
|---|---|---|---|
| **G1** zero-harness install | one installer → working session | ⛔ | MSI build (blocked: WiX/dotnet), missing CLI verbs (`run`, `package install`, `session`) |
| **G2** customer brings model | 5 backends pass ONE conformance suite | ◑ | 5 adapters exist + a shared contract corpus (test_conformance) — but NOT the behavioral corpus §3.2 names (tool-call fidelity, parallel calls, streaming interruption, long-context, JSON-mode). Live parts need Ollama. |
| **G3** nothing executes unsigned | verified at load **AND at each execution** | ⛔ **P0** | loader never called at session start; no package install/registry; runtime path never re-verifies. |
| **G4** everything evidenced | offline-verifiable hash chain | ✅ | complete + tested (ledger, evidence bundle, offline verify). |
| **G5** crash-safe | kill at any point → resume or clean rollback, no double-fire | ◑ **P0** | recovery ✅; but **cancel is a status-flip, not a process-tree kill** (§3.6 "all child process trees terminated on session cancel"). |
| **G6** policy-gated | kernel mediates every tool call, deny-by-default, policies are data | ✅ (core) | HITL + attenuation + policy tighten ✅. Missing: classification ceilings; parent-revocation invalidates chain; policy directory loader. |
| **G7** installable & updatable | signed MSI/winget/.deb, delta updates + rollback | ◑ | updater ✅ (staged+rollback+offline); installers scaffolds only, build blocked; no SBOM/signed artifacts; no schema migrations. |
| **G8** auditable by a stranger | verify signatures + ledger + egress with no calls to us | ✅ (core) | signatures ✅, ledger offline ✅, `package verify` self-tests ✅; telemetry-off default ✅. Egress-claim doc pending. |

**Closure order (per owner directive, G-impact first):** G3 → G5 → G1(CLI verbs)
→ G2(conformance behaviors, live parts after Ollama) → G6/G7 remainder.

---

## B. §9 coverage map (current vs ~845 target)

| Suite (original §9) | Target≈ | Current | Under by | Closure note |
|---|---|---|---|---|
| Kernel unit | 180 | ~29 (`test_kernel`,`test_recovery`,`test_atomic`,`test_approval`) | ~151 | Real gaps: parallel-safe tool exec, summarization-on-overflow, cost-ceiling, deterministic-replay — behaviors NOT yet implemented, so tests can't exist until built. Track as feature gaps, not padding. |
| Adapter conformance | 300 (60×5) | ~28 (`test_gateway`,`test_adapters_cloud`,`test_conformance`) | ~272 | Contract corpus done; behavioral corpus (tool-call fidelity, parallel, streaming interruption, long-context, JSON-mode) partly needs a live model → **blocked on Ollama** for the live rows; offline rows expandable now. |
| Capability adversarial | 120 | ~37 (`test_capability`,`test_tools_native`,`test_policy`) | ~83 | Expandable now with real escapes (more ADS/reparse/UNC/8.3-shortname/arg-injection/net-bypass/attenuation-violation vectors). |
| Crash/chaos | 70 | ~11 (`test_chaos_kill`,`test_recovery`,`test_hardening`) | ~59 | Expandable: kill at EVERY journal state (not just one), fsync-fault at each artifact, disk-full at each writer, clock-jump variants. |
| Ledger adversarial | 40 | ~17 (`test_ledger`,`test_evidence`) | ~23 | Expandable: more splice/reorder/truncation/seal-forgery permutations. |
| Package security | 35 | ~26 (`test_loader`,`test_signing`,`test_merkle`,`test_package`) | ~9 | Near target; a few more downgrade/dep/semver/min-runtime cases close it. |
| Concurrency | 45 | ~5 (`test_locks`,`test_hardening` storm) | ~40 | Expandable: stale-lock (PID+boot-id+heartbeat) detection, parallel tool-exec races, cancel storm. Stale-lock detection is also a FEATURE gap (see §3.5). |
| E2E scenarios | 30 | ~15 (`test_service`,`test_sessions`,`test_content_migration`) | ~15 | Needs the §5 install→…→verify flow (CLI verbs + MSI); partly blocked. |
| Upgrade/rollback | 25 | ~10 (`test_updater`,`test_backup`) | ~15 | Expandable: version matrix, failed-migration auto-restore (migrations are a FEATURE gap). |
| **Total** | **~845** | **~230** | **~615** | Mix of real-test expansion (unblocked) + feature-build-then-test + Ollama/MSI-blocked rows. |

**Honest headline:** ~230/845. The shortfall is NOT padding-sized — it is (a)
real adversarial vectors not yet enumerated, (b) design features not yet built
(parallel exec, summarization, migrations, stale-lock, classification
ceilings), and (c) live-model/installer rows blocked on Ollama/WiX. Each row
above says which.

---

## C. New divergences the original surfaces (not in the reconstruction)

| # | Original requirement | Class | Where |
|---|---|---|---|
| C1 | Kernel executes tool calls **in parallel where declared safe** | [MISSING] | §3.1 "EXEC (parallel where declared safe)"; kernel is sequential |
| C2 | **Summarization-on-overflow** context management | [MISSING] | §3.1; kernel only STOPS on token-estimate, no summarize |
| C3 | Deterministic **replay mode** | [MISSING] | §3.1 "deterministic replay mode" |
| C4 | Package **re-verified at each execution** (not only install) | [MISSING] **G3** | §3.4/§1-G3 |
| C5 | **Real process-tree kill on cancel** | [PARTIAL] **G5** | §3.6; cancel is a status-flip |
| C6 | Manifest **semver + deps + min-runtime + model requirements**; **hot reload** | [MISSING] | §3.4 |
| C7 | **Classification ceilings** in capability kernel | [MISSING] | §3.3 |
| C8 | **Parent revocation invalidates the delegation chain** at runtime | [MISSING] | §3.3 (SEVER-aligned) |
| C9 | **Stale-lock detection** (PID + boot-id + heartbeat) | [DIVERGENT] | §3.5; impl relies on OS-release-on-death only |
| C10 | Backup key **wrapped by DPAPI/keyring** (not raw 32-byte in) | [PARTIAL] | §3.5 |
| C11 | Windows **restricted token**; Linux **no-new-privs + seccomp** | [MISSING] | §3.6 |
| C12 | **Schema migrations** forward-only + pre-migration snapshot + auto-restore | [MISSING] | §6 |
| C13 | Package update **shadow-install → self-test → promote** | [MISSING] | §6 |
| C14 | **Seat enforcement** in team mode | [MISSING] | §7 |
| C15 | **SBOM per release + reproducible build + signed artifacts** | [MISSING] | §8, DoD |
| C16 | Named-pipe transport + **named-pipe SD** on Windows | [MISSING] | §3.7, §8 |
| C17 | `scr doctor`: reachability, signatures, disk, **clock skew** | [PARTIAL] | §3.8; impl checks integrity+models only |
| C18 | Metrics **/metrics** endpoint (off by default) | [MISSING] | §3.8 |
| C19 | Missing CLI verbs: `run`, `package install/list`, `session list/resume/export`, `model test`, `backup/restore` | [MISSING] **G1** | §3.7 |
| C20 | Log **rotation** (size-capped, rotating) | [MISSING] | §3.8 |

Carried from the prior audit and still valid: loader-at-session-start (=C4),
fake cancel (=C5), missing CLI verbs (=C19), admin-override config layer,
`/metrics`, log rotation, HTTP-MCP restart/health loop.

---

## D. What is fully done and matches the original (no gap)

Kernel journaled loop + idempotency-classified recovery (G5 recovery half);
capability manifest deny-by-default + resolved-path containment (traversal/ADS/
symlink/reparse) + monotonic attenuation; Ed25519+Merkle signing, pinning,
signed revocation, tamper-localized fail-closed loader; hash-chained ledger +
HMAC seals + offline evidence bundle (G4, G8 core); SQLite WAL + atomic writes
+ cross-process lock; sandboxed workers (Job Objects/rlimit+setsid) + env
isolation + tree-kill-on-timeout; 5 adapters + contract conformance; FastAPI
service + RBAC + durable idempotent queue; DPAPI vault; offline Ed25519 license
with grace read-only; staged updater with rollback; JSON logs + redaction +
off-by-default metrics registry; encrypted backup/restore.

---

## E. Execution plan (this reconciliation pass onward, gated)

1. **G3 — verify-at-execution.** Package registry: install a verified package
   into the SCR home, and re-verify (signature + per-file tamper) at session
   start before any run. Refuse to run a tampered/unsigned/revoked installed
   package. Adversarial tests: install-then-tamper-on-disk → run refused;
   revoke → run refused.
2. **G5 — real cancel.** Track live sandbox process trees per job; cancel kills
   the tree and cooperatively stops the kernel between tool calls; in-flight
   non-idempotent tool → quarantined on recovery. Test: background run with a
   long grandchild → cancel → no orphan survives, job cancelled.
3. **G1 — CLI verbs.** `package install/list`, `run`, `session list/resume/
   export`, `model test`, `backup/restore`, `approve/deny`. Tests via `main()`.
4. **Coverage expansion** of the unblocked §9 rows (capability, crash, ledger,
   package, concurrency) with real vectors; justifications recorded for rows
   that are feature-blocked or live-model/installer-blocked.
5. Remaining features (C1–C3, C6–C18, C20) sequenced by goal impact in
   subsequent gated commits; each lands with its adversarial suite.
