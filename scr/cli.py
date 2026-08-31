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
                  base_url=args.base_url or "", secret_ref=secret_ref)
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


def cmd_run(args) -> int:
    cfg = Config(args.home)
    mgr, store = _session_manager(cfg, args)
    import uuid
    job = mgr.enqueue(args.task, idem_key=args.idem or uuid.uuid4().hex)
    result = mgr.run_job(job.job_id)
    print(f"job {job.job_id} [{result.stopped_reason}] session {job.session_id}")
    if result.final_text:
        print(result.final_text)
    return 0 if result.stopped_reason in ("completed", "awaiting_approval") else 1


def cmd_session_list(args) -> int:
    from .state import Store
    cfg = Config(args.home)
    store = Store(_store_path(cfg.home))
    for j in store.jobs_all():
        print(f"{j['job_id']}  {j['status']}  session={j['session_id']}")
    return 0


def cmd_session_resume(args) -> int:
    cfg = Config(args.home)
    mgr, store = _session_manager(cfg, args)
    result = mgr.resume_job(args.job_id)
    print(f"resumed {args.job_id} [{result.stopped_reason}]")
    return 0 if result.stopped_reason in ("completed", "awaiting_approval") else 1


def cmd_session_export(args) -> int:
    from .evidence import export_bundle, seal_on_close
    from .state import Store
    cfg = Config(args.home)
    key = bytes.fromhex(args.key)
    store = Store(_store_path(cfg.home))
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
    create_backup(cfg.home, bytes.fromhex(args.key), args.out)
    print(f"backup written to {args.out}")
    return 0


def cmd_restore(args) -> int:
    from .backup import restore_backup
    cfg = Config(args.home)
    restore_backup(args.archive, bytes.fromhex(args.key), cfg.home)
    print(f"restored into {cfg.home}")
    return 0


def cmd_doctor(args) -> int:
    cfg = Config(args.home)
    from .state import Store
    store = Store(_store_path(cfg.home))
    integrity = store.conn.execute("PRAGMA integrity_check;").fetchone()[0]
    print(f"SCR {__version__}")
    print(f"home:      {cfg.home}")
    print(f"db:        {_store_path(cfg.home)}")
    print(f"integrity: {integrity}")
    print(f"models:    {len(cfg.models())} configured")
    return 0 if integrity == "ok" else 1


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
    rn.add_argument("task"); rn.add_argument("--model", default=None)
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
    bk.add_argument("out"); bk.add_argument("--key", required=True)
    bk.set_defaults(func=cmd_backup)
    rs = sub.add_parser("restore")
    rs.add_argument("archive"); rs.add_argument("--key", required=True)
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

    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
