"""SBOM + signed release artifacts (§8)."""
import json
import os

import pytest

from scr.release import (
    generate_sbom,
    parse_pinned_deps,
    sign_artifact,
    verify_artifact,
)
from scr.signing import Keystore, generate_keypair

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parse_pinned_deps_from_real_pyproject():
    deps = parse_pinned_deps(os.path.join(ROOT, "pyproject.toml"))
    # the actual pinned runtime deps must be present and exactly versioned
    assert deps.get("cryptography") == "50.0.1"
    assert deps.get("fastapi") == "0.115.6"
    assert "pyyaml" in deps


def test_sbom_is_valid_cyclonedx():
    deps = parse_pinned_deps(os.path.join(ROOT, "pyproject.toml"))
    sbom = generate_sbom("selfconnect-runtime", "0.2.0", deps)
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["metadata"]["component"]["name"] == "selfconnect-runtime"
    names = {c["name"] for c in sbom["components"]}
    assert {"cryptography", "fastapi", "pyyaml"} <= names
    for c in sbom["components"]:
        assert c["purl"].startswith("pkg:pypi/")     # every component has a PURL
    # serializes to JSON cleanly
    json.dumps(sbom)


def test_artifact_sign_and_verify(tmp_path):
    artifact = tmp_path / "scr-0.2.0.msi"
    artifact.write_bytes(b"pretend installer bytes" * 100)
    priv, pub = generate_keypair()
    manifest = sign_artifact(str(artifact), priv, pub)
    ks = Keystore(); ks.add(pub)
    ok, msg = verify_artifact(str(artifact), manifest, ks)
    assert ok, msg


def test_tampered_artifact_fails(tmp_path):
    artifact = tmp_path / "a.bin"
    artifact.write_bytes(b"original")
    priv, pub = generate_keypair()
    manifest = sign_artifact(str(artifact), priv, pub)
    artifact.write_bytes(b"tampered")                # change after signing
    ks = Keystore(); ks.add(pub)
    ok, msg = verify_artifact(str(artifact), manifest, ks)
    assert not ok and "digest" in msg


def test_untrusted_signing_key_rejected(tmp_path):
    artifact = tmp_path / "a.bin"
    artifact.write_bytes(b"bytes")
    priv, pub = generate_keypair()
    manifest = sign_artifact(str(artifact), priv, pub)
    ok, msg = verify_artifact(str(artifact), manifest, Keystore())   # trusts nobody
    assert not ok and "pinned" in msg


def test_forged_signature_rejected(tmp_path):
    artifact = tmp_path / "a.bin"
    artifact.write_bytes(b"bytes")
    priv, pub = generate_keypair()
    manifest = sign_artifact(str(artifact), priv, pub)
    manifest["signature"] = "00" * 64
    ks = Keystore(); ks.add(pub)
    ok, msg = verify_artifact(str(artifact), manifest, ks)
    assert not ok and "signature" in msg
