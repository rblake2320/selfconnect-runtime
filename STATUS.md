# STATUS — SelfConnect Runtime

Honest per-phase state. Claims here must not exceed what tests prove.
Last updated: 2026-08-31.

| Phase | Scope | State | Tests |
|---|---|---|---|
| 1 | Kernel, recovery, capability core, ledger, gateway, atomic, locks | ✅ complete | 69 (66 pass + 3 POSIX-skip on Windows) |
| 2 | Sandboxed tool execution + MCP client host | ✅ complete | +25 (94 cumulative; 89 pass + 5 platform-skip on Windows) |
| 3 | Capability kernel completion | ✅ complete | +13 (107 cumulative; 102 pass + 5 platform-skip on Windows) |
| 4 | Package format, signing, loader | ✅ complete | +26 (133 cumulative; 128 pass + 5 platform-skip on Windows) |
| 5 | Ledger completion + evidence export | ✅ complete | +6 (139 cumulative; 134 pass + 5 platform-skip on Windows) |
| 6 | Service, API, sessions, orchestration | ✅ complete | +22 (161 cumulative; 155 pass + 6 platform-skip on Windows) |
| 7 | Vault, config, CLI, updater, licensing (installers scaffolded) | ⚠ core complete; installer BUILD open | +27 (188 cumulative; 182 pass + 6 platform-skip on Windows) |
| 8 | Remaining adapters + ops surface | ✅ complete | +21 (209 cumulative; 203 pass + 6 platform-skip on Windows) |
| 9 | Hardening + full matrix | ✅ complete | +6 (215 cumulative; 209 pass + 6 platform-skip on Windows) |

## Design reconciliation & goal-impact closure (2026-08-31, post-Phase-9)

- **Original design doc placed** at `docs/SELFCONNECT_RUNTIME_DESIGN.md`
  (Production Design Document, author Ron Blake; ADR-011). It supersedes the
  reconstruction and is authoritative. `docs/DESIGN_GAP_ANALYSIS.md` is
  reconciled against it: goal-impact map (G1–G8), §9 coverage map (current vs
  the ~845 target), and 20 new divergences the original surfaces.
- **Closed, sequenced by goal impact (owner directive: G3/G5 first):**
  - **G3 — verify-at-execution** (was a G3 violation). `scr/registry.py`
    installs verified packages and re-verifies the STORED package at every
    session start; tamper-on-disk / post-install revocation → run refused
    before touching the model or a tool. `tests/test_registry.py` (+5).
  - **G5 — real cancel** (was a status-flip). Sandbox tracks live worker trees
    + `kill_all()`; kernel gains cooperative `cancel_check`; session cancel
    kills the in-flight tree AND stops the loop — a grandchild never orphans a
    cancel. `tests/test_cancel.py` (+3).
  - **G1 — CLI verbs.** Added `package install/list`, `run`, `session
    list/resume/export`, `model test`, `backup/restore` (`scr/cli.py` +
    `scr/model_factory.py`). `tests/test_cli_verbs.py` (+4) incl. a real
    `scr run` against a local Ollama-shaped stub.
  - **§5 install story E2E** (steps 2–6) scripted in
    `tests/test_e2e_install_story.py` (+1): init→model add→package
    install→run→export→verify. Offline via stub; **live via `SCR_OLLAMA_URL`**
    (this is also the Ollama closure harness). `docs/CLEAN_BOX_TEST.md` carries
    the manual VM + MSI-build steps.
  - **C17 — full `scr doctor`** (design §3.8): DB integrity, disk headroom,
    installed-package tamper detection (self-consistent signature), lock
    health, models, clock; exits non-zero on any FAIL. `tests/test_doctor.py`.
  - Coverage: §3.2 shared conformance corpus (`tests/test_conformance.py`, +11),
    the newline-injected-exec-arg denial (capability regression, +1), and
    OllamaAdapter server-error surfacing (+1, exposed by the live run).
  - Suite now **247 tests (241 pass + 6 skip)**, up from 230.
- **Still OPEN (owner-side inputs / environment):**
  - **MSI build + clean-box step 1** — WiX/dotnet not usable here (`wix`
    absent, `dotnet --version` errors). Steps 2–6 automated; step 1 manual
    procedure documented. Also needs a thin `scr-service.exe` entry.
  - **Live Ollama self-test — ✅ CLOSED (2026-08-31).** Ran the §5 install-story
    E2E AND the selfconnect-enterprise package self-tests LIVE against the DGX
    Spark Ollama (`http://192.168.12.220:11434`, model **gemma3:latest**, Ollama
    0.24.0 / GB10). Full chain green (19.6s): init → model add → live model test
    → package install → live run → sealed export → offline verify → VERIFIED;
    both package self-tests PASS. (Spark RAM was first freed from vLLM
    contention — see docs/CONTENT_MIGRATION.md.) G2 "customer brings the model"
    now has a live-Ollama proof, not just the offline contract corpus.
  - **§9 ~845 coverage** — at ~244; the large remainder is (a) unbuilt
    features (parallel-safe exec, summarization-on-overflow, deterministic
    replay, schema migrations, stale-lock detection, classification ceilings,
    parent-revocation chain invalidation — see DESIGN_GAP_ANALYSIS §C), (b)
    live-model conformance rows (Ollama), (c) installer/E2E rows (MSI). Each
    row justified in the gap analysis; sequenced by goal impact next.

## §9 backlog closure (goal-impact order, gated commits)

Sequenced per the owner directive; one gated commit per item with its
adversarial suite. ~845 is a target not a quota — each test proves a
currently-unproven claim.

- **P0.1 — Schema migrations + pre-migration snapshot + auto-restore (§6).**
  `scr/migrations.py`: forward-only migrations keyed on `PRAGMA user_version`;
  each runs in a transaction AND behind a SQLite-backup snapshot; a failure
  rolls back AND restores from the snapshot, so a partial/non-transactional
  migration can never corrupt a customer DB. `tests/test_migrations.py` (+7):
  ordered apply + version advance, idempotent re-run, forward-only (no
  downgrade), **failed migration auto-restores and holds the version**,
  snapshot-restore primitive reverts an auto-committed change, duplicate
  version rejected. Closes gap C12 (G5 crash-safety of the updater path).
- **P0.2 — Stale-lock detection (PID + boot-id + heartbeat) (§3.5).**
  `scr/locks.py` gains a metadata sidecar (never OS-locked) recording pid,
  boot-id, host, heartbeat; `probe()` classifies free / live /
  stale_other_boot / stale_heartbeat; `break_stale()` reclaims a dead/hung
  lock but refuses a live one; `heartbeat()` keeps a long holder live.
  `tests/test_lock_staleness.py` (+7): live-while-held, heartbeat refresh,
  **hung holder (OS lock held + stale heartbeat) detected as stale**,
  **previous-boot leftover is reclaimable so a restart isn't blocked**,
  break_stale refuses a live lock / clears a dead one. Existing OS-lock
  contention + death-release tests unchanged. Closes C9 (G5).
  - **Correction (researched, not assumed):** the Windows `boot_id` derived
    `GetTickCount64` via ctypes with the default `restype` (c_int), which
    truncates the 64-bit tick past ~49 days uptime. Confirmed against the
    ctypes docs and fixed to `restype = c_uint64`; verified empirically on-box.
- **P0.3 — Parallel-safe tool execution (§3.1).** `ToolSpec.parallel_safe`
  (default False); a run of consecutive parallel-safe + idempotent + granted +
  non-approval-gated calls executes its tool fns concurrently
  (ThreadPoolExecutor), while ALL store writes (journal/ledger/idempotency)
  stay on the main thread — the single serialization point, so no races. The
  fns run before the tight sequential journal, preserving the
  single-dangling-intent recovery model. Native fs_read/fs_list/http_get are
  parallel_safe; fs_write/proc_exec are not. `tests/test_parallel_exec.py`
  (+4), **real work, no fakes**: 4 concurrent `http_get` through real sandbox
  worker subprocesses against a real ThreadingHTTPServer finish well under the
  sequential floor (proves real parallelism); 6 concurrent real-file reads
  leave the hash-chained ledger intact with matched EXEC_INTENT/EXEC_DONE
  pairs (no store corruption); a non-parallel-safe write breaks the batch and
  runs sequentially; a lone parallel-safe call is not batched. Closes C1 (G5).
- **P1.4 — Parent-revocation chain invalidation + classification ceilings
  (§3.3).** IP-sensitive (MELD-gated): the SEMANTICS are implemented and
  tested; the encoding/wire format is intentionally undocumented anywhere in
  docs/. Classification: `CapabilityManifest.classification_ceiling` +
  `check_classification` (deny-by-default, unknown level rejected);
  `ToolSpec.classification`; the kernel folds an over-ceiling call as a denial
  and never executes it; attenuation lowers the ceiling to the more
  restrictive. Revocation: `Team.revoke()` severs the delegation chain — a
  revoked node invalidates itself and all descendants (`effective_manifest`
  raises), siblings on other paths unaffected. `tests/
  test_classification_revocation.py` (+8): ceiling denies higher / allows
  within / unknown rejected / attenuation lowers / kernel folds denial;
  revoking a parent severs descendants, revoking a node spares siblings,
  revoking the root severs everything. Closes C7 + C8 (G6/G3).

## Content migration (SelfConnect → `.scpkg`)

- Done + tested: `packages/selfconnect-enterprise/` ported to `.scpkg` source
  (lead/worker agents, default policy with approval gates, MCP config, native
  tools, a skill, self-tests). `scripts/build_enterprise_pkg.py` builds + signs
  + verifies it; `tests/test_content_migration.py` proves build→sign→load,
  tamper localization, and self-tests passing against a stand-in customer
  model. `docs/CONTENT_MIGRATION.md` flags every Claude-Code assumption
  rewritten against SCR's real tools/attenuation/policy.
- **OPEN:** the design's "self-tests pass against **Ollama**" — Ollama was not
  reachable during this pass, so self-tests ran against a scripted stand-in.
  Closing it needs a live Ollama run (`scr package verify …`).

## Definition of Done — evidence map

| DoD item (design §10) | Status | Evidence |
|---|---|---|
| Survives kill -9 / TerminateProcess mid-task; no corruption, no double-fire | ✅ | `test_chaos_kill.py` (POSIX), `test_hardening.py::test_terminateprocess_chaos_then_recover` (Windows), `test_recovery.py`, `test_sessions.py` |
| Never executes what a manifest doesn't permit (incl. MCP) | ✅ | `test_capability.py`, `test_tools_native.py`, `test_mcp_host.py::test_denied_capability_mcp_call_not_sent` |
| Any single-byte package tamper detected + localized; revoked versions refuse | ✅ | `test_loader.py`, `test_merkle.py`, `test_signing.py` |
| Evidence bundle verifies offline on a clean machine | ✅ | `test_evidence.py::test_offline_standalone_verifier_no_scr` |
| Service E2E: kill mid-run → restart → resume | ✅ | `test_sessions.py::test_kill_mid_run_then_recover_quarantines`, `test_service.py` |
| Package re-verified at each execution (G3) | ✅ | `test_registry.py` (tamper-on-disk / revocation after install → run refused) |
| Session cancel kills in-flight process tree, no orphan (G5) | ✅ | `test_cancel.py::test_cancel_kills_inflight_tree_no_orphan` |
| §5 install story steps 2–6 over the CLI | ✅ | `test_e2e_install_story.py` (offline + LIVE gemma3 on the Spark), `test_cli_verbs.py` (step 1 MSI = OPEN) |
| Package self-tests pass against Ollama (G2) | ✅ live | selfconnect-enterprise self-tests PASS live on gemma3 (docs/CONTENT_MIGRATION.md) |
| No secret ever on disk plaintext; redaction proven | ✅ | `test_vault.py`, `test_redaction.py`, `test_backup.py` |
| MSI installs on a clean Windows box; init→…→export under 30 min | ⛔ OPEN | installer scaffolds only; WiX build + clean-box run not performed here |
| License expiry → read-only evidence, never bricks | ✅ | `test_license.py::test_expired_license_grace_readonly` |
| STATUS claims ⊆ tested reality | ✅ | this file; OPEN items labeled OPEN |

**OPEN items carried forward** (not met, not hidden): installer BUILD +
clean-box install (needs WiX toolset, absent here); DPAPI-NG (classic DPAPI
used, ADR-008); live network conformance for all adapters (offline
build/parse tested); cgroups-v2 POSIX isolation (ADR-002); CREATE_SUSPENDED
Windows job spawn (ADR-003).

## Phase 9 — implemented / tested

- Windows TerminateProcess chaos twin + junction-escape twin of the POSIX
  SIGKILL/symlink tests; disk-full atomic-write chaos; backward clock-jump
  chaos (ledger + recovery unaffected); dual-instance lock contention storm
  (mutual exclusion holds); upgrade-path matrix (v1→v2→v3-rollback→v4).
- `docs/PEN_REVIEW.md`: adversarial review of the capability kernel + sandbox;
  every considered attack has a disposition backed by a named test; two
  by-design limitations documented with compensating controls. No exploitable
  finding lacked a control.

## Phase 1 — implemented / tested / deferred

- Implemented + tested: journaled agent loop with guards; crash-recovery
  classification (reissue/resume/safe_reissue/quarantine); deny-by-default
  capability manifests with resolved-path containment and monotonic
  attenuation; SHA-256 hash-chained ledger with HMAC seals and offline
  verify; SQLite WAL store; atomic writes; cross-process workspace lock;
  Mock/OpenAI-compat/Ollama/Anthropic adapters (build_request/parse tested
  offline).
- Deferred (by design, to later phases): live network conformance runs
  (Phase 8 CI), Windows twins of the 3 POSIX-skipped tests (Phase 9).

## Phase 2 — implemented / tested / deferred

- Implemented + tested: `scr/sandbox.py` worker subprocess execution with
  restricted-env allowlist, cwd jail, wall timeout with process-tree kill,
  memory cap (Windows Job Object / POSIX RLIMIT_AS), explicit-cancel tree
  reaping, and structured classification of worker timeout/crash/garbage
  output. `scr/worker.py` re-validating job entry (fs_read/write/list,
  http_get, proc_exec). `scr/tools_native.py` capability-checked-before-spawn
  native ToolSpecs with correct idempotency flags. `scr/mcp_host.py` stdio +
  streamable-HTTP MCP client host: initialize handshake, tools/list,
  tools/call, scoped-env-only, manifest-scoped tool projection
  (deny-by-default), crash→restart-with-backoff, and idempotent-defaults-false.
- Tested adversarially: timeout tree-kill (no orphan grandchild survives),
  cancel reaping, env-secret isolation (parent + MCP), traversal/symlink
  escape denied before spawn, non-allowlisted host/binary denied, MCP server
  crash-mid-stream recovery, denied-capability MCP call never sent, kernel
  E2E driving an MCP tool under enforcement.
- Deferred (by design): cgroups-v2 memory isolation on POSIX (ADR-002 —
  rlimits+setsid fallback used; cgroups in Phase 9); CREATE_SUSPENDED spawn
  to close the Windows job-assignment window (ADR-003 — Phase 9 pen review);
  Windows junction-escape twin of the POSIX symlink test (Phase 9 matrix).

## Phase 3 — implemented / tested / deferred

- Implemented + tested: `scr/policy.py` — YAML policy load; `require_approval`
  rules matching by tool name and by argument regex; admin `tighten`
  (intersection only) with `PolicyError` on any widening attempt.
  `scr/kernel.py` extended (Phase 1 loop refactored, not rewritten) with a
  journaled `AWAITING_APPROVAL` pause storing the remaining pending calls,
  resumable `resume()`, `approve()`/`deny()` writing ledgered events carrying
  approver identity, an `approval_id` binding each approval to the exact
  action (replay-safe), and a token budget governor summing real adapter
  token counts from the ledger against `Guards.max_total_tokens`.
- Tested adversarially: approval-required call pauses without executing;
  approve→resume executes exactly once; deny→resume folds a denial and the
  tool never runs; a wrong/forged approval_id does not authorize; crash
  during approval wait (fresh Store over the same DB) recovers to the same
  gate; approval/denial are ledger events with approver identity and the
  chain still verifies; token budget governor stops a run on real counts.
- Deferred (ADR-004): root/exec-rule tightening (Phase 3 tightens tools +
  net_hosts — enough to prove intersection-only + widening rejection);
  full root/exec tightening in Phase 9 hardening.

## Phase 4 — implemented / tested / deferred

- Implemented + tested: `scr/merkle.py` domain-separated SHA-256 Merkle root;
  `scr/signing.py` Ed25519 sign/verify, `key_id`, deny-by-default `Keystore`
  (publisher pin + customer keys), signed `RevocationList` honored only when
  itself signed by a trusted key; `scr/package.py` `.scpkg` build/read
  (path-traversal-safe in-memory member reads); `scr/signer.py` publisher
  signer entry (`python -m scr.signer`, never shipped to customers);
  `scr/loader.py` fail-closed `verify_package` (hash→manifest→root→signature→
  pinning→revocation, each failure localized) and a `run_selftests` runner
  executing `tests/*.yaml` through the kernel against the configured model.
- Tested adversarially: valid load; unsigned rejected; untrusted/wrong key
  rejected; single-leaf byte tamper rejected AND localized to the file;
  manifest/content mismatch (extra/missing/wrong-hash) rejected; downgrade to
  a revoked version rejected; a revocation list from an untrusted key does NOT
  brick a good package (fail closed on trust); self-tests pass/fail correctly
  and refuse to run on an unverified package.
- Deferred: loader wiring at session start (Phase 6 service); full CLI verbs
  `scr package install|verify` (Phase 7 — functions exist now).

## Phase 5 — implemented / tested / deferred

- Implemented + tested: `scr/_evidence_verifier.py` — pure-stdlib
  (hashlib/hmac/json) hash-chain + seal verifier, the single source of truth.
  `scr/evidence.py` — `export_bundle` writes a self-verifying `.scevidence`
  zip (bundle.json + bundle.hmac + an embedded copy of `verify.py` + README);
  `verify_bundle` (SCR side) and `seal_on_close` (per-session seal). The
  embedded verifier is the exact `_evidence_verifier` source, so standalone
  and SCR-side verification cannot diverge.
- Tested adversarially: export→verify OK; wrong key fails both seals; event
  content tamper breaks the chain; bundle metadata mutation breaks the bundle
  seal; unsealed session reports session-seal n/a but still verifies chain +
  bundle; **offline proof** — the embedded `verify.py` run in a subprocess
  from an isolated cwd (scr not importable, `-S` no-site) verifies a good
  bundle (exit 0, "VERIFIED") and rejects a tampered one (exit 1, "TAMPERED").
- Deferred: sealing key sourced from the vault (Phase 7 — key is passed in
  now); `scr ledger export|verify` CLI verbs (Phase 7 — functions exist now).

## Phase 6 — implemented / tested / deferred

- Implemented + tested: `scr/rbac.py` deny-by-default role matrix
  (admin/operator/auditor/viewer); `scr/sessions.py` `SessionManager` +
  durable SQLite job queue with idempotency-key dedupe, cancel, and
  `recover_all()` that reclassifies crashed `running` jobs via kernel
  recovery; `scr/orchestration.py` team topology with per-edge
  `capability.attenuate`, depth limits, and a persisted inter-agent mailbox;
  `scr/service.py` FastAPI app (REST runs/jobs/approve/deny/ledger + WS event
  stream), Bearer-token auth, RBAC-guarded routes, and a loopback-only
  bind-guard that refuses non-loopback without TLS+auth.
- Tested adversarially: RBAC matrix (viewer can't run, auditor can't run,
  operator can't manage tokens, unknown token/role denied); idempotent
  enqueue runs once and returns the same job; **kill mid-run then
  `recover_all()` quarantines** a non-idempotent job (no double-fire);
  delegation attenuates per edge and grandchild ⊆ child ⊆ parent; depth limit
  enforced; over REST — auth required, RBAC per route, idempotent run,
  ledger-read role split, WS streams events, and the approval gate
  (run→awaiting_approval→approve→resume→completed) runs the tool exactly once.
- Deferred: TLS termination + real-port serving config (Phase 7 installer
  wires uvicorn); WS live-tailing during a long run (current WS replays the
  journaled events for a job — sufficient and tested); Windows named-pipe
  transport (Phase 9 matrix).

## Phase 7 — implemented / tested / deferred

- Implemented + tested: `scr/vault.py` DPAPI-backed credential vault
  (Windows, real DPAPI round-trip tested here; ciphertext-at-rest verified;
  POSIX keyring backend skip-marked); `scr/redaction.py` log-redaction filter
  (registered secrets + key-shaped patterns scrubbed, non-secrets pass);
  `scr/license.py` offline Ed25519 licenses (valid / expired→grace-read-only /
  invalid); `scr/config.py` layered config storing vault refs not secrets;
  `scr/updater.py` staged update with health-probe + auto-rollback;
  `scr/cli.py` `scr` CLI (init, model add/list, package verify, ledger
  export/verify, license status, doctor) — all exercised via `main()`.
- Tested adversarially: vault blob on disk never contains plaintext; expired
  license degrades to read-only evidence (never bricks); tampered/wrong-key
  license rejected; updater rolls back on a failing or throwing health probe
  (never switches to a bad build); CLI package verify rejects an untrusted
  package.
- **OPEN (honestly not verified in this environment):** the MSI/winget/deb
  BUILD and the "installs on a clean Windows box in <30 min" Definition-of-Done
  item. Installer manifests (`installers/windows/scr.wxs`, `winget.yaml`,
  `installers/linux/scr.service`, `debian/control`) are authored scaffolds;
  building them needs the WiX toolset (absent here) and a clean-box run. Also
  deferred: DPAPI-NG/NCrypt (ADR-008 — classic DPAPI used and tested now);
  first-run `scr model add` LIVE smoke test against a real endpoint (needs a
  configured model); `scr run/service` verbs.

## Phase 8 — implemented / tested / deferred

- Implemented + tested: `scr/adapters_cloud.py` `BedrockAdapter` (AWS SigV4,
  signing-key derivation checked against AWS's published vector) and
  `AzureOpenAIAdapter`, both on the gateway contract; `scr/resilience.py`
  circuit breaker (closed→open→half-open→closed, injectable clock) +
  `FallbackChain`; `scr/backup.py` AES-256-GCM encrypted snapshot with atomic
  restore; `scr/observability.py` JSON logging with a correlation-id contextvar
  and an off-by-default Prometheus metrics registry. Internal docs:
  ADMIN_GUIDE, SECURITY_OVERVIEW, NIST_800-171_mapping (draft).
- Tested adversarially: SigV4 vector match; deterministic signed Bedrock
  request; breaker opens/half-opens/closes and the chain skips open breakers,
  raises when all down, and reuses a recovered primary after cooldown;
  backup→restore round-trip; wrong key + single-byte tamper fail (GCM tag);
  restore is atomic (no partial home on failure); metrics inert when disabled;
  JSON logs carry the correlation id and redact secrets.
- Deferred: live network conformance for the cloud adapters (Phase 9 CI with
  real endpoints); `scr backup`/`doctor` CLI verbs wired to the service
  (functions exist; doctor CLI present).

## Dependencies

- `pytest==8.3.4` (dev) — test runner; industry standard, no runtime footprint.
- `pyyaml==6.0.2` — policy files are YAML per design §3.3; safe_load only.
- `cryptography==50.0.1` — Ed25519 package signing per design §3.4;
  constant-time verify, no hand-rolled crypto.
- `fastapi==0.115.6` — REST + WebSocket service per design §3.1.
- `uvicorn==0.34.0` — ASGI server for the installed service.
- `httpx==0.28.1` — TestClient + the REST client the `scr` CLI uses (Phase 7).
- `websockets==14.1` — WebSocket transport for run-event streaming.
- Phase 7 tested core added **zero** dependencies (DPAPI via ctypes; license
  via existing `cryptography`). `keyring` is an OPTIONAL POSIX-only extra.
- Phase 8 added **zero** dependencies (SigV4 via stdlib hmac/hashlib — no
  boto3; AES-GCM via existing `cryptography`).
- Phase 5 added **zero** dependencies (evidence path is stdlib-only by design,
  so bundles verify with nothing installed).
- Phase 2 added **zero** runtime dependencies (stdlib only: subprocess,
  ctypes Job Objects, resource, threading, queue, urllib, json).

## Known gaps / notes

- Design doc is a reconstruction (docs/DECISIONS.md ADR-001).
- Pre-commit secret scan (rule 7) lands with the vault in Phase 7; repo is
  private in the interim and holds no secrets (audited: no keys/tokens in
  tracked files).
