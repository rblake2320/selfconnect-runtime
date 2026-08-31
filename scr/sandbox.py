"""Sandboxed worker subprocess execution.

Every native tool call runs in a separate worker process with:
  * a restricted environment (explicit allowlist + computed vars only);
  * a working-directory jail (cwd set inside an allowed root);
  * a wall-clock timeout enforced by the parent;
  * a memory cap;
  * whole-tree kill on timeout or cancel — no orphaned children.

Windows: the worker is placed in a Job Object with
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and a per-process memory limit; children
inherit job membership, so TerminateJobObject (or closing the handle) reaps
the entire tree.
POSIX: the worker starts a new session (setsid); RLIMIT_AS caps memory;
os.killpg(SIGKILL) reaps the tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# Environment variables copied from the parent when present. Everything else
# is dropped — secrets in the service environment never reach a worker.
_WINDOWS_ENV_ALLOWLIST = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)
_POSIX_ENV_ALLOWLIST = ("LANG", "LC_ALL", "TZ")


def restricted_env(extra: Optional[dict[str, str]] = None,
                   tmp_dir: Optional[str] = None) -> dict[str, str]:
    """Build the worker environment from scratch: allowlist + computed vars."""
    allow = _WINDOWS_ENV_ALLOWLIST if os.name == "nt" else _POSIX_ENV_ALLOWLIST
    env: dict[str, str] = {}
    for key in allow:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    # The worker must be able to import the scr package.
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONIOENCODING"] = "utf-8"
    if tmp_dir:
        env["TEMP"] = env["TMP"] = env["TMPDIR"] = tmp_dir
    if extra:
        env.update(extra)
    return env


# ------------------------------------------------------------ Windows job
if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008

    def _make_job(memory_limit_bytes: int, max_processes: int) -> int:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        )
        info.BasicLimitInformation.ActiveProcessLimit = max_processes
        info.ProcessMemoryLimit = memory_limit_bytes
        ok = _kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            _kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        return job


@dataclass
class SandboxResult:
    status: str          # ok | timeout | killed | error
    stdout: str = ""
    stderr: str = ""
    returncode: Optional[int] = None
    detail: str = ""


class SandboxProc:
    """Handle for a running sandboxed process tree. kill() reaps the tree."""

    def __init__(self, proc: subprocess.Popen, job_handle: Optional[int],
                 stdin_data: Optional[bytes]):
        self._proc = proc
        self._job = job_handle
        self._stdin_data = stdin_data
        self._killed = False

    @property
    def pid(self) -> int:
        return self._proc.pid

    def wait(self, timeout: float) -> SandboxResult:
        try:
            out, err = self._proc.communicate(input=self._stdin_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            # communicate() again to drain pipes after the tree is dead
            try:
                out, err = self._proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                out, err = b"", b""
            return SandboxResult("timeout",
                                 out.decode("utf-8", "replace"),
                                 err.decode("utf-8", "replace"),
                                 self._proc.returncode,
                                 "wall-clock timeout; process tree killed")
        finally:
            self._release_job_after_exit()
        status = "killed" if self._killed else ("ok" if self._proc.returncode == 0 else "error")
        return SandboxResult(status,
                             out.decode("utf-8", "replace"),
                             err.decode("utf-8", "replace"),
                             self._proc.returncode)

    def kill(self) -> None:
        """Kill the entire process tree. Idempotent."""
        self._killed = True
        if os.name == "nt":
            if self._job is not None:
                _kernel32.TerminateJobObject(self._job, 1)
            else:
                self._proc.kill()
        else:
            import signal
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def _release_job_after_exit(self) -> None:
        # Once every process in the job has exited, closing the handle is
        # a no-op kill-wise; while any survive, KILL_ON_JOB_CLOSE reaps them.
        if os.name == "nt" and self._job is not None:
            _kernel32.CloseHandle(self._job)
            self._job = None


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: float = 60.0
    memory_limit_bytes: int = 512 * 1024 * 1024
    max_processes: int = 8


class SandboxRunner:
    """Spawns sandboxed process trees. One instance per runtime."""

    def __init__(self, limits: SandboxLimits = SandboxLimits(),
                 tmp_dir: Optional[str] = None):
        self.limits = limits
        self.tmp_dir = tmp_dir
        import threading
        self._live: list[SandboxProc] = []
        self._live_lock = threading.Lock()

    def _register(self, proc: "SandboxProc") -> None:
        with self._live_lock:
            self._live.append(proc)

    def _unregister(self, proc: "SandboxProc") -> None:
        with self._live_lock:
            if proc in self._live:
                self._live.remove(proc)

    def kill_all(self) -> int:
        """Kill every in-flight worker process tree spawned via run_worker.
        Returns how many were killed. Used by session cancel (G5)."""
        with self._live_lock:
            live = list(self._live)
        for proc in live:
            proc.kill()
        return len(live)

    def start(self, argv: list[str], cwd: str,
              stdin_data: Optional[bytes] = None,
              extra_env: Optional[dict[str, str]] = None,
              limits: Optional[SandboxLimits] = None) -> SandboxProc:
        lim = limits or self.limits
        env = restricted_env(extra_env, self.tmp_dir)
        kwargs: dict[str, Any] = dict(
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, env=env,
        )
        job = None
        if os.name == "nt":
            job = _make_job(lim.memory_limit_bytes, lim.max_processes)
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(argv, **kwargs)
            # Assigned immediately after spawn; see docs/DECISIONS.md ADR-003.
            if not _kernel32.AssignProcessToJobObject(job, int(proc._handle)):
                err = ctypes.get_last_error()
                proc.kill()
                _kernel32.CloseHandle(job)
                raise OSError(err, "AssignProcessToJobObject failed")
        else:
            import resource

            def _preexec():  # runs in the child before exec
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (lim.memory_limit_bytes, lim.memory_limit_bytes))

            kwargs["start_new_session"] = True
            kwargs["preexec_fn"] = _preexec
            proc = subprocess.Popen(argv, **kwargs)
        return SandboxProc(proc, job, stdin_data)

    def run_worker(self, job_payload: dict[str, Any], cwd: str,
                   limits: Optional[SandboxLimits] = None) -> dict[str, Any]:
        """Run one scr.worker job to completion; returns the worker's JSON
        result, or a structured error dict on timeout/crash/garbage output."""
        lim = limits or self.limits
        handle = self.start(
            [sys.executable, "-s", "-m", "scr.worker"], cwd=cwd,
            stdin_data=json.dumps(job_payload).encode("utf-8"), limits=lim,
        )
        self._register(handle)
        try:
            res = handle.wait(lim.timeout_seconds)
        finally:
            self._unregister(handle)
        if getattr(handle, "_killed", False) and res.status != "timeout":
            return {"ok": False, "error": "cancelled",
                    "detail": "worker tree killed by session cancel"}
        if res.status == "timeout":
            return {"ok": False, "error": "timeout",
                    "detail": f"exceeded {lim.timeout_seconds}s; tree killed"}
        if res.status != "ok":
            return {"ok": False, "error": "worker_crash",
                    "detail": f"rc={res.returncode} stderr={res.stderr[-500:]}"}
        try:
            parsed = json.loads(res.stdout)
            if not isinstance(parsed, dict):
                raise ValueError("not an object")
            return parsed
        except (json.JSONDecodeError, ValueError):
            return {"ok": False, "error": "bad_worker_output",
                    "detail": res.stdout[-500:]}
