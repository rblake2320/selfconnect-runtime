# Phase 2 plan — Sandboxed tool execution + MCP client host (design §3.6)

## Files

| File | Purpose |
|---|---|
| `scr/sandbox.py` | `SandboxRunner`: spawn worker subprocesses with restricted env allowlist, cwd jail, wall timeout, memory cap, and process-tree kill. Windows: Job Object (KILL_ON_JOB_CLOSE + ProcessMemoryLimit) via ctypes; POSIX: `start_new_session` + rlimits + `killpg`. Exposes `start()` → handle with `wait()/kill()` so cancel is first-class. |
| `scr/worker.py` | Subprocess entry (`python -m scr.worker`): reads one JSON job from stdin (`fs_read`/`fs_write`/`fs_list`/`http_get`/`proc_exec`), re-validates paths against the roots passed in the job (defense in depth — parent already checked), performs the op, writes one JSON result to stdout. `proc_exec` children are spawned *inside* the worker, so they inherit job/session membership and die with the tree. |
| `scr/tools_native.py` | Builds kernel `ToolSpec`s (fs_read, fs_write, fs_list, http_get, proc_exec) bound to a `CapabilityManifest` + `SandboxRunner`. Capability checks (path containment, net allowlist, exec rules) run in the parent BEFORE any spawn; denials never reach a worker. fs_read/fs_list/http_get idempotent=True; fs_write/proc_exec idempotent=False. |
| `scr/mcp_host.py` | Declarative config → supervised MCP servers. stdio transport (newline-delimited JSON-RPC 2.0: initialize handshake, tools/list, tools/call) with scoped env only; streamable-HTTP transport (JSON response mode). Health = process liveness + protocol response; restart with capped exponential backoff. Tools project into the kernel as `ToolSpec`s named `mcp__<server>__<tool>`, `idempotent` from config, default **false**. Per-server scope: a server's tools are additionally gated by the manifest's tool set (deny-by-default holds). |
| `tests/fixtures/mcp_fixture_server.py` | Minimal in-repo MCP stdio server for tests: echo/add tools, scripted crash-after-N-calls via env, env-echo tool to prove scoped env. |

## Tests (adversarial-first)

- `test_sandbox.py`: timeout kills tree (no orphan survives); explicit
  cancel (`kill()`) reaps grandchildren; restricted env — a sentinel secret
  in the parent env never appears in the worker; memory cap kills an
  allocator (Windows job object; POSIX rlimit); worker exit code / garbage
  stdout handled as structured error.
- `test_tools_native.py`: `..` traversal denied before spawn; symlink
  (POSIX) / junction (Windows) escape denied; write outside write-roots
  denied; read works inside jail; http to non-allowlisted host denied
  without any network activity; exec of non-allowlisted binary denied;
  exec output captured; CRLF-safe write.
- `test_mcp_host.py`: handshake + tools/list + tools/call round-trip;
  scoped env only (fixture echoes env — parent sentinel absent, configured
  var present); server crash mid-stream → restart with backoff → next call
  succeeds; call timeout enforced; denied-capability MCP call folded as
  denial (never sent to the server); ToolSpec idempotent defaults to False
  (interrupted MCP call ⇒ kernel quarantines — asserted via ToolSpec flag +
  existing recovery semantics); kernel E2E: MockAdapter drives an MCP tool
  through the full loop with capability enforcement.

## Risks

- Job Object assignment happens just after `Popen` (not CREATE_SUSPENDED):
  a few-ms window before membership. Recorded as ADR; revisited in Phase 9
  pen review. Worker is our code and allowlisted binaries only, so the
  window is not attacker-controllable in Phase 2 scope.
- Windows pipe reads have no select(): MCP host uses a reader thread +
  queue with timeouts.
- cgroups-v2 deferred: POSIX uses rlimits + setsid (the design's stated
  fallback) — ADR.
