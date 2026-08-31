"""Multi-agent team execution (§3.1, §3.7) — adversarial suite (non-crash)."""
import json
import os

import pytest
import yaml

from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import ToolSpec
from scr.state import Store
from scr.team import (
    TeamLoadError,
    TeamRunner,
    load_team_from_dir,
    verify_team_mailbox,
)


def _write_agents(tmp_path, agents: dict) -> str:
    ad = tmp_path / "src" / "agents"
    ad.mkdir(parents=True, exist_ok=True)
    for name, body in agents.items():
        body = dict(body)
        body["name"] = name
        (ad / f"{name}.yaml").write_text(yaml.safe_dump(body))
    return str(tmp_path / "src")


def _runner(tmp_path, agents, scripts, tools=None, max_depth=4):
    src = _write_agents(tmp_path, agents)
    ws = str(tmp_path / "ws")
    os.makedirs(ws, exist_ok=True)
    loaded = load_team_from_dir(src, ws)
    store = Store(":memory:")
    adapters = {a: MockAdapter(list(s)) for a, s in scripts.items()}
    tset = tools or {}
    return TeamRunner(store, loaded, lambda a: adapters[a],
                      lambda m: dict(tset), max_depth=max_depth), store


CAPS = {"tools": ["fs_read"], "fs_read_roots": ["${WORKSPACE}"]}


def _events(store, sid):
    return [json.loads(r["event"]) for r in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,)).fetchall()]


# ------------------------------------------------------------------ load
def test_valid_team_loads_and_runs(tmp_path):
    agents = {
        "lead": {"role": "lead", "capabilities": CAPS, "delegates": ["worker"]},
        "worker": {"role": "worker", "capabilities": CAPS},
    }
    scripts = {
        "lead": [ModelResponse("", (ToolCall("c1", "delegate",
                                    {"agent": "worker", "task": "do the thing"}),)),
                 ModelResponse("assembled the worker's result")],
        "worker": [ModelResponse("worker completed the thing")],
    }
    runner, store = _runner(tmp_path, agents, scripts)
    res = runner.run("lead", "run the job")
    assert res.stopped_reason == "completed"
    assert res.final_text == "assembled the worker's result"
    tool_msgs = [m for m in store.get_messages(res.session_id) if m["role"] == "tool"]
    assert any("worker completed the thing" in m["content"] for m in tool_msgs)
    dele = [e for e in _events(store, res.session_id) if e.get("type") == "delegate"]
    assert dele and dele[0]["parent"] == "lead" and dele[0]["child"] == "worker"
    assert "eff_cap_sha256" in dele[0]
    assert [e for e in _events(store, res.session_id) if e.get("type") == "mailbox"]


def test_widening_child_rejected_at_load(tmp_path):
    agents = {
        "lead": {"capabilities": {"tools": ["fs_read"]}, "delegates": ["worker"]},
        "worker": {"capabilities": {"tools": ["fs_read", "proc_exec"]}},
    }
    src = _write_agents(tmp_path, agents)
    with pytest.raises(TeamLoadError, match="widens tools"):
        load_team_from_dir(src, str(tmp_path / "ws"))


def test_cycle_rejected_at_load(tmp_path):
    agents = {
        "a": {"capabilities": CAPS, "delegates": ["b"]},
        "b": {"capabilities": CAPS, "delegates": ["a"]},
    }
    src = _write_agents(tmp_path, agents)
    with pytest.raises(TeamLoadError):
        load_team_from_dir(src, str(tmp_path / "ws"))


def test_multiple_parents_rejected(tmp_path):
    agents = {
        "a": {"capabilities": CAPS, "delegates": ["c"]},
        "b": {"capabilities": CAPS, "delegates": ["c"]},
        "c": {"capabilities": CAPS},
    }
    src = _write_agents(tmp_path, agents)
    with pytest.raises(TeamLoadError):
        load_team_from_dir(src, str(tmp_path / "ws"))


# ---------------------------------------------------------- runtime denials
def test_child_tool_outside_parent_effective_denied_run_continues(tmp_path):
    agents = {
        "lead": {"capabilities": {"tools": ["fs_read"]}, "delegates": ["worker"]},
        "worker": {"capabilities": {"tools": ["fs_read"]}},
    }
    scripts = {
        "lead": [ModelResponse("", (ToolCall("c1", "delegate",
                                    {"agent": "worker", "task": "x"}),)),
                 ModelResponse("done")],
        "worker": [ModelResponse("", (ToolCall("w1", "proc_exec",
                                    {"binary": "x", "args": []}),)),
                   ModelResponse("worker finished despite the denial")],
    }
    danger = ToolSpec("proc_exec", lambda a: "SHOULD NOT RUN", idempotent=False)
    runner, store = _runner(tmp_path, agents, scripts, tools={"proc_exec": danger})
    res = runner.run("lead", "go")
    assert res.stopped_reason == "completed"
    members = store.team_members(runner.last_team_id)
    wsid = [m["session_id"] for m in members if m["agent"] == "worker"][0]
    ev = _events(store, wsid)
    assert any(e.get("type") == "cap_denied" and e.get("tool") == "proc_exec" for e in ev)


def test_depth_limit_stops_delegation(tmp_path):
    agents = {
        "a": {"capabilities": CAPS, "delegates": ["b"]},
        "b": {"capabilities": CAPS, "delegates": ["c"]},
        "c": {"capabilities": CAPS, "delegates": ["d"]},
        "d": {"capabilities": CAPS},
    }
    scripts = {
        "a": [ModelResponse("", (ToolCall("1", "delegate", {"agent": "b", "task": "t"}),)),
              ModelResponse("a done")],
        "b": [ModelResponse("", (ToolCall("2", "delegate", {"agent": "c", "task": "t"}),)),
              ModelResponse("b done")],
        "c": [ModelResponse("", (ToolCall("3", "delegate", {"agent": "d", "task": "t"}),)),
              ModelResponse("c done")],
        "d": [ModelResponse("d done")],
    }
    runner, store = _runner(tmp_path, agents, scripts, max_depth=1)
    res = runner.run("a", "go")
    assert res.stopped_reason == "completed"
    denied = False
    for m in store.team_members(runner.last_team_id):
        for e in _events(store, m["session_id"]):
            if e.get("type") == "delegate_denied" and e.get("reason") == "depth_limit":
                denied = True
    assert denied


def test_revoked_node_delegation_denied_and_ledgered(tmp_path):
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["worker"]},
        "worker": {"capabilities": CAPS},
    }
    scripts = {
        "lead": [ModelResponse("", (ToolCall("c1", "delegate",
                                    {"agent": "worker", "task": "x"}),)),
                 ModelResponse("lead proceeded without the worker")],
        "worker": [ModelResponse("should not run")],
    }
    runner, store = _runner(tmp_path, agents, scripts)
    runner.revoke("worker")
    res = runner.run("lead", "go")
    assert res.stopped_reason == "completed"
    tool_msgs = [m for m in store.get_messages(res.session_id) if m["role"] == "tool"]
    assert any("DENIED" in m["content"] for m in tool_msgs)
    assert any(e.get("type") == "delegate_denied" and "severed" in e.get("reason", "")
               for e in _events(store, res.session_id))


def test_mailbox_tamper_detected_on_fold(tmp_path):
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["worker"]},
        "worker": {"capabilities": CAPS},
    }
    scripts = {
        "lead": [ModelResponse("", (ToolCall("c1", "delegate",
                                    {"agent": "worker", "task": "x"}),)),
                 ModelResponse("done")],
        "worker": [ModelResponse("original worker result")],
    }
    runner, store = _runner(tmp_path, agents, scripts)
    runner.run("lead", "go")
    ok, problems = verify_team_mailbox(store, runner.last_team_id)
    assert ok, problems
    store.conn.execute(
        "UPDATE mailbox SET body=? WHERE id=(SELECT MAX(id) FROM mailbox)",
        ("FORGED RESULT",))
    ok2, problems2 = verify_team_mailbox(store, runner.last_team_id)
    assert not ok2 and problems2


def test_unknown_target_lists_available(tmp_path):
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["worker"]},
        "worker": {"capabilities": CAPS},
    }
    src = _write_agents(tmp_path, agents)
    loaded = load_team_from_dir(src, str(tmp_path / "ws"))
    # known resolves
    assert loaded.entry_for("lead") == "lead"
    # unknown raises with a listing of available agents/teams
    with pytest.raises(TeamLoadError, match="unknown team/agent"):
        loaded.entry_for("does-not-exist")
