"""Package shadow-install updates (§6): verify → shadow → self-test → promote,
with failed-promote rollback."""
import os

import pytest

from scr.gateway import ModelResponse
from scr.registry import PackageRegistry
from scr.signer import sign_package
from scr.signing import Keystore, generate_keypair


class _Model:
    """Stand-in customer model (the update FLOW is under test, not the model)."""
    def __init__(self, reply):
        self.reply = reply
    def complete(self, messages, tools):
        return ModelResponse(self.reply)


def _pkg(tmp_path, name, version, test_expect):
    src = tmp_path / f"src-{version}"
    (src / "agents").mkdir(parents=True)
    (src / "tests").mkdir(parents=True)
    (src / "agents" / "a.yaml").write_bytes(b"role: lead\n")
    (src / "tests" / "smoke.yaml").write_text(
        f"name: smoke\nprompt: hello\nexpect_contains: {test_expect}\n")
    priv, pub = generate_keypair()
    out = str(tmp_path / f"{name}-{version}.scpkg")
    sign_package(str(src), out, name, version, priv)
    return out, priv, pub


def test_shadow_update_promotes_on_passing_selftests(tmp_path):
    v1, priv, pub = _pkg(tmp_path, "ent", "1.0.0", "ready")
    # v2 signed by the SAME publisher key
    src2 = tmp_path / "src2" / "agents"; src2.mkdir(parents=True)
    (tmp_path / "src2" / "tests").mkdir(parents=True)
    (src2 / "a.yaml").write_bytes(b"role: lead v2\n")
    (tmp_path / "src2" / "tests" / "smoke.yaml").write_text(
        "name: smoke\nprompt: hello\nexpect_contains: ready\n")
    v2 = str(tmp_path / "ent-2.0.0.scpkg")
    sign_package(str(tmp_path / "src2"), v2, "ent", "2.0.0", priv)

    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    reg.install(v1)
    assert reg.get("ent").version == "1.0.0"

    out = reg.shadow_update(v2, _Model("all ready here"))
    assert out.promoted and out.version == "2.0.0"
    assert reg.get("ent").version == "2.0.0"          # active version advanced
    assert reg.verify_installed("ent").ok
    # no leftover shadow files
    assert not any(f.endswith(".shadow") for f in os.listdir(reg.dir))


def test_shadow_update_rolls_back_on_failing_selftests(tmp_path):
    v1, priv, pub = _pkg(tmp_path, "ent", "1.0.0", "ready")
    # v2 whose self-test expects a token the model won't produce
    src2 = tmp_path / "src2" / "agents"; src2.mkdir(parents=True)
    (tmp_path / "src2" / "tests").mkdir(parents=True)
    (src2 / "a.yaml").write_bytes(b"role: bad v2\n")
    (tmp_path / "src2" / "tests" / "smoke.yaml").write_text(
        "name: smoke\nprompt: hello\nexpect_contains: SOMETHING_MODEL_WONT_SAY\n")
    v2 = str(tmp_path / "ent-2.0.0.scpkg")
    sign_package(str(tmp_path / "src2"), v2, "ent", "2.0.0", priv)

    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    reg.install(v1)

    out = reg.shadow_update(v2, _Model("just a normal greeting"))
    assert not out.promoted and out.reason == "self-tests failed"
    assert reg.get("ent").version == "1.0.0"          # ROLLBACK: still on v1
    assert reg.verify_installed("ent").ok
    assert not any(f.endswith(".shadow") for f in os.listdir(reg.dir))  # shadow discarded


def test_shadow_update_rejects_unverified_package(tmp_path):
    v1, priv, pub = _pkg(tmp_path, "ent", "1.0.0", "ready")
    # v2 signed by a DIFFERENT (untrusted) key
    src2 = tmp_path / "src2" / "agents"; src2.mkdir(parents=True)
    (src2 / "a.yaml").write_bytes(b"x\n")
    otherpriv, _ = generate_keypair()
    v2 = str(tmp_path / "ent-2.0.0.scpkg")
    sign_package(str(tmp_path / "src2"), v2, "ent", "2.0.0", otherpriv)

    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    reg.install(v1)
    out = reg.shadow_update(v2, _Model("ready"))
    assert not out.promoted and "verify failed" in out.reason
    assert reg.get("ent").version == "1.0.0"          # untrusted update never promoted
