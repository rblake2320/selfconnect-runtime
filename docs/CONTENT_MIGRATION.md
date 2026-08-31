# Content Migration — SelfConnect → SCR `.scpkg`

INTERNAL. Ports the SelfConnect agent/team/skill content into
`packages/selfconnect-enterprise/` (`.scpkg` source form) and flags every spot
where the original content silently assumed Claude Code supplied the execution
engine.

## Claude-Code assumptions found and rewritten

| Original assumption (implicit in the SelfConnect folders) | Why it breaks under SCR | Rewrite |
|---|---|---|
| The agent loop, retries, and context window are "just there" | SCR *is* the loop now; nothing is implicit | Agents carry only a `system_prompt` + declared capabilities; the kernel owns the loop, guards, and recovery |
| Tool names were Claude Code's (`Read`, `Write`, `Bash`, `Edit`, subagent `Task`) | SCR exposes its own tools | Mapped to SCR native tools: `fs_read`/`fs_list`/`fs_write`/`proc_exec`; delegation via team `delegates:` not `Task` |
| Subagents inherited the parent's full authority | SCR attenuates per delegation edge | `worker` capability is intersected with `lead` at the edge (`capability.attenuate`); worker can never exceed lead |
| "Just run it" side effects (deploys, prod writes) executed silently | SCR gates by policy | `policies/default.yaml` marks prod/release writes and deploy/rm execs `require_approval` → journaled pause |
| Ambient env / secrets reachable by tools | SCR sandboxes with scoped env only | MCP servers get scoped env; native tools run in workers with an env allowlist |
| MCP servers assumed always-on and trusted | SCR supervises + scopes them | `mcp/servers.yaml` declares transport, scoped env, idempotency, timeout; tools gated by the manifest |
| Skills carried hidden runtime behavior | SCR skills are plain instruction text | `skills/summarize.md` is instructions only; any write delegates to the worker |

## Build & verify (performed)

`scripts/build_enterprise_pkg.py` builds and Ed25519-signs the package, then the
loader verifies it and runs the `tests/*.yaml` self-tests.
`tests/test_content_migration.py` proves: the package builds from source, the
signed `.scpkg` loads (tamper-localized), and its self-tests pass against a
stand-in customer model.

## Self-test against Ollama — PENDING (honest status)

The design's "package self-tests must pass against Ollama before the migration
is called done" is **NOT yet satisfied**: Ollama was not reachable at
`localhost:11434` during this pass, so the self-tests ran against a scripted
stand-in adapter (proving the runner + package wiring), not a live local model.
To close this: start Ollama, then
`scr package verify packages/selfconnect-enterprise-<v>.scpkg` with a configured
Ollama model. Until then this item is OPEN in STATUS.
