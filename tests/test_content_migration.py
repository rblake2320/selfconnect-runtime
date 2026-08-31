"""Content migration: the selfconnect-enterprise package builds, signs, loads,
and its self-tests pass against a stand-in customer model.

(Ollama live self-test is tracked OPEN in docs/CONTENT_MIGRATION.md — Ollama
was not reachable during authoring, so a scripted adapter stands in here.)
"""
import os
import zipfile

import pytest

from scr.gateway import ModelResponse
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


def test_package_source_present():
    for rel in ("agents/lead.yaml", "agents/worker.yaml", "policies/default.yaml",
                "mcp/servers.yaml", "tests/smoke.yaml"):
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
            if item == "agents/worker.yaml":
                data += b"# tamper\n"
            zout.writestr(item, data)
    res = verify_package(tampered, ks)
    assert not res.ok and "agents/worker.yaml" in res.detail


class _CustomerModel:
    """Stand-in for the customer-supplied model (Ollama in production)."""

    def complete(self, messages, tools):
        user = " ".join(m["content"] for m in messages if m["role"] == "user").lower()
        if "greeting" in user:
            return ModelResponse("ready to help — hello")
        if "runtime" in user:
            return ModelResponse("SelfConnect")
        return ModelResponse("ok")


def test_selftests_pass_against_customer_model(tmp_path):
    out, ks = _build(tmp_path)
    result = run_selftests(out, _CustomerModel(), ks)
    assert result["verified"]
    assert result["ok"], result["results"]
    names = {r["name"] for r in result["results"]}
    assert {"smoke-greeting", "identity-check"} <= names
