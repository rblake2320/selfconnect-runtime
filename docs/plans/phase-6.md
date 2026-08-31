# Phase 6 plan — Service, API, sessions, orchestration (design §3.1, §3.7)

## Files

| File | Purpose |
|---|---|
| `scr/rbac.py` | Roles Admin/Operator/Auditor/Viewer; token→(subject, role) table; `require(role, action)` permission matrix (deny-by-default). |
| `scr/sessions.py` | `SessionManager` over a durable job queue (SQLite): enqueue a run with an idempotency key (dupe key returns the existing job, never double-runs), claim/execute via the kernel, cancel with process-tree cleanup, and `recover_all()` that reclassifies in-flight jobs after a crash (resume/quarantine per kernel recovery). |
| `scr/orchestration.py` | Team topology from package `agents/`; `spawn` a subagent whose effective manifest is `capability.attenuate(parent, child)` enforced per delegation edge; persisted inter-agent mailbox (SQLite) with depth limits. |
| `scr/service.py` | FastAPI app: REST (create session, run, get status, list, cancel, approve/deny, ledger export) + WebSocket run-event stream. Loopback-only bind by default; refuses non-loopback without TLS+auth. Token auth + RBAC dependency. |

## State additions (`scr/state.py`)

- `jobs` table: job_id, session_id, idem_key (UNIQUE), status (queued/running/
  done/cancelled/needs_review), created_at.
- `mailbox` table: id, session_id, from_agent, to_agent, body, created_at.
- `agent_tokens` table: token, subject, role.

## Tests

- `test_rbac.py`: matrix — Viewer cannot run; Auditor can read ledger, cannot
  run; Operator can run, cannot manage tokens; Admin can; unknown token denied.
- `test_sessions.py`: enqueue+run happy path; **idempotency** — same key twice
  runs once and returns the same job; cancel marks cancelled; **kill mid-run
  then recover** — a real subprocess runs a non-idempotent tool, is killed,
  and `recover_all()` on a fresh manager quarantines it (no double-fire).
- `test_orchestration.py`: delegation attenuates per edge (child can't exceed
  parent; grandchild ⊆ child ⊆ parent); depth limit enforced; mailbox
  messages persist and are delivered in order; a subagent denied a capability
  the parent lacks.
- `test_service.py` (FastAPI TestClient, no real port): auth required;
  create→run→status→ledger export over REST; RBAC enforced on each route;
  non-loopback bind refused without TLS; WebSocket streams run events; the
  approval gate surfaces over the API (run → awaiting_approval → approve →
  resume → completed).

## Decisions (ADR-007)

- New deps: `fastapi==0.115.6`, `uvicorn==0.34.0` (ASGI server for the
  installed service), `httpx==0.28.1` (TestClient; also the REST client used
  by `scr` CLI in Phase 7), `websockets==14.1` (WS streaming). All mandated
  by design §3.1's "FastAPI REST + WebSocket".
- Tests use Starlette's in-process TestClient — no bound port, hermetic and
  Windows-safe. Real-port bind behavior (loopback refusal) is asserted via
  the bind-guard function directly.
- Durable queue is SQLite (already the state store), not a new broker.

## Risks

- WebSocket + kernel run: the kernel loop is synchronous; the WS route runs it
  in a threadpool and streams journaled events. Cancellation cooperatively
  flips the job status and kills any sandbox tree.
