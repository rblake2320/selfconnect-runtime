# STATUS — SelfConnect Runtime

Honest per-phase state. Claims here must not exceed what tests prove.
Last updated: 2026-08-31.

| Phase | Scope | State | Tests |
|---|---|---|---|
| 1 | Kernel, recovery, capability core, ledger, gateway, atomic, locks | ✅ complete | 69 (66 pass + 3 POSIX-skip on Windows) |
| 2 | Sandboxed tool execution + MCP client host | ⏳ in progress | — |
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

## Dependencies

- `pytest==8.3.4` (dev) — test runner; industry standard, no runtime footprint.

## Known gaps / notes

- Design doc is a reconstruction (docs/DECISIONS.md ADR-001).
