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


# ------------------------------- RUN-E crash: a tool must never kill the run
def test_malformed_tool_call_folds_instead_of_crashing(tmp_path):
    """RUN E: qwen called fs_write without 'content'; the raw KeyError killed
    the whole frozen process mid-team-run. Reproduce the exact call through a
    real kernel run — it must fold to a TOOL ERROR, ledger it, and CONTINUE."""
    from scr.capability import CapabilityManifest
    from scr.kernel import Kernel
    from scr.sandbox import SandboxRunner
    from scr.tools_native import build_native_tools
    ws = tmp_path / "ws"; (ws / "out").mkdir(parents=True)
    manifest = CapabilityManifest(tools=frozenset({"fs_write"}),
                                  fs_write_roots=(str(ws / "out"),),
                                  fs_read_roots=(str(ws),))
    tools = build_native_tools(manifest, SandboxRunner())
    store = Store(":memory:")
    adapter = MockAdapter([
        ModelResponse("", (ToolCall("w1", "fs_write",
                       {"path": str(ws / "out" / "r.md")}),)),   # NO content
        ModelResponse("recovered and finished"),
    ])
    k = Kernel(store, adapter, tools, manifest)
    sid = store.create_session()
    res = k.run(sid, "write the report")
    assert res.stopped_reason == "completed"          # runtime survived
    assert res.final_text == "recovered and finished"
    ev = [json.loads(r["event"]) for r in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,))]
    te = [e for e in ev if e.get("type") == "tool_error"]
    assert te and te[0]["class"] == "bad_args"        # folded + chain fact


def test_tool_fn_raising_any_exception_is_folded(tmp_path):
    # defense in depth: even a tool whose fn RAISES cannot kill the kernel
    from scr.capability import CapabilityManifest
    from scr.kernel import Kernel
    def bomb(args):
        raise RuntimeError("boom")
    tools = {"t": ToolSpec("t", bomb, idempotent=True)}
    manifest = CapabilityManifest(tools=frozenset({"t"}))
    store = Store(":memory:")
    adapter = MockAdapter([
        ModelResponse("", (ToolCall("c1", "t", {}),)),
        ModelResponse("done"),
    ])
    k = Kernel(store, adapter, tools, manifest)
    sid = store.create_session()
    res = k.run(sid, "go")
    assert res.stopped_reason == "completed"
    ev = [json.loads(r["event"]) for r in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,))]
    assert any(e.get("type") == "tool_error"
               and e.get("class") == "tool_exception" for e in ev)


def test_session_export_refuses_unknown_or_empty_id(tmp_path, capsys):
    # RUN E: `session export ""` produced a 0-event bundle that VERIFIED.
    import pytest as _pytest
    from scr.cli import main
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    out = str(tmp_path / "x.scevidence")
    for bad in ("", "deadbeef" * 4):
        with _pytest.raises(SystemExit, match="no such session"):
            main(["--home", home, "session", "export", bad, out,
                  "--key", "ab" * 32])
        assert not os.path.exists(out)


# ---------------------- RUN-E review finding M2: vault traversal — REFUTED
def test_vault_name_sanitization_defeats_traversal(tmp_path):
    """qwen's security review (RUN E) claimed vault sanitize() permits ../
    traversal. Empirically REFUTED — separators are stripped so every
    hostile name lands inside the vault dir. Kept as a permanent
    adversarial regression so it stays true."""
    from scr.vault import Vault
    v = Vault.__new__(Vault)
    v.dir = str(tmp_path / "vaultdir")
    attacks = ["..", r"..\..\evil", "../../evil", r"C:\Windows\x",
               "a:b:stream", ".", r"..\..\..\Users\x", "....//....//etc"]
    for name in attacks:
        p = os.path.abspath(v._path(name))
        assert os.path.commonpath([p, os.path.abspath(v.dir)]) == \
            os.path.abspath(v.dir), f"escaped vault: {name!r} -> {p}"


# ---------------- class sweep: everywhere model output meets runtime code
def test_adapter_exception_stops_gracefully_not_crash():
    """Same class as RUN-E's P0, other direction: a TimeoutError (or any
    adapter I/O failure) mid-run must journal a graceful stop, never
    stack-trace the process (RUN A attempt 1 crashed exactly this way)."""
    from scr.capability import CapabilityManifest
    from scr.kernel import Kernel

    class _DyingAdapter:
        def complete(self, messages, tools):
            raise TimeoutError("model took too long")

    store = Store(":memory:")
    k = Kernel(store, _DyingAdapter(), {}, CapabilityManifest())
    sid = store.create_session()
    res = k.run(sid, "go")                       # must NOT raise
    assert res.stopped_reason == "model_error"
    ev = [json.loads(r["event"]) for r in store.conn.execute(
        "SELECT event FROM ledger WHERE session_id=? ORDER BY seq", (sid,))]
    me = [e for e in ev if e.get("type") == "model_error"]
    assert me and me[0]["class"] == "TimeoutError"


def test_malformed_tool_arguments_json_folds_to_empty():
    # OpenAI-compat: the arguments string is MODEL-CONTROLLED; malformed JSON
    # folds to {} so the tool's bad_args validation corrects the model.
    from scr.gateway import OpenAICompatAdapter
    a = OpenAICompatAdapter("http://h/v1", "k", "m")
    payload = {"choices": [{"message": {
        "content": "",
        "tool_calls": [
            {"id": "1", "function": {"name": "fs_read",
                                     "arguments": "{not json!!"}},
            {"id": "2", "function": {"name": "fs_read",
                                     "arguments": "[1,2,3]"}},   # non-dict
            {"id": "3", "function": {"name": "fs_read",
                                     "arguments": "{\"path\": \"x\"}"}},
        ]}}]}
    resp = a.parse_response(payload)
    assert [c.arguments for c in resp.tool_calls] == [{}, {}, {"path": "x"}]


def test_child_abnormal_stop_yields_explanatory_text(tmp_path):
    # a child that stops without a result must hand the parent an explanation,
    # not None (which crashed the delegate tool's len() before the fold).
    class _Dying:
        def complete(self, messages, tools):
            raise TimeoutError("down")

    agents = {"lead": {"capabilities": CAPS, "delegates": ["worker"]},
              "worker": {"capabilities": CAPS}}
    loaded = load_team_from_dir(_write(tmp_path, agents), str(tmp_path / "ws"))
    store = Store(":memory:")
    lead_adapter = MockAdapter([
        ModelResponse("", (ToolCall("c", "delegate",
                       {"agent": "worker", "task": "t"}),)),
        ModelResponse("reported the failure honestly"),
    ])
    adapters = {"lead": lead_adapter, "worker": _Dying()}
    runner = TeamRunner(store, loaded, lambda a: adapters[a], lambda m: {})
    res = runner.run("lead", "go")
    assert res.stopped_reason == "completed"          # parent survived
    assert res.final_text.startswith("reported the failure honestly")
    # the parent SAW the explanation via the mailbox delivery
    row = store.conn.execute(
        "SELECT content FROM messages WHERE role='tool' AND content LIKE ?",
        ('%CHILD STOPPED [model_error]%',)).fetchone()
    assert row is not None


# ---------------- owner bug: session list showed nothing for a team home
def test_session_list_shows_team_sessions(tmp_path, capsys):
    """`session list` listed only JOBS; team runs create sessions directly, so
    a team home printed nothing. It must show every session with its team id,
    agent, depth, status, and start time."""
    from scr.cli import main
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    # drive a real (mock-model) team run INTO the home's store
    agents = {"lead": {"capabilities": CAPS, "delegates": ["worker"]},
              "worker": {"capabilities": CAPS}}
    scripts = {"lead": [ModelResponse("", (ToolCall("c", "delegate",
                        {"agent": "worker", "task": "t"}),)),
                        ModelResponse("done")],
               "worker": [ModelResponse("w")]}
    loaded = load_team_from_dir(_write(tmp_path, agents), str(tmp_path / "ws"))
    store = Store(os.path.join(home, "scr.db"))
    adapters = {a: MockAdapter(list(s)) for a, s in scripts.items()}
    runner = TeamRunner(store, loaded, lambda a: adapters[a], lambda m: {})
    runner.run("lead", "go")
    store.conn.close()
    capsys.readouterr()
    assert main(["--home", home, "session", "list"]) == 0
    out = capsys.readouterr().out
    assert f"team={runner.last_team_id}" in out
    assert "agent=lead" in out and "depth=0" in out
    assert "agent=worker" in out and "depth=1" in out
    assert "started=" in out and "status=" in out


# -------------- owner finding: review must never write into the target
def test_output_binding_keeps_workspace_read_only(tmp_path):
    """RUN E/F: the team wrote its report INTO the reviewed repo. ${OUTPUT}
    binds write roots to the run home's output dir; the workspace root is
    absent from the effective write roots."""
    agents = {"lead": {"capabilities": {
        "tools": ["fs_read", "fs_write"],
        "fs_read_roots": ["${WORKSPACE}"],
        "fs_write_roots": ["${OUTPUT}"]}}}
    ws = str(tmp_path / "customer-repo"); os.makedirs(ws)
    out = str(tmp_path / "home" / "out"); os.makedirs(out)
    loaded = load_team_from_dir(_write(tmp_path, agents), ws, out)
    m = loaded.specs["lead"].manifest
    assert m.fs_write_roots == (out,)          # deliverables → run home
    assert ws in m.fs_read_roots               # target readable
    assert all(not r.startswith(ws) for r in m.fs_write_roots)


def test_output_placeholder_without_output_dir_fails_fast(tmp_path):
    import pytest as _pytest
    from scr.team import TeamLoadError
    agents = {"lead": {"capabilities": {"tools": ["fs_write"],
                                        "fs_write_roots": ["${OUTPUT}"]}}}
    with _pytest.raises(TeamLoadError, match="OUTPUT"):
        load_team_from_dir(_write(tmp_path, agents), str(tmp_path / "ws"))


def test_cli_run_does_not_mutate_workspace(tmp_path):
    """The old CLI created <workspace>/out at startup — mutating the
    customer's repo before the model did anything. A run attempt must leave
    the workspace byte-for-byte untouched."""
    import pytest as _pytest
    from scr.cli import main
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    ws = tmp_path / "customer-repo"; ws.mkdir()
    (ws / "code.py").write_text("x = 1\n")
    before = sorted(os.listdir(ws))
    with _pytest.raises(SystemExit):           # no packages → unknown team
        main(["--home", home, "run", "--workspace", str(ws), "team", "task"])
    assert sorted(os.listdir(ws)) == before    # NOTHING created in the target
    assert not (ws / "out").exists()
    assert os.path.isdir(os.path.join(home, "out"))   # output lives at home
