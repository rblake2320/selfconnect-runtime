"""CLI end-to-end: init, model add, package verify, ledger verify, license
status, doctor — invoking main() with args and capturing stdout."""
import os
import time

import pytest

from scr.cli import main
from scr.evidence import export_bundle, seal_on_close
from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse, ToolCall
from scr.kernel import Kernel, ToolSpec
from scr.license import License
from scr.signer import sign_package
from scr.signing import generate_keypair
from scr.state import Store


def test_init_creates_home(tmp_path, capsys):
    rc = main(["--home", str(tmp_path), "init"])
    assert rc == 0
    assert (tmp_path / "config.json").exists()
    assert (tmp_path / "packages").is_dir()
    assert "initialized" in capsys.readouterr().out


def test_model_add_records_ref(tmp_path, capsys):
    main(["--home", str(tmp_path), "init"])
    rc = main(["--home", str(tmp_path), "model", "add", "local",
               "--adapter", "ollama", "--model", "llama3.1"])
    assert rc == 0
    listing = main(["--home", str(tmp_path), "model", "list"])
    out = capsys.readouterr().out
    assert "local" in out and "ollama" in out


def test_package_verify(tmp_path, capsys):
    src = tmp_path / "src" / "agents"
    src.mkdir(parents=True)
    (src / "a.yaml").write_bytes(b"role: lead\n")
    priv, pub = generate_keypair()
    pkg = str(tmp_path / "p.scpkg")
    sign_package(str(tmp_path / "src"), pkg, "ent", "1.0.0", priv)
    trust = tmp_path / "trust.txt"
    trust.write_text(pub + "\n")
    rc = main(["package", "verify", pkg, "--trust", str(trust)])
    assert rc == 0
    assert "VERIFIED ent 1.0.0" in capsys.readouterr().out


def test_package_verify_untrusted_fails(tmp_path, capsys):
    src = tmp_path / "src" / "agents"
    src.mkdir(parents=True)
    (src / "a.yaml").write_bytes(b"x\n")
    priv, _ = generate_keypair()
    pkg = str(tmp_path / "p.scpkg")
    sign_package(str(tmp_path / "src"), pkg, "ent", "1.0.0", priv)
    rc = main(["package", "verify", pkg])   # no trust file → untrusted
    assert rc == 1
    assert "REJECTED" in capsys.readouterr().out


def test_ledger_verify(tmp_path, capsys):
    home = tmp_path / "home"
    main(["--home", str(home), "init"])
    store = Store(os.path.join(str(home), "scr.db"))
    sid = store.create_session()
    Kernel(store, MockAdapter([ModelResponse("done")]), {},
           CapabilityManifest()).run(sid, "hi")
    key = "ab" * 32
    seal_on_close(store, sid, bytes.fromhex(key))
    bundle = str(tmp_path / "e.scevidence")
    export_bundle(store, sid, bytes.fromhex(key), bundle)
    store.close()
    rc = main(["ledger", "verify", bundle, "--key", key])
    assert rc == 0
    assert "VERIFIED" in capsys.readouterr().out


def test_license_status(tmp_path, capsys):
    priv, pub = generate_keypair()
    now = 1_700_000_000.0
    lic = License.issue("acme", 3, ["run"], now + 86400, priv, pub)
    lic_path = tmp_path / "lic.json"
    lic_path.write_text(lic.to_text())
    rc = main(["license", "status", str(lic_path), "--pubkey", pub, "--now", str(now)])
    assert rc == 0
    assert "valid" in capsys.readouterr().out


def test_doctor(tmp_path, capsys):
    main(["--home", str(tmp_path), "init"])
    rc = main(["--home", str(tmp_path), "doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "integrity: ok" in out
