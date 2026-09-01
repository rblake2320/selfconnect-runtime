"""Delegation policy (§3.1) — runtime-enforced, ledger-provable. Every policy
decision is a ledger event so the evidence tree proves ENFORCEMENT, not just
delegation. This is the fix for the 'model wrote an Auditor section the ledger
says never ran' finding."""
import json
import os

import pytest
import yaml

from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import ToolSpec
from scr.state import Store
from scr.team import TeamLoadError, TeamRunner, load_team_from_dir

CAPS = {"tools": ["fs_read"], "fs_read_roots": ["${WORKSPACE}"]}


def _write(tmp_path, agents):
    ad = tmp_path / "src" / "agents"; ad.mkdir(parents=True, exist_ok=True)
    for name, body in agents.items():
        body = dict(body); body["name"] = name
        (ad / f"{name}.yaml").write_text(yaml.safe_dump(body))
    return str(tmp_path / "src")


def _runner(tmp_path, agents, scripts, tools=None):
    src = _write(tmp_path, agents)
    loaded = load_team_from_dir(src, str(tmp_path / "ws"))
    store = Store(":memory:")
    adapters = {a: MockAdapter(list(s)) for a, s in scripts.items()}
    return TeamRunner(store, loaded, lambda a: adapters[a],
                      lambda m: dict(tools or {})), store


def _events(store, sid):
    return [json.loads(r["event"]) for r in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,)).fetchall()]


def _delegate(agent, task="t"):
    return ToolCall(f"c-{agent}", "delegate", {"agent": agent, "task": task})


# ---------------------------------------------- policy references undeclared
def test_policy_referencing_undeclared_child_rejected_at_load(tmp_path):
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["researcher"],
                 "delegation_policy": {"required_children": ["auditor"]}},  # not a delegate
        "researcher": {"capabilities": CAPS},
    }
    with pytest.raises(TeamLoadError, match="undeclared child"):
        load_team_from_dir(_write(tmp_path, agents), str(tmp_path / "ws"))


# ------------------------------------------------------ required_children
def test_finalize_refused_until_required_child_completes(tmp_path):
    # lead is scripted to try to finalize immediately (no delegation); the
    # policy must refuse until it delegates to the required auditor.
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["researcher", "auditor"],
                 "delegation_policy": {"required_children": ["auditor"]}},
        "researcher": {"capabilities": CAPS},
        "auditor": {"capabilities": CAPS},
    }
    scripts = {
        "lead": [
            ModelResponse("done early"),                       # tries to finalize — REFUSED
            ModelResponse("", (_delegate("auditor"),)),        # now delegates auditor
            ModelResponse("final report with real audit"),     # now allowed to finalize
        ],
        "researcher": [ModelResponse("r")],
        "auditor": [ModelResponse("audit: risk medium")],
    }
    runner, store = _runner(tmp_path, agents, scripts)
    res = runner.run("lead", "review")
    assert res.stopped_reason == "completed"
    assert res.final_text == "final report with real audit"
    ev = _events(store, res.session_id)
    assert any(e.get("type") == "policy" and e.get("decision") == "finalize_refused" for e in ev)
    assert any(e.get("type") == "policy" and e.get("child") == "auditor"
               and e.get("decision") == "completed" for e in ev)


def test_run_cannot_complete_if_required_child_never_delegated(tmp_path):
    # lead keeps trying to finalize and never delegates the required auditor →
    # the run can NEVER report completed (hits the iteration guard instead).
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["auditor"],
                 "delegation_policy": {"required_children": ["auditor"]}},
        "auditor": {"capabilities": CAPS},
    }
    scripts = {"lead": [ModelResponse("trying to finalize")] * 40,
               "auditor": [ModelResponse("a")]}
    runner, store = _runner(tmp_path, agents, scripts)
    res = runner.run("lead", "review")
    assert res.stopped_reason != "completed"      # never allowed to finalize
    ev = _events(store, res.session_id)
    assert sum(1 for e in ev if e.get("decision") == "finalize_refused") >= 2


# ---------------------------------------------- max_delegations_per_child
def test_max_delegations_per_child_caps_storm(tmp_path):
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["researcher"],
                 "delegation_policy": {"max_delegations_per_child": 2}},
        "researcher": {"capabilities": CAPS},
    }
    scripts = {
        "lead": [ModelResponse("", (_delegate("researcher", f"t{i}"),)) for i in range(5)]
                + [ModelResponse("done")],
        "researcher": [ModelResponse(f"finding {i}") for i in range(6)],
    }
    runner, store = _runner(tmp_path, agents, scripts)
    res = runner.run("lead", "go")
    assert res.stopped_reason == "completed"
    ev = _events(store, res.session_id)
    # exactly 2 real delegations, the rest denied by policy
    assert sum(1 for e in ev if e.get("type") == "delegate") == 2
    assert sum(1 for e in ev if e.get("type") == "policy"
               and e.get("rule") == "max_delegations_per_child"
               and e.get("decision") == "denied") >= 1


# ---------------------------------------------- no_redelegate_after_denial
def test_no_redelegate_after_all_denied_blocks_retry_loop(tmp_path):
    # researcher's effective grants only fs_read but its model calls proc_exec
    # (not granted) → all-denied. lead re-delegates the SAME task → blocked.
    agents = {
        "lead": {"capabilities": {"tools": ["fs_read"]}, "delegates": ["researcher"],
                 "delegation_policy": {"no_redelegate_after_denial": True}},
        "researcher": {"capabilities": {"tools": ["fs_read"]}},
    }
    scripts = {
        "lead": [ModelResponse("", (_delegate("researcher", "same task"),)),
                 ModelResponse("", (_delegate("researcher", "same task"),)),  # retry — blocked
                 ModelResponse("gave up retrying")],
        # researcher tries a denied tool every time → all-denied run
        "researcher": [ModelResponse("", (ToolCall("w", "proc_exec", {"binary": "x", "args": []}),)),
                       ModelResponse("could not proceed")],
    }
    danger = ToolSpec("proc_exec", lambda a: "SHOULD NOT RUN", idempotent=False)
    runner, store = _runner(tmp_path, agents, scripts, tools={"proc_exec": danger})
    res = runner.run("lead", "go")
    assert res.stopped_reason == "completed"
    ev = _events(store, res.session_id)
    assert sum(1 for e in ev if e.get("type") == "delegate") == 1     # spawned once
    assert any(e.get("type") == "policy" and e.get("rule") == "no_redelegate_after_denial"
               and e.get("decision") == "denied" for e in ev)          # retry blocked + ledgered
