"""Agent kernel: the loop Claude Code used to provide for free.

Deterministic state machine per turn:

  ASSEMBLE -> MODEL_CALL_INTENT -> MODEL_CALL_DONE
    -> [no tool calls] FINALIZE
    -> [tool calls] per call: CAP_CHECK -> (denied? fold denial)
         EXEC_INTENT -> execute -> persist idempotent result -> EXEC_DONE
    -> fold -> ASSEMBLE (loop)

Every transition with a side effect writes a WRITE-AHEAD journal record.
Guards on each iteration: max depth, token-estimate budget, wall clock,
and cycle detection (identical repeated tool-call sets).

Recovery classifies a crash by the journal tail:
  MODEL_CALL_INTENT dangling  -> reissue_model_call (no customer side effects yet)
  EXEC_INTENT dangling:
      result already persisted -> resumed (fold, never re-execute)
      tool idempotent          -> safe_reissue (re-run with same idem key)
      tool has side effects    -> quarantined (FAILED_NEEDS_REVIEW; a human decides)
Nothing side-effecting is ever silently re-executed.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from .capability import CapabilityDenied, CapabilityManifest

if TYPE_CHECKING:
    from .policy import Policy
from .gateway import Adapter, ModelResponse, ToolCall, ToolDef
from .ledger import Ledger
from .state import Store


# ------------------------------------------------------------------ tools
def _run_tool_fn(fn, call) -> str:
    """A tool must NEVER kill the runtime. RUN-E live crash (2026-09-01): the
    model called fs_write without 'content'; the raw KeyError propagated out of
    the tool fn and took down the whole frozen process mid-team-run. ANY
    exception from a tool folds to an error result the model can react to,
    and _ledger_tool_error records it as a chain fact."""
    try:
        return fn(call.arguments)
    except Exception as e:  # noqa: BLE001 — fold, never crash
        return (f"TOOL ERROR [tool_exception]: {type(e).__name__}: "
                f"{str(e)[:200]} (tool={call.name})")


def _arg_path(args: dict) -> dict:
    """Evidence enrichment: for path-taking tools (fs_*), record WHICH path was
    touched (bounded) — "what did it actually read" is the first question a
    customer auditor asks of a review run. Everything else stays hash-only."""
    p = args.get("path")
    if isinstance(p, str) and p:
        return {"path": p[:300]}
    return {}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable[[dict[str, Any]], str]
    idempotent: bool
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    # §3.1: only tools DECLARED parallel-safe may run concurrently, and only
    # when also idempotent (so a crash mid-batch is always safe to reissue).
    parallel_safe: bool = False
    # §3.3: the tool's classification; a manifest may only invoke tools at or
    # below its classification ceiling.
    classification: str = "public"


# ------------------------------------------------------------------ config
@dataclass(frozen=True)
class Guards:
    max_iterations: int = 25
    max_token_estimate: int = 200_000
    max_wall_seconds: float = 3600.0
    cycle_repeat_threshold: int = 3
    # Budget governor: real accumulated adapter token counts (in+out) across
    # the session. Default high so it never trips unexpectedly.
    max_total_tokens: int = 2_000_000
    # Summarization-on-overflow (§3.1): when the assembled context estimate
    # exceeds `summarize_at_tokens`, older messages are compacted into a summary
    # (the STORE keeps the full history; only the model's view is compacted),
    # keeping the last `summarize_keep_recent` messages verbatim. Set below
    # max_token_estimate so a long session degrades gracefully instead of
    # hitting the hard stop.
    summarize_at_tokens: int = 150_000
    summarize_keep_recent: int = 6


@dataclass
class RunResult:
    session_id: str
    final_text: str
    iterations: int
    stopped_reason: str  # completed | max_iterations | budget | wall_clock | loop_detected | awaiting_approval
    pending_approval: Optional[str] = None  # approval_id when awaiting_approval


@dataclass
class RecoveryReport:
    session_id: str
    status: str  # clean | reissue_model_call | resumed | safe_reissue | quarantined
    detail: str = ""


def _estimate_tokens(messages: list[dict[str, str]]) -> int:
    return sum(len(m["content"]) for m in messages) // 4


def _idem_key(session_id: str, journal_seq: int, tool: str, args: dict[str, Any]) -> str:
    payload = json.dumps(
        {"s": session_id, "q": journal_seq, "t": tool, "a": args},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _cycle_signature(calls: tuple[ToolCall, ...]) -> str:
    sig = sorted(
        json.dumps({"n": c.name, "a": c.arguments}, sort_keys=True, separators=(",", ":"))
        for c in calls
    )
    return hashlib.sha256("|".join(sig).encode()).hexdigest()


def _approval_id(session_id: str, call: ToolCall) -> str:
    """Bind an approval to the EXACT action: session, tool, args, and the
    model-emitted call id. Approving one action cannot authorize another."""
    payload = json.dumps(
        {"s": session_id, "t": call.name, "a": call.arguments, "c": call.id},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class Kernel:
    def __init__(
        self,
        store: Store,
        adapter: Adapter,
        tools: dict[str, ToolSpec],
        manifest: CapabilityManifest,
        guards: Guards = Guards(),
        system_prompt: str = "You are a SelfConnect agent.",
        policy: Optional["Policy"] = None,
    ):
        self.store = store
        self.ledger = Ledger(store)
        self.adapter = adapter
        self.tools = tools
        self.manifest = manifest
        self.guards = guards
        self.system_prompt = system_prompt
        self.policy = policy
        # G5: optional cooperative cancel. A zero-arg predicate; when it returns
        # True the loop stops with reason "cancelled" between iterations and
        # before each tool call. Set by the SessionManager after construction.
        self.cancel_check: Optional[Callable[[], bool]] = None
        # Team delegation policy hook: given the session id, returns a reason to
        # REFUSE finalization (injected so the model continues) or None to allow.
        self.finalize_guard: Optional[Callable[[str], Optional[str]]] = None

    # ---------------------------------------------------------------- run
    def run(self, session_id: str, user_text: str) -> RunResult:
        self.store.add_message(session_id, "user", user_text)
        self.store.journal_append(session_id, "ASSEMBLE", {"task": True})
        return self._loop(session_id)

    def _summarize(self, msgs: list[dict[str, str]]) -> str:
        """Deterministic extractive compaction of older messages into one
        bounded summary. Real (not a stub): preserves an ordered role-tagged
        trace so the model retains context of what came before."""
        parts = []
        for m in msgs:
            content = " ".join(m["content"].split())      # collapse whitespace
            parts.append(f"{m['role']}: {content[:160]}")
        joined = " | ".join(parts)
        return (f"[SUMMARY of {len(msgs)} earlier messages] {joined}")[:4000]

    def _assemble_context(self, session_id: str) -> list[dict[str, str]]:
        """Build the model's view of the conversation, compacting older
        messages when the estimate overflows. The STORE (and ledger) keep the
        complete history untouched — only the model's window is compacted."""
        history = self.store.get_messages(session_id)
        messages = [{"role": "system", "content": self.system_prompt}] + history
        if _estimate_tokens(messages) <= self.guards.summarize_at_tokens:
            return messages
        keep = self.guards.summarize_keep_recent
        if len(history) <= keep:
            return messages          # nothing meaningful to compact
        old, recent = history[:-keep], history[-keep:]
        summary = {"role": "system", "content": self._summarize(old)}
        return [messages[0], summary] + recent

    def _total_tokens(self, session_id: str) -> int:
        rows = self.store.conn.execute(
            "SELECT event FROM ledger WHERE session_id=?", (session_id,)
        ).fetchall()
        total = 0
        for r in rows:
            try:
                e = json.loads(r["event"])
            except (json.JSONDecodeError, TypeError):
                continue
            if e.get("type") == "model_call":
                total += int(e.get("in_tokens", 0)) + int(e.get("out_tokens", 0))
        return total

    def _loop(self, session_id: str) -> RunResult:
        start = time.monotonic()
        cycle_history: list[str] = []
        tool_defs = [
            ToolDef(t.name, t.description, t.parameters) for t in self.tools.values()
        ]

        for iteration in range(1, self.guards.max_iterations + 1):
            # ---- cooperative cancel (G5) --------------------------------
            if self.cancel_check is not None and self.cancel_check():
                return self._stop(session_id, iteration, "cancelled")

            messages = self._assemble_context(session_id)

            # ---- guards --------------------------------------------------
            if _estimate_tokens(messages) > self.guards.max_token_estimate:
                return self._stop(session_id, iteration, "budget")
            if time.monotonic() - start > self.guards.max_wall_seconds:
                return self._stop(session_id, iteration, "wall_clock")

            # ---- model call (write-ahead) --------------------------------
            seq = self.store.journal_append(
                session_id, "MODEL_CALL_INTENT", {"iteration": iteration}
            )
            resp: ModelResponse = self.adapter.complete(messages, tool_defs)
            self.store.journal_append(
                session_id, "MODEL_CALL_DONE",
                {"iteration": iteration, "intent_seq": seq,
                 "tool_calls": len(resp.tool_calls)},
            )
            self.ledger.append(session_id, {
                "type": "model_call", "iteration": iteration,
                "text_sha256": hashlib.sha256(resp.text.encode()).hexdigest(),
                "tool_calls": [c.name for c in resp.tool_calls],
                "in_tokens": resp.input_tokens, "out_tokens": resp.output_tokens,
            })

            # ---- budget governor: real adapter token counts --------------
            if self._total_tokens(session_id) > self.guards.max_total_tokens:
                return self._stop(session_id, iteration, "budget")

            if not resp.tool_calls:
                # Delegation policy: refuse to finalize until required work is done.
                if self.finalize_guard is not None:
                    reason = self.finalize_guard(session_id)
                    if reason:
                        if resp.text:
                            self.store.add_message(session_id, "assistant", resp.text)
                        self.store.add_message(session_id, "user", reason)
                        self.ledger.append(session_id, {
                            "type": "policy", "rule": "required_children",
                            "decision": "finalize_refused", "reason": reason})
                        continue          # loop again; do NOT finalize
                self.store.add_message(session_id, "assistant", resp.text)
                self.store.journal_append(session_id, "FINALIZE", {"iteration": iteration})
                self.ledger.append(session_id, {"type": "finalize", "iteration": iteration})
                return RunResult(session_id, resp.text, iteration, "completed")

            # ---- cycle detection ----------------------------------------
            sig = _cycle_signature(resp.tool_calls)
            cycle_history.append(sig)
            n = self.guards.cycle_repeat_threshold
            if len(cycle_history) >= n and len(set(cycle_history[-n:])) == 1:
                return self._stop(session_id, iteration, "loop_detected")

            if resp.text:
                self.store.add_message(session_id, "assistant", resp.text)

            # ---- execute tool calls (may pause for approval) ------------
            pending = self._execute_pending(session_id, list(resp.tool_calls))
            if pending is not None:
                return RunResult(session_id, "", iteration, "awaiting_approval",
                                 pending_approval=pending)

        return self._stop(session_id, self.guards.max_iterations, "max_iterations")

    def _batch_eligible(self, call: ToolCall) -> bool:
        """A call may join a concurrent batch only if its tool is declared
        parallel-safe AND idempotent, it is granted, and it is not
        approval-gated. Everything else runs sequentially."""
        spec = self.tools.get(call.name)
        if spec is None or not (spec.parallel_safe and spec.idempotent):
            return False
        if self.policy is not None and self.policy.requires_approval(call):
            return False
        try:
            self.manifest.check_tool(call.name)
            self.manifest.check_classification(spec.classification)
        except CapabilityDenied:
            return False       # over-ceiling → sequential path folds the denial
        return True

    def _persist_precomputed(self, session_id: str, call: ToolCall,
                             precomputed: str) -> str:
        """Journal + fold a result computed OUTSIDE the store (used by the
        parallel batch, where fns ran concurrently but all store writes stay on
        the main thread — the single serialization point, so no races)."""
        tail = self.store.journal_tail(session_id)
        seq = self.store.journal_append(
            session_id, "EXEC_INTENT",
            {"tool": call.name, "args": call.arguments, "idempotent": True,
             "idem_key": _idem_key(session_id, (tail["seq"] if tail else 0) + 1,
                                    call.name, call.arguments)},
        )
        idem_key = self.store.journal_all(session_id)[-1]["payload"]["idem_key"]
        cached = self.store.tool_result_get(idem_key)
        if cached is not None:
            result = cached
        else:
            result = precomputed
            self.store.tool_result_put(idem_key, session_id, call.name, result)
        self.store.journal_append(session_id, "EXEC_DONE",
                                  {"intent_seq": seq, "idem_key": idem_key})
        self.ledger.append(session_id, {
            "type": "tool_exec", "tool": call.name,
            "args_sha256": hashlib.sha256(
                json.dumps(call.arguments, sort_keys=True).encode()).hexdigest(),
            "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
            "idem_key": idem_key, "parallel": True,
            **_arg_path(call.arguments),
        })
        self._ledger_tool_error(session_id, call.name, result)
        return result

    def _run_parallel_batch(self, session_id: str, batch: list[ToolCall]) -> None:
        """Run the batch's tool fns concurrently (they are idempotent reads with
        no shared mutable state), then journal/fold each on the main thread in
        original order."""
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(len(batch), 8)) as ex:
            results = list(ex.map(
                lambda c: _run_tool_fn(self.tools[c.name].fn, c), batch))
        for call, res in zip(batch, results):
            text = self._persist_precomputed(session_id, call, res)
            self._add_tool_msg(session_id, call, text)

    def _execute_pending(self, session_id: str, calls: list[ToolCall]) -> Optional[str]:
        """Execute a list of tool calls, honoring HITL approval gates and
        running consecutive parallel-safe calls concurrently. Returns None when
        all executed; returns an approval_id (and journals AWAITING_APPROVAL
        with the remaining calls) when it pauses."""
        i = 0
        while i < len(calls):
            call = calls[i]
            if self.cancel_check is not None and self.cancel_check():
                self.store.journal_append(session_id, "CANCELLED",
                                          {"at_call": call.name})
                return None
            # Greedily collect a run of consecutive batch-eligible calls.
            if self._batch_eligible(call):
                j = i
                while j < len(calls) and self._batch_eligible(calls[j]):
                    j += 1
                if j - i >= 2:
                    self._run_parallel_batch(session_id, calls[i:j])
                    i = j
                    continue
            # Fall through: single call, sequential path (approval + exec).
            if self.policy is not None and self.policy.requires_approval(call):
                aid = _approval_id(session_id, call)
                rec = self.store.approval_get(aid)
                if rec is None:
                    remaining = [
                        {"id": c.id, "name": c.name, "arguments": c.arguments}
                        for c in calls[i:]
                    ]
                    self.store.journal_append(
                        session_id, "AWAITING_APPROVAL",
                        {"approval_id": aid, "tool": call.name,
                         "args": call.arguments, "pending": remaining},
                    )
                    self.ledger.append(session_id, {
                        "type": "approval_required", "tool": call.name,
                        "approval_id": aid,
                        "args_sha256": hashlib.sha256(
                            json.dumps(call.arguments, sort_keys=True).encode()).hexdigest(),
                    })
                    return aid
                if rec["status"] == "denied":
                    self.ledger.append(session_id, {
                        "type": "approval_denied_exec", "tool": call.name,
                        "approval_id": aid, "approver": rec["approver"],
                    })
                    self._add_tool_msg(
                        session_id, call,
                        f"DENIED by approver {rec['approver']}: action not authorized")
                    i += 1
                    continue
                # approved → record and fall through to execute
                self.ledger.append(session_id, {
                    "type": "approval_granted_exec", "tool": call.name,
                    "approval_id": aid, "approver": rec["approver"],
                })
            result_text = self._execute_call(session_id, call)
            self._add_tool_msg(session_id, call, result_text)
            i += 1
        return None

    def _add_tool_msg(self, session_id: str, call: ToolCall, result_text: str) -> None:
        self.store.add_message(
            session_id, "tool",
            json.dumps({"tool": call.name, "id": call.id, "result": result_text}),
        )

    # ----------------------------------------------------- approval gate
    def approve(self, session_id: str, approval_id: str, approver: str) -> None:
        """Record an approval, ledgered with approver identity. Bound to the
        exact approval_id — cannot authorize a different action."""
        self.store.approval_put(approval_id, session_id, "approved", approver)
        self.ledger.append(session_id, {
            "type": "approval", "decision": "approved",
            "approval_id": approval_id, "approver": approver,
        })

    def deny(self, session_id: str, approval_id: str, approver: str) -> None:
        self.store.approval_put(approval_id, session_id, "denied", approver)
        self.ledger.append(session_id, {
            "type": "approval", "decision": "denied",
            "approval_id": approval_id, "approver": approver,
        })

    def resume(self, session_id: str) -> RunResult:
        """Resume a run paused at an approval gate. Executes the pending calls
        honoring approval records, then re-enters the model loop."""
        tail = self.store.journal_tail(session_id)
        if tail is None or tail["state"] != "AWAITING_APPROVAL":
            return RunResult(session_id, "", 0, "resume_noop", pending_approval=None)
        pending = tail["payload"]["pending"]
        calls = [ToolCall(c["id"], c["name"], c["arguments"]) for c in pending]
        still_pending = self._execute_pending(session_id, calls)
        if still_pending is not None:
            return RunResult(session_id, "", 0, "awaiting_approval",
                             pending_approval=still_pending)
        return self._loop(session_id)

    def _execute_call(self, session_id: str, call: ToolCall) -> str:
        # capability check — deny-by-default, denial folded as a tool result
        spec = self.tools.get(call.name)
        try:
            self.manifest.check_tool(call.name)
            if spec is None:
                raise CapabilityDenied(f"unknown tool: {call.name!r}")
            self.manifest.check_classification(spec.classification)
        except CapabilityDenied as e:
            self.ledger.append(session_id, {
                "type": "cap_denied", "tool": call.name, "reason": str(e),
            })
            return f"DENIED by capability kernel: {e}"

        tail = self.store.journal_tail(session_id)
        seq = self.store.journal_append(
            session_id, "EXEC_INTENT",
            {"tool": call.name, "args": call.arguments,
             "idempotent": spec.idempotent,
             "idem_key": _idem_key(session_id, (tail["seq"] if tail else 0) + 1,
                                    call.name, call.arguments)},
        )
        entry = self.store.journal_all(session_id)[-1]
        idem_key = entry["payload"]["idem_key"]

        cached = self.store.tool_result_get(idem_key)
        if cached is not None:
            result = cached
        else:
            result = _run_tool_fn(spec.fn, call)
            self.store.tool_result_put(idem_key, session_id, call.name, result)

        self.store.journal_append(
            session_id, "EXEC_DONE", {"intent_seq": seq, "idem_key": idem_key}
        )
        self.ledger.append(session_id, {
            "type": "tool_exec", "tool": call.name,
            "args_sha256": hashlib.sha256(
                json.dumps(call.arguments, sort_keys=True).encode()).hexdigest(),
            "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
            "idem_key": idem_key,
            **_arg_path(call.arguments),
        })
        self._ledger_tool_error(session_id, call.name, result)
        return result

    def _ledger_tool_error(self, session_id: str, tool: str, result: str) -> None:
        """RUN-D correction: a tool that executes and RETURNS an error string
        (e.g. "TOOL ERROR [worker_crash]: ...") looked like a successful
        tool_exec in the chain — the model truthfully reported crashes the
        ledger appeared to disprove. Error results are now chain facts."""
        if isinstance(result, str) and result.startswith("TOOL ERROR ["):
            klass = result[len("TOOL ERROR ["):].split("]", 1)[0]
            self.ledger.append(session_id, {
                "type": "tool_error", "tool": tool, "class": klass,
                "detail": result[:200],
            })

    def _stop(self, session_id: str, iteration: int, reason: str) -> RunResult:
        self.store.journal_append(session_id, "STOPPED", {"reason": reason})
        self.ledger.append(session_id, {"type": "stopped", "reason": reason})
        return RunResult(session_id, "", iteration, reason)

    # ----------------------------------------------------------- recovery
    def recover(self, session_id: str) -> RecoveryReport:
        tail = self.store.journal_tail(session_id)
        if tail is None:
            return RecoveryReport(session_id, "clean", "no journal")
        state = tail["state"]

        if state == "MODEL_CALL_INTENT":
            # Model call may or may not have reached the endpoint; either way
            # no customer-side effects occurred. Safe to reissue.
            self.store.journal_append(
                session_id, "RECOVERED", {"from": state, "action": "reissue_model_call"}
            )
            return RecoveryReport(session_id, "reissue_model_call",
                                  "model call interrupted before completion record")

        if state == "EXEC_INTENT":
            payload = tail["payload"]
            idem_key = payload["idem_key"]
            cached = self.store.tool_result_get(idem_key)
            if cached is not None:
                self.store.journal_append(
                    session_id, "EXEC_DONE",
                    {"intent_seq": tail["seq"], "idem_key": idem_key, "recovered": True},
                )
                return RecoveryReport(session_id, "resumed",
                                      "result persisted before crash; folded without re-execution")
            if payload["idempotent"]:
                self.store.journal_append(
                    session_id, "RECOVERED", {"from": state, "action": "safe_reissue",
                                               "idem_key": idem_key}
                )
                return RecoveryReport(session_id, "safe_reissue",
                                      f"idempotent tool {payload['tool']!r} may be re-executed")
            self.store.journal_append(
                session_id, "FAILED_NEEDS_REVIEW",
                {"from": state, "tool": payload["tool"], "idem_key": idem_key},
            )
            self.store.set_session_status(session_id, "needs_review")
            return RecoveryReport(session_id, "quarantined",
                                  f"non-idempotent tool {payload['tool']!r} interrupted; "
                                  "human review required — will not re-execute")

        return RecoveryReport(session_id, "clean", f"tail state {state}")
