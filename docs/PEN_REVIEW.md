# Capability Kernel & Sandbox — Pen-style Self-Review (Phase 9)

INTERNAL. An adversarial read of the enforcement surface. Each considered
attack has a disposition: DEFENDED (with the test), LIMITATION (documented,
by-design), or FIXED (with the regression test added).

## Path containment (`capability.resolve_within`)

- **`..` traversal** — DEFENDED. Containment is checked on the fully *resolved*
  path. `test_capability.py`, `test_tools_native.py::test_traversal_denied_before_spawn`.
- **Symlink escape (POSIX)** — DEFENDED. realpath follows the link before the
  check. `test_tools_native.py::test_symlink_escape_denied_posix`.
- **Directory junction / reparse point (Windows)** — DEFENDED. Windows twin
  proves a junction inside the jail resolving outside is denied.
  `test_hardening.py::test_windows_junction_escape_denied`.
- **NTFS alternate data stream (`name:stream`)** — DEFENDED. `_reject_ads`
  rejects a colon in any non-drive component. `test_capability.py`.
- **Prefix-sibling root (`/wsX` vs root `/ws`)** — DEFENDED. `commonpath`
  equality, not string prefix. `test_capability.py`.
- **Windows case-fold** — LIMITATION (fail-closed). `commonpath` is
  case-sensitive, so a differently-cased but contained path is *denied*, never
  wrongly *allowed*. Safe direction; noted for a future case-normalized check.

## Exec (`check_exec`, `proc_exec`)

- **Non-allowlisted binary** — DEFENDED. `test_tools_native.py`.
- **Argument injection** — DEFENDED. The full arg string must fullmatch the
  rule's regex. `test_capability.py`.
- **An allowlisted binary reading outside the jail** — LIMITATION, by design.
  Granting exec of a binary trusts that binary; the control is the allowlist +
  arg pattern, and proc_exec is idempotent=False so a crash mid-exec is
  quarantined. Documented in SECURITY_OVERVIEW. Operators scope exec narrowly.

## Delegation (`attenuate`)

- **Child widening tools/hosts/roots** — DEFENDED. Result is child ∩ parent;
  roots must be contained. `test_capability.py`, `test_orchestration.py`.
- **Exec-rule widening** — DEFENDED. A child exec rule survives only if an
  identical (binary, arg_pattern) parent rule exists.
- **Depth-limit bypass** — DEFENDED. `Team.path_to` caps depth.
  `test_orchestration.py::test_depth_limit_enforced`.

## Approval gate

- **Replay an approval against a different action** — DEFENDED. approval_id
  binds session+tool+args+call.id; a mismatched id does not authorize.
  `test_approval.py::test_approval_bound_to_action_wrong_id_does_not_authorize`.
- **Skip the gate by crashing mid-wait** — DEFENDED. The pause is journaled;
  recovery returns to the same gate. `test_approval.py::test_crash_during_approval_wait_recovers_to_same_gate`.

## Sandbox

- **Orphaned children after cancel/timeout** — DEFENDED. Job Object
  KILL_ON_JOB_CLOSE (Windows) / setsid+killpg (POSIX). `test_sandbox.py`.
- **Env-secret leak into a worker/MCP server** — DEFENDED. Env built from an
  allowlist. `test_sandbox.py`, `test_mcp_host.py::test_scoped_env_only`.
- **Job-assignment micro-window (Windows)** — LIMITATION (ADR-003). The worker
  is our own code and proc_exec only launches allowlisted binaries, so nothing
  hostile runs in the window. A CREATE_SUSPENDED spawn path is the future fix.

## Evidence & packages

- **Tamper any single byte** — DEFENDED + localized. `test_loader.py`,
  `test_evidence.py`.
- **Forge/replace signing key** — DEFENDED (pinning). `test_loader.py`.
- **Forged revocation list DoS** — DEFENDED (list must be trusted-signed).
  `test_signing.py`, `test_loader.py::test_revoked_via_untrusted_list_still_loads`.

## Outcome

No exploitable finding surfaced that lacked a control. Two by-design
LIMITATIONS (allowlisted-exec trust; Windows job micro-window) are documented
with their compensating controls and future-hardening path. The Windows
case-fold containment is fail-closed. All dispositions above are backed by the
named tests.
