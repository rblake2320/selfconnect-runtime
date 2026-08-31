"""Circuit breaker + fallback chain, with an injectable clock."""
import pytest

from scr.gateway import ModelResponse
from scr.resilience import AllAdaptersFailed, CircuitBreaker, FallbackChain


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Adapter:
    def __init__(self, fail=False, text="ok"):
        self.fail = fail
        self.text = text
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return ModelResponse(self.text)


def test_breaker_opens_after_threshold():
    clk = _Clock()
    b = CircuitBreaker(failure_threshold=3, cooldown_seconds=10, clock=clk)
    for _ in range(3):
        assert b.allow()
        b.record_failure()
    assert b.state == "open"
    assert not b.allow()          # blocked while open


def test_breaker_half_opens_then_closes():
    clk = _Clock()
    b = CircuitBreaker(failure_threshold=1, cooldown_seconds=10, clock=clk)
    b.record_failure()
    assert b.state == "open" and not b.allow()
    clk.t = 10                     # cooldown elapsed
    assert b.allow() and b.state == "half_open"
    b.record_success()
    assert b.state == "closed"


def test_fallback_uses_first_healthy():
    primary, secondary = _Adapter(text="primary"), _Adapter(text="secondary")
    chain = FallbackChain([primary, secondary])
    assert chain.complete([], []).text == "primary"
    assert secondary.calls == 0


def test_fallback_skips_failing_primary():
    primary, secondary = _Adapter(fail=True), _Adapter(text="secondary")
    chain = FallbackChain([primary, secondary], failure_threshold=1)
    assert chain.complete([], []).text == "secondary"


def test_fallback_raises_when_all_down():
    chain = FallbackChain([_Adapter(fail=True), _Adapter(fail=True)],
                          failure_threshold=1)
    with pytest.raises(AllAdaptersFailed):
        chain.complete([], [])


def test_recovered_primary_used_after_cooldown():
    clk = _Clock()
    primary, secondary = _Adapter(fail=True, text="primary"), _Adapter(text="secondary")
    chain = FallbackChain([primary, secondary], failure_threshold=1,
                          cooldown_seconds=5, clock=clk)
    assert chain.complete([], []).text == "secondary"   # primary trips open
    assert chain.breaker_states()[0] == "open"
    primary.fail = False
    clk.t = 5                                            # cooldown elapsed
    assert chain.complete([], []).text == "primary"     # half-open trial succeeds
    assert chain.breaker_states()[0] == "closed"
