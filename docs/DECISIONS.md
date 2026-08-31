# Architecture Decision Records

One paragraph each. Newest at the bottom.

## ADR-001 — Design doc reconstructed (2026-08-31)

The original `SELFCONNECT_RUNTIME_DESIGN.md` was authored in a claude.ai
conversation and was not among the files present on this machine (searched
Downloads, Desktop, Documents, Owner's Inbox, and all rblake2320 GitHub
repos; only `scr-phase1.zip` was delivered). Per rule 10 (choose the
stricter option and continue), the design doc was reconstructed from the
master prompt's phase plan, the Phase 1 README's module→section mapping, and
the Phase 1 code, whose interfaces are treated as normative. The
reconstruction is labeled as such in the doc header. If the original
document is provided later, it supersedes the reconstruction and any
divergence found is treated as P0.

## ADR-002 — POSIX memory/isolation via rlimits + setsid, not cgroups-v2 (2026-08-31)

Design §3.6 says "cgroups-v2 where available else rlimits + setsid." cgroups-v2
delegation requires either root or a pre-delegated user slice, which a
customer install cannot assume. Phase 2 implements the stated fallback only:
`RLIMIT_AS` for the memory cap and `start_new_session` + `killpg` for
process-tree reaping. cgroups-v2 (when a delegated slice exists) is deferred
to Phase 9's hardening matrix. On Windows the Job Object provides both the
memory cap and tree kill natively, so there is no equivalent gap there.

## ADR-003 — Job Object assigned immediately after spawn, not via CREATE_SUSPENDED (2026-08-31)

On Windows the worker is assigned to its Job Object in the few milliseconds
after `Popen` returns, rather than spawning suspended and assigning before
the first instruction. The stricter CREATE_SUSPENDED approach needs raw
CreateProcess handling that subprocess.Popen does not expose without
reimplementing process creation. The residual window is not attacker-
controllable in Phase 2 scope: the worker is our own code (`scr.worker`) and
`proc_exec` only launches operator-allowlisted binaries, so nothing hostile
runs in that window. Revisit with a CREATE_SUSPENDED spawn path in the
Phase 9 pen-review of the sandbox.
