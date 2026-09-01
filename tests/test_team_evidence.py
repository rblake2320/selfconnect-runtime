"""Team evidence bundle: the full delegation tree in one sealed, offline-
verifiable bundle (§3.5 for teams)."""
import json
import os
import subprocess
import sys
import zipfile

import pytest
import yaml

from scr.evidence import export_team_bundle, verify_bundle
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.state import Store
from scr.team import TeamRunner, load_team_from_dir

KEY = bytes.fromhex("ab" * 32)
CAPS = {"tools": ["fs_read"], "fs_read_roots": ["${WORKSPACE}"]}


def _team_run(tmp_path):
    ad = tmp_path / "src" / "agents"; ad.mkdir(parents=True)
    for name, body in {
        "lead": {"capabilities": CAPS, "delegates": ["researcher", "auditor"]},
        "researcher": {"capabilities": CAPS},
        "auditor": {"capabilities": CAPS},
    }.items():
        body = dict(body); body["name"] = name
        (ad / f"{name}.yaml").write_text(yaml.safe_dump(body))
    loaded = load_team_from_dir(str(tmp_path / "src"), str(tmp_path / "ws"))
    store = Store(str(tmp_path / "t.db"))
    scripts = {
        "lead": [ModelResponse("", (ToolCall("d1", "delegate", {"agent": "researcher", "task": "r"}),)),
                 ModelResponse("", (ToolCall("d2", "delegate", {"agent": "auditor", "task": "a"}),)),
                 ModelResponse("final report assembled")],
        "researcher": [ModelResponse("research findings")],
        "auditor": [ModelResponse("audit clean")],
    }
    runner = TeamRunner(store, loaded, lambda a: MockAdapter(list(scripts[a])),
                        lambda m: {})
    runner.run("lead", "run the security review")
    return store, runner.last_team_id


def test_team_bundle_shows_tree_and_verifies(tmp_path):
    store, tid = _team_run(tmp_path)
    out = str(tmp_path / "team.scevidence")
    export_team_bundle(store, tid, KEY, out, package={"name": "sce", "version": "1.0"})
    report = verify_bundle(out, KEY)
    assert report.ok, report.text
    assert report.report["team"] is True
    agents = {n["agent"] for n in report.report["delegation_tree"]}
    assert {"lead", "researcher", "auditor"} <= agents        # full tree present
    assert len(report.report["sessions"]) == 3                # all sessions verified
    assert "delegation tree" in report.text.lower()


def test_team_bundle_detects_child_session_tamper(tmp_path):
    store, tid = _team_run(tmp_path)
    out = str(tmp_path / "team.scevidence")
    export_team_bundle(store, tid, KEY, out, package={"name": "sce", "version": "1.0"})
    with zipfile.ZipFile(out) as z:
        bundle = json.loads(z.read("bundle.json"))
        others = {n: z.read(n) for n in z.namelist() if n != "bundle.json"}
    # tamper an event inside a child session
    for s in bundle["sessions"]:
        if s["agent"] == "researcher" and s["events"]:
            ev = json.loads(s["events"][0]["event"]); ev["_tamper"] = "x"
            s["events"][0]["event"] = json.dumps(ev, sort_keys=True, separators=(",", ":"))
            break
    tampered = str(tmp_path / "t.scevidence")
    with zipfile.ZipFile(tampered, "w") as z:
        z.writestr("bundle.json", json.dumps(bundle, sort_keys=True, separators=(",", ":")))
        for n, d in others.items():
            z.writestr(n, d)
    report = verify_bundle(tampered, KEY)
    assert not report.ok


def test_team_bundle_offline_verify_no_scr(tmp_path):
    store, tid = _team_run(tmp_path)
    out = str(tmp_path / "team.scevidence")
    export_team_bundle(store, tid, KEY, out)
    work = tmp_path / "offline"; work.mkdir()
    with zipfile.ZipFile(out) as z:
        for n in ("bundle.json", "bundle.hmac", "verify.py"):
            (work / n).write_bytes(z.read(n))
    r = subprocess.run([sys.executable, "-S", "verify.py", "bundle.json", "--key", KEY.hex()],
                       cwd=str(work), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERIFIED" in r.stdout and "delegation tree" in r.stdout.lower()
