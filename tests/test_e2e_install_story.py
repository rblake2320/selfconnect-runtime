"""§5 Installation-Story E2E — the exact chain a customer runs, driven through
the `scr` CLI:

    init → model add → package install → run → session export → ledger verify

Offline by default (a local Ollama-shaped stub stands in for the model). Set
SCR_OLLAMA_URL=http://<SPARK_IP>:11434 and SCR_OLLAMA_MODEL=<name> to run the
model step LIVE against the DGX Spark — this is also the Ollama closure harness.
The MSI install of §5 step 1 is validated separately (see docs/CLEAN_BOX_TEST.md);
this test covers steps 2–5 end to end.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from scr.cli import main
from scr.signer import sign_package
from scr.signing import generate_keypair


class _Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"message": {"content": "security review ready"},
                           "prompt_eval_count": 3, "eval_count": 2}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def model_endpoint():
    live = os.environ.get("SCR_OLLAMA_URL")
    if live:
        yield live, os.environ.get("SCR_OLLAMA_MODEL", "llama3.1"), True
        return
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", "smoke", False
    srv.shutdown()


def test_install_story_end_to_end(tmp_path, capsys, model_endpoint):
    url, model, is_live = model_endpoint
    home = str(tmp_path / "home")

    # sign an enterprise-style package + a trust file (publisher pin)
    src = tmp_path / "src" / "agents"
    src.mkdir(parents=True)
    (src / "lead.yaml").write_bytes(b"role: lead\n")
    priv, pub = generate_keypair()
    pkg = str(tmp_path / "sce.scpkg")
    sign_package(str(tmp_path / "src"), pkg, "selfconnect-enterprise", "1.0.0", priv)
    trust = tmp_path / "trust.txt"
    trust.write_text(pub + "\n")

    # 2. init
    assert main(["--home", home, "init"]) == 0
    # 3. model add (Ollama — customer brings the model)
    assert main(["--home", home, "model", "add", "spark", "--adapter", "ollama",
                 "--model", model, "--base-url", url]) == 0
    # live smoke test of the endpoint
    assert main(["--home", home, "model", "test", "spark"]) == 0
    # 4. package install (verifies signature + pins publisher key)
    assert main(["--home", home, "package", "install", pkg, "--trust", str(trust)]) == 0
    capsys.readouterr()
    # 5. run a task against the customer model
    assert main(["--home", home, "run",
                 "Run the security review on C:/target/repo"]) == 0
    out = capsys.readouterr().out
    assert "completed" in out
    session_id = out.split("session ")[1].split()[0]

    # evidence: export sealed bundle, verify offline
    key = "ab" * 32
    bundle = str(tmp_path / "evidence.scevidence")
    assert main(["--home", home, "session", "export", session_id, bundle,
                 "--key", key, "--seal"]) == 0
    capsys.readouterr()
    assert main(["ledger", "verify", bundle, "--key", key]) == 0
    report = capsys.readouterr().out
    assert "VERIFIED" in report

    if is_live:
        print(f"\n[LIVE] model={model} endpoint={url}")
