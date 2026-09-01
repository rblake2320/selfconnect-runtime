# STATUS — SelfConnect Runtime

Honest per-phase state. Claims here must not exceed what tests prove.
Last updated: 2026-09-01.

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
- **P1.5 — Summarization-on-overflow (§3.1).** When the assembled context
  estimate exceeds `Guards.summarize_at_tokens`, the kernel compacts older
  messages into one bounded extractive summary and keeps the last
  `summarize_keep_recent` verbatim — so a long session degrades gracefully
  instead of hitting the hard budget stop. Crucially the compaction is a VIEW:
  the store and ledger keep the complete history (evidence) untouched.
  `tests/test_summarization.py` (+3): overflow compacts the model's context
  (system + SUMMARY + recent, fewer than the full history) yet the run
  completes and the summary preserves an early-message trace; the full history
  remains in the store; no compaction below threshold. Closes C2 (G-graceful).
- **P2.6 — Ops trio (§7, §3.8).**
  - **Seat enforcement (§7):** `scr/seats.py` `SeatManager` + `seat_holders`
    table; distinct concurrent holders bounded to the license seat count;
    re-acquire is free, release frees a seat, driven end-to-end by
    `License.seats`. `tests/test_seats.py` (+5).
  - **/metrics off-by-default (§3.8):** service `/metrics` route returns
    Prometheus text only when the registry is enabled (404 otherwise); run
    counters increment on `/runs`. `tests/test_metrics_logrotate.py`.
  - **Log rotation (§3.8, §5):** `configure_rotating_json_logging`
    (RotatingFileHandler, size-capped, backups, redaction). Tests prove
    rotation creates backups and secrets are redacted. `test_metrics_logrotate.py`
    (+4 combined). Closes C14, C18, C20.

## §9 tail closure (goal-impact order)

- **C11 — Worker privilege reduction (§3.6).** Researched empirically on-box:
  a restricted + low-integrity token blocks system writes but also blocks the
  worker's own temp/jail writes and Medium-IL pipe replies (would break the
  worker IPC), so the worker **self-hardens at startup** instead —
  `scr/privdrop.py`: `AdjustTokenPrivileges(DisableAllPrivileges=TRUE)`
  (Windows) / `prctl(PR_SET_NO_NEW_PRIVS)` (Linux), wired into `scr.worker`.
  `tests/test_privdrop.py` (+5, real): worker token drops from N enabled
  privileges to 0 (in an isolated subprocess); a real proc_exec write to
  `C:\Windows` is OS-denied; the sandbox still serves a real jail write
  (hardening didn't break it); the capability kernel denies an out-of-jail
  read of a file the parent CAN read. **Residual (ADR-012, honest):** OS-level
  read isolation of arbitrary paths needs AppContainer (Win) / Landlock
  (Linux); the capability kernel is the enforced read/write jail today. Also
  fixed the `GetCurrentProcess`/`GetTokenInformation` ctypes restype pitfalls
  (researched, same class as the boot_id fix). Closes C11 (with C11b residual).
- **C10 — DPAPI-wrapped backup key (§3.5).** `backup.py` default mode now
  generates a random AES-256 key per backup and WRAPS it with DPAPI (reusing
  the vault primitives); only the wrapped blob is stored in the archive
  (format `SCRBAK02`), so the key is never on disk in plaintext and only the
  same user/machine can unwrap. An explicit-key mode remains for air-gapped
  cross-machine restore. CLI `--key` is now optional (DPAPI by default).
  `tests/test_backup_dpapi.py` (+4): wrapped round-trip with no key; wrapped
  key present + load-bearing (tamper fails); ciphertext tamper fails; explicit
  mode still works incl. wrong-key rejection. Existing explicit-key backup
  tests unchanged. Closes C10.
- **C15 — SBOM + signed release artifacts (§8, DoD).** `scr/release.py`:
  `parse_pinned_deps` reads the real pinned versions from pyproject; a
  CycloneDX 1.5 SBOM is generated with PURLs for every component (`scr release
  sbom <out>`; committed `sbom.json` has 7 components). Ed25519 detached
  artifact signing over the file's SHA-256 with the publisher key +
  fail-closed `verify_artifact` (digest match, signature, key pinning).
  `tests/test_release.py` (+6): SBOM valid + reflects real deps; sign/verify
  round-trip; tampered artifact, untrusted key, forged signature all rejected.
  Authenticode signing of the MSI is layered on later with the code-signing
  cert. Closes C15 (MSI Authenticode pending the cert).
- **C13 — Package shadow-install updates (§6).** `PackageRegistry.shadow_update`:
  verify → shadow-install (staged `.shadow` copy) → run the package self-tests
  against the model → promote (atomic replace + index update) only on pass; on
  verify failure or failing self-tests the currently-installed version stays
  active (rollback) and the shadow is discarded. `tests/test_shadow_update.py`
  (+3): passing self-tests promote to the new version; **failing self-tests
  roll back to the prior version with no leftover shadow**; an untrusted update
  is rejected and never promoted. Closes C13.
- **C6 — Manifest semver / deps / min-runtime + hot-reload (§3.4).**
  `scr/semver.py` (parse + constraint satisfies); `registry.check_requirements`
  validates a manifest's `runtime.min`, `requires` dependency constraints, and
  `model_requirements` (min_context, tool_calls) — deny-by-default on a bad
  constraint. `registry.reload(name)` hot-reloads by re-verifying the installed
  package and returning its fresh manifest without a restart, refusing tampered
  content. `tests/test_manifest_reqs.py` (+7). Closes C6.
- **C3 — Deterministic replay (§3.1).** `scr/replay.py` re-executes a session
  into a fresh store under the SAME session id and compares ledger heads (the
  store now accepts an explicit session id; idem keys bind to it). Equal heads
  = faithful reproduction; unequal = divergence surfaced. `tests/test_replay.py`
  (+4): replay reproduces an identical ledger chain; divergent model output
  yields a different head; replay matches a recorded session and a tampered
  script does not; same-session-id required. Closes C3.
- **C16 — Named-pipe transport (§3.7).** `scr/pipe_transport.py` (Windows-only,
  ctypes, no new deps): a real `\\.\pipe\<name>` request/response transport
  (byte mode, newline-framed JSON) — `NamedPipeServer(name, handler)` with
  `serve_one`/`serve_forever`/`start`/`stop`, and `pipe_client_request`.
  `tests/test_pipe_transport.py` (+4, real round-trips): echo; version
  dispatch; handler exception → structured error; serve_forever handles
  multiple sequential requests. POSIX equivalent is uvicorn's UDS. Owner-only
  SD noted as the multi-user hardening. Closes C16.

**Tail complete:** every §C divergence (C1–C20) is now closed or explicitly
residual (C11b OS-level read isolation → AppContainer/Landlock).

## Team dispatch — the last design-vs-implementation gap (§3.1, §3.7)

`scr/team.py`: `scr run <team-or-agent> "<task>"` (bare run stays single-agent;
unknown name lists available). Topology loaded from a package's agents/,
**widening / cycles / multiple-parents rejected at load**. A framework
`delegate` tool spawns each child per topology edge with `capability.attenuate`
enforced on every edge; each agent runs in its own linked session
(team_sessions), tasks/results flow through the mailbox, and the ledger records
one event per delegation edge (parent, child, effective-cap hash) + per mailbox
delivery. Team crash-safety: `team_recover` classifies each subagent session
individually (real process-kill → killed child quarantined, completed sibling
preserved, DB intact); `TeamRunner.cancel()` reaps every subagent process tree
(cancel storm → zero orphans). Evidence: `export_team_bundle` seals every
session and bundles the full delegation tree; `verify.py` verifies it offline.

- Suites: `test_team.py` (+9), `test_team_chaos.py` (+2 real kills),
  `test_team_evidence.py` (+3), `test_content_migration.py` rewritten to run a
  REAL 3-agent team self-test. **339 tests (332 pass + 7 skip)** incl.
  delegation policy (see below).
- **Model tool-call fidelity (real finding):** the `delegate` tool requires a
  tool-capable model. **gemma3 → "does not support tools" (HTTP 400)** — cannot
  drive a team; **qwen3.6:27b emits correct `delegate` tool calls** — it can.
  Single-agent `run` works on gemma3 (no tools sent).
- **Live team proof — ✅ (2026-09-01).** `scr run sce.security-team "Run the
  security review on C:/target/repo"` through the **frozen `scr.exe`** against
  the Spark (qwen3.6:27b): stopped_reason **completed**, final report began
  "security review complete". Delegation tree **depth 0→1, 3 sessions**
  (orchestrator `lead` + 2 delegated subagents); wall time **~1100 s (18.3 min)**
  — a slow 27B reasoning model. The exported **team evidence bundle VERIFIED**
  (per-session chains + seals + bundle seal, offline `verify.py`, full tree).
  Two honest notes: (a) the subagents correctly got **Access Denied** on
  `C:/target/repo` — it is outside their attenuated fs roots, a live proof of
  per-edge capability enforcement; (b) qwen delegated to `researcher` **twice**
  (the ledger records the real tree), diverging from its own prose narrative
  that claimed an auditor ran — the tamper-evident ledger over the model's
  words. First attempt failed with a per-call `TimeoutError` (300 s too short
  for the 27B thinking model) → fixed via `scr model add --timeout` (local
  default 600 s); the re-run succeeded.
- **Model tool-call requirement:** teams need a tool-capable model. gemma3
  cannot (400 "does not support tools"); qwen3.6:27b drives delegation.

## Delegation policy — runtime-enforced, ledger-provable (2026-09-01)

Motivation (owner finding): a prior qwen run printed an "Auditor Risk
Assessment" heading while the ledger showed **six researcher delegations and
zero auditor** — the model narrated work that never happened. Prompt guidance
cannot fix this; enforcement must be data-driven and provable from the ledger.

- **Feature.** Per-agent `delegation_policy` in `agents/*.yaml`, enforced by the
  kernel and ledgered on every decision: `required_children` (a `finalize_guard`
  refuses to finalize until each listed child completes — the run *cannot* report
  `completed` with required work missing), `max_delegations_per_child` (excess
  folded as denials), `no_redelegate_after_denial` (an all-denied child is not
  re-delegated the same task — the team analogue of the kernel cycle guard).
  Policy referencing an undeclared child is rejected at load.
  `tests/test_delegation_policy.py` (+5, adversarial); suite **339 (332 pass +
  7 skip)**. `selfconnect-enterprise` carries `required_children:
  [researcher, auditor]`, `max 2`, `no_redelegate`. Re-signed; frozen
  `scr.exe`/`scr-service.exe` + 43 MB MSI rebuilt with the policy code.
- **Harness caught its own operator error (unfaked evidence).** While wiring the
  enterprise self-test, a mis-driven researcher went all-denied and the
  `finalize_guard` correctly refused to let the run report completion —
  enforcement firing on real mis-driven input, not just in the happy-path test.

### Live proof — RUN A (auditor question), per the ledger not the prose

`scr run --workspace … sce.security-team "Run the security review …"` through
the frozen `scr.exe` against the Spark (qwen3.6:27b), ~28.5 min wall. Exported +
sealed, **team evidence bundle RESULT: VERIFIED** (per-session chains + seals +
bundle seal, offline `verify.py`). Verified delegation tree:

```
lead  (session 3e1bce3b, depth 0)   22 events
    auditor      (session 2fa7229f, depth 1)   4 events
    researcher   (session cbd92bce, depth 1)  14 events
    researcher   (session 1fa99f3a, depth 1)  17 events
```

- **AUDITOR RAN: YES** — a real `lead → auditor` edge and a completed auditor
  session, in a VERIFIED bundle. The required-children policy forced the auditor
  to actually run before finalize; the researcher delegation was capped at **2**
  (max_delegations_per_child held — no storm). This closes the
  "heading-with-no-auditor" finding, proven from the ledger.
- **Checks 2/3 (files read / substantive findings): NOT tested this run — and
  it was MY harness's fault, not the runtime's.** The live-run bash script
  passed `--workspace C:\dev\selfconnect-runtime` **unquoted**; bash stripped the
  backslashes to a drive-relative `C:devselfconnect-runtime`, which Windows
  `abspath` resolved to `C:\dev\selfconnect-runtime\devselfconnect-runtime` — a
  nonexistent dir. Proven deterministically offline (mangled input reproduces
  the exact observed path; the forward-slash form resolves to the real repo).
  So the researcher got Access Denied on everything and the review was empty —
  **but honestly empty**: the model reported it was blocked rather than
  fabricating findings. Harness fixed (quote + forward slashes); a python
  convenience-dump also crashed on an msys path (fixed) — irrelevant, the
  verified bundle's tree is the authoritative source.
- **RUN A runtime-side finding (owner, G6 fail-fast): `--workspace` accepted a
  nonexistent directory and the run proceeded to completion with zero file
  access.** Worse: the CLI's `os.makedirs(workspace/out, exist_ok=True)`
  silently *created* the mangled directory and ran 20+ minutes against the
  empty dir it had just made. A run against nothing is a customer support
  ticket, not a nicety. **Fixed:** an explicit `--workspace` is validated at
  CLI time — must exist, be a directory, and pass a real read probe
  (`os.listdir`, since `os.access` lies on Windows) — otherwise exit non-zero
  BEFORE any session is created, with the resolved absolute path in the error
  so shell path-mangling is visible instantly. Tests (+2, adversarial):
  nonexistent workspace → refused, resolved path in message, directory NOT
  created, zero sessions added; a file as workspace → refused. Suite **341
  (334 pass + 7 skip)**. The junk directory RUN A created was removed.
  Frozen `scr.exe`/MSI rebuild with this fix is pending RUN B's completion
  (Windows locks the running exe).
### Live proof — RUN B (valid workspace), per the ledger

Corrected `--workspace "C:/dev/selfconnect-runtime"`, same model/task. Sealed
bundle **RESULT: VERIFIED**; wall **772 s (~13 min)**; tree depth 0→1, 5
sessions. Ledger dump: `DELEGATIONS [researcher, researcher, auditor, auditor]`,
`COMPLETED_PER_LEDGER [auditor, researcher]`, `AUDITOR_RAN True`.

- **Policy held under a live retry storm:** both children capped at exactly 2
  (10+ `max_delegations_per_child` denials ledgered while qwen retried).
  Auditor ran again — required_children is now 2-for-2 live.
- **Check 2 STILL failed with a VALID workspace — root cause found, and it is
  a real runtime gap:** `${WORKSPACE}` was substituted only into capability
  manifest roots; **nothing ever told the model its granted paths.** The
  researcher blind-guessed container roots in RUN A and in RUN B returned
  **0 chars without attempting a single tool call**. Deny-by-default is
  unusable if the model cannot see what IS granted.
- **New loophole discovered (RUN B):** a child returning 0 chars **counted as
  completed** and satisfied `required_children` — a model can satisfy the
  policy empty-handed (`_child_all_denied` needs denied>0; doing nothing at
  all sailed through).
- **Provenance gap confirmed twice:** both VERIFIED bundles showed
  `package: {}` — the evidence could not answer "which signed package governed
  this run".
- **Prose-vs-ledger divergence, third occurrence:** RUN B's report blamed
  "worker crashes"; the ledger shows zero crashes, just empty returns. The
  pattern is systematic: the model narrates plausible causes for gaps in its
  knowledge. Only the ledger is evidence.

### Fixes from the RUN A/B findings (all tested; suite 345 = 338 pass + 7 skip)

- **Capability-grant context injection** (`_caps_context`): every team agent's
  system prompt now carries a block derived from its EFFECTIVE (attenuated)
  manifest — tools, readable/writable roots, net hosts — so the model is told
  exactly what it was granted and nothing more (data-driven, cannot overstate).
  `${WORKSPACE}` is also substituted in authored system prompts. Tests: the
  adapter provably receives the resolved workspace root.
- **`require_nonempty_result` policy rule:** an empty child result is ledgered
  `not_counted` and does not satisfy `required_children` (0 chars is a
  mechanical ledger fact, so it is enforceable). On in the enterprise package.
- **Run provenance in evidence:** the governing package (name, version,
  key_id, content SHA-256) is ledgered INSIDE the lead session's hash chain at
  run start and surfaced as the bundle's `package` field — the evidence now
  proves which signed package governed the run, tamper-evidently.
- **Build reproducibility (found while rebuilding):** the frozen-exe build
  was tribal knowledge → scripted (`installers/windows/build_frozen.sh`);
  `build_enterprise_pkg.py` generated a fresh keypair but never wrote
  `publisher_key.txt`, so a stale pin would silently reject each fresh build →
  the pin is now written next to the package. Frozen `scr.exe` smoke-tested:
  install-against-pin OK, doctor OK, **workspace fail-fast fires in the frozen
  artifact** (resolved path + raw argument, exit 1).
- **RUN C in flight** with all three fixes (new exe + re-signed package) to
  test checks 2/3: real file reads and file/line-specific findings.

## Installer / packaging closure (2026-08-31)

- **`scr-service.exe` — Windows Service host** (`scr/service_main.py`): console
  mode (uvicorn, testable) + a real ctypes SCM dispatcher (no pywin32) with
  graceful stop that closes the store and releases the workspace lock.
  `tests/test_service_host.py` (+2): real uvicorn serve on a live port →
  graceful shutdown releases the lock; loopback bind-guard.
- **Frozen artifacts** via PyInstaller (`pyinstaller==6.11.1`, build tool):
  `scr.exe` (19.7 MB) + `scr-service.exe` (24 MB), self-contained (no repo
  venv). Both exercised standalone; the **full §5 story ran LIVE through
  `scr.exe`** against the DGX Spark Ollama (gemma3): init → model add → live
  model test → package install → live run → sealed export → **VERIFIED**, and
  the embedded `verify.py` offline-verified → **VERIFIED**.
- **WiX v7 MSI** (`installers/windows/Package.wxs`, v4+ unified schema, `wix
  build`): installs the service (ServiceInstall/ServiceControl), the CLI on
  system PATH, and a terminal launcher stub. **Builds clean** to a valid 43 MB
  MSI. Unsigned (Authenticode residual). WiX v7 required accepting the OSMF
  EULA (`--acceptEula wix7`, per-invocation) — a FireGiant commercial
  maintenance-fee agreement; evaluate for production or pin WiX v4 to avoid it.
- **A real frozen-artifact bug was caught and fixed** (invisible in the venv
  tests): `evidence._load_verifier_source` read a `.py` from disk, which a
  PyInstaller exe doesn't ship — now bundles the verifier as package data and
  resource-loads it (with a source-tree fallback). This is exactly why the
  design tests installed artifacts, not the venv.
- **OPEN (human, elevated):** the `msiexec` install + service SCM lifecycle on
  a clean box — needs Administrator rights this environment lacks; scripted end
  to end in `docs/CLEAN_BOX_TEST.md` with the 30-minute budget and pass
  criteria. Plus: MSI Authenticode (pending cert), Electron terminal (ships
  separately).

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
| MSI installs on a clean Windows box; init→…→export under 30 min | ◑ engineering done; elevated run = human | MSI **builds** (`wix build`, WiX v7); frozen `scr.exe`/`scr-service.exe` run the **full §5 story LIVE** on the Spark → VERIFIED; the elevated `msiexec` install + service SCM lifecycle need admin this env lacks → literal script in `docs/CLEAN_BOX_TEST.md` |
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
