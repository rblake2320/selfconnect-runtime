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
| 7 | Vault, config, CLI, installers, updater, licensing | not started | — |
| 8 | Remaining adapters + ops surface | not started | — |
| 9 | Hardening + full matrix | not started | — |

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

## Dependencies

- `pytest==8.3.4` (dev) — test runner; industry standard, no runtime footprint.
- `pyyaml==6.0.2` — policy files are YAML per design §3.3; safe_load only.
- `cryptography==50.0.1` — Ed25519 package signing per design §3.4;
  constant-time verify, no hand-rolled crypto.
- `fastapi==0.115.6` — REST + WebSocket service per design §3.1.
- `uvicorn==0.34.0` — ASGI server for the installed service.
- `httpx==0.28.1` — TestClient + the REST client the `scr` CLI uses (Phase 7).
- `websockets==14.1` — WebSocket transport for run-event streaming.
- Phase 5 added **zero** dependencies (evidence path is stdlib-only by design,
  so bundles verify with nothing installed).
- Phase 2 added **zero** runtime dependencies (stdlib only: subprocess,
  ctypes Job Objects, resource, threading, queue, urllib, json).

## Known gaps / notes

- Design doc is a reconstruction (docs/DECISIONS.md ADR-001).
- Pre-commit secret scan (rule 7) lands with the vault in Phase 7; repo is
  private in the interim and holds no secrets (audited: no keys/tokens in
  tracked files).
