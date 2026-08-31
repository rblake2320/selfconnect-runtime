"""Manifest semver / deps / min-runtime + hot-reload (§3.4)."""
import os

import pytest

from scr.registry import PackageRegistry, check_requirements
from scr.semver import SemverError, parse, satisfies
from scr.signer import sign_package
from scr.signing import Keystore, generate_keypair


# ------------------------------------------------------------------ semver
def test_semver_parse_and_compare():
    assert parse("1.2.3") == (1, 2, 3)
    assert satisfies("1.2.3", ">=1.0.0")
    assert satisfies("1.2.3", "<=2.0.0")
    assert satisfies("1.2.3", "==1.2.3")
    assert not satisfies("1.2.3", ">1.2.3")
    assert not satisfies("0.9.0", ">=1.0.0")
    with pytest.raises(SemverError):
        parse("not-a-version")


# ---------------------------------------------------------- requirements
def test_min_runtime_enforced():
    m = {"runtime": {"min": "0.5.0"}}
    assert check_requirements(m, "0.2.0", {}) == \
        ["requires runtime >= 0.5.0, have 0.2.0"]
    assert check_requirements(m, "0.5.0", {}) == []
    assert check_requirements(m, "1.0.0", {}) == []


def test_dependency_constraints():
    m = {"requires": {"base": ">=1.0.0"}}
    assert "missing dependency" in check_requirements(m, "0.2.0", {})[0]
    assert check_requirements(m, "0.2.0", {"base": "1.2.0"}) == []
    bad = check_requirements(m, "0.2.0", {"base": "0.9.0"})
    assert "does not satisfy" in bad[0]


def test_model_requirements():
    m = {"model_requirements": {"min_context": 32000, "tool_calls": True}}
    problems = check_requirements(m, "0.2.0", {}, model_caps={"context": 8000, "tool_calls": False})
    assert any("context" in p for p in problems)
    assert any("tool calls" in p for p in problems)
    assert check_requirements(m, "0.2.0", {},
                              model_caps={"context": 128000, "tool_calls": True}) == []


def test_no_requirements_means_compatible():
    assert check_requirements({}, "0.2.0", {}) == []


# --------------------------------------------------------------- hot reload
def _signed(tmp_path, body):
    src = tmp_path / "src" / "agents"; src.mkdir(parents=True)
    (src / "a.yaml").write_bytes(body)
    priv, pub = generate_keypair()
    out = str(tmp_path / "ent.scpkg")
    sign_package(str(tmp_path / "src"), out, "ent", "1.0.0", priv)
    return out, priv, pub


def test_hot_reload_reverifies_and_returns_manifest(tmp_path):
    out, priv, pub = _signed(tmp_path, b"role: lead\n")
    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    reg.install(out)
    ok, manifest = reg.reload("ent")
    assert ok and manifest["name"] == "ent" and manifest["version"] == "1.0.0"


def test_hot_reload_refuses_tampered(tmp_path):
    import zipfile
    out, priv, pub = _signed(tmp_path, b"role: lead\n")
    ks = Keystore(); ks.add(pub)
    reg = PackageRegistry(str(tmp_path / "home"), ks)
    reg.install(out)
    stored = reg.get("ent").path
    # tamper the stored package on disk
    tmp = stored + ".x"
    with zipfile.ZipFile(stored) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for it in zin.namelist():
            d = zin.read(it)
            if it == "agents/a.yaml":
                d += b"# evil\n"
            zout.writestr(it, d)
    os.replace(tmp, stored)
    ok, manifest = reg.reload("ent")
    assert not ok and manifest is None      # refuses to serve tampered content
