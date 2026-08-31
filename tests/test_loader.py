"""Adversarial package loader tests: valid load, unsigned, wrong key,
single-leaf tamper (localized), manifest/content mismatch, revocation."""
import json
import os
import zipfile

import pytest

from scr.gateway import ModelResponse
from scr.loader import run_selftests, verify_package
from scr.package import MANIFEST_NAME, SIGNATURE_NAME, Package
from scr.signer import sign_package
from scr.signing import Keystore, RevocationList, generate_keypair


def _make_src(tmp_path):
    src = tmp_path / "src"
    (src / "agents").mkdir(parents=True)
    (src / "tests").mkdir(parents=True)
    (src / "agents" / "lead.yaml").write_text("role: lead\n")
    (src / "policies" if False else src / "agents" / "worker.yaml").write_text("role: worker\n")
    (src / "tests" / "smoke.yaml").write_text(
        "name: smoke\nprompt: say hi\nexpect_contains: hello\n")
    return str(src)


def _trusted(tmp_path):
    priv, pub = generate_keypair()
    out = str(tmp_path / "pkg.scpkg")
    sign_package(_make_src(tmp_path), out, "enterprise", "1.0.0", priv)
    ks = Keystore()
    ks.add(pub)
    return out, ks, priv, pub


def test_valid_package_loads(tmp_path):
    out, ks, _, _ = _trusted(tmp_path)
    res = verify_package(out, ks)
    assert res.ok, res.error
    assert res.package == "enterprise" and res.version == "1.0.0"


def test_unsigned_rejected(tmp_path):
    out, ks, _, _ = _trusted(tmp_path)
    # strip SIGNATURE by rewriting the zip without it
    stripped = str(tmp_path / "unsigned.scpkg")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(stripped, "w") as zout:
        for item in zin.namelist():
            if item != SIGNATURE_NAME:
                zout.writestr(item, zin.read(item))
    res = verify_package(stripped, ks)
    assert not res.ok and res.error == "unsigned"


def test_untrusted_key_rejected(tmp_path):
    out, _, _, _ = _trusted(tmp_path)
    empty_ks = Keystore()  # trusts nobody
    res = verify_package(out, empty_ks)
    assert not res.ok and res.error == "untrusted_key"


def test_single_leaf_tamper_localized(tmp_path):
    out, ks, _, _ = _trusted(tmp_path)
    tampered = str(tmp_path / "tampered.scpkg")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(tampered, "w") as zout:
        for item in zin.namelist():
            data = zin.read(item)
            if item == "agents/worker.yaml":
                data = data + b"# injected\n"     # flip one leaf
            zout.writestr(item, data)
    res = verify_package(tampered, ks)
    assert not res.ok and res.error == "tampered_file"
    assert "agents/worker.yaml" in res.detail   # localized


def test_extra_file_is_manifest_mismatch(tmp_path):
    out, ks, _, _ = _trusted(tmp_path)
    mutated = str(tmp_path / "extra.scpkg")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(mutated, "w") as zout:
        for item in zin.namelist():
            zout.writestr(item, zin.read(item))
        zout.writestr("agents/sneaky.yaml", "role: sneaky\n")  # not in manifest
    res = verify_package(mutated, ks)
    assert not res.ok and res.error == "unexpected_files"


def test_wrong_key_signature_rejected(tmp_path):
    # Sign with key A, present key B as the signing key in SIGNATURE.
    src = _make_src(tmp_path)
    privA, pubA = generate_keypair()
    out = str(tmp_path / "a.scpkg")
    sign_package(src, out, "enterprise", "1.0.0", privA)
    # Forge SIGNATURE to claim a different (trusted) public key.
    _, pubB = generate_keypair()
    forged = str(tmp_path / "forged.scpkg")
    with zipfile.ZipFile(out) as zin, zipfile.ZipFile(forged, "w") as zout:
        for item in zin.namelist():
            if item == SIGNATURE_NAME:
                sig = json.loads(zin.read(item))
                sig["public_key"] = pubB      # claim B signed it (it didn't)
                zout.writestr(item, json.dumps(sig))
            else:
                zout.writestr(item, zin.read(item))
    ks = Keystore()
    ks.add(pubB)
    res = verify_package(forged, ks)
    assert not res.ok and res.error == "bad_signature"


def test_downgrade_to_revoked_version_rejected(tmp_path):
    out, ks, priv, pub = _trusted(tmp_path)
    rl = RevocationList.create([("enterprise", "1.0.0")], priv, pub)
    res = verify_package(out, ks, revocations=rl)
    assert not res.ok and res.error == "revoked"


def test_revoked_via_untrusted_list_still_loads(tmp_path):
    """A revocation list signed by an untrusted key must NOT deny a good
    package (fail closed on trust, not on the attacker's word)."""
    out, ks, _, _ = _trusted(tmp_path)
    rogue_priv, rogue_pub = generate_keypair()      # not in keystore
    rl = RevocationList.create([("enterprise", "1.0.0")], rogue_priv, rogue_pub)
    res = verify_package(out, ks, revocations=rl)
    assert res.ok  # rogue revocation ignored


class _AlwaysAdapter:
    def __init__(self, text):
        self._text = text

    def complete(self, messages, tools):
        return ModelResponse(self._text)


def test_selftests_pass_and_fail(tmp_path):
    out, ks, _, _ = _trusted(tmp_path)
    good = run_selftests(out, _AlwaysAdapter("well hello there"), ks)
    assert good["verified"] and good["ok"]
    bad = run_selftests(out, _AlwaysAdapter("goodbye"), ks)
    assert bad["verified"] and not bad["ok"]


def test_selftests_refuse_unverified_package(tmp_path):
    out, _, _, _ = _trusted(tmp_path)
    res = run_selftests(out, _AlwaysAdapter("hello"), Keystore())  # untrusted
    assert not res["verified"] and not res["ok"]
