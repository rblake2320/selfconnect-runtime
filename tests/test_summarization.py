"""Summarization-on-overflow (§3.1): a long session degrades gracefully — the
model's context is compacted while the store keeps the full history."""
from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse
from scr.kernel import Guards, Kernel
from scr.state import Store


class _RecordingAdapter:
    """Real adapter that records exactly what context it was handed."""
    def __init__(self, reply):
        self.reply = reply
        self.seen = None

    def complete(self, messages, tools):
        self.seen = [dict(m) for m in messages]
        return ModelResponse(self.reply)


def _big_history(store, sid, n, size):
    for i in range(n):
        store.add_message(sid, "user" if i % 2 == 0 else "assistant",
                          f"msg{i} " + ("x" * size))


def test_overflow_compacts_context_but_completes(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    sid = store.create_session()
    # 20 messages of ~800 chars → well over a 500-token summarize threshold.
    _big_history(store, sid, 20, 800)
    adapter = _RecordingAdapter("done")
    guards = Guards(summarize_at_tokens=500, summarize_keep_recent=4,
                    max_token_estimate=1_000_000)
    kernel = Kernel(store, adapter, {}, CapabilityManifest(), guards=guards)

    res = kernel.run(sid, "final question")
    assert res.stopped_reason == "completed"          # graceful, not a budget stop

    seen = adapter.seen
    # compacted view: system + a SUMMARY + the last few messages (not all 21)
    assert seen[0]["role"] == "system"
    assert any(m["content"].startswith("[SUMMARY of") for m in seen)
    assert len(seen) < 21                              # fewer than the full history
    # the summary preserves a trace of an early message
    summary = [m for m in seen if m["content"].startswith("[SUMMARY of")][0]
    assert "msg0" in summary["content"]
    # the most recent real turn (the user question) is still present verbatim
    assert any("final question" in m["content"] for m in seen)


def test_full_history_preserved_in_store(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    sid = store.create_session()
    _big_history(store, sid, 20, 800)
    guards = Guards(summarize_at_tokens=500, summarize_keep_recent=4,
                    max_token_estimate=1_000_000)
    Kernel(store, _RecordingAdapter("done"), {}, CapabilityManifest(),
           guards=guards).run(sid, "q")
    # store keeps EVERYTHING (evidence): 20 seeded + 1 user + 1 assistant reply
    roles = [m["role"] for m in store.get_messages(sid)]
    assert len(roles) == 22
    assert "msg0" in store.get_messages(sid)[0]["content"]   # oldest still there


def test_no_compaction_below_threshold(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    sid = store.create_session()
    store.add_message(sid, "user", "hi")
    adapter = _RecordingAdapter("done")
    Kernel(store, adapter, {}, CapabilityManifest(),
           guards=Guards(summarize_at_tokens=150_000)).run(sid, "q")
    assert not any(m["content"].startswith("[SUMMARY") for m in adapter.seen)
