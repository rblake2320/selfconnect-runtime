# Phase 3 plan — Capability kernel completion (design §3.3)

## Files

| File | Purpose |
|---|---|
| `scr/policy.py` | `Policy` loaded from YAML: `require_approval` rules (match by tool name + optional arg-regex) and a `tighten` block (tools / net_hosts subsets). `requires_approval(call)`; `validate_tightening(manifest)` raises on any widening attempt; `tighten(manifest)` returns the intersected manifest. `PolicyError` for widening. |
| `scr/state.py` (extend) | `approvals` table (approval_id PK, session_id, status, approver, created_at) + `approval_put` / `approval_get`. |
| `scr/kernel.py` (extend) | HITL approval gate inside the tool-execution phase; journaled `AWAITING_APPROVAL` pause storing the remaining pending calls; resumable `resume()`; `approve()`/`deny()` writing ledgered approval events bound to a per-call `approval_id`; token budget governor summing real adapter token counts from the ledger, enforced against `Guards.max_total_tokens`. |

## Design of the pause/resume

- Approval id = `sha256(session | tool | canonical(args) | call.id)` — unique
  per model-emitted call, so approving one action cannot authorize a
  different tool or arguments (replay-safe).
- On the first unapproved approval-required call, the kernel journals
  `AWAITING_APPROVAL` with the *remaining* pending calls (from that index on)
  and stops the run with reason `awaiting_approval`. Calls before the pause
  already executed and their tool messages persisted.
- `approve(session, approval_id, approver)` / `deny(...)` write an approval
  row and a ledger event carrying approver identity.
- `resume(session)` reads the `AWAITING_APPROVAL` tail, executes the pending
  calls honoring approval records (approved → execute; denied → fold denial;
  still pending → re-pause), then re-enters the main loop. Crash-safe: the
  pending list lives in the journal, so a crash during approval wait recovers
  to the same pause.

## Budget governor

`Guards.max_total_tokens` (default 2_000_000 so existing tests are
unaffected). After each model call the kernel sums `in_tokens + out_tokens`
across ledger `model_call` events; over budget → stop reason `budget`. This
is the *real* usage from adapters, distinct from the pre-call length/4
estimate guard which stays.

## Tests (`test_policy.py`, `test_approval.py`)

- Policy: YAML load; `requires_approval` matches by tool and by arg-regex;
  `validate_tightening` raises on a tool/host not in the base manifest
  (widening rejected); `tighten` intersects and never widens.
- Approval: approval-required call pauses (reason `awaiting_approval`, journal
  `AWAITING_APPROVAL`, nothing executed); approve → resume executes exactly
  once; deny → resume folds a denial, tool never runs; approval bound to
  idem/approval_id — a forged/wrong approval_id does not authorize; crash
  during approval wait (fresh Store) → resume still pauses then proceeds;
  approval + denial are ledger events with approver identity; token budget
  governor stops a run when real adapter counts exceed the cap.

## Risks / deferrals (ADR-004)

- `tighten` covers tools + net_hosts in Phase 3 (enough to prove
  intersection-only + widening rejection). Root/exec-rule tightening deferred
  to Phase 9 hardening — noted in STATUS.
- New dependency: `PyYAML` (policy files are YAML per design §3.3).
