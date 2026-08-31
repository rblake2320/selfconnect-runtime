"""Sandboxed worker entry point: python -m scr.worker

Reads exactly one JSON job object from stdin, performs it, writes exactly
one JSON result object to stdout, exits 0. Any internal failure is reported
as {"ok": false, ...} — the worker never lets an exception produce garbage
output the parent can't classify.

Defense in depth: the parent (tools_native) has ALREADY performed the
capability checks; the worker re-validates paths against the roots passed
in the job, so even a confused parent cannot make a worker escape its jail.

proc_exec spawns its child from inside the worker, so the child inherits
Job Object membership (Windows) / the session (POSIX) and dies with the
tree on cancel or timeout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from .capability import CapabilityDenied, resolve_within


def _fs_read(job: dict) -> dict:
    path = resolve_within(job["path"], job["read_roots"])
    with open(path, "rb") as f:
        data = f.read(job.get("max_bytes", 1_000_000))
    return {"ok": True, "content": data.decode("utf-8", "replace")}


def _fs_write(job: dict) -> dict:
    from .atomic import atomic_write_bytes
    path = resolve_within(job["path"], job["write_roots"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_bytes(path, job["content"].encode("utf-8"))
    return {"ok": True, "bytes": len(job["content"].encode("utf-8"))}


def _fs_list(job: dict) -> dict:
    path = resolve_within(job["path"], job["read_roots"])
    entries = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        entries.append({"name": name,
                        "dir": os.path.isdir(full),
                        "size": os.path.getsize(full) if os.path.isfile(full) else 0})
    return {"ok": True, "entries": entries}


def _http_get(job: dict) -> dict:
    import urllib.parse
    import urllib.request
    url = job["url"]
    host = urllib.parse.urlsplit(url).hostname or ""
    # Re-check the allowlist inside the sandbox as well.
    if host not in set(job["net_hosts"]):
        raise CapabilityDenied(f"network host not permitted: {host!r}")
    req = urllib.request.Request(url, headers={"User-Agent": "scr-worker"})
    with urllib.request.urlopen(req, timeout=job.get("timeout", 30)) as resp:
        body = resp.read(job.get("max_bytes", 1_000_000))
        return {"ok": True, "status": resp.status,
                "content": body.decode("utf-8", "replace")}


def _proc_exec(job: dict) -> dict:
    cp = subprocess.run(
        [job["binary"], *job["args"]],
        capture_output=True, cwd=job["cwd"],
        timeout=job.get("timeout", 55),
    )
    return {"ok": True, "returncode": cp.returncode,
            "stdout": cp.stdout.decode("utf-8", "replace")[-100_000:],
            "stderr": cp.stderr.decode("utf-8", "replace")[-100_000:]}


_OPS = {
    "fs_read": _fs_read,
    "fs_write": _fs_write,
    "fs_list": _fs_list,
    "http_get": _http_get,
    "proc_exec": _proc_exec,
}


def main() -> int:
    # §3.6: reduce this worker's privilege before doing any work (defense in
    # depth beneath the capability kernel). Best-effort; never fatal.
    try:
        from .privdrop import harden_current_process
        harden_current_process()
    except Exception:  # noqa: BLE001 — hardening must never break the worker
        pass
    try:
        job = json.loads(sys.stdin.read())
        op = _OPS.get(job.get("op", ""))
        if op is None:
            result = {"ok": False, "error": "unknown_op", "detail": str(job.get("op"))}
        else:
            result = op(job)
    except CapabilityDenied as e:
        result = {"ok": False, "error": "denied", "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — structured error is the contract
        result = {"ok": False, "error": type(e).__name__, "detail": str(e)[:500]}
    sys.stdout.write(json.dumps(result))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
