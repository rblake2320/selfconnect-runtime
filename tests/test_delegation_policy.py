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


# ------------------------------------------------- require_nonempty_result
def test_empty_child_result_not_counted_as_completed(tmp_path):
    # RUN-B live finding: a child returned 0 chars yet satisfied
    # required_children. With require_nonempty_result the empty run is not
    # counted; finalize stays refused until a NON-empty child run completes.
    agents = {
        "lead": {"capabilities": CAPS, "delegates": ["researcher"],
                 "delegation_policy": {"required_children": ["researcher"],
                                       "require_nonempty_result": True}},
        "researcher": {"capabilities": CAPS},
    }
    scripts = {
        "lead": [ModelResponse("", (_delegate("researcher", "t1"),)),
                 ModelResponse("done"),                      # refused: empty run not counted
                 ModelResponse("", (_delegate("researcher", "t2"),)),
                 ModelResponse("final with real findings")],
        "researcher": [ModelResponse(""),                    # empty-handed
                       ModelResponse("finding: real content")],
    }
    runner, store = _runner(tmp_path, agents, scripts)
    res = runner.run("lead", "go")
    assert res.stopped_reason == "completed"
    assert res.final_text == "final with real findings"
    ev = _events(store, res.session_id)
    assert any(e.get("type") == "policy" and e.get("rule") == "require_nonempty_result"
               and e.get("decision") == "not_counted" for e in ev)
    assert any(e.get("decision") == "finalize_refused" for e in ev)


# ------------------------------------------------- caps context injection
class _PromptCapture:
    """Adapter that records the system prompt it is given, then finalizes."""
    def __init__(self):
        self.system = []

    def complete(self, messages, tools):
        self.system.append(next(
            (m["content"] for m in messages if m["role"] == "system"), ""))
        return ModelResponse("ok")


def test_agent_is_told_its_effective_grants(tmp_path):
    # RUN-B live finding: agents had real fs roots granted but were never TOLD
    # them — the model blind-guessed /workspace etc. and every call was denied.
    # The runtime must inject the EFFECTIVE grants into the system prompt.
    agents = {"solo": {"capabilities": CAPS}}
    src = _write(tmp_path, agents)
    ws = str(tmp_path / "ws")
    loaded = load_team_from_dir(src, ws)
    store = Store(":memory:")
    cap = _PromptCapture()
    runner = TeamRunner(store, loaded, lambda a: cap, lambda m: {})
    runner.run("solo", "task")
    sys_prompt = cap.system[0]
    assert "capability grant" in sys_prompt
    assert os.path.abspath(ws) in sys_prompt      # the RESOLVED workspace root
    assert "fs_read" in sys_prompt


def test_workspace_placeholder_substituted_in_authored_prompt(tmp_path):
    agents = {"solo": {"capabilities": CAPS,
                       "system_prompt": "Review the repo at ${WORKSPACE}."}}
    ws = str(tmp_path / "ws")
    loaded = load_team_from_dir(_write(tmp_path, agents), ws)
    assert ws in loaded.specs["solo"].system_prompt
    assert "${WORKSPACE}" not in loaded.specs["solo"].system_prompt


# ------------------------------------------------------------- provenance
def test_provenance_ledgered_and_surfaced_in_team_bundle(tmp_path):
    # RUN A+B finding: both VERIFIED bundles showed package:{} — the evidence
    # could not answer "which signed package governed this run". Provenance is
    # ledgered inside the lead session's hash chain and surfaced in the bundle.
    from scr.evidence import export_team_bundle
    import json as _json
    import zipfile
    agents = {"lead": {"capabilities": CAPS, "delegates": ["researcher"]},
              "researcher": {"capabilities": CAPS}}
    scripts = {"lead": [ModelResponse("", (_delegate("researcher"),)),
                        ModelResponse("done")],
               "researcher": [ModelResponse("r")]}
    src = _write(tmp_path, agents)
    loaded = load_team_from_dir(src, str(tmp_path / "ws"))
    store = Store(":memory:")
    adapters = {a: MockAdapter(list(s)) for a, s in scripts.items()}
    prov = {"package": "selfconnect-enterprise", "version": "1.0.0",
            "key_id": "k1", "content_sha256": "ab" * 32}
    runner = TeamRunner(store, loaded, lambda a: adapters[a], lambda m: {},
                        provenance=prov)
    res = runner.run("lead", "go")
    # 1) inside the lead session's hash chain
    ev = _events(store, res.session_id)
    pe = next(e for e in ev if e.get("type") == "provenance")
    assert pe["package"] == "selfconnect-enterprise"
    assert pe["content_sha256"] == "ab" * 32
    # 2) surfaced in the exported bundle without being passed explicitly
    out = str(tmp_path / "t.scevidence")
    export_team_bundle(store, runner.last_team_id, b"k" * 32, out)
    with zipfile.ZipFile(out) as z:
        bundle = _json.loads(z.read("bundle.json"))
    assert bundle["package"]["package"] == "selfconnect-enterprise"
    assert bundle["package"]["version"] == "1.0.0"
