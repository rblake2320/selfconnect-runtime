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

## What Phase 1 deliberately does NOT include

Sandboxed worker processes, MCP client host, `.scpkg` signing/loading,
FastAPI surface, installers, licensing — Phases 2–8 per the design doc.
No gap between claims and code: everything listed above is implemented
and tested; everything not listed is not claimed.
