# SelfConnect Runtime (SCR) — Design

> **Provenance note (see docs/DECISIONS.md ADR-001):** the original design
> document was authored in a claude.ai conversation and was not among the
> files delivered to this machine. This document is a faithful reconstruction
> from (a) the master prompt's phase plan with its § references, (b) the
> Phase 1 README's module→section mapping, and (c) the Phase 1 code itself,
> whose interfaces are treated as normative. Section numbering matches every
> external reference (§3.1–§3.8, §5, §6, §7, §9, §10). If the original doc
> resurfaces, it supersedes this file and any divergence is a P0.

INTERNAL DOCUMENT — this repo is private, pre-disclosure. No wire-format or
protocol details from here may appear in any public artifact.

## §1 Purpose

SCR is a self-hosted service a customer installs on their own machines. It
loads signed capability packages (`.scpkg`), runs their agents through SCR's
own journaled agent loop against a model **the customer supplies** (enterprise
gateway, local Ollama, or a cloud key they own), enforces deny-by-default
policy on every tool call, and leaves a tamper-evident, offline-verifiable
ledger of everything it did. It replaces the AI harness (Claude Code) that
previously supplied the execution engine implicitly.

## §2 Design principles

1. **Crash-safe by construction.** Every side effect is preceded by a
   write-ahead journal record; recovery classifies every interruption as
   resume / safe_reissue / quarantine. Nothing side-effecting is ever
   silently re-executed. File artifacts only via atomic replace.
2. **Deny-by-default.** The model may request anything; only what the
   effective capability manifest permits executes. No ambient authority
   reaches a worker or MCP subprocess. Delegation is monotonic
   (intersection only).
3. **Evidence over claims.** Hash-chained ledger, HMAC seals, offline
   verification. Docs may only claim what tests prove.
4. **Vendor neutrality.** One internal model schema; adapters translate.
   SCR ships zero vendor keys.
5. **Windows-first, Linux parity.** Windows 11 / Server 2022 is the primary
   target; the same suite runs on Linux with platform branches.
6. **Stdlib-first.** Each third-party dependency is justified in STATUS.md.

## §3 Architecture

### §3.1 Agent kernel and service

The kernel (`scr/kernel.py`, Phase 1) is a deterministic per-turn state
machine: `ASSEMBLE → MODEL_CALL_INTENT → MODEL_CALL_DONE →` either
`FINALIZE` (no tool calls) or, per call,
`CAP_CHECK → EXEC_INTENT → execute → persist idempotent result → EXEC_DONE`,
then fold and loop. Guards per iteration: max depth, token-estimate budget,
wall clock, cycle detection. Recovery classification by journal tail:

| Journal tail | Classification | Behavior |
|---|---|---|
| `MODEL_CALL_INTENT` dangling | `reissue_model_call` | no customer side effects yet; reissue |
| `EXEC_INTENT`, result persisted | `resumed` | fold cached result, never re-execute |
| `EXEC_INTENT`, tool idempotent | `safe_reissue` | re-run under same idem key |
| `EXEC_INTENT`, side-effecting | `quarantined` | `FAILED_NEEDS_REVIEW`; a human decides |

The **service** (Phase 6) wraps the kernel in a long-running process:
FastAPI REST + WebSocket streaming, localhost-only by default, refusing any
non-loopback bind without TLS + auth; optional Windows named-pipe transport.
A session manager owns multi-session lifecycle (create / run / resume /
cancel with full process-tree cleanup) over a durable job queue keyed by
idempotency keys. AuthN is token-based; AuthZ is RBAC with four roles:
Admin, Operator, Auditor (read ledger/evidence only), Viewer.

**Multi-agent orchestration:** team topology comes from the package's
`agents/`. Each delegation edge spawns a subagent whose effective manifest is
`capability.attenuate(parent_effective, child_manifest)` — enforced by the
runtime, not by convention. Inter-agent messages persist in mailbox tables.
Delegation depth is bounded.

### §3.2 Model gateway, adapters, configuration

One internal schema (`ToolDef`, `ToolCall`, `ModelResponse`); the kernel
never sees vendor formats. Adapters (Phase 1: Mock, OpenAI-compat, Ollama,
Anthropic; Phase 8: Bedrock SigV4, Azure OpenAI) expose `build_request()`
(pure, offline-testable) and `complete()` (network). All adapters pass one
shared conformance corpus. Phase 8 adds fallback chains and circuit breakers
(consecutive-failure trip, half-open probe). Configuration is layered:
defaults → config file → admin overrides; credentials never in config —
config stores vault references only (§7 vault, held in Phase 7 scope).

### §3.3 Capability kernel

Phase 1 core: `CapabilityManifest` (tools, fs read/write roots, net host
allowlist, exec rules, budget) with deny-by-default checks; resolved-path
containment (traversal / symlink / NTFS ADS defense) in `resolve_within`;
monotonic `attenuate` (child ∩ parent — capabilities only shrink down a
delegation chain).

Phase 3 completes it:

- **HITL approval gates.** Policy rules mark action classes
  `require_approval`. The kernel journals `AWAITING_APPROVAL` and pauses,
  resumable across restarts. Approval and denial are ledger events carrying
  approver identity. An approval names the exact intent (idem key); it
  cannot be replayed against a different action.
- **Policy files** (`policies/*.yaml`). A package ships policy; an admin may
  only *tighten* (intersection), never widen. Any attempted widening is
  rejected and ledgered.
- **Budget governor** wired to real token counts from adapter usage fields,
  replacing the length/4 estimate as the enforcement input (estimate remains
  a pre-call guard).

### §3.4 Package format, signing, loader

`.scpkg` = zip: `manifest.json`, `agents/`, `skills/`, `tools/`, `mcp/`,
`policies/`, `tests/`, `SIGNATURE`. Integrity: per-file SHA-256 leaves →
Merkle root; Ed25519 detached signature over the root (via `cryptography`).
Trust: publisher key pinned at install, customer-added keys allowed, and a
signed revocation list; installing a revoked version (downgrade) is refused.
The loader verifies at install AND at every session start; tamper is
localized in the error (which file, which leaf). `scr package verify` runs
the package's own `tests/*.yaml` scenarios against the configured model.
The publisher signer (`scr-sign`) is a separate entry point never shipped in
the customer installer.

### §3.5 Durable state, ledger, evidence

SQLite in WAL mode, `synchronous=FULL` (`scr/state.py`): sessions, messages,
write-ahead journal, idempotency table, ledger rows, seals. File artifacts
via `scr/atomic.py` (tmp → fsync → `os.replace` → dir-fsync on POSIX;
CRLF-safe byte-verbatim writes). Single-writer workspace lock
(`scr/locks.py`): msvcrt / flock, OS-released on process death.

Ledger (`scr/ledger.py`): `hash_n = SHA-256(hash_{n-1} || canonical(E_n))`,
canonical = sorted-key compact JSON UTF-8; session close seals with
`HMAC-SHA256(key, head || count)`. `verify()` detects modification,
reorder, splice, mid-chain deletion, truncation/extension after seal, and
seal forgery.

Phase 5 completes it: **evidence bundle export** — events + chain heads +
seals + runtime/package versions in one HMAC-sealed archive;
`scr ledger verify <bundle>` runs offline on a machine with nothing else
installed and prints a clear human-readable report. Sealing keys come from
the vault; every session is sealed on close.

### §3.6 Sandboxed tool execution and MCP client host

Native tools (fs read/write/list, http, process exec) execute in **worker
subprocesses**, never in the service process: restricted environment (scoped
allowlist only — no ambient env), working-dir jail via
`capability.resolve_within`, per-call timeouts, kill-on-cancel, and
process-tree reaping so no orphaned children survive a cancel.
Windows: Job Objects (memory/CPU caps, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
Linux: cgroups-v2 where available, else rlimits + `setsid`.

**MCP client host:** launches/supervises stdio MCP servers and connects
streamable-HTTP servers from declarative config. Per-server capability
scopes; health checks; restart with exponential backoff; scoped env only.
MCP tools surface to the kernel as ordinary `ToolSpec`s with `idempotent`
declared in config — defaulting to **false**, so an MCP tool interrupted
mid-call is quarantined, never replayed.

### §3.7 CLI and API surface

`scr` CLI (built out across Phases 4–8):

```
scr init                 first-run wizard
scr model add|list|test  configure customer model endpoints (live smoke test)
scr package install|verify|list|remove
scr run / scr sessions list|resume|cancel
scr ledger export|verify
scr approve / scr deny   HITL gate actions
scr service install|start|stop|status
scr doctor               environment + integrity diagnostics
scr backup / scr restore encrypted snapshot
scr license install|status
```

REST API mirrors the CLI nouns; WebSocket streams run events. Localhost
binding by default (§3.1).

### §3.8 Ops surface and observability

Structured JSON logging with correlation IDs (session, run, tool-call);
log-redaction filter that provably strips vault material (tested).
Prometheus metrics endpoint, **off by default**. `scr doctor` checks:
Python/OS, DB integrity (`PRAGMA integrity_check`), lock health, package
signature status, model endpoint reachability, disk headroom.
`scr backup`/`restore`: encrypted snapshot of state + config (no plaintext
secrets); restore is exercised in CI.

## §4 Security model (summary)

Threats considered: malicious/compromised model output (mediated by
capability kernel), tampered packages (signing + Merkle localization),
tampered evidence (hash chain + seals), path escapes (resolved-path
containment incl. symlink/ADS), orphaned/runaway processes (job objects /
setsid + reaping), credential theft from disk (vault, DPAPI-NG/libsecret),
policy escalation (intersection-only tightening; monotonic attenuation),
replayed approvals (approval bound to idem key), downgrade to revoked
package versions (signed revocation list).

## §5 Operations

Installed footprint: service + CLI + data dir (`%ProgramData%\SelfConnect\SCR`
on Windows; `/var/lib/scr` on Linux). Backup/restore per §3.8. Logs are
structured JSON files with rotation. Admin guide, security overview, and a
NIST 800-171 control-mapping table (internal draft) ship in Phase 8.

## §6 Installers and updater

Windows: Service wrapper, MSI (WiX), winget manifest. Linux: systemd unit,
`.deb`. Target: install on a stranger's Windows box in under 30 minutes.
Updater: staged side-by-side install → health probe → switch, with automatic
rollback on probe failure; offline update files supported (air-gapped
customers). An update never mutates the running install in place.

## §7 Licensing

Offline Ed25519-signed license files (no phone-home). Expiry grace mode =
read-only evidence access — a lapsed license can always export and verify
its ledger; the product never bricks. Seat accounting in team mode.

## §8 Testing philosophy

Adversarial-first: every phase ships escape attempts, forgery attempts,
fault injection, and kill-mid-operation chaos alongside happy paths. A
feature without tests does not exist. OS-specific behavior gets both
variants with skip marks. The full cumulative suite runs at every phase
gate.

## §9 Hardening and full matrix (Phase 9)

Full-matrix runs on Windows + Linux. Windows twins for every POSIX-skipped
test (reparse points for symlink escapes; TerminateProcess chaos for
SIGKILL). Upgrade-path matrix across released versions. Disk-full and
clock-jump chaos. Dual-instance contention storm against the workspace
lock. Pen-style self-review of the capability kernel with findings fixed
in-repo. Final STATUS.md maps the Definition of Done to evidence (test
names); anything unmet is listed OPEN, never hidden.

## §10 Phase plan

| Phase | Scope | Key sections |
|---|---|---|
| 1 | Kernel, recovery, capability core, ledger, gateway, atomic, locks | §3.1–§3.3, §3.5 |
| 2 | Sandboxed tool execution + MCP client host | §3.6 |
| 3 | Capability kernel completion (HITL, policies, budget) | §3.3 |
| 4 | Package format, signing, loader | §3.4 |
| 5 | Ledger completion + evidence export | §3.5 |
| 6 | Service, API, sessions, orchestration | §3.1, §3.7 |
| 7 | Vault, config, CLI, installers, updater, licensing | §3.2, §5, §6, §7 |
| 8 | Remaining adapters + ops surface | §3.2, §3.8, §5 |
| 9 | Hardening + full matrix | §9 |

### Definition of Done

- [ ] Survives kill -9 / TerminateProcess mid-task with no state corruption
      and no double-fired side effects (chaos suites, both OSes).
- [ ] Never executes what a manifest doesn't permit (adversarial capability
      suite, incl. MCP paths).
- [ ] Package tampering of any single byte is detected and localized;
      revoked versions refuse to load.
- [ ] Evidence bundle verifies offline on a clean machine.
- [ ] Service E2E: kill mid-run → restart → resume, over the API.
- [ ] No secret ever on disk plaintext; redaction filter proven.
- [ ] MSI installs on a clean Windows box; init→model add→package
      install→run→export E2E under 30 minutes.
- [ ] License expiry degrades to read-only evidence access, never bricks.
- [ ] STATUS.md claims ⊆ tested reality (spot-audited).
