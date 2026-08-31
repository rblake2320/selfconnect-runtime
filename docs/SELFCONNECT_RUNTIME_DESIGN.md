# SelfConnect Runtime — Production Design Document
**Codename:** SCR (SelfConnect Runtime) · **Baseline:** SelfConnect Capability OS harness + selfconnect-terminal
**Author:** Ron Blake · **Status:** DESIGN v1.0 — internal, not for public disclosure (MELD provisional gate applies to GUMBO-adjacent claims)
**Date:** 2026-08-31

---

## 0. One-Sentence Definition

SelfConnect Runtime is a self-hosted, model-agnostic agent operating layer that a customer installs on their own infrastructure; it loads signed SelfConnect capability packages (agents, teams, skills, tools, policies), executes them through its own agent loop against a **customer-supplied** model endpoint (enterprise gateway or local Ollama), and produces tamper-evident, hash-chained evidence of everything it does.

The customer never receives raw "files and folders." They receive an **installer + signed packages + a license**. The runtime is the product; the packages are the content; the model is theirs.

---

## 1. Goals (100% bars, not MVP bars)

| # | Goal | Acceptance bar |
|---|------|----------------|
| G1 | Runs with zero Claude Code / Codex dependency | Fresh Windows Server or workstation, one installer, one command → working agent session |
| G2 | Customer brings the model | Anthropic gateway, OpenAI-compatible, Azure OpenAI, AWS Bedrock, and Ollama all pass the same conformance suite |
| G3 | Nothing executes unsigned | Every package Ed25519-signed; runtime refuses unsigned/tampered packages; verified at load AND at each execution |
| G4 | Everything is evidenced | Hash-chained ledger of every model call, tool call, file mutation; ledger verifiable offline |
| G5 | Crash-safe | Kill -9 / power-pull at any point → restart resumes or cleanly rolls back; no corrupted state, no double-executed side effects |
| G6 | Policy-gated execution | Capability kernel mediates every tool call; deny-by-default; policies are data, not code |
| G7 | Installable & updatable | Signed MSI/winget (Windows), .deb + systemd (Linux); delta updates with rollback |
| G8 | Auditable by a stranger | A customer security team can verify signatures, ledger integrity, and network egress claims without calling you |

## 2. Non-Goals (v1)

- Hosted SaaS (design keeps the door open; do not build it now).
- Fine-tuning or model hosting beyond pointing at Ollama.
- Mobile clients.
- Marketplace for third-party packages (schema supports it; store comes later).

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLIENTS                                                            │
│  selfconnect-terminal (Electron/xterm)   scr CLI    REST/WS API     │
└───────────────┬─────────────────────────────────────────────────────┘
                │ localhost mTLS / named pipe (Windows)
┌───────────────▼─────────────────────────────────────────────────────┐
│  SCR CORE SERVICE  (Python 3.12, FastAPI, runs as Windows Service   │
│                     / systemd unit; single binary via PyInstaller   │
│                     or pyoxidizer for tamper-resistance)            │
│                                                                     │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────┐ │
│  │ API Gateway  │  │ Session Mgr   │  │ Scheduler / Job Queue    │ │
│  │ authn/authz  │  │ (multi-user,  │  │ (durable, resumable,     │ │
│  │ RBAC         │  │  RBAC scoped) │  │  idempotency keys)       │ │
│  └──────┬───────┘  └──────┬────────┘  └───────────┬──────────────┘ │
│         └─────────────────┴───────────────────────┘                │
│                           │                                        │
│  ┌────────────────────────▼─────────────────────────────────────┐  │
│  │ AGENT KERNEL (the loop you currently borrow from Claude Code)│  │
│  │  context assembly → model call → tool-call parse →           │  │
│  │  capability check → tool exec → result fold → loop/stop      │  │
│  │  · deterministic replay mode  · token budget governor        │  │
│  │  · loop-breaker (max depth, cycle detection, cost ceiling)   │  │
│  └───────┬───────────────────────────────┬──────────────────────┘  │
│          │                               │                          │
│  ┌───────▼────────────┐        ┌─────────▼─────────────────────┐   │
│  │ MODEL GATEWAY      │        │ CAPABILITY KERNEL              │   │
│  │ pluggable adapters:│        │ deny-by-default policy engine  │   │
│  │  anthropic-gateway │        │ per-agent capability manifests │   │
│  │  openai-compat     │        │ path/network/exec allowlists   │   │
│  │  azure-openai      │        │ human-approval gates (HITL)    │   │
│  │  bedrock           │        │ classification ceilings        │   │
│  │  ollama (local)    │        └─────────┬─────────────────────┘   │
│  │ unified msg schema │                  │                          │
│  │ streaming, retry,  │        ┌─────────▼─────────────────────┐   │
│  │ circuit breaker    │        │ TOOL EXECUTION LAYER           │   │
│  └───────┬────────────┘        │  · native tools (fs, proc,    │   │
│          │                     │    http) in sandboxed workers │   │
│  ┌───────▼────────────┐        │  · MCP client host (stdio +   │   │
│  │ CREDENTIAL VAULT   │        │    streamable-http servers)   │   │
│  │ Windows: DPAPI-NG/ │        │  · per-tool timeouts, kill,   │   │
│  │ CNG; Linux: keyring│        │    resource caps (JobObjects  │   │
│  │ never in config or │        │    on Win / cgroups on Linux) │   │
│  │ env files          │        └─────────┬─────────────────────┘   │
│  └────────────────────┘                  │                          │
│                                          │                          │
│  ┌──────────────────┐  ┌────────────────▼───────────────────────┐  │
│  │ PACKAGE MANAGER  │  │ STATE & EVIDENCE                        │  │
│  │ .scpkg loader    │  │ SQLite (WAL) per-tenant DB              │  │
│  │ Ed25519 verify   │  │ atomic write protocol (tmp→fsync→rename)│  │
│  │ semver + deps    │  │ hash-chained event ledger (per session) │  │
│  │ hot reload       │  │ HMAC-sealed export bundles              │  │
│  │ signed revocation│  │ cross-process file locks                │  │
│  └──────────────────┘  │ crash-recovery journal                  │  │
│                        └────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.1 The Agent Kernel (this is the piece you don't own today)

The kernel is a deterministic state machine per turn:

```
IDLE → ASSEMBLE_CONTEXT → MODEL_CALL → PARSE
     → [no tool calls] → FINALIZE → IDLE
     → [tool calls]    → CAP_CHECK → (DENY→fold denial as tool result)
                        → EXEC (parallel where declared safe)
                        → FOLD_RESULTS → ASSEMBLE_CONTEXT (loop)
Guards on every transition: token budget, wall-clock budget, depth limit,
cycle hash (detect identical repeated tool-call sets), cost ceiling.
```

Every transition writes a journal record BEFORE acting (write-ahead). Recovery after crash: read journal, classify last state, either resume (model call not yet issued), re-issue with same idempotency key (tool declared idempotent), or mark FAILED_NEEDS_REVIEW (tool with side effects, non-idempotent). No silent re-execution of side-effecting tools — this is the rule that makes G5 real.

**Context assembly** replicates what Claude Code gives you for free: system prompt composition from package manifests, agent persona, team topology, tool schemas, conversation window management with summarization-on-overflow, and file/skill injection. All composition rules are data-driven from the package, so packages behave identically across model backends (modulo model quality).

### 3.2 Model Gateway

- **Unified internal schema** (messages, tool definitions, tool results, streaming deltas). Adapters translate to/from vendor wire formats.
- **Adapters v1:** `anthropic` (customer's enterprise gateway URL + key), `openai-compat` (covers OpenAI, vLLM, LM Studio, many gateways), `azure-openai`, `bedrock` (SigV4, customer IAM), `ollama` (no key; localhost or LAN).
- **Conformance suite:** one test corpus (tool-call fidelity, parallel tool calls, streaming interruption, long-context behavior, JSON-mode reliability) run against every adapter in CI. An adapter ships only when green. This is how "model-agnostic" becomes a tested claim instead of a slogan.
- **Resilience:** exponential backoff w/ jitter, circuit breaker per endpoint, fallback chains (`primary → secondary → local ollama`) if the customer configures them, request/response size guards.
- **No vendor keys from you, ever.** First-run wizard asks the customer for endpoint + credential; credential goes straight into the vault. License validation is offline (see §7) so the product itself needs zero network egress — a checkable claim for air-gapped sales.

### 3.3 Capability Kernel (your differentiator — where SCE/GUMBO thinking lives)

- Each agent in a package declares a **capability manifest**: filesystem scopes (glob allowlists, read/write split), network scopes (host allowlists), process-exec scopes (binary allowlists + arg patterns), MCP tool scopes, max concurrency, budget.
- Runtime enforces manifests at the tool boundary — the model can *ask* for anything; the kernel only *permits* what's declared, and the customer admin can further restrict (intersection, never union).
- **HITL gates:** policies can require human approval for classes of actions (e.g., `process.exec:*`, writes outside workspace, any network POST). Approvals surface in terminal/UI and are themselves ledger events.
- **Delegation:** when an orchestrator agent spawns a subagent, the child's effective capabilities = child manifest ∩ parent effective set (monotonic attenuation — capabilities can only shrink down a delegation chain). Revocation of a parent invalidates the chain (SEVER-aligned; keep the cryptographic chain format behind the MELD gate until filed — v1 ships attenuation + revocation semantics without publishing the wire format).

### 3.4 Package Format (`.scpkg`)

Zip container:

```
manifest.json        # name, semver, min-runtime, model requirements (min context,
                     # tool-call support), declared capabilities, entry agents
agents/*.yaml        # personas, prompts, team topology, delegation rules
skills/**            # skill instruction files (your current layer-3 content)
tools/**             # native tool descriptors + optional wheels for custom tools
mcp/*.json           # MCP server declarations (command or URL; capabilities scoped)
policies/*.yaml      # default policy set (customer admin can tighten)
tests/*.yaml         # package self-test scenarios (run at install & on demand)
SIGNATURE            # Ed25519 detached sig over canonical hash tree (Merkle root)
```

- Signed by your publisher key; runtime pins your publisher pubkey plus any customer-added keys. Per-file Merkle leaves → tamper localization ("skills/redteam.md modified") not just "package bad."
- **Package self-tests** (G8): `scr package verify selfconnect-enterprise` runs signature check + smoke scenarios against the customer's configured model and prints a pass/fail report their security team can file.
- Signed **revocation list** distributed with updates for pulled package versions.

### 3.5 State, Storage, Evidence

- **SQLite in WAL mode**, one DB per tenant/workspace: sessions, messages, journal, jobs, approvals, ledger.
- **Atomic write protocol** for all file artifacts: write `*.tmp` → `fsync` → `MoveFileEx(REPLACE_EXISTING|WRITE_THROUGH)` on Windows / `rename(2)` on Linux → fsync dir (Linux). CRLF-safe: artifacts written binary; text normalization only at explicit export.
- **Ledger:** append-only event stream; each event `E_n` stores `hash_n = SHA-256(hash_{n-1} ‖ canonical(E_n))`; session close seals segment with HMAC (key from vault). `scr ledger verify <session>` re-walks the chain offline. Exported evidence bundles = events + chain heads + HMAC seal + runtime version + package versions — your existing sealed-bundle discipline, productized.
- **Cross-process locking:** single-writer lock per workspace DB via `LockFileEx` (Windows) / `flock` (Linux) with stale-lock detection (PID + boot-id + heartbeat); prevents two service instances corrupting state.
- **Backup/restore:** `scr backup` produces an encrypted (AES-256-GCM, key wrapped by DPAPI/keyring) snapshot: DBs + packages + config, minus vault secrets (non-exportable by design; restore re-prompts for credentials). Restore is tested in CI, not aspirational.

### 3.6 Sandboxing tool execution

- Native tools run in worker subprocesses, not in the service process. Windows: Job Objects (memory/CPU caps, kill-on-close), restricted token, working-dir jail + path canonicalization checks (defeat `..\` and NTFS ADS tricks, reject reparse-point escapes). Linux: cgroups v2 + no-new-privs + seccomp basic profile.
- MCP servers launched by the runtime inherit scoped env only (no ambient credentials), get per-server capability scopes, and are supervised (restart w/ backoff, health checks, kill on policy violation).
- All child process trees are terminated on session cancel — no orphaned "security team" processes.

### 3.7 API + Clients

- REST + WebSocket on localhost by default (named pipe option on Windows); optional LAN exposure requires TLS cert + auth enabled — the runtime refuses to bind non-loopback without both.
- **AuthN:** local OS-user trust for single-user mode; token auth + RBAC (Admin / Operator / Auditor / Viewer) for multi-user server mode.
- selfconnect-terminal (your existing Electron/xterm surface) becomes the flagship client, talking only to this API — one UI codebase for local and server installs.
- `scr` CLI: `init`, `model add|test`, `package install|verify|list`, `run <agent|team> "<task>"`, `session list|resume|export`, `ledger verify`, `backup|restore`, `doctor`.

### 3.8 Observability

- Structured JSON logs (rotating, size-capped) with correlation IDs = session/turn/tool-call.
- `scr doctor`: checks service health, DB integrity (`PRAGMA integrity_check`), model endpoint reachability, package signatures, disk space, clock skew — prints a support bundle path.
- Metrics endpoint (Prometheus format) off by default; zero telemetry to you unless customer opts in (make this a stated, verifiable claim — it sells in GovCon-adjacent accounts).

---

## 4. Deployment Shapes

| Shape | Description | Notes |
|-------|-------------|-------|
| Workstation | Service + terminal on one Windows box | Default; Ollama-only works fully offline |
| Team server | Service on Windows Server/Linux; N users via terminal/API | RBAC on; TLS required |
| Air-gapped | Offline installer + offline license + Ollama | No egress at all; evidence bundles walked out by media |

## 5. Installation Story (the one-pager a customer sees)

1. Run `SelfConnectRuntime-Setup.msi` (Authenticode-signed) or `winget install SelfConnect.Runtime`. Installs service + CLI + terminal.
2. `scr init` — creates workspace, generates instance keys, applies license file.
3. `scr model add` — wizard: pick adapter, enter endpoint + credential (stored in DPAPI vault) or select local Ollama; runs live conformance smoke test.
4. `scr package install selfconnect-enterprise.scpkg` — verifies signature, runs package self-tests, reports.
5. `scr run sce.security-team "Run the security review on C:\target\repo"` — or open the terminal and work interactively.

Linux deltas: `.deb`/`.rpm`, systemd unit, `keyring`/libsecret vault, flock/cgroups — semantics identical, covered by the same test suite via CI matrix.

## 6. Update Mechanism

- Signed update feed (or offline update files for air-gapped). Runtime updates: staged side-by-side install → health probe → atomic switch → auto-rollback on failed probe. Package updates: verify → shadow-install → run package self-tests → promote. Every promote/rollback is a ledger event.
- Config/DB schema migrations are versioned, forward-only, with pre-migration snapshot; failed migration = automatic restore.

## 7. Licensing & Activation (offline-first)

- License = Ed25519-signed JSON (customer, tier, seats, expiry, entitled packages). Validated locally; no phone-home. Grace behavior on expiry: runtime keeps working read-only (sessions/ledger/export) but won't start new agent runs — never brick evidence access.
- Seat enforcement in team mode via session accounting in the tenant DB.

## 8. Security Model Summary

- Threats addressed: tampered packages (signing + Merkle), credential theft (DPAPI/CNG vault, never on disk in plaintext, never in logs), prompt-injection-driven tool abuse (capability kernel + HITL gates + path canonicalization), runaway loops (budgets, cycle detection), evidence tampering (hash chain + HMAC seals), rogue local process (locked-down service ACLs, named-pipe SD on Windows), supply chain (SBOM per release, pinned deps, reproducible build target, signed artifacts).
- Threats explicitly out of scope v1 (document honestly): malicious customer admin, compromised model endpoint returning poisoned outputs (mitigated but not eliminated by capability kernel), side channels on shared hosts.
- For your buyer profile: map controls to NIST 800-171 families in a one-page matrix (AC, AU, CM, IA, SC, SI). Not certification — a mapping document. It shortens security review by weeks.

## 9. Testing Strategy (state of "works 100%")

| Suite | Proves | Approx count |
|---|---|---|
| Kernel unit | State machine transitions, guards, journal WAL semantics | ~180 |
| Adapter conformance | Identical behavior corpus across all 5 backends | ~60 × 5 |
| Capability adversarial | Path traversal, ADS, symlink/reparse escape, arg-injection into exec allowlists, network allowlist bypass attempts, delegation-attenuation violations | ~120 |
| Crash/chaos | Kill -9 at every journal state, power-pull sim (fsync fault injection), disk-full mid-write, clock jump | ~70 |
| Ledger adversarial | Bit-flip detection, truncation, splice, reorder, seal forgery attempts | ~40 |
| Package security | Unsigned, resigned-wrong-key, tampered leaf, downgrade, revoked version | ~35 |
| Concurrency | Dual-instance lock contention, parallel tool exec races, session cancel storm | ~45 |
| E2E scenarios | Install→model add→package install→team run→export→verify, per OS, per shape | ~30 |
| Upgrade/rollback | Version matrix upgrades, failed-migration auto-restore, backup/restore round-trip | ~25 |

CI matrix: Windows Server 2022, Windows 11, Ubuntu 22.04/24.04 × Python 3.12 × all adapters (Ollama in CI via small model; cloud adapters via customer-style mock + weekly live run).

## 10. Build Order & AI-Execution Time (Claude Code multi-agent workspace, your hardware)

| Phase | Deliverable | AI time |
|---|---|---|
| 1 | Kernel + journal + SQLite state + atomic writes + unit/chaos suites | 2–3 days |
| 2 | Model gateway + anthropic/openai-compat/ollama adapters + conformance corpus | 1–2 days |
| 3 | Capability kernel + sandboxed tool workers + adversarial suite | 2–3 days |
| 4 | Package format, signer toolchain, loader, self-tests; migrate current SelfConnect content into first .scpkg | 1–2 days |
| 5 | Ledger + seals + export/verify CLI | 1 day |
| 6 | API + RBAC + wire selfconnect-terminal to it | 1–2 days |
| 7 | Installers (MSI/winget/.deb), service integration, updater, license tool | 2 days |
| 8 | Bedrock/Azure adapters, backup/restore, doctor, docs, 800-171 matrix | 1–2 days |
| 9 | Full-matrix hardening pass: chaos, upgrade matrix, pen-style review of capability kernel | 2 days |

Total: roughly 13–18 AI-days of focused multi-agent execution, phase-gated (a phase merges only with its suite green).

## 11. Provisional Claim Seeds (hold behind MELD gate; do not publish)

1. Monotonic capability attenuation across dynamically spawned agent delegation chains, enforced at a tool-execution boundary independent of model behavior.
2. Write-ahead journaled agent loop with idempotency-classified tool recovery (resume / safe-reissue / quarantine) surviving arbitrary process termination.
3. Per-leaf Merkle-verified capability packages with signed revocation and install-time behavioral self-test against a customer-supplied model endpoint.
4. Hash-chained, HMAC-sealed execution ledger binding model I/O, capability decisions, and tool side effects into one offline-verifiable evidence stream.
5. Model-adapter conformance gating: adapters admitted to a runtime only upon passing a behavioral corpus, yielding portable agent packages with warranted cross-model semantics.

## 12. Things you hadn't listed that this design adds

Licensing/activation (offline), signed updater with rollback, credential vault (DPAPI/CNG), sandboxed workers + process-tree reaping, adapter conformance gating, package self-tests as a security-review artifact, RBAC + Auditor role, backup/restore with tested recovery, schema migrations with auto-restore, SBOM/signed releases, `scr doctor` supportability, telemetry-off-by-default as a verifiable claim, NIST 800-171 mapping doc, license-expiry grace that never locks evidence.

---

## Definition of Done (product, not MVP)

- All §9 suites green on the full CI matrix; zero known P0/P1.
- A stranger completes §5 install on a clean machine from docs alone, on Windows and Ubuntu, in under 30 minutes.
- `scr ledger verify` and `scr package verify` pass on a customer-style machine with no network.
- Kill-the-power test during a team run: restart, `session resume`, correct outcome, intact ledger.
- Signed release artifacts + SBOM published to your distribution point.
