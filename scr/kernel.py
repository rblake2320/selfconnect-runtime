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
from typing import Any, Callable, Optional

from .capability import CapabilityDenied, CapabilityManifest
from .gateway import Adapter, ModelResponse, ToolCall, ToolDef
from .ledger import Ledger
from .state import Store


# ------------------------------------------------------------------ tools
@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable[[dict[str, Any]], str]
    idempotent: bool
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


# ------------------------------------------------------------------ config
@dataclass(frozen=True)
class Guards:
    max_iterations: int = 25
    max_token_estimate: int = 200_000
    max_wall_seconds: float = 3600.0
    cycle_repeat_threshold: int = 3


@dataclass
class RunResult:
    session_id: str
    final_text: str
    iterations: int
    stopped_reason: str  # completed | max_iterations | budget | wall_clock | loop_detected


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


class Kernel:
    def __init__(
        self,
        store: Store,
        adapter: Adapter,
        tools: dict[str, ToolSpec],
        manifest: CapabilityManifest,
        guards: Guards = Guards(),
        system_prompt: str = "You are a SelfConnect agent.",
    ):
        self.store = store
        self.ledger = Ledger(store)
        self.adapter = adapter
        self.tools = tools
        self.manifest = manifest
        self.guards = guards
        self.system_prompt = system_prompt

    # ---------------------------------------------------------------- run
    def run(self, session_id: str, user_text: str) -> RunResult:
        self.store.add_message(session_id, "user", user_text)
        self.store.journal_append(session_id, "ASSEMBLE", {"task": True})
        start = time.monotonic()
        cycle_history: list[str] = []
        tool_defs = [
            ToolDef(t.name, t.description, t.parameters) for t in self.tools.values()
        ]

        for iteration in range(1, self.guards.max_iterations + 1):
            messages = [{"role": "system", "content": self.system_prompt}]
            messages += self.store.get_messages(session_id)

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

            if not resp.tool_calls:
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

            # ---- execute tool calls -------------------------------------
            for call in resp.tool_calls:
                result_text = self._execute_call(session_id, call)
                self.store.add_message(
                    session_id, "tool",
                    json.dumps({"tool": call.name, "id": call.id, "result": result_text}),
                )

        return self._stop(session_id, self.guards.max_iterations, "max_iterations")

    def _execute_call(self, session_id: str, call: ToolCall) -> str:
        # capability check — deny-by-default, denial folded as a tool result
        spec = self.tools.get(call.name)
        try:
            self.manifest.check_tool(call.name)
            if spec is None:
                raise CapabilityDenied(f"unknown tool: {call.name!r}")
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
            result = spec.fn(call.arguments)
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
        })
        return result

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
