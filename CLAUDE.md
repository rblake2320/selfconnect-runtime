# SelfConnect Runtime (SCR) — Session Rules

This repo builds the SelfConnect Runtime: a self-hosted service that loads
signed capability packages and runs them through its own journaled agent loop
against a customer-supplied model, with deny-by-default policy and a
tamper-evident ledger. It replaces Claude Code as the execution engine.

On session start: read `STATUS.md` (current phase + honest state), then
`docs/SELFCONNECT_RUNTIME_DESIGN.md`. Resume from where STATUS.md says we are.
The full mission is `CLAUDE_CODE_MASTER_PROMPT.md`.

## Non-negotiable rules

1. **Phase gates.** A phase is complete only when its full test suite passes
   locally. One git commit per completed phase, message
   `phase-N: <summary> — <X> tests passing (<total> cumulative)`. Never start
   phase N+1 with phase N red. Push after each gate.
2. **Test discipline.** Every phase ships adversarial tests, not just
   happy-path (fault injection, SIGKILL/TerminateProcess chaos, escape
   attempts, forgery attempts — follow the pattern in `tests/`). State test
   counts in each commit. A feature without tests does not exist.
3. **No claim/code divergence.** README and docs may only describe what is
   implemented and tested. Maintain `STATUS.md`: per-phase table of
   implemented / tested / deferred, updated in the same commit as the code.
   Any divergence is P0.
4. **Windows-first.** Primary target: Windows 11 / Server 2022 (PowerShell,
   CRLF-safe artifacts, DPAPI/CNG, Job Objects, msvcrt locks, named pipes).
   Linux parity via the same suite with platform branches. OS-specific tests
   get both variants with skip marks (pattern: `test_chaos_kill.py`).
5. **Crash-safety bars.** Atomic writes (`scr/atomic.py`) for every file
   artifact. Every new side-effecting operation gets a write-ahead journal
   record and a recovery classification (resume / safe_reissue / quarantine).
   Idempotency keys on anything re-executable.
6. **Deny-by-default.** Every new tool, MCP server, and network path is
   mediated by the capability kernel. No ambient authority ever reaches a
   worker or MCP subprocess.
7. **Secrets.** Credentials only via the vault (Phase 7). Never in config
   files, env-file examples, logs, test fixtures, or commits. Pre-commit
   secret scan required.
8. **Dependencies.** Minimal, pinned exact versions in `pyproject.toml`.
   Prefer stdlib. Justify each new dependency in one line in STATUS.md.
   Python 3.12.
9. **Repo hygiene.** This repo is PRIVATE and stays private (pre-disclosure
   firewall — capability-attenuation and ledger formats are adjacent to
   unfiled MELD provisional claims). No public-facing marketing text,
   benchmark publications, or protocol wire-format documentation. Internal
   docs only. Run any future visibility change through PICKET first.
10. **When blocked.** If a design decision is genuinely ambiguous, choose the
    safer/stricter option, record it in `docs/DECISIONS.md` (ADR style, one
    paragraph each), and continue. Ask the human only if the choice is
    irreversible or touches money/legal.

## Working style

- Plan each phase before coding it: write `docs/plans/phase-N.md` (files to
  create, test list, risks), then implement.
- Do NOT rewrite Phase 1 modules (`scr/kernel.py`, `state.py`, `capability.py`,
  `ledger.py`, `gateway.py`, `atomic.py`, `locks.py`); extend them.
- Use subagents where parallelism is safe; merge through the phase gate
  serially.
- Keep individual files under ~500 lines; split modules rather than grow them.
- After each phase: run the FULL cumulative suite
  (`.venv\Scripts\python.exe -m pytest tests/ -q`), not just the new one.
- Venv: `.venv` at repo root, Python 3.12 (`py -3.12`).
