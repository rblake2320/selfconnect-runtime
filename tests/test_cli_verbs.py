"""G1 CLI verbs: package install/list, run (against a local Ollama-shaped
stub), session list/export, ledger verify, backup/restore, model test."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scr.cli import main
from scr.signer import sign_package
from scr.signing import generate_keypair


# ------------------------------------------------ local Ollama-shaped stub
class _OllamaStub(BaseHTTPRequestHandler):
    reply = "ready"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({"message": {"content": self._reply()},
                           "prompt_eval_count": 2, "eval_count": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply(self):
        return type(self).reply

    def log_message(self, *a):
        pass


@pytest.fixture
def ollama_stub():
    srv = HTTPServer(("127.0.0.1", 0), _OllamaStub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _signed_pkg(tmp_path):
    src = tmp_path / "src" / "agents"
    src.mkdir(parents=True)
    (src / "a.yaml").write_bytes(b"role: lead\n")
    priv, pub = generate_keypair()
    out = str(tmp_path / "ent.scpkg")
    sign_package(str(tmp_path / "src"), out, "ent", "1.0.0", priv)
    trust = tmp_path / "trust.txt"
    trust.write_text(pub + "\n")
    return out, str(trust)


def test_package_install_and_list(tmp_path, capsys):
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    pkg, trust = _signed_pkg(tmp_path)
    assert main(["--home", home, "package", "install", pkg, "--trust", trust]) == 0
    assert "installed ent 1.0.0" in capsys.readouterr().out
    assert main(["--home", home, "package", "list"]) == 0
    assert "ent 1.0.0" in capsys.readouterr().out


def test_model_test_failure_path(tmp_path, capsys):
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    main(["--home", home, "model", "add", "dead", "--adapter", "ollama",
          "--model", "x", "--base-url", "http://127.0.0.1:1"])
    capsys.readouterr()
    rc = main(["--home", home, "model", "test", "dead"])
    assert rc == 1
    assert "FAILED" in capsys.readouterr().out


def test_run_against_ollama_stub_then_export_and_verify(tmp_path, capsys, ollama_stub):
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    main(["--home", home, "model", "add", "local", "--adapter", "ollama",
          "--model", "smoke", "--base-url", ollama_stub])
    capsys.readouterr()

    # model test hits the stub live → OK
    assert main(["--home", home, "model", "test", "local"]) == 0
    assert "OK" in capsys.readouterr().out

    # run a task → completed, produces a session
    assert main(["--home", home, "run", "say hi"]) == 0
    out = capsys.readouterr().out
    assert "completed" in out
    session_id = out.split("session ")[1].split()[0]

    # session list shows the job
    assert main(["--home", home, "session", "list"]) == 0
    assert "done" in capsys.readouterr().out

    # export sealed evidence, then verify it
    key = "ab" * 32
    bundle = str(tmp_path / "ev.scevidence")
    assert main(["--home", home, "session", "export", session_id, bundle,
                 "--key", key, "--seal"]) == 0
    capsys.readouterr()
    assert main(["ledger", "verify", bundle, "--key", key]) == 0
    assert "VERIFIED" in capsys.readouterr().out


def test_backup_restore_via_cli(tmp_path, capsys):
    home = str(tmp_path / "home")
    main(["--home", home, "init"])
    main(["--home", home, "model", "add", "m", "--adapter", "ollama", "--model", "x"])
    key = "cd" * 32
    archive = str(tmp_path / "b.scbak")
    capsys.readouterr()
    assert main(["--home", home, "backup", archive, "--key", key]) == 0
    # wipe config, restore, confirm model returns
    os.remove(os.path.join(home, "config.json"))
    assert main(["--home", home, "restore", archive, "--key", key]) == 0
    capsys.readouterr()
    main(["--home", home, "model", "list"])
    assert "m" in capsys.readouterr().out
