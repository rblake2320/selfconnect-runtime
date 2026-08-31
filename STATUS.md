# STATUS — SelfConnect Runtime

Honest per-phase state. Claims here must not exceed what tests prove.
Last updated: 2026-08-31.

| Phase | Scope | State | Tests |
|---|---|---|---|
| 1 | Kernel, recovery, capability core, ledger, gateway, atomic, locks | ✅ complete | 69 (66 pass + 3 POSIX-skip on Windows) |
| 2 | Sandboxed tool execution + MCP client host | ✅ complete | +25 (94 cumulative; 89 pass + 5 platform-skip on Windows) |
| 3 | Capability kernel completion | not started | — |
| 4 | Package format, signing, loader | not started | — |
| 5 | Ledger completion + evidence export | not started | — |
| 6 | Service, API, sessions, orchestration | not started | — |
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

## Dependencies

- `pytest==8.3.4` (dev) — test runner; industry standard, no runtime footprint.
- Phase 2 added **zero** runtime dependencies (stdlib only: subprocess,
  ctypes Job Objects, resource, threading, queue, urllib, json).

## Known gaps / notes

- Design doc is a reconstruction (docs/DECISIONS.md ADR-001).
- Pre-commit secret scan (rule 7) lands with the vault in Phase 7; repo is
  private in the interim and holds no secrets (audited: no keys/tokens in
  tracked files).
