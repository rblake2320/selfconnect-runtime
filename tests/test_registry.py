"""G3 — package registry: install verifies, and every execution re-verifies
the stored package. Tamper-on-disk or revocation after install → run refused.
"""
import os
import zipfile

import pytest

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel, ToolSpec
from scr.registry import PackageRegistry
from scr.sessions import SessionManager
from scr.signer import sign_package
from scr.signing import Keystore, RevocationList, generate_keypair
from scr.state import Store


def _make_pkg(tmp_path, name="ent", version="1.0.0"):
    src = tmp_path / "src" / "agents"
    src.mkdir(parents=True)
    (src / "a.yaml").write_bytes(b"role: lead\n")
    priv, pub = generate_keypair()
    out = str(tmp_path / f"{name}.scpkg")
    sign_package(str(tmp_path / "src"), out, name, version, priv)
    return out, priv, pub


def test_install_verifies_and_lists(tmp_path):
    out, _, pub = _make_pkg(tmp_path)
    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    res = reg.install(out)
    assert res.ok
    installed = reg.list_installed()
    assert len(installed) == 1 and installed[0].name == "ent"
    assert reg.verify_installed("ent").ok


def test_install_rejects_untrusted(tmp_path):
    out, _, _ = _make_pkg(tmp_path)
    reg = PackageRegistry(str(tmp_path / "home"), Keystore())  # trusts nobody
    res = reg.install(out)
    assert not res.ok and res.error == "untrusted_key"
    assert reg.list_installed() == []


def test_tamper_on_disk_after_install_is_caught_at_execution(tmp_path):
    out, _, pub = _make_pkg(tmp_path)
    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    reg.install(out)
    # Swap the STORED package for a tampered copy (attacker with disk access).
    stored = reg.get("ent").path
    tampered = stored + ".tmp"
    with zipfile.ZipFile(stored) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "agents/a.yaml":
                data += b"# injected\n"
            zout.writestr(item, data)
    os.replace(tampered, stored)
    res = reg.verify_installed("ent")
    assert not res.ok and res.error == "tampered_file"


def test_revocation_after_install_refuses_at_execution(tmp_path):
    out, priv, pub = _make_pkg(tmp_path)
    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    reg.install(out)
    assert reg.verify_installed("ent").ok
    # Publisher revokes this version afterwards.
    reg.revocations = RevocationList.create([("ent", "1.0.0")], priv, pub)
    res = reg.verify_installed("ent")
    assert not res.ok and res.error == "revoked"


def test_session_refused_when_package_fails_verification(tmp_path):
    out, _, pub = _make_pkg(tmp_path)
    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    reg.install(out)
    stored = reg.get("ent").path

    store = Store(":memory:")
    ran = []

    def factory(s, sid):
        tool = ToolSpec("noop", lambda a: ran.append(1) or "ok", idempotent=True)
        return Kernel(s, MockAdapter([
            ModelResponse("", (ToolCall("c1", "noop", {}),)),
            ModelResponse("done"),
        ]), {"noop": tool}, CapabilityManifest(tools=frozenset({"noop"})))

    mgr = SessionManager(store, factory, package_guard=reg.session_guard("ent"))

    # healthy install → runs
    job = mgr.enqueue("go", "k1")
    assert mgr.run_job(job.job_id).stopped_reason == "completed"
    assert ran == [1]

    # now corrupt the stored package on disk → next run is refused, tool never runs
    with open(stored, "r+b") as f:
        f.seek(0)
        f.write(b"XXXX")   # destroy the zip
    ran.clear()
    job2 = mgr.enqueue("go again", "k2")
    res = mgr.run_job(job2.job_id)
    assert res.stopped_reason.startswith("package_unverified")
    assert ran == []
    assert mgr.status(job2.job_id)["status"] == "refused"
