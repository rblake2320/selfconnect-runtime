# Design Gap Analysis

**Scope of this document.** The task was to diff the ADR-001 *reconstruction*
against the *original* design doc. As of this writing the file at
`docs/SELFCONNECT_RUNTIME_DESIGN.md` is still the reconstruction (SHA-256
`58b017bf…`, byte-identical to commit e521cab; it still carries the ADR-001
provenance banner), and no original was found on disk. The `~600-test` §9
target and any suite table referenced by the owner exist only in the original,
which is not yet present here.

So this document is what CAN be produced honestly and is what actually drives
coverage/DoD decisions: a **§-by-§ audit of the current code against the
design-on-disk and the Definition of Done**, listing every requirement that is
UNIMPLEMENTED, UNDER-TESTED, or IMPLEMENTED-DIFFERENTLY. When the true original
arrives, a second pass will reconcile these findings against its exact wording
and its §9 suite targets (that reconciliation is the only part currently
blocked).

Legend: **[MISSING]** not implemented · **[PARTIAL]** implemented narrower than
the spec · **[UNTESTED]** implemented but a claimed behavior has no test ·
**[DIVERGENT]** implemented differently than described (with rationale).

---

## §3.1 Agent kernel and service

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 1.1 | Windows **named-pipe transport** for the service | [MISSING] | `scr/service.py` exposes only the FastAPI HTTP/WS app; no named-pipe listener. Design §3.1/§3.7 call it an option. |
| 1.2 | Session **cancel with full process-tree cleanup** | [PARTIAL] | `SessionManager.cancel` (sessions.py:72) flips job status to `cancelled`; it does NOT kill an in-flight sandbox tree. There is no live `SandboxProc` registry per job, and `run_job` is synchronous, so a truly in-flight run can't be cancelled mid-tool. Design §3.1 says "cancel with full process-tree cleanup." |
| 1.3 | **Multi-agent orchestration wired into a real run** | [PARTIAL] | `orchestration.py` (Team/attenuate/Mailbox) is unit-tested in isolation, but the kernel/service never actually spawns subagents from a package's `agents/` during a run. Delegation is proven as a function, not as a live team execution. |
| 1.4 | Service E2E "kill mid-run → restart → resume over the API" | [PARTIAL] | Proven at the SessionManager level (`test_sessions.py` SIGKILL) and at the API level separately; there is no single test that kills the *running service process* mid-run over HTTP and resumes. |

## §3.2 Model gateway, adapters, configuration

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 2.1 | **"All adapters pass one shared conformance corpus"** | [MISSING] | Each adapter has ad-hoc build/parse tests; there is no single corpus asserting the same invariants across Mock/OpenAI/Ollama/Anthropic/Bedrock/Azure. **Closed in this pass** — see `tests/test_conformance.py`. |
| 2.2 | Config **admin-override file layer** | [PARTIAL] | `Config.override()` is in-memory only; there is no `overrides.json`/admin layer merged at load. Design §3.2 "defaults → config file → admin overrides." |
| 2.3 | `scr model test` (live smoke test) | [MISSING] | §3.7 lists `scr model add|list|test`; only add/list exist. First-run wizard live smoke test not wired. |

## §3.3 Capability kernel

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 3.1 | Policy **root/exec-rule tightening** | [PARTIAL] | Tightening covers tools + net_hosts only (ADR-004). Roots/exec tightening deferred. |
| 3.2 | **`policies/*.yaml` directory loader** (merge multiple) | [MISSING] | `Policy.from_file` loads one file; nothing loads/merges a directory of admin policies. |
| 3.3 | HITL, budget governor | OK | Fully implemented + tested. |

## §3.4 Package format, signing, loader

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 4.1 | **Loader runs at session start** | [MISSING] | `verify_package` exists but the service never calls it; there is no package install/load flow in the runtime path. Design §3.4 "verifies at install AND at every session start." |
| 4.2 | `scr package install / list / remove` | [MISSING] | Only `scr package verify` exists. |
| 4.3 | Signing, Merkle, revocation, tamper localization | OK | Fully implemented + tested. |

## §3.5 Durable state, ledger, evidence

All implemented + tested (ledger chain, seals, offline evidence bundle,
atomic writes, locks). No gap found.

## §3.6 Sandboxed execution + MCP host

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 6.1 | **Streamable-HTTP MCP restart/backoff** | [PARTIAL/UNTESTED] | stdio transport has crash→restart-with-backoff tested; the HTTP client has no restart logic and only a connection-refused test. |
| 6.2 | MCP **periodic health checks** | [PARTIAL] | Liveness is checked on demand around a call; there is no supervisory health-check loop as §3.6 implies. |
| 6.3 | Sandbox jail, env isolation, tree-kill | OK | Fully implemented + tested (both OS). |

## §3.7 CLI and API surface

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 7.1 | Missing CLI verbs | [MISSING] | Present: init, model add/list, package verify, ledger export/verify, license status, doctor. **Missing:** `package install/list/remove`, `run`, `sessions list/resume/cancel`, `approve`/`deny`, `service install/start/stop/status`, `backup`/`restore`, `license install`, `model test`. |
| 7.2 | REST parity for approve/deny/ledger-export | [PARTIAL] | approve/deny exist over REST; `scr` CLI equivalents and a REST `ledger export` route do not. |

## §3.8 Ops surface and observability

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 8.1 | `scr doctor` **full check set** | [PARTIAL] | Checks integrity + model count only (cli.py:110-116). Design §3.8 also wants lock health, package signature status, model endpoint reachability, disk headroom. |
| 8.2 | **Prometheus `/metrics` endpoint** on the service | [MISSING] | Registry exists (`observability.py`) but no `/metrics` route; §3.8 wants an endpoint (off by default). |
| 8.3 | JSON logging + redaction + backup/restore | OK | Implemented + tested. |

## §5 Operations

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 5.1 | **Log rotation** | [MISSING] | JSON logs are emitted; no rotating file handler / retention. |

## §6 Installers and updater

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 6a | MSI / winget / deb **build + clean-box install** | [MISSING] | Scaffolds only; build blocked (WiX/dotnet not usable in this environment). DoD item OPEN. |
| 6b | Updater staged install + rollback + offline files | OK | Implemented + tested. |

## §7 Licensing

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 7a | **Seat accounting in team mode** | [MISSING] | `License.seats` is carried and signed but nothing enforces a seat count at run time. |
| 7b | Offline license, grace read-only | OK | Implemented + tested. |

## §9 / Definition of Done

| # | Finding | Class | Evidence / notes |
|---|---|---|---|
| 9.1 | **~600-test §9 target** | [BLOCKED] | Current suite is 219 (213 pass + 6 skip). The original's per-suite targets are not on disk; cannot map to them until the original arrives. |
| 9.2 | MSI clean-box install DoD item | OPEN | Blocked on WiX (§6a). |
| 9.3 | Ollama package self-test | OPEN | Blocked on the DGX Spark IP. |

---

## Prioritized closure plan (for the gated coverage pass once the original lands)

**P0 — functional gaps that change what the product does**
- 4.1 loader-at-session-start + 4.2 `package install/list/remove` (the actual
  install→run flow a customer uses).
- 1.2 real cancel/kill of an in-flight job's process tree.
- 1.3 live team execution (subagent spawn during a run).
- 7.1 the missing `scr` CLI verbs (run, sessions, approve/deny, backup/restore).

**P1 — observability/ops completeness**
- 8.1 full `scr doctor` checks · 8.2 `/metrics` route · 5.1 log rotation ·
  2.2 admin-override layer · 3.2 policy directory loader.

**P2 — hardening/edge**
- 6.1/6.2 HTTP MCP restart + health loop · 7a seat accounting · 2.3 `model test`.

**Down-payment closed in THIS pass (unblocked, pure test proving an existing
claim):** 2.1 shared adapter conformance corpus.

The remaining items are deliberately NOT half-built here: several are feature
work that belongs in its own phase-gated commit with its own adversarial tests,
and the coverage-target mapping (step 2) needs the original's suite table.
