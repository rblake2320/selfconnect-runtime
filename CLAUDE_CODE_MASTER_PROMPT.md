# MASTER PROMPT — SelfConnect Runtime, Phases 2–9

Copy everything below the line into Claude Code, launched from the repo root.

---

You are building the SelfConnect Runtime (SCR) into a complete, installable
product. This repo already contains:

- `docs/SELFCONNECT_RUNTIME_DESIGN.md` — the authoritative design. Read it fully before writing any code. Where this prompt and the design doc conflict, the design doc wins.
- `scr/` and `tests/` — Phase 1, complete: agent kernel, write-ahead journal, crash recovery, capability kernel, hash-chained ledger, model gateway (mock/openai-compat/ollama/anthropic), atomic writes, cross-process locks. 69 tests passing. Do NOT rewrite Phase 1 modules; extend them. Run `pytest tests/ -q` first and confirm green before anything else.

## Mission
Execute Phases 2 through 9 from design doc §10, in order, phase-gated.

## Non-negotiable rules

1. **Phase gates.** A phase is complete only when its full test suite passes locally. One git commit per completed phase, message `phase-N: <summary> — <X> tests passing (<total> cumulative)`. Never start phase N+1 with phase N red. Push after each gate.
2. **Test discipline.** Every phase ships adversarial tests, not just happy-path. Follow the pattern already in `tests/` (fault injection, SIGKILL/TerminateProcess chaos, escape attempts, forgery attempts). State test counts in each commit. A feature without tests does not exist.
3. **No claim/code divergence.** README and docs may only describe what is implemented and tested. Maintain a `STATUS.md` at repo root: per-phase table of implemented / tested / deferred. Update it in the same commit as the code. Treat any divergence as P0.
4. **Windows-first.** Primary target: Windows 11 / Server 2022 (PowerShell, CRLF-safe artifacts, DPAPI/CNG, Job Objects, msvcrt locks, named pipes). Linux parity via the same test suite with platform branches. When a test can only run on one OS, write both variants and skip-mark appropriately (pattern: `test_chaos_kill.py`).
5. **Crash-safety bars.** Atomic writes (`scr/atomic.py`) for every file artifact. Every new side-effecting operation gets a write-ahead journal record and a recovery classification (resume / safe_reissue / quarantine). Idempotency keys on anything re-executable.
6. **Deny-by-default.** Every new tool, MCP server, and network path is mediated by the capability kernel. No ambient authority ever reaches a worker or MCP subprocess.
7. **Secrets.** Credentials only via the vault (Phase 7). Never in config files, env-file examples, logs, test fixtures, or commits. Add a pre-commit secret scan.
8. **Dependencies.** Minimal, pinned in `pyproject.toml` with exact versions. Prefer stdlib. Justify each new dependency in one line in STATUS.md. Python 3.12.
9. **Repo hygiene.** This repo is PRIVATE and stays private (pre-disclosure firewall). Never add public-facing marketing text, benchmark publications, or protocol wire-format documentation. Internal docs only.
10. **When blocked.** If a design decision is genuinely ambiguous, choose the safer/stricter option, record it in `docs/DECISIONS.md` (ADR style, one paragraph each), and continue. Ask the human only if the choice is irreversible or touches money/legal.

## Phase plan (from design §10 — expand each against the design's section references)

**Phase 2 — Sandboxed tool execution + MCP client host** (design §3.6)
- Native tools (fs read/write/list, http, process exec) run in worker subprocesses: restricted env, working-dir jail via `capability.resolve_within`, timeouts, kill-on-cancel, process-tree reaping. Windows: Job Objects (memory/CPU caps, KILL_ON_JOB_CLOSE); Linux: cgroups-v2 where available else rlimits + setsid.
- MCP client host: launch/supervise stdio MCP servers and connect streamable-HTTP servers from declarative config; per-server capability scopes; health checks; restart w/ backoff; scoped env only. MCP tools appear to the kernel as ToolSpecs with `idempotent` declared in config (default false = quarantine-on-crash).
- Adversarial suite: sandbox escape attempts (traversal, symlink, env leakage, orphaned children after cancel), MCP server crash/restart, tool timeout enforcement, denied-capability MCP calls.

**Phase 3 — Capability kernel completion** (design §3.3)
- HITL approval gates: policy rules that mark action classes `require_approval`; kernel pauses (journaled `AWAITING_APPROVAL` state, resumable), approval/denial are ledger events with approver identity.
- Policy files (`policies/*.yaml`) loading + admin-tightening (intersection only, never widening). Budget governor wired to real token counts from adapters.
- Adversarial: approval bypass attempts, policy-widening attempts rejected, resume-after-approval crash recovery.

**Phase 4 — Package format, signing, loader** (design §3.4)
- `.scpkg` zip: manifest.json, agents/, skills/, tools/, mcp/, policies/, tests/, SIGNATURE. Per-file SHA-256 Merkle leaves → root; Ed25519 detached signature over root (use `cryptography` lib). Publisher key pinning + customer-added keys + signed revocation list.
- Loader: verify at install AND at session start; tamper localization in error messages. Package self-test runner (`scr package verify`) executes tests/*.yaml scenarios against the configured model.
- Signer CLI for the publisher side (`scr-sign` — separate entry point, never shipped in customer installer).
- Adversarial: unsigned, wrong-key, single-leaf tamper, downgrade to revoked version, manifest/content mismatch.

**Phase 5 — Ledger completion + evidence export** (design §3.5)
- Evidence bundle export: events + chain heads + seals + runtime/package versions, HMAC-sealed, single-file archive; `scr ledger verify <bundle>` works offline on a machine with nothing else installed.
- Sealing keys from the vault; per-session seal on close; verify CLI with clear human-readable report.

**Phase 6 — Service, API, sessions, orchestration** (design §3.1, §3.7)
- FastAPI service: REST + WebSocket streaming; localhost-only default; refuses non-loopback bind without TLS+auth. Windows named-pipe option.
- Session manager (multi-session, resume, cancel with full process-tree cleanup), durable job queue with idempotency keys, token-auth + RBAC (Admin/Operator/Auditor/Viewer).
- Multi-agent orchestration: team topology from package agents/, subagent spawn with `capability.attenuate` enforced per delegation edge, inter-agent messages persisted (mailbox tables), depth limits.
- E2E test: full team run over the API with a scripted mock adapter; kill service mid-run, restart, resume.

**Phase 7 — Vault, config, CLI, installers, updater, licensing** (design §3.2, §5, §6, §7)
- Credential vault: DPAPI-NG (Windows) / keyring-libsecret (Linux); nothing secret ever on disk plaintext; log-redaction filter tested.
- `scr` CLI per design §3.7 command list. First-run wizard (`scr init`, `scr model add` with live smoke test).
- Windows Service wrapper + MSI (WiX) + winget manifest; Linux systemd unit + .deb. Staged side-by-side updater with health-probe + auto-rollback; offline update files.
- Offline Ed25519 license files; expiry grace = read-only evidence access, never brick. Seat accounting in team mode.
- Tests: install→init→model add→package install→run→export E2E scripted in CI where possible; updater rollback simulation; license expiry behavior.

**Phase 8 — Remaining adapters + ops surface** (design §3.2, §3.8, §5)
- Bedrock (SigV4) + Azure OpenAI adapters through the same conformance corpus. Fallback chains + circuit breakers.
- `scr doctor`, `scr backup`/`restore` (encrypted snapshot, restore tested in CI), Prometheus metrics endpoint (off by default), structured JSON logging with correlation IDs.
- Docs: admin guide, security overview, NIST 800-171 control mapping table (internal draft).

**Phase 9 — Hardening + full matrix** (design §9)
- Run/extend every suite across the OS matrix. Windows twins for POSIX-skipped tests (reparse points, TerminateProcess chaos). Upgrade-path matrix, disk-full and clock-jump chaos, dual-instance contention storm, pen-style self-review of the capability kernel with findings fixed.
- Final `STATUS.md` = design §"Definition of Done" checklist with evidence links (test names) per item. Anything not met is listed as OPEN, not hidden.

## Content migration (after Phase 4 lands)
Create `packages/selfconnect-enterprise/` and port the existing SelfConnect
agent/team/skill content into `.scpkg` source form. Flag every spot where the
content silently assumed Claude Code behavior (its tool names, subagent
semantics, context handling) in `docs/CONTENT_MIGRATION.md` and rewrite
against SCR's actual tools. Package self-tests must pass against Ollama
before the migration is called done.

## Working style
- Plan each phase before coding it: write `docs/plans/phase-N.md` (files to create, test list, risks), then implement.
- Use subagents where parallelism is safe (e.g., adapter conformance vs. CLI work), but merge through the phase gate serially.
- Keep individual files under ~500 lines; split modules rather than grow them.
- After each phase: run the FULL cumulative suite, not just the new one.

Begin now: read the design doc, run the existing suite, write `docs/plans/phase-2.md`, then execute Phase 2.
