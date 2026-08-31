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

## What is NOT yet included

`.scpkg` signing/loading, HITL approval gates, FastAPI surface, installers,
licensing — Phases 3–8 per the design doc. No gap between claims and code:
everything listed above is implemented and tested; everything not listed is
not claimed.
