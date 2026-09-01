"""Content migration: the selfconnect-enterprise package builds, signs, loads,
and its self-tests pass by running a REAL multi-agent team.

The package self-test drives the orchestrator → researcher → auditor team. A
conversation-aware stand-in model plays the roles here (deterministic, offline);
the LIVE proof against Ollama qwen3.6:27b is recorded in STATUS.md /
docs/CONTENT_MIGRATION.md.
"""
import os
import zipfile

import pytest

from scr.gateway import ModelResponse, ToolCall
from scr.loader import run_selftests, verify_package
from scr.signer import sign_package
from scr.signing import Keystore, generate_keypair

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "packages", "selfconnect-enterprise")


def _build(tmp_path):
    priv, pub = generate_keypair()
    out = str(tmp_path / "ent.scpkg")
    sign_package(SRC, out, "selfconnect-enterprise", "1.0.0", priv)
    ks = Keystore()
    ks.add(pub)
    return out, ks


def test_package_source_is_a_team():
    for rel in ("agents/lead.yaml", "agents/researcher.yaml", "agents/auditor.yaml",
                "team.yaml", "policies/default.yaml", "tests/team_review.yaml"):
        assert os.path.exists(os.path.join(SRC, rel)), rel


def test_package_builds_and_verifies(tmp_path):
    out, ks = _build(tmp_path)
    res = verify_package(out, ks)
    assert res.ok, res.error
    assert res.package == "selfconnect-enterprise"


def test_tamper_localized(tmp_path):
    out, ks = _build(tmp_path)
    tampered = str(tmp_path / "t.scpkg")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "agents/researcher.yaml":
                data += b"# tamper\n"
            zout.writestr(item, data)
    res = verify_package(tampered, ks)
    assert not res.ok and "agents/researcher.yaml" in res.detail


class _TeamStandIn:
    """Conversation-aware model: as the orchestrator it delegates to researcher
    then auditor then assembles; as a child it returns a role finding. Keys off
    the agent's system prompt + the number of tool results so far."""

    def complete(self, messages, tools):
        system = next((m["content"] for m in messages if m["role"] == "system"), "").lower()
        tool_results = [m for m in messages if m["role"] == "tool"]
        if "orchestrator" in system:
            if len(tool_results) == 0:
                return ModelResponse("", (ToolCall("d1", "delegate",
                                    {"agent": "researcher", "task": "gather findings"}),))
            if len(tool_results) == 1:
                return ModelResponse("", (ToolCall("d2", "delegate",
                                    {"agent": "auditor", "task": "assess risk"}),))
            return ModelResponse("security review complete: findings gathered and risk assessed")
        if "researcher" in system:
            return ModelResponse("findings: 2 issues in dependencies")
        if "auditor" in system:
            return ModelResponse("risk verdict: medium")
        return ModelResponse("ok")


def test_team_selftest_runs_real_multi_agent(tmp_path):
    out, ks = _build(tmp_path)
    result = run_selftests(out, _TeamStandIn(), ks)
    assert result["verified"]
    assert result["ok"], result["results"]
    r = result["results"][0]
    assert r["name"] == "security-team-review"
    assert r["team"] is True                 # exercised the team, not a single turn
