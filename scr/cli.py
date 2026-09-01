"""`scr` command-line interface (design §3.7). Thin dispatch over the runtime
modules. Credentials are read/written only through the vault; the config
persists a vault reference, never a secret.

Commands: init, model add/list, package verify, run, ledger export/verify,
license install/status, doctor.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from . import __version__
from .config import Config, scr_home


def _store_path(home: str) -> str:
    return os.path.join(home, "scr.db")


def cmd_init(args) -> int:
    cfg = Config(args.home)
    from .state import Store
    Store(_store_path(cfg.home)).close()
    os.makedirs(os.path.join(cfg.home, "packages"), exist_ok=True)
    cfg.save()
    print(f"initialized SCR home at {cfg.home}")
    return 0


def cmd_model_add(args) -> int:
    cfg = Config(args.home)
    secret_ref = ""
    if args.secret:
        from .vault import Vault
        secret_ref = f"model:{args.name}"
        Vault(cfg.home).store_secret(secret_ref, args.secret)
    cfg.add_model(args.name, args.adapter, args.model,
                  base_url=args.base_url or "", secret_ref=secret_ref,
                  timeout=args.timeout, num_ctx=args.num_ctx)
    cfg.save()
    print(f"added model {args.name!r} (adapter={args.adapter}, secret_ref={secret_ref or 'none'})")
    return 0


def cmd_model_list(args) -> int:
    cfg = Config(args.home)
    for name, m in cfg.models().items():
        default = " [default]" if name == cfg.get("default_model") else ""
        print(f"{name}{default}: adapter={m['adapter']} model={m['model']} "
              f"secret_ref={m.get('secret_ref') or 'none'}")
    return 0


def cmd_package_verify(args) -> int:
    from .loader import verify_package
    from .signing import Keystore
    ks = Keystore()
    if args.trust:
        with open(args.trust, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    ks.add(line)
    res = verify_package(args.package, ks)
    if res.ok:
        print(f"VERIFIED {res.package} {res.version}")
        return 0
    print(f"REJECTED [{res.error}]: {res.detail}")
    return 1


def cmd_ledger_export(args) -> int:
    from .evidence import export_bundle
    from .state import Store
    cfg = Config(args.home)
    key = bytes.fromhex(args.key)
    store = Store(_store_path(cfg.home))
    export_bundle(store, args.session, key, args.out)
    print(f"exported evidence bundle to {args.out}")
    return 0


def cmd_ledger_verify(args) -> int:
    from .evidence import verify_bundle
    key = bytes.fromhex(args.key) if args.key else b""
    report = verify_bundle(args.bundle, key)
    print(report.text)
    return 0 if report.ok else 1


def cmd_license_status(args) -> int:
    import time as _time
    from .license import check
    with open(args.license, "r", encoding="utf-8") as f:
        text = f.read()
    now = args.now if args.now is not None else _time.time()
    status = check(text, args.pubkey, now)
    print(f"license: {status.state} ({status.reason or 'ok'}) "
          f"may_run={status.may_run} may_read_evidence={status.may_read_evidence}")
    return 0 if status.state != "invalid" else 1


def _keystore_from_trust(path: Optional[str]):
    from .signing import Keystore
    ks = Keystore()
    if path:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    ks.add(line)
    return ks


def cmd_package_install(args) -> int:
    from .registry import PackageRegistry
    cfg = Config(args.home)
    ks = _keystore_from_trust(args.trust)
    reg = PackageRegistry(cfg.home, ks)
    res = reg.install(args.package)
    if res.ok:
        print(f"installed {res.package} {res.version}")
        return 0
    print(f"REJECTED [{res.error}]: {res.detail}")
    return 1


def cmd_package_list(args) -> int:
    from .registry import PackageRegistry
    from .signing import Keystore
    cfg = Config(args.home)
    reg = PackageRegistry(cfg.home, Keystore())
    for p in reg.list_installed():
        print(f"{p.name} {p.version}  key_id={p.key_id}")
    return 0


def _session_manager(cfg, args):
    """Build a SessionManager whose kernel uses the configured model."""
    from .capability import CapabilityManifest
    from .kernel import Kernel
    from .model_factory import build_adapter
    from .sessions import SessionManager
    from .state import Store
    from .vault import Vault

    name = getattr(args, "model", None) or cfg.get("default_model")
    if not name or name not in cfg.models():
        raise SystemExit(f"no configured model {name!r}; run `scr model add` first")
    model_cfg = cfg.models()[name]
    secret = None
    if model_cfg.get("secret_ref"):
        secret = Vault(cfg.home).get_secret(model_cfg["secret_ref"])
    adapter = build_adapter(model_cfg, secret)
    store = Store(_store_path(cfg.home))

    def factory(s, sid):
        return Kernel(s, adapter, {}, CapabilityManifest())

    return SessionManager(store, factory), store


def _adapter_from_config(cfg, args):
    from .model_factory import build_adapter
    from .vault import Vault
    name = getattr(args, "model", None) or cfg.get("default_model")
    if not name or name not in cfg.models():
        raise SystemExit(f"no configured model {name!r}; run `scr model add` first")
    mc = cfg.models()[name]
    secret = Vault(cfg.home).get_secret(mc["secret_ref"]) if mc.get("secret_ref") else None
    return build_adapter(mc, secret)


def _run_team(cfg, args, target: str, task: str) -> int:
    """Team/agent run: load the topology from the installed package that
    provides `target` and execute it through the runtime."""
    import sys as _sys

    from .capability import CapabilityManifest
    from .registry import PackageRegistry
    from .sandbox import SandboxRunner
    from .signing import Keystore
    from .state import Store
    from .team import TeamLoadError, TeamRunner, load_team_from_package
    from .tools_native import build_native_tools

    # ${WORKSPACE} in agent capability roots binds here. Default: SCR home;
    # pass --workspace <path> to point the team at a real target (e.g. a repo).
    workspace_arg = getattr(args, "workspace", None)
    workspace = os.path.abspath(workspace_arg or cfg.home)
    if workspace_arg is not None:
        # Fail fast (G6): an explicit --workspace must already be a readable
        # directory — refuse BEFORE any session/store is touched rather than
        # burn a model run against nothing. The resolved absolute path is in
        # the error so shell path-mangling is visible instantly.
        if not os.path.isdir(workspace):
            raise SystemExit(
                f"--workspace is not an existing directory: {workspace} "
                f"(from argument {workspace_arg!r})")
        try:
            os.listdir(workspace)  # real read probe; os.access lies on Windows
        except OSError as exc:
            raise SystemExit(
                f"--workspace is not readable: {workspace} ({exc})")
    os.makedirs(os.path.join(workspace, "out"), exist_ok=True)

    reg = PackageRegistry(cfg.home, Keystore())
    adapter = _adapter_from_config(cfg, args)
    store = Store(_store_path(cfg.home))
    # tmp_dir: workers need a writable TEMP (the PyInstaller bootloader
    # extracts there; RUN-D crashed with "Could not create temporary
    # directory!" because the restricted env had none).
    sb_tmp = os.path.join(cfg.home, "tmp")
    os.makedirs(sb_tmp, exist_ok=True)
    runner_sb = SandboxRunner(tmp_dir=sb_tmp)

    loaded = None
    provenance: dict = {}
    available: dict[str, list[str]] = {}
    for pkg in reg.list_installed():
        try:
            lt = load_team_from_package(pkg.path, workspace)
        except TeamLoadError:
            continue
        available[pkg.name] = sorted(lt.specs) + [f"team:{a}" for a in lt.aliases]
        if target in lt.specs or target in lt.aliases:
            loaded = lt
            # Provenance: which signed package governs this run — ledgered into
            # the lead session so the evidence bundle can prove it.
            import hashlib as _hashlib
            with open(pkg.path, "rb") as f:
                content_sha = _hashlib.sha256(f.read()).hexdigest()
            provenance = {"package": pkg.name, "version": pkg.version,
                          "key_id": pkg.key_id, "content_sha256": content_sha}
            break
    if loaded is None:
        listing = "; ".join(f"{p}: {v}" for p, v in available.items()) or "(none installed)"
        raise SystemExit(f"unknown team/agent {target!r}. Available — {listing}")

    def progress(msg):
        print(msg, file=_sys.stderr, flush=True)

    trunner = TeamRunner(store, loaded, lambda a: adapter,
                         lambda m: build_native_tools(m, runner_sb),
                         sandbox=runner_sb, on_event=progress,
                         provenance=provenance)
    print(f"workspace: {workspace}", file=_sys.stderr, flush=True)
    result = trunner.run(target, task)
    print(f"team {target} [{result.stopped_reason}] session {result.session_id} "
          f"(team {trunner.last_team_id})")
    if result.final_text:
        print(result.final_text)
    return 0 if result.stopped_reason in ("completed", "awaiting_approval") else 1


def cmd_run(args) -> int:
    cfg = Config(args.home)
    parts = args.target_and_task
    if len(parts) >= 2:
        # `scr run <team-or-agent> "<task>"` (design §3.7)
        return _run_team(cfg, args, parts[0], " ".join(parts[1:]))
    # bare `scr run "<task>"` → single-agent default
    task = parts[0]
    mgr, store = _session_manager(cfg, args)
    import uuid
    job = mgr.enqueue(task, idem_key=args.idem or uuid.uuid4().hex)
    result = mgr.run_job(job.job_id)
    print(f"job {job.job_id} [{result.stopped_reason}] session {job.session_id}")
    if result.final_text:
        print(result.final_text)
    return 0 if result.stopped_reason in ("completed", "awaiting_approval") else 1


def cmd_session_list(args) -> int:
    # Owner bug report (2026-09-01): team runs create sessions directly (no
    # job rows), so a jobs-only listing printed NOTHING for a team home.
    # List every session, joined with its team membership when it has one.
    import datetime
    from .state import Store
    cfg = Config(args.home)
    store = Store(_store_path(cfg.home))
    rows = store.conn.execute(
        "SELECT s.id, s.status, s.created_at, t.team_id, t.agent, t.depth "
        "FROM sessions s LEFT JOIN team_sessions t ON t.session_id = s.id "
        "ORDER BY s.created_at, t.depth").fetchall()
    if not rows:
        print("(no sessions)")
        return 0
    for r in rows:
        started = datetime.datetime.fromtimestamp(
            r["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        team = r["team_id"] or "-"
        agent = r["agent"] or "-"
        depth = r["depth"] if r["depth"] is not None else "-"
        print(f"{r['id']}  team={team}  agent={agent}  depth={depth}  "
              f"status={r['status']}  started={started}")
    for j in store.jobs_all():
        print(f"job {j['job_id']}  {j['status']}  session={j['session_id']}")
    return 0


def cmd_session_resume(args) -> int:
    cfg = Config(args.home)
    mgr, store = _session_manager(cfg, args)
    result = mgr.resume_job(args.job_id)
    print(f"resumed {args.job_id} [{result.stopped_reason}]")
    return 0 if result.stopped_reason in ("completed", "awaiting_approval") else 1


def cmd_session_export(args) -> int:
    from .evidence import export_bundle, export_team_bundle, seal_on_close
    from .state import Store
    cfg = Config(args.home)
    key = bytes.fromhex(args.key)
    store = Store(_store_path(cfg.home))
    # If the id names a team (a team_id, or a session that belongs to one),
    # export the whole delegation tree as one bundle (team export always seals).
    team_id = args.session if store.team_members(args.session) else \
        store.team_id_for_session(args.session)
    if not team_id and not store.conn.execute(
            "SELECT 1 FROM sessions WHERE id=?",
            (args.session,)).fetchone():
        # RUN-E: an empty TEAMID produced a 0-event bundle that VERIFIED —
        # a green checkmark on nothing. Unknown/empty ids are refused.
        raise SystemExit(
            f"no such session or team: {args.session!r} — nothing exported")
    if team_id:
        export_team_bundle(store, team_id, key, args.out)
        n = len(store.team_members(team_id))
        print(f"exported TEAM evidence ({n} sessions) for {team_id} to {args.out}")
        return 0
    if args.seal:
        seal_on_close(store, args.session, key)
    export_bundle(store, args.session, key, args.out)
    print(f"exported evidence for session {args.session} to {args.out}")
    return 0


def cmd_model_test(args) -> int:
    from .gateway import ToolDef
    from .model_factory import build_adapter
    from .vault import Vault
    cfg = Config(args.home)
    if args.name not in cfg.models():
        print(f"no model {args.name!r}")
        return 1
    model_cfg = cfg.models()[args.name]
    secret = Vault(cfg.home).get_secret(model_cfg["secret_ref"]) if model_cfg.get("secret_ref") else None
    adapter = build_adapter(model_cfg, secret)
    try:
        resp = adapter.complete(
            [{"role": "user", "content": "Reply with the single word: ready"}], [])
        print(f"model {args.name!r} OK — replied {resp.text[:60]!r}")
        return 0
    except Exception as e:  # noqa: BLE001 — smoke test reports failure plainly
        print(f"model {args.name!r} FAILED: {type(e).__name__}: {str(e)[:200]}")
        return 1


def cmd_backup(args) -> int:
    from .backup import create_backup
    cfg = Config(args.home)
    key = bytes.fromhex(args.key) if args.key else None   # None → DPAPI-wrapped
    create_backup(cfg.home, key, args.out)
    print(f"backup written to {args.out}" + ("" if key else " (DPAPI-wrapped key)"))
    return 0


def cmd_restore(args) -> int:
    from .backup import restore_backup
    cfg = Config(args.home)
    key = bytes.fromhex(args.key) if args.key else None
    restore_backup(args.archive, key, cfg.home)
    print(f"restored into {cfg.home}")
    return 0


def cmd_release_sbom(args) -> int:
    import json as _json

    from .release import generate_sbom, parse_pinned_deps
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pyproject = args.pyproject or os.path.join(root, "pyproject.toml")
    deps = parse_pinned_deps(pyproject)
    sbom = generate_sbom("selfconnect-runtime", __version__, deps)
    with open(args.out, "w", encoding="utf-8") as f:
        _json.dump(sbom, f, indent=2, sort_keys=True)
    print(f"wrote CycloneDX SBOM ({len(sbom['components'])} components) to {args.out}")
    return 0


def cmd_doctor(args) -> int:
    """Design §3.8: DB integrity, disk headroom, installed-package signatures,
    lock health, model count, clock. Prints OK/WARN/FAIL per check; exits
    non-zero if any check FAILs."""
    import datetime
    import shutil

    from .registry import PackageRegistry
    from .signing import Keystore
    from .state import Store

    cfg = Config(args.home)
    checks: list[tuple[str, str, str]] = []   # (name, status, detail)

    print(f"SCR {__version__}")
    print(f"home: {cfg.home}")

    # DB integrity
    try:
        store = Store(_store_path(cfg.home))
        integ = store.conn.execute("PRAGMA integrity_check;").fetchone()[0]
        checks.append(("db_integrity", "OK" if integ == "ok" else "FAIL", integ))
    except Exception as e:  # noqa: BLE001
        checks.append(("db_integrity", "FAIL", str(e)[:120]))

    # Disk headroom
    try:
        du = shutil.disk_usage(cfg.home)
        free_gb = du.free / (1024 ** 3)
        checks.append(("disk_headroom",
                       "OK" if free_gb >= 1.0 else "WARN",
                       f"{free_gb:.1f} GiB free"))
    except Exception as e:  # noqa: BLE001
        checks.append(("disk_headroom", "WARN", str(e)[:120]))

    # Installed-package integrity (tamper detection, self-consistent signature)
    reg = PackageRegistry(cfg.home, Keystore())
    from .loader import integrity_check
    installed = reg.list_installed()
    if not installed:
        checks.append(("packages", "OK", "none installed"))
    for p in installed:
        res = integrity_check(p.path)
        checks.append((f"pkg:{p.name}",
                       "OK" if res.ok else "FAIL",
                       f"{p.version} " + ("intact" if res.ok else f"{res.error}: {res.detail}")))

    # Lock health (best-effort: is the workspace lock currently free?)
    from .locks import LockHeld, WorkspaceLock
    lock_path = os.path.join(cfg.home, "workspace.lock")
    try:
        wl = WorkspaceLock(lock_path)
        wl.acquire()
        wl.release()
        checks.append(("lock_health", "OK", "workspace lock free"))
    except LockHeld:
        checks.append(("lock_health", "WARN", "held by another instance"))
    except Exception as e:  # noqa: BLE001
        checks.append(("lock_health", "WARN", str(e)[:120]))

    # Models + clock
    checks.append(("models", "OK", f"{len(cfg.models())} configured"))
    checks.append(("clock", "OK",
                   datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))

    width = max(len(n) for n, _, _ in checks)
    for name, status, detail in checks:
        print(f"  [{status:>4}] {name:<{width}}  {detail}")
    return 0 if all(s != "FAIL" for _, s, _ in checks) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scr", description="SelfConnect Runtime")
    p.add_argument("--home", default=None, help="SCR home dir (default: SCR_HOME or platform default)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    m = sub.add_parser("model")
    msub = m.add_subparsers(dest="mcmd", required=True)
    ma = msub.add_parser("add")
    ma.add_argument("name"); ma.add_argument("--adapter", required=True)
    ma.add_argument("--model", required=True); ma.add_argument("--base-url", dest="base_url")
    ma.add_argument("--secret", default=None)
    ma.add_argument("--timeout", type=float, default=None,
                    help="per-call timeout seconds (default 600; raise for slow local reasoning models)")
    ma.add_argument("--num-ctx", dest="num_ctx", type=int, default=None,
                    help="context window tokens for local models (default 16384; "
                         "Ollama's own ~4k default silently truncates prompts)")
    ma.set_defaults(func=cmd_model_add)
    msub.add_parser("list").set_defaults(func=cmd_model_list)

    ma_t = msub.add_parser("test")
    ma_t.add_argument("name")
    ma_t.set_defaults(func=cmd_model_test)

    pk = sub.add_parser("package")
    pksub = pk.add_subparsers(dest="pcmd", required=True)
    pv = pksub.add_parser("verify")
    pv.add_argument("package"); pv.add_argument("--trust", help="file of trusted public keys (hex, one per line)")
    pv.set_defaults(func=cmd_package_verify)
    pi = pksub.add_parser("install")
    pi.add_argument("package"); pi.add_argument("--trust", help="file of trusted public keys")
    pi.set_defaults(func=cmd_package_install)
    pksub.add_parser("list").set_defaults(func=cmd_package_list)

    rn = sub.add_parser("run")
    rn.add_argument("target_and_task", nargs="+",
                    help='either "<task>" (single agent) or <team-or-agent> "<task>"')
    rn.add_argument("--model", default=None)
    rn.add_argument("--workspace", default=None,
                    help="bind ${WORKSPACE} in agent capability roots to this path")
    rn.add_argument("--idem", default=None)
    rn.set_defaults(func=cmd_run)

    se = sub.add_parser("session")
    sesub = se.add_subparsers(dest="scmd", required=True)
    sesub.add_parser("list").set_defaults(func=cmd_session_list)
    sr = sesub.add_parser("resume")
    sr.add_argument("job_id"); sr.add_argument("--model", default=None)
    sr.set_defaults(func=cmd_session_resume)
    sx = sesub.add_parser("export")
    sx.add_argument("session"); sx.add_argument("out"); sx.add_argument("--key", required=True)
    sx.add_argument("--seal", action="store_true")
    sx.set_defaults(func=cmd_session_export)

    bk = sub.add_parser("backup")
    bk.add_argument("out"); bk.add_argument("--key", default=None,
                    help="explicit 32-byte hex key (omit for DPAPI-wrapped)")
    bk.set_defaults(func=cmd_backup)
    rs = sub.add_parser("restore")
    rs.add_argument("archive"); rs.add_argument("--key", default=None)
    rs.set_defaults(func=cmd_restore)

    lg = sub.add_parser("ledger")
    lgsub = lg.add_subparsers(dest="lcmd", required=True)
    le = lgsub.add_parser("export")
    le.add_argument("session"); le.add_argument("out"); le.add_argument("--key", required=True)
    le.set_defaults(func=cmd_ledger_export)
    lv = lgsub.add_parser("verify")
    lv.add_argument("bundle"); lv.add_argument("--key", default="")
    lv.set_defaults(func=cmd_ledger_verify)

    lic = sub.add_parser("license")
    licsub = lic.add_subparsers(dest="liccmd", required=True)
    ls = licsub.add_parser("status")
    ls.add_argument("license"); ls.add_argument("--pubkey", required=True)
    ls.add_argument("--now", type=float, default=None)
    ls.set_defaults(func=cmd_license_status)

    rel = sub.add_parser("release")
    relsub = rel.add_subparsers(dest="relcmd", required=True)
    rsb = relsub.add_parser("sbom")
    rsb.add_argument("out"); rsb.add_argument("--pyproject", default=None)
    rsb.set_defaults(func=cmd_release_sbom)

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    return p


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["__scr_worker__"]:
        # Frozen-exe worker dispatch: the sandbox re-invokes THIS executable
        # (sys.executable is scr.exe under PyInstaller); hand off to the worker
        # before argparse can reject it. See sandbox._worker_cmd.
        from .worker import main as worker_main
        return worker_main()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
