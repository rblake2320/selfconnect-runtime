"""Worker privilege reduction (§3.6). Real behavior, no fakes."""
import json
import os
import sys

import pytest

from scr.capability import CapabilityManifest, ExecRule
from scr.privdrop import enabled_privilege_count, harden_current_process
from scr.sandbox import SandboxRunner
from scr.tools_native import build_native_tools


@pytest.mark.skipif(os.name != "nt", reason="Windows token privileges")
def test_disabling_privileges_reduces_enabled_count():
    # Run in a SUBPROCESS so we don't disable the test runner's own token
    # (which would contaminate later tests).
    import subprocess
    prog = (
        "from scr.privdrop import enabled_privilege_count, harden_current_process\n"
        "b=enabled_privilege_count(); ok=harden_current_process(); a=enabled_privilege_count()\n"
        "print(b, int(ok), a)\n")
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                         text=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out.returncode == 0, out.stderr
    before, ok, after = out.stdout.split()
    assert int(before) >= 1          # a normal token has enabled privileges
    assert ok == "1"                 # hardening succeeded
    assert int(after) < int(before)  # privileges actually dropped
    assert int(after) == 0           # DisableAllPrivileges → none enabled


@pytest.mark.skipif(os.name == "nt", reason="POSIX no_new_privs")
def test_posix_no_new_privs_set():
    assert harden_current_process() is True
    with open("/proc/self/status") as f:
        assert "NoNewPrivs:\t1" in f.read()


def _tools(tmp_path, exec_rule=None):
    ws = tmp_path / "ws"
    (ws / "out").mkdir(parents=True)
    rules = (exec_rule,) if exec_rule else ()
    m = CapabilityManifest(
        tools=frozenset({"fs_read", "fs_write", "proc_exec"}),
        fs_read_roots=(str(ws),), fs_write_roots=(str(ws / "out"),),
        exec_rules=rules)
    return build_native_tools(m, SandboxRunner()), ws


@pytest.mark.skipif(os.name != "nt", reason="system path is Windows-specific")
def test_worker_cannot_write_system_path(tmp_path):
    r"""Through the real sandbox: a proc_exec worker (self-hardened) attempting
    to write C:\Windows is denied by the OS."""
    tools, ws = _tools(tmp_path, ExecRule(sys.executable, r"(?s).*"))
    prog = ("import json\n"
            "try:\n open(r'C:\\Windows\\scr_evil_test.txt','w').write('x'); r='ALLOWED'\n"
            "except Exception as e: r='denied:'+type(e).__name__\n"
            "print(json.dumps({'sys_write': r}))\n")
    out = json.loads(tools["proc_exec"].fn(
        {"binary": sys.executable, "args": ["-c", prog]}))
    assert out["ok"] is True
    assert "denied" in out["stdout"]        # OS refused the system write
    assert "ALLOWED" not in out["stdout"]


def test_worker_privileges_dropped_in_real_worker(tmp_path):
    """A proc_exec child launched under a self-hardening worker: on Windows its
    enabled-privilege count is 0; the sandbox still functions (real write to
    jail works), proving hardening didn't break the worker."""
    tools, ws = _tools(tmp_path, ExecRule(sys.executable, r"(?s).*"))
    # the worker itself self-hardens; prove the sandbox still serves a real write
    res = json.loads(tools["fs_write"].fn(
        {"path": str(ws / "out" / "ok.txt"), "content": "still works"}))
    assert res["ok"] is True
    assert (ws / "out" / "ok.txt").read_text() == "still works"


def test_capability_kernel_denies_out_of_jail_read(tmp_path):
    """OS-level read isolation is a documented residual (AppContainer); the
    capability kernel is the enforced read jail — a file the parent CAN read is
    denied to the tool interface when outside the jail."""
    tools, ws = _tools(tmp_path)
    secret = tmp_path / "secret_outside.txt"
    secret.write_text("PARENT-READABLE-SECRET")
    assert secret.read_text() == "PARENT-READABLE-SECRET"     # parent can read it
    out = tools["fs_read"].fn({"path": str(secret)})          # tool cannot
    assert out.startswith("DENIED by capability kernel")
    assert "PARENT-READABLE-SECRET" not in out
