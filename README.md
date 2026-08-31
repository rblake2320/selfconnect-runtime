# SelfConnect Runtime — Phase 1: Agent Kernel Core

Implements §3.1, §3.2 (schema + 3 adapters), §3.3 (core checks + attenuation),
§3.5 (state/journal/ledger/atomic/locks) of `SELFCONNECT_RUNTIME_DESIGN.md`.
This is the layer that replaces Claude Code as the thing that makes your
files run.

## Run the suite (Windows-first)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install pytest
pytest tests/ -q
```

Linux delta: `python3 -m venv .venv && source .venv/bin/activate` — identical suite.
(Two POSIX-symlink tests and the SIGKILL chaos test skip on Windows by design;
Windows equivalents land in Phase 9's CI matrix via TerminateProcess.)

## Modules

| Module | Responsibility |
|---|---|
| `scr/kernel.py` | Journaled agent loop, guards (depth/budget/wall/cycle), idempotency-classified crash recovery |
| `scr/gateway.py` | Unified model schema; Mock, OpenAI-compat, Ollama, Anthropic adapters (customer-supplied endpoints; zero vendor keys shipped) |
| `scr/capability.py` | Deny-by-default manifests; resolved-path containment (traversal/symlink/ADS defense); monotonic delegation attenuation |
| `scr/ledger.py` | SHA-256 hash-chained ledger + HMAC seals + offline `verify()` |
| `scr/state.py` | SQLite WAL store: sessions, messages, write-ahead journal, idempotency table |
| `scr/atomic.py` | tmp → fsync → `os.replace` → dir-fsync atomic writes, CRLF-safe |
| `scr/locks.py` | Cross-process single-writer lock (flock / msvcrt), OS-released on death |

## Test suites — 69 tests, all passing

| Suite | Count | Proves |
|---|---|---|
| `test_kernel.py` | 10 | Loop correctness, guard enforcement, denial folding, ledger emission |
| `test_recovery.py` | 7 | Crash at every journal state → correct classification; **non-idempotent side effects are never silently re-executed**; recovery itself is idempotent |
| `test_chaos_kill.py` | 1 | Real SIGKILL of a live run mid-side-effect → quarantine + `PRAGMA integrity_check` clean |
| `test_capability.py` | 21 | Adversarial: `..` traversal, symlink escape (file + dir), prefix-sibling roots, NTFS ADS syntax, exec arg-injection, net allowlist, 3-level attenuation monotonicity |
| `test_ledger.py` | 11 | Adversarial: bit flip, reorder, mid-chain deletion, splice with recomputed hash, truncation/extension after seal, seal forgery with wrong key |
| `test_gateway.py` | 11 | Request construction + response parsing for all adapters; one shared internal schema |
| `test_atomic.py` | 6 | Fault-injected crash before rename → original intact, temp cleaned; CRLF bytes verbatim |
| `test_locks.py` | 4 | Same-process and true cross-process contention; lock freed on holder death |

## Phase 2 — Sandboxed execution + MCP host (+25 tests, 94 cumulative)

| Module | Responsibility |
|---|---|
| `scr/sandbox.py` | Worker subprocesses: restricted-env allowlist, cwd jail, wall timeout with whole-tree kill, memory cap (Windows Job Object / POSIX RLIMIT_AS), cancel reaping, structured worker-output classification |
| `scr/worker.py` | Sandboxed job entry (fs read/write/list, http_get, proc_exec); re-validates paths in-sandbox (defense in depth) |
| `scr/tools_native.py` | Capability-checked-**before-spawn** native ToolSpecs; correct idempotency flags for crash recovery |
| `scr/mcp_host.py` | stdio + streamable-HTTP MCP client host: handshake/list/call, scoped-env-only, manifest-scoped (deny-by-default) tool projection, crash→restart-with-backoff, idempotent-defaults-false |

| Suite | Count | Proves |
|---|---|---|
| `test_sandbox.py` | 8 | Timeout kills the whole tree (no orphan grandchild survives); explicit cancel reaps the tree; parent env secrets never reach a worker; memory cap enforced; garbage/timeout worker output classified, never crashed-through |
| `test_tools_native.py` | 10 | `..` traversal + symlink escape denied **before** any spawn; write outside write-roots denied; non-allowlisted host/binary denied; real read/write/list/exec inside the jail; idempotency flags match the design |
| `test_mcp_host.py` | 7 | Handshake/list/call round-trip; scoped env only; crash-mid-stream → restart → retry succeeds; denied-capability MCP call never sent to the server; idempotent defaults to false; full kernel loop drives an MCP tool under enforcement |

## Phase 3 — Capability kernel completion (+13 tests, 107 cumulative)

| Module | Responsibility |
|---|---|
| `scr/policy.py` | YAML policy: `require_approval` rules (by tool + optional arg-regex); admin `tighten` (intersection only) with `PolicyError` on any widening |
| `scr/kernel.py` (extended) | Journaled `AWAITING_APPROVAL` pause + resumable `resume()`; `approve()`/`deny()` as ledgered events with approver identity; `approval_id` binding each approval to the exact action; token budget governor on real adapter counts |

| Suite | Count | Proves |
|---|---|---|
| `test_policy.py` | 6 | Approval matching by tool and by arg-regex; tightening intersects; **widening is rejected**, not silently granted |
| `test_approval.py` | 7 | Pause without executing; approve→resume runs exactly once; deny→tool never runs; wrong/forged approval_id does not authorize; crash mid-wait recovers to the same gate; approval/denial ledgered with approver; token budget governor stops on real counts |

## Phase 4 — Package format, signing, loader (+26 tests, 133 cumulative)

| Module | Responsibility |
|---|---|
| `scr/merkle.py` | Domain-separated SHA-256 Merkle root over `{path: filehash}` |
| `scr/signing.py` | Ed25519 sign/verify; deny-by-default `Keystore` (publisher pin + customer keys); signed `RevocationList` honored only if itself signed by a trusted key |
| `scr/package.py` | `.scpkg` build/read; in-memory member reads (path-traversal-safe) |
| `scr/signer.py` | Publisher signer (`python -m scr.signer`) — never shipped to customers |
| `scr/loader.py` | Fail-closed `verify_package` (hash→manifest→root→signature→pinning→revocation, each localized) + `tests/*.yaml` self-test runner |

| Suite | Count | Proves |
|---|---|---|
| `test_merkle.py` | 5 | Root is order-independent and changes on any leaf/file change |
| `test_signing.py` | 8 | Sign/verify; wrong key + tampered message fail; keystore deny-by-default; revocation list must be trusted-signed; forged revocation rejected |
| `test_package.py` | 3 | Build/read round-trip; manifest covers every payload file; excludes itself |
| `test_loader.py` | 10 | Valid load; **unsigned / untrusted-key / single-leaf-tamper (localized) / manifest mismatch / revoked-version** all rejected; rogue revocation can't brick a good package; self-tests pass/fail and refuse unverified packages |

## Phase 5 — Ledger completion + evidence export (+6 tests, 139 cumulative)

| Module | Responsibility |
|---|---|
| `scr/_evidence_verifier.py` | Pure-stdlib hash-chain + seal verifier — the single source of truth, embedded into every bundle |
| `scr/evidence.py` | `export_bundle` (self-verifying `.scevidence` zip: bundle.json + bundle.hmac + embedded `verify.py`), `verify_bundle`, `seal_on_close` |

| Suite | Count | Proves |
|---|---|---|
| `test_evidence.py` | 6 | Export→verify OK; wrong key fails seals; event tamper breaks the chain; bundle mutation breaks the bundle seal; unsealed session handled; **offline subprocess proof** — embedded `verify.py` verifies a good bundle and rejects a tampered one with nothing but Python stdlib |

## Phase 6 — Service, API, sessions, orchestration (+22 tests, 161 cumulative)

| Module | Responsibility |
|---|---|
| `scr/rbac.py` | Deny-by-default role matrix (admin/operator/auditor/viewer) |
| `scr/sessions.py` | `SessionManager` + durable SQLite job queue: idempotency-key dedupe, cancel, `recover_all()` reclassifying crashed jobs via kernel recovery |
| `scr/orchestration.py` | Team topology with per-edge `capability.attenuate`, depth limits, persisted inter-agent mailbox |
| `scr/service.py` | FastAPI REST + WebSocket; Bearer auth; RBAC-guarded routes; loopback-only bind-guard (refuses non-loopback without TLS+auth) |

| Suite | Count | Proves |
|---|---|---|
| `test_rbac.py` | 6 | Role matrix deny-by-default across all four roles |
| `test_orchestration.py` | 5 | Delegation attenuates per edge (grandchild ⊆ child ⊆ parent); depth limit; mailbox order |
| `test_sessions.py` | 4 | Enqueue/run; idempotent enqueue runs once; cancel; **kill mid-run → `recover_all()` quarantines** (no double-fire) |
| `test_service.py` | 7 | Auth required; RBAC per route; idempotent run; ledger-read role split; bind-guard; WS streams events; approval gate over REST runs the tool exactly once |

## What is NOT yet included

Credential vault, `scr` CLI + first-run wizard, installers (MSI/winget/deb),
updater, licensing — Phase 7; extra adapters + ops surface — Phase 8;
full-matrix hardening — Phase 9. No gap between claims and code: everything
listed above is implemented and tested; everything not listed is not claimed.
