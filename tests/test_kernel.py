import json

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Guards, Kernel, ToolSpec
from scr.ledger import Ledger
from scr.state import Store


def build(tmp_path, script, tools=None, manifest=None, guards=Guards()):
    store = Store(str(tmp_path / "k.db"))
    tools = tools or {}
    manifest = manifest or CapabilityManifest(tools=frozenset(tools.keys()))
    kernel = Kernel(store, MockAdapter(script), tools, manifest, guards)
    sid = store.create_session()
    return store, kernel, sid


def echo_tool(calls_log):
    def fn(args):
        calls_log.append(args)
        return f"echo:{args.get('v', '')}"
    return ToolSpec("echo", fn, idempotent=True, description="echo")


def test_plain_completion_no_tools(tmp_path):
    store, kernel, sid = build(tmp_path, [ModelResponse("final answer")])
    r = kernel.run(sid, "hello")
    assert r.stopped_reason == "completed" and r.final_text == "final answer"
    states = [j["state"] for j in store.journal_all(sid)]
    assert states == ["ASSEMBLE", "MODEL_CALL_INTENT", "MODEL_CALL_DONE", "FINALIZE"]


def test_tool_loop_executes_then_completes(tmp_path):
    log = []
    tool = echo_tool(log)
    script = [
        ModelResponse("", (ToolCall("c1", "echo", {"v": "one"}),)),
        ModelResponse("done"),
    ]
    store, kernel, sid = build(tmp_path, script, tools={"echo": tool})
    r = kernel.run(sid, "go")
    assert r.stopped_reason == "completed"
    assert log == [{"v": "one"}]
    # tool result folded into conversation for the second model call
    msgs = store.get_messages(sid)
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert json.loads(tool_msgs[0]["content"])["result"] == "echo:one"


def test_multiple_tool_calls_in_one_turn(tmp_path):
    log = []
    tool = echo_tool(log)
    script = [
        ModelResponse("", (ToolCall("c1", "echo", {"v": "a"}),
                            ToolCall("c2", "echo", {"v": "b"}))),
        ModelResponse("done"),
    ]
    store, kernel, sid = build(tmp_path, script, tools={"echo": tool})
    r = kernel.run(sid, "go")
    assert r.stopped_reason == "completed" and log == [{"v": "a"}, {"v": "b"}]


def test_undeclared_tool_denied_and_folded_not_crashed(tmp_path):
    """Model requests a tool outside the manifest → denial becomes a tool
    result; the run continues and the denial is a ledger event."""
    script = [
        ModelResponse("", (ToolCall("c1", "shell", {"cmd": "rm -rf /"}),)),
        ModelResponse("understood, stopping"),
    ]
    store, kernel, sid = build(tmp_path, script, tools={})
    r = kernel.run(sid, "go")
    assert r.stopped_reason == "completed"
    tool_msgs = [m for m in store.get_messages(sid) if m["role"] == "tool"]
    assert "DENIED by capability kernel" in json.loads(tool_msgs[0]["content"])["result"]
    events = [json.loads(row["event"]) for row in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=?", (sid,))]
    assert any(e["type"] == "cap_denied" for e in events)


def test_max_iterations_guard(tmp_path):
    log = []
    tool = echo_tool(log)
    script = [ModelResponse("", (ToolCall(f"c{i}", "echo", {"v": str(i)}),))
              for i in range(10)]
    store, kernel, sid = build(
        tmp_path, script, tools={"echo": tool},
        guards=Guards(max_iterations=3, cycle_repeat_threshold=99),
    )
    r = kernel.run(sid, "go")
    assert r.stopped_reason == "max_iterations" and r.iterations == 3


def test_loop_detection_identical_tool_call_sets(tmp_path):
    log = []
    tool = echo_tool(log)
    same = ModelResponse("", (ToolCall("c", "echo", {"v": "same"}),))
    script = [ModelResponse("", (ToolCall("c", "echo", {"v": "same"}),)) for _ in range(6)]
    store, kernel, sid = build(
        tmp_path, script, tools={"echo": tool},
        guards=Guards(cycle_repeat_threshold=3),
    )
    r = kernel.run(sid, "go")
    assert r.stopped_reason == "loop_detected"
    assert r.iterations == 3  # stopped at the threshold, not max_iterations


def test_token_budget_guard(tmp_path):
    store, kernel, sid = build(
        tmp_path, [ModelResponse("x")],
        guards=Guards(max_token_estimate=5),
    )
    r = kernel.run(sid, "this message alone blows the tiny budget")
    assert r.stopped_reason == "budget"


def test_idempotency_cache_prevents_double_execution(tmp_path):
    """Same idem_key → cached result reused, tool fn NOT called again."""
    from scr.kernel import _idem_key
    store, kernel, sid = build(tmp_path, [ModelResponse("x")])
    calls = []
    spec = ToolSpec("t", lambda a: (calls.append(1), "r1")[1], idempotent=True)
    key = _idem_key(sid, 1, "t", {"x": 1})
    store.tool_result_put(key, sid, "t", "r1")
    assert store.tool_result_get(key) == "r1"
    # second put with same key is ignored (INSERT OR IGNORE)
    store.tool_result_put(key, sid, "t", "DIFFERENT")
    assert store.tool_result_get(key) == "r1"


def test_ledger_written_and_verifies_after_run(tmp_path):
    log = []
    tool = echo_tool(log)
    script = [
        ModelResponse("", (ToolCall("c1", "echo", {"v": "a"}),)),
        ModelResponse("done"),
    ]
    store, kernel, sid = build(tmp_path, script, tools={"echo": tool})
    kernel.run(sid, "go")
    ledger = Ledger(store)
    r = ledger.verify(sid)
    assert r.ok and r.count >= 3  # model_call, tool_exec, model_call, finalize
    key = b"seal-key-000000000000000000000000"
    ledger.seal(sid, key)
    assert ledger.verify(sid, key).ok
