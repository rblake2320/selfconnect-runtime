# SelfConnect Layers — Inventory for Migration to the Runtime (SCR)

**Status: INVENTORY ONLY. Nothing here is ported. Awaiting Ron's port-order marks.**
Generated 2026-09-01 from three read-only sweeps: PKA SDK (`C:\Users\techai\PKA testing\selfconnect`),
projects-2 (`…\Desktop\projects 2\selfconnect_{plugins,audio}`, `selfconnect-alt`), and 9 GitHub repos.

## DECISIONS (Ron, 2026-09-01) — authoritative

- **G-A:** SCR is canonical. `selfconnect-terminal` v3 becomes the **UI client over SCR's API**;
  its engine (loop/ledger/providers) is **RETIRED: superseded by SCR**. Harvest only its unique
  capabilities (tiered memory, MCP server mode, context-gauge) as SCR layers (order #7).
- **G-B:** `PKA testing/selfconnect` is the **source of truth**. `selfconnect` repo + `UncNeph/pka`
  are archives — consulted only for source PKA lacks. **One row per layer, no triple-count.**
- **G-C:** `launch/` + `services/` source **NOT FOUND** anywhere (PKA SDK pyc-only; archive repos
  lack the dirs entirely; absent from OneDrive C:/D:, D:\backups, .local_archives). Ruling:
  **rebuild fresh on SCR** (a harness-era boot layer rebuilt on the runtime that replaces the
  harness). Decompile the `.pyc` for **reference only**, never as the ported artifact. Deferred to
  Tier-S/H (does not block order #1).
- **G-D:** HOPE parked.
- **RETIRED — do NOT port** (superseded by an SCR-native feature):
  | Retired layer | Replaced by (SCR feature) |
  |---|---|
  | Codex-chime tone detector (audio) | SCR native readiness/ack events |
  | CC TUI-prompt approval watchers (`approval_partner`/`telegram`/`peer_watcher`/`claudego`/`approve_codex`/`approve_gemini`) | SCR kernel HITL approval gate (`approval_id`) |
  | `sc_spawn` claude/`cmd /k claude` launch + CC-hook ACK | SCR native team spawn + delegation events |
  | `sc_hooks` / `sc_hook_emit` (`.claude/settings.local.json`) | SCR event bus |
  | `sc_transcript` (reads `~/.claude/projects/*.jsonl`) | SCR session store + hash-chained ledger |
  | `selfconnect-terminal` v3 engine (loop/ledger/providers) | SCR kernel/ledger/model_factory |

- **PORT ORDER (authoritative):** 1) Provenance/Sentinel · 2) `sc_local_agent_runtime`+`sc_qwen_core`
  · 3) crypto/identity/governance kernel (`sc_identity`/PCTC, `sc_firewall`, `sc_pq`, migration/assignment)
  · 4) `sc_tasks` A2A fabric + transport primitives · 5) plugin system + enterprise governed_runtime
  · 6) Tier-H welds one at a time (rewrite shown before each) · 7) v3 harvest layers.
- **Gate:** port #1 only; signed + self-tested live on qwen through `scr.exe` + Ron runs
  `verify_run.ps1`; do NOT start #2 until Ron says VERIFIED.

## How to read this
- **Tier** — P = portable to SCR today, S = needs a transport/event shim, H = welded to a
  harness (Claude Code / Codex / Gemini), X = stub / duplicate / out-of-scope.
- **"working" means** — the one testable sentence that must pass live on qwen3.6:27b through the
  frozen `scr.exe` (then `verify_run.ps1`) for the layer to count as done.
- **CC-assumption** — the specific place it assumes Claude Code / Codex / injection, or "none".
- **Order (Ron)** — empty. You mark it. I port strictly in the order you write here.

---

## ⚠ DECISION GATES — resolve before/around porting (these are yours, not mine)

| # | Gate | Why it blocks a clean port | My recommendation |
|---|---|---|---|
| **G-A** | **`selfconnect-terminal` v3 is a SECOND "replace Claude Code" engine** — daemon+PTY+policy+ledger+MCP+tiered-memory, providers = direct Anthropic/Ollama/OpenAI. Same mission as SCR. | Two independent engines exist. "Porting" terminal into SCR may mean *merging two runtimes*, not moving a layer. | Declare **SCR canonical**; harvest terminal's *ideas* (tiered context store, cost kernel) as SCR features, don't port it as a "layer". Confirm. |
| **G-B** | **SDK lineage exists in 3 copies**: `PKA testing/selfconnect` (v0.12), the `selfconnect` GitHub repo, and `UncNeph/pka` (public). | Porting "the SDK" 3× would triple-count and risk pushing the private lineage. | Treat **`PKA testing/selfconnect` as the single source of truth**; the other two are mirrors. (Known provenance issue — see memory `project_pka_uncneph_repo`.) |
| **G-C** | **`launch/` and `services/` have NO source** — only `__pycache__/*.pyc`. `sc_shell.py` (the intended harness-neutral boot) imports from `launch/*`. | The natural home for a Claude-Code-free spawn/boot path is compiled-only; can't port what we can't read. | You locate the source, or authorize decompile, or we rebuild the boot layer fresh on SCR. Decide before Tier-S. |
| **G-D** | **HOPE not found** anywhere (no repo/dir/memory). | You named "the SelfConnect that lives inside HOPE" as in-scope. | Parked per your call. Point me to it when ready; I'll append its rows. |

---

## TIER P — portable to SCR today (the product spine)

These have **no Claude Code / Codex dependency in the code path**. This is the real IP.

| Layer | Source | Type | What it does | Depends on | "working" means (live, no CC) | CC-assumption |
|---|---|---|---|---|---|---|
| **sc_identity (PCTC)** | PKA SDK | protocol | Ed25519 DID:key + macaroon-style DelegationToken + ProvenanceLedger + MCP/A2A auth adapters | cryptography | Mint a DID, issue an attenuated delegation token, verify it, and append a ledger entry — all inside a package self-test on qwen | none |
| **sc_seat_identity** | PKA SDK | protocol | Binds a mesh "seat" to an Ed25519 key (handles are routing only) | cryptography, sqlite | A seat identity signs and a mismatched seat is rejected | none |
| **sc_migration** | PKA SDK | protocol | Ed25519-verified, replay-safe role-handoff manifests | cryptography, sqlite | A signed migration transfers a role; a replayed one is refused | none |
| **sc_assignment_protocol (+watchdog)** | PKA SDK | protocol | Worker acts only on a coordinator-signed seat+target assignment | cryptography | An unsigned/foreign assignment is refused; a signed one authorizes exactly one action | none |
| **sc_firewall** | PKA SDK | protocol | Decision firewall (goal-hijack / tool-misuse / memory-poison, OWASP Agentic 2026) | stdlib | A hijack-shaped proposed action is blocked and ledgered | none |
| **sc_pq** | PKA SDK | protocol | Hybrid Ed25519 + ML-DSA (FIPS 204) signing | cryptography/PQ lib | A hybrid-signed envelope verifies; tamper fails | none |
| **sc_reliability** | PKA SDK | tool | pass^k reliability metric over multi-trial runs | stdlib | Computes pass^k on a recorded trial set | none |
| **sc_tasks / sc_done / sc_envelope / sc_event_kinds** | PKA SDK | protocol | File-backed A2A task board + explicit completion verb + HMAC envelopes + versioned event kinds ("no Win32, no network") | stdlib | A task goes new→claimed→done with a hash-chained event log; unknown event kind fails closed | none (explicitly harness-free) |
| **self_connect core transport** (`self_connect.py`, `_win32_abi`, `sc_guarded_submit`, `sc_observation`, `sc_echo_filter`, `sc_terminal_tab`) | PKA SDK | core | Win32 window discovery + WM_CHAR/WriteConsoleInput send + PrintWindow/UIA read + guarded submit + echo classification | Win32 only | `guarded_submit` delivers to a verified HWND and readback confirms, wrong-target refused — no CC/Codex present | none (targets any window) |
| **sc_fabric_* (v2 IPC)** | PKA SDK | service | Named-pipe frame/mailbox transport + IOCP host + session router + Windows service wrapper | Win32 AF_PIPE/IOCP, pywin32 | A frame round-trips host→router→mailbox with ACK across a restart | none |
| **sc_mesh_registry / sc_mesh_lease** | PKA SDK | mesh | birth-id assignment, role lifecycle, hash-chained mesh_events; transport-neutral role leases | stdlib | A role is leased, a second claimant is denied, events chain-verify | none |
| **⭐ Local-Ollama agent path** (`sc_local_agent_runtime`, `sc_qwen_core`, `sc_local_agent_harness`, `sc_local_model_role`, `mesh_demo`) | PKA SDK | agent | A local Ollama model acts as a full mesh agent (read/observe broad, input/exec gated) with **no CC/Codex** | ext:Ollama :11434, self_connect | A qwen agent claims a task from the board and returns a signed result — zero Claude Code anywhere | **none — this IS the CC-free path; the strongest existing proof of the whole goal** |
| **sc_mcp / sc_cli / sc_send** | PKA SDK | service/tool | Optional MCP server (doctor/list/read/capture/send) + CLI + generic peer-send | mcp lib, self_connect | Any MCP client calls a governed tool; `sc_cli doctor` runs standalone | none (server CC *could* consume, not the reverse) |
| **Cross-machine bridges** (`sc_nats_bridge`, `hub_relay`, `spark2_client`) | PKA SDK | service | Signed envelopes over NATS; Windows↔Spark hub relay; Linux RPC client | ext:NATS/SSH/Hub:8765 | A signed envelope delivers across nodes with redelivery on drop | none |
| **vision_server** | PKA SDK | service | Localhost FastAPI :7421 — window capture/detection/VL/macros/actions, token auth | uvicorn, self_connect, a VL model | An authed client gets a window capture over REST | none |
| **skills/selfconnect-win32 (SKILL.md)** | PKA SDK | skill | Portable skill: adapter waterfall (UIA→WM_GETTEXT→PrintWindow→OCR), packaging, validation | self_connect, sc_cli | SCR loads the skill and its probe/adapter steps run | none (harness-neutral core) |
| **selfconnect_plugins** (loader + `SelfConnectPlugin` ABC + event_types + editions) | projects-2 | plugin | Plugin discovery, edition-gating, lifecycle fan-out with per-plugin isolation; context of bound callables | pyyaml | SCR binds runtime callables into `PluginContext`, loads a plugin, `emit()` reaches it | none (callables are `Callable|None`) |
| **audio plugin core** (bus, WASAPI capture, tone detector, health, config, NullConnector) | projects-2 | plugin | Captures system audio, fires typed events on a thread-safe bus; NullConnector = standalone (no mesh) | numpy, WASAPI, pywin32 | Plugin loads under SCR with NullConnector and emits `audio.tone.detected` | none (tone detector's *purpose* is sensing the Codex chime — semantic only) |
| **enterprise governed_runtime** (policy / operator / CngLedger / mcp_governor / acp_shim) | gh: enterprise | layer | Deny-by-default policy → operator approval → signed hash-chain ledger; governed MCP + ACP surfaces | Win32 CNG, cryptography, sqlite | A synthetic action yields a policy decision + signed ledger entry with no AI process running | none (registry.py uses "claude_code" only as a role label) |
| **provenance ("Sentinel")** | gh: provenance | service | Ingests signed ledger → maps to NIST 800-53 / ISO 42001 / EU AI Act; drift rules; Merkle auditor bundles | FastAPI, ECDSA; ledger JSONL schema | `provenance serve` demo-mode dashboard maps synthetic events to controls, zero AI | none (⚠ built vs Enterprise v0.8.0 schema; ~285 LOC RPC gap) |
| **selfconnect-store** | gh: store | layer | SQLite query projection over ecosystem JSONL (cost, deny-rate, chain-of-custody) | stdlib (core) | Ingest conforming JSONL + run queries; no AI | ⚠ one ingest source reads `~/.claude/projects/**/*.jsonl` — swap for SCR's usage log |
| **NarraZero / NZP** | gh: ai-benchmark | protocol | Deterministic content-addressed zero-narrative agent-coordination wire protocol + sealed benchmark | Python | Two agents coordinate a task over NZP frames with seal/replay verification | none (benchmark was *run* via CLIs; protocol is wire-level) |
| **ultra_server (BPC/TSK sidecar)** | gh: enterprise | service | Node.js signed-credential lifecycle + HA controller | Node, Redis, Postgres | `node server.js` + `.test.mjs` suites pass | none |

---

## TIER S — needs a transport/event shim (mechanism sound, coupling swappable)

| Layer | Source | Type | Coupling to break | "working" means | CC-assumption |
|---|---|---|---|---|---|
| **audio connectors** (`connector_sdk`, `connector_enterprise`) | projects-2 | plugin | Hard-import the Win32 injection SDK / enterprise WM_COPYDATA API | SCR binds a `send_frame`-equivalent over its own transport; an `audio.*` event reaches a peer | the connector files (not the plugin) |
| **sc_mesh roster classifier** | PKA SDK | mesh | Labels peers by CC/Codex screen strings ("auto mode", "gpt-5", "Codex") | Classifier identifies an SCR agent by an SCR-native marker, not a CC UI string | roster kind-detection strings |
| **sc_shell boot / sc_resume** | PKA SDK | agent | Imports `launch/*` (⚠ source missing — see G-C) | An SCR agent boots: provenance→session-index→role→mesh-register→loop→seal | none by design (blocked on G-C) |

---

## TIER H — welded to a harness (rewrite required; SHOW-BEFORE-I-CHANGE)

These are the "flag every Claude-Code assumption" rows. **I will show you each rewrite before making it.**

| Layer | Source | The weld (specific) | What a CC-free rewrite means |
|---|---|---|---|
| **sc_spawn** | PKA SDK | hardcodes `claude_cmd="claude"` + `cmd.exe /k claude` (L258/265/361); ACK contract depends on CC **UserPromptSubmit** hook firing | Spawn the SCR agent binary; ACK from an SCR-native readiness event, not a CC hook |
| **sc_hooks / sc_hook_emit** | PKA SDK | Writes & is invoked by **Claude Code hooks** in `.claude/settings.local.json` (UserPromptSubmit/Notification/Stop) | Replace with SCR's own event bus emitting the same 3 signals (ack / input-required / stop) |
| **sc_transcript** | PKA SDK | Reads **CC's private JSONL** at `~/.claude/projects/*` for lossless readback | Read SCR's own session store/ledger (which already exists) instead of CC transcripts |
| **approval layer** (`approval_partner`, `approval_telegram`, `peer_watcher`, `claudego/*`, `approve_codex`, `approve_gemini`) | PKA SDK + alt | Regex-match **CC/Codex TUI approval-prompt text** and inject y/n | SCR already has a native HITL approval gate (kernel `approval_id`); route to that, drop the screen-scraping. **The rules engine (`decide`/`extract_tool_call`) is pure and portable — keep it, re-source its input.** |
| **spawn/brief script sediment** (`_spawn_claude`, `_spawn_codex`, `launch_codex`, `spawn_observer`, `brief_*`, `gemini_bridge`, `first_contact_codex`, ~40 files) | PKA SDK + alt + core repo | Each launches a **named CLI binary** + injects a briefing | Not layers — ops glue for the old model. Most become obsolete once SCR spawns SCR agents; keep 0–1 as a reference |
| **Electron/WebView injectors** (`antigravity_controller`, `inject_webview*`, `redirect_gemini`) | PKA SDK | Bound to Antigravity/Electron chat apps (app-specific, not CC) | Out of the CC-free critical path; port only if driving foreign GUI agents is a product goal |

---

## TIER X — stub / duplicate / out-of-scope (do NOT port; listed so nothing hides)

| Item | Source | Verdict |
|---|---|---|
| STT / TTS / wake-word | projects-2 audio | ABC + config stubs; **no implementation**. Nothing to port. |
| `selfconnect` repo core + `UncNeph/pka` | gh | **Duplicate SDK lineage** of PKA SDK (G-B). Don't port twice. |
| `SelfConnect-Mac`, `selfconnect-alt` | gh + projects-2 | **Forks of core**; real delta small (Mac backend layer; 8 Win32 perf optimizations). Harvest the delta only if Mac/perf is a goal. |
| `selfconnect-ecosystem` | gh | Pointer umbrella; only first-party code is `selfconnect-py` (an **HTTP/TSK client** — different thing from the Win32 SDK, same brand) + CI gates. |
| `selfconnect-terminal` bridge `*.ps1` | gh | Codex/Claude-inject **evidence scripts**, not product. |
| Runbooks: `agent_launch_registry`, `enter_claude_tui`, `submit-pending-input…wm-char`, `mesh_agent_bootstrap` | PKA SDK | CC/Codex/Gemini TUI doctrine — **docs**, become historical once SCR is the harness. |

---

## Proposed port order (RECOMMENDATION — Ron overrides in the column below)

Rationale: **prove the spine first, cheapest-highest-signal, each independently self-testable on qwen.**
Then the shim tier, then the welds you actually still want.

| # | Layer | Why this position | Order (Ron) |
|---|---|---|---|
| 1 | **sc_identity (PCTC)** | The patent-adjacent trust kernel; pure crypto; anchors everything else | |
| 2 | **sc_tasks + sc_done + sc_envelope + sc_event_kinds** | The A2A fabric every agent uses; zero deps; fast to prove | |
| 3 | **sc_firewall** | Decision-guard; pure; high product value | |
| 4 | **⭐ Local-Ollama agent path** | The literal "runs without Claude Code" proof, on the model SCR already drives | |
| 5 | **selfconnect_plugins + audio(NullConnector)** | Proves the plugin system loads+emits under SCR | |
| 6 | **enterprise governed_runtime** | The governance engine; overlaps SCR's own kernel — porting reveals what SCR already covers | |
| 7 | **provenance (Sentinel)** | Cleanest standalone; compliance story; only a schema contract | |
| 8 | **Tier S shims** (audio connector, mesh classifier) | After the spine exists to shim onto | |
| 9 | **Tier H rewrites** (sc_spawn/hooks/transcript, approval routing) | Only after G-C resolved; each shown before change | |

---

## Notes carried
- **HOPE** — parked (G-D). Rows appended when located.
- **`out/security-review-report.md`** committed in the SCR repo is the RUN E/F exhibit, kept deliberately.
- Proof matrix lives in `STATUS.md`; rows are added there only as each layer clears all three gates
  (ported+signed / self-test live on qwen / verified by Ron).
