"""Build gate: a FROZEN artifact's worker must physically execute a real
fs_list under the sandbox's EXACT restricted env (Job Object, allowlist env,
provisioned TEMP) — or the build fails.

Why this exists (RUN D, 2026-09-01): every previous live run silently skipped
the frozen worker spawn path; when a valid workspace finally exercised it, the
worker died on spawn ("Could not create temporary directory!" — no TEMP for
the PyInstaller bootloader, plus `-m scr.worker` args scr.exe's CLI would
reject). Venv tests can never catch this class. This gate runs on every build
so the next TEMP-shaped defect cannot ship.

Also measures spawn latency (owner directive: quantify onefile's per-spawn
extraction tax vs onedir).

Usage: python scripts/frozen_worker_gate.py <frozen-exe> [<frozen-exe> ...]
Exit 0 only if EVERY exe passes.
"""
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scr.sandbox import SandboxRunner  # noqa: E402  (real path, no re-implementation)

SENTINEL = "pyproject.toml"   # a file the listing of the repo root must contain


def gate(exe: str) -> tuple[bool, float, str]:
    exe = os.path.abspath(exe)   # CreateProcess is unreliable with relative argv[0]
    if not os.path.isfile(exe):
        return False, 0.0, f"no such artifact: {exe}"
    tmp = os.path.join(_ROOT, "build", "gate-tmp")
    os.makedirs(tmp, exist_ok=True)
    sb = SandboxRunner(tmp_dir=tmp)   # identical env construction to production
    job = {"op": "fs_list", "path": _ROOT, "read_roots": [_ROOT]}
    t0 = time.perf_counter()
    handle = sb.start([exe, "__scr_worker__"], cwd=_ROOT,
                      stdin_data=json.dumps(job).encode("utf-8"))
    res = handle.wait(120)
    dt = time.perf_counter() - t0
    if res.status != "ok":
        return False, dt, f"status={res.status} rc={res.returncode} stderr={res.stderr[:300]}"
    try:
        out = json.loads(res.stdout)
    except json.JSONDecodeError:
        return False, dt, f"garbage stdout: {res.stdout[:200]}"
    if not out.get("ok"):
        return False, dt, f"worker error: {out}"
    names = {e["name"] for e in out.get("entries", [])}
    if SENTINEL not in names:
        return False, dt, f"listing missing sentinel {SENTINEL!r}: {sorted(names)[:10]}"
    return True, dt, f"listed {len(names)} entries"


def main() -> int:
    exes = sys.argv[1:]
    if not exes:
        print("usage: frozen_worker_gate.py <frozen-exe> [...]", file=sys.stderr)
        return 2
    failed = False
    for exe in exes:
        ok, dt, detail = gate(exe)
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] worker gate {os.path.basename(exe)}: "
              f"spawn+exec {dt:.2f}s - {detail}")
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
