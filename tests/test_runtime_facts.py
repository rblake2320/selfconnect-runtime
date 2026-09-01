"""Owner directives 2026-09-01: (a) ledger the grant block (what the agent was
TOLD) next to the enforced manifest hash — divergence between them is the
prompt-injection surface; (b) every team report ends with a ledger-derived
execution summary — the runtime's facts beside the model's prose, because the
model confabulated causes/work in three of three live runs."""
import json
import os

import yaml

from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import ToolSpec
from scr.state import Store
from scr.team import TeamRunner, load_team_from_dir, team_execution_summary

CAPS = {"tools": ["fs_read"], "fs_read_roots": ["${WORKSPACE}"]}


def _write(tmp_path, agents):
    ad = tmp_path / "src" / "agents"; ad.mkdir(parents=True, exist_ok=True)
    for name, body in agents.items():
        body = dict(body); body["name"] = name
        (ad / f"{name}.yaml").write_text(yaml.safe_dump(body))
    return str(tmp_path / "src")


def _events(store, sid):
    return [json.loads(r["event"]) for r in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,)).fetchall()]


# ---------------------------------------------------- grant-block ledgering
def test_grant_context_ledgered_next_to_enforced_manifest(tmp_path):
    import hashlib
    from scr.team import _caps_context, _eff_hash
    agents = {"lead": {"capabilities": CAPS, "delegates": ["worker"]},
              "worker": {"capabilities": CAPS}}
    scripts = {"lead": [ModelResponse("", (ToolCall("c", "delegate",
                        {"agent": "worker", "task": "t"}),)),
                        ModelResponse("done")],
               "worker": [ModelResponse("w")]}
    loaded = load_team_from_dir(_write(tmp_path, agents), str(tmp_path / "ws"))
    store = Store(":memory:")
    adapters = {a: MockAdapter(list(s)) for a, s in scripts.items()}
    runner = TeamRunner(store, loaded, lambda a: adapters[a], lambda m: {})
    res = runner.run("lead", "go")
    # every session (lead AND child) carries a grant_context event
    for m in store.team_members(runner.last_team_id):
        ev = _events(store, m["session_id"])
        gc = [e for e in ev if e.get("type") == "grant_context"]
        assert len(gc) == 1, f"missing grant_context for {m['agent']}"
        assert gc[0]["agent"] == m["agent"]
        assert len(gc[0]["eff_cap_sha256"]) == 64
        assert len(gc[0]["grant_block_sha256"]) == 64
        assert len(gc[0]["system_prompt_sha256"]) == 64
    # the child's hashes are recomputable from the effective manifest — what
    # was TOLD is bound to what is ENFORCED
    child_eff = loaded.team.effective_manifest("worker")
    child_sid = next(m["session_id"] for m in store.team_members(runner.last_team_id)
                     if m["agent"] == "worker")
    gc = next(e for e in _events(store, child_sid) if e.get("type") == "grant_context")
    assert gc["eff_cap_sha256"] == _eff_hash(child_eff)
    assert gc["grant_block_sha256"] == hashlib.sha256(
        _caps_context(child_eff).encode()).hexdigest()


# ------------------------------------------------ ledger-derived summary
def test_team_report_ends_with_ledger_derived_summary(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    target = ws / "code.py"; target.write_text("x = 1\n")
    agents = {"lead": {"capabilities": CAPS, "delegates": ["worker"]},
              "worker": {"capabilities": CAPS}}
    read_calls = []

    def fs_read(args):
        read_calls.append(args["path"])
        return open(args["path"], encoding="utf-8").read()

    tools = {"fs_read": ToolSpec("fs_read", fs_read, idempotent=True,
                                 parameters={"type": "object", "properties": {
                                     "path": {"type": "string"}},
                                     "required": ["path"]})}
    scripts = {
        "lead": [ModelResponse("", (ToolCall("c", "delegate",
                 {"agent": "worker", "task": "read it"}),)),
                 ModelResponse("report: reviewed the file")],
        "worker": [ModelResponse("", (ToolCall("r", "fs_read",
                   {"path": str(target)}),)),
                   ModelResponse("content seen")],
    }
    loaded = load_team_from_dir(_write(tmp_path, agents), str(ws))
    store = Store(":memory:")
    adapters = {a: MockAdapter(list(s)) for a, s in scripts.items()}
    runner = TeamRunner(store, loaded, lambda a: adapters[a],
                        lambda m: dict(tools))
    res = runner.run("lead", "go")
    assert res.final_text.startswith("report: reviewed the file")
    body = res.final_text
    assert "RUNTIME EXECUTION SUMMARY" in body
    assert "lead -> worker" in body                 # delegation edge, from chain
    assert str(target) in body                      # the file ACTUALLY read
    assert "fs_read x1" in body
    # regenerable standalone from the store — derived output, never model-made
    again = team_execution_summary(store, runner.last_team_id)
    assert str(target) in again and "lead -> worker" in again


def test_summary_states_none_when_nothing_was_read(tmp_path):
    agents = {"solo": {"capabilities": CAPS}}
    loaded = load_team_from_dir(_write(tmp_path, agents), str(tmp_path / "ws"))
    store = Store(":memory:")
    runner = TeamRunner(store, loaded,
                        lambda a: MockAdapter([ModelResponse("all good, trust me")]),
                        lambda m: {})
    res = runner.run("solo", "go")
    # the model's happy prose is followed by the runtime's blunt fact
    assert "files touched: NONE" in res.final_text


# ------------------------------------- frozen worker dispatch (RUN D crash)
def test_worker_cmd_frozen_vs_venv(monkeypatch):
    import sys as _sys
    from scr.sandbox import _worker_cmd
    assert _worker_cmd()[1:] == ["-s", "-m", "scr.worker"]      # venv path
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    assert _worker_cmd() == [_sys.executable, "__scr_worker__"]  # frozen path


def test_cli_dispatches_worker_token(monkeypatch):
    import scr.worker as worker_mod
    from scr.cli import main
    monkeypatch.setattr(worker_mod, "main", lambda: 7)
    assert main(["__scr_worker__"]) == 7


def test_restricted_env_carries_temp_for_bootloader(tmp_path):
    from scr.sandbox import restricted_env
    env = restricted_env(tmp_dir=str(tmp_path))
    assert env["TEMP"] == env["TMP"] == env["TMPDIR"] == str(tmp_path)
    # and without tmp_dir there is intentionally NO TEMP (the RUN-D state)
    assert "TEMP" not in restricted_env()


# --------------------------------------- tool errors become chain facts
def test_tool_error_result_is_ledgered_and_summarized(tmp_path):
    agents = {"lead": {"capabilities": CAPS, "delegates": ["worker"]},
              "worker": {"capabilities": CAPS}}
    crash = ToolSpec("fs_read", lambda a: "TOOL ERROR [worker_crash]: rc=-1 "
                     "stderr=[PYI-1:ERROR] Could not create temporary directory!",
                     idempotent=True,
                     parameters={"type": "object", "properties": {
                         "path": {"type": "string"}}, "required": ["path"]})
    scripts = {
        "lead": [ModelResponse("", (ToolCall("c", "delegate",
                 {"agent": "worker", "task": "read"}),)),
                 ModelResponse("report done")],
        "worker": [ModelResponse("", (ToolCall("r", "fs_read",
                   {"path": str(tmp_path)}),)),
                   ModelResponse("saw an error")],
    }
    loaded = load_team_from_dir(_write(tmp_path, agents), str(tmp_path / "ws"))
    store = Store(":memory:")
    adapters = {a: MockAdapter(list(s)) for a, s in scripts.items()}
    runner = TeamRunner(store, loaded, lambda a: adapters[a],
                        lambda m: {"fs_read": crash})
    res = runner.run("lead", "go")
    child_sid = next(m["session_id"] for m in store.team_members(runner.last_team_id)
                     if m["agent"] == "worker")
    ev = _events(store, child_sid)
    te = [e for e in ev if e.get("type") == "tool_error"]
    assert te and te[0]["class"] == "worker_crash"      # chain fact, not prose
    assert "ERROR[worker_crash]" in res.final_text       # surfaced in summary
