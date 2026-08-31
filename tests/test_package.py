"""Package build/read round-trip and manifest coverage."""
import os

from scr.package import (
    MANIFEST_NAME,
    SIGNATURE_NAME,
    Package,
    build_manifest,
    build_files_map,
)
from scr.signer import sign_package
from scr.signing import generate_keypair


def _src(tmp_path):
    src = tmp_path / "src"
    (src / "agents").mkdir(parents=True)
    (src / "mcp").mkdir(parents=True)
    (src / "agents" / "a.yaml").write_bytes(b"x: 1\n")   # byte-exact
    (src / "mcp" / "servers.yaml").write_bytes(b"servers: []\n")
    return str(src)


def test_manifest_covers_every_payload_file(tmp_path):
    src = _src(tmp_path)
    files = build_files_map(src)
    assert set(files) == {"agents/a.yaml", "mcp/servers.yaml"}
    assert all(len(h) == 64 for h in files.values())


def test_manifest_excludes_manifest_and_signature(tmp_path):
    src = _src(tmp_path)
    # place stray MANIFEST/SIGNATURE in the source; they must not self-hash
    (tmp_path / "src" / MANIFEST_NAME).write_text("{}")
    (tmp_path / "src" / SIGNATURE_NAME).write_text("{}")
    files = build_files_map(src)
    assert MANIFEST_NAME not in files and SIGNATURE_NAME not in files


def test_build_read_roundtrip(tmp_path):
    src = _src(tmp_path)
    priv, _ = generate_keypair()
    out = str(tmp_path / "p.scpkg")
    sign_package(src, out, "pkg", "2.3.4", priv)
    with Package(out) as pkg:
        assert pkg.manifest["name"] == "pkg"
        assert pkg.manifest["version"] == "2.3.4"
        assert pkg.signature is not None
        assert pkg.read_member("agents/a.yaml") == b"x: 1\n"
        assert set(pkg.actual_file_hashes()) == set(pkg.manifest["files"])
