"""Fallback chains + circuit breakers (design §3.8).

A CircuitBreaker trips OPEN after N consecutive failures, blocks calls until a
cooldown elapses, then goes HALF-OPEN to trial one call; success closes it,
failure re-opens it. A FallbackChain tries adapters in order, skipping any
whose breaker is open, and raises only when every adapter is exhausted.

The clock is injectable so behavior is deterministic under test.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .gateway import Adapter, ModelResponse, ToolDef


class AllAdaptersFailed(Exception):
    pass


class CircuitOpen(Exception):
    pass


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    clock: Callable[[], float] = time.monotonic

    state: str = "closed"        # closed | open | half_open
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state == "open":
            if self.clock() - self._opened_at >= self.cooldown_seconds:
                self.state = "half_open"
                return True
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self.state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        if self.state == "half_open" or self._failures >= self.failure_threshold:
            self.state = "open"
            self._opened_at = self.clock()


@dataclass
class _Backed:
    adapter: Adapter
    breaker: CircuitBreaker


class FallbackChain:
    """Adapter that delegates to the first healthy backing adapter."""

    def __init__(self, adapters: list[Adapter],
                 failure_threshold: int = 3, cooldown_seconds: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self._backed = [
            _Backed(a, CircuitBreaker(failure_threshold, cooldown_seconds, clock))
            for a in adapters
        ]

    def complete(self, messages: list[dict[str, str]],
                 tools: list[ToolDef]) -> ModelResponse:
        last_error: Optional[Exception] = None
        any_attempted = False
        for backed in self._backed:
            if not backed.breaker.allow():
                continue
            any_attempted = True
            try:
                resp = backed.adapter.complete(messages, tools)
                backed.breaker.record_success()
                return resp
            except Exception as e:  # noqa: BLE001 — try the next adapter
                backed.breaker.record_failure()
                last_error = e
        raise AllAdaptersFailed(
            "all adapters failed or open" if any_attempted
            else "all adapter circuits open") from last_error

    def breaker_states(self) -> list[str]:
        return [b.breaker.state for b in self._backed]
