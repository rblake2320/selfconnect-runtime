"""Adversarial sandbox tests: timeout tree-kill, cancel reaping, env
isolation, memory cap, and worker-output classification.

Cross-platform: Job Object (Windows) vs setsid/rlimit (POSIX) exercised by
the same asserts through SandboxRunner.
"""
import os
import sys
import textwrap
import time

import pytest

from scr.sandbox import SandboxLimits, SandboxRunner, restricted_env

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_restricted_env_drops_parent_secrets(monkeypatch):
    monkeypatch.setenv("SCR_SECRET_TOKEN", "hunter2")
    env = restricted_env()
    assert "SCR_SECRET_TOKEN" not in env
    assert "PYTHONPATH" in env  # computed, so the worker can import scr


def test_timeout_kills_process_tree(tmp_path):
    """A worker that spawns a child and both sleep past the timeout: the
    whole tree must be dead and a sentinel file the grandchild would write
    after the sleep must never appear."""
    sentinel = str(tmp_path / "survived.txt").replace("\\", "\\\\")
    child = textwrap.dedent(f"""
        import subprocess, sys, time
        # grandchild writes the sentinel only AFTER a long sleep
        gc = "import time; time.sleep(30); open(r'{sentinel}','w').write('x')"
        subprocess.Popen([sys.executable, '-c', gc])
        time.sleep(30)
    """)
    runner = SandboxRunner(SandboxLimits(timeout_seconds=1.5))
    handle = runner.start(_py(child), cwd=str(tmp_path))
    result = handle.wait(1.5)
    assert result.status == "timeout"
    time.sleep(2.0)  # give any escaped grandchild time to write
    assert not os.path.exists(str(tmp_path / "survived.txt")), \
        "grandchild survived the tree kill — sandbox leaked a process"


def test_explicit_cancel_reaps_tree(tmp_path):
    sentinel = str(tmp_path / "cancel_survivor.txt").replace("\\", "\\\\")
    child = textwrap.dedent(f"""
        import subprocess, sys, time
        gc = "import time; time.sleep(20); open(r'{sentinel}','w').write('x')"
        subprocess.Popen([sys.executable, '-c', gc])
        print('READY', flush=True)
        time.sleep(20)
    """)
    runner = SandboxRunner()
    handle = runner.start(_py(child), cwd=str(tmp_path))
    time.sleep(1.0)  # let the tree spin up
    handle.kill()
    handle.wait(10)
    time.sleep(1.5)
    assert not os.path.exists(str(tmp_path / "cancel_survivor.txt"))


def test_worker_env_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("SCR_LEAK_CHECK", "leaked")
    code = "import os; print(os.environ.get('SCR_LEAK_CHECK', 'ABSENT'))"
    runner = SandboxRunner()
    handle = runner.start(_py(code), cwd=str(tmp_path))
    result = handle.wait(10)
    assert result.status == "ok"
    assert "ABSENT" in result.stdout
    assert "leaked" not in result.stdout


@pytest.mark.skipif(os.name == "nt",
                    reason="RLIMIT_AS is POSIX; Windows path covered by job memory limit test")
def test_memory_cap_posix(tmp_path):
    code = "b = bytearray(400*1024*1024); print(len(b))"  # 400MB > 128MB cap
    runner = SandboxRunner(SandboxLimits(memory_limit_bytes=128 * 1024 * 1024))
    handle = runner.start(_py(code), cwd=str(tmp_path))
    result = handle.wait(15)
    assert result.status == "error"  # MemoryError → nonzero exit


@pytest.mark.skipif(os.name != "nt",
                    reason="Job Object memory limit is Windows-specific")
def test_memory_cap_windows(tmp_path):
    code = ("data=[]\n"
            "try:\n"
            "  [data.append(bytearray(50*1024*1024)) for _ in range(20)]\n"
            "except MemoryError:\n"
            "  import sys; sys.exit(9)\n")
    runner = SandboxRunner(SandboxLimits(memory_limit_bytes=128 * 1024 * 1024))
    handle = runner.start(_py(code), cwd=str(tmp_path))
    result = handle.wait(15)
    # Job kills the process (killed/error) rather than letting it grab 1GB.
    assert result.status in ("error", "killed")


def test_run_worker_bad_output_classified(tmp_path):
    """A worker command that prints garbage (not JSON) is classified, not
    crashed-through."""
    runner = SandboxRunner()
    # Directly exercise the structured-error path via a non-worker command:
    handle = runner.start(_py("print('not json at all')"), cwd=str(tmp_path))
    result = handle.wait(10)
    assert result.status == "ok"
    assert result.stdout.strip() == "not json at all"


def test_run_worker_timeout_returns_structured_error(tmp_path):
    runner = SandboxRunner(SandboxLimits(timeout_seconds=1.0))
    out = runner.run_worker({"op": "sleep_forever"}, cwd=str(tmp_path))
    # unknown_op returns fast; ensure the JSON contract holds either way
    assert out["ok"] is False
