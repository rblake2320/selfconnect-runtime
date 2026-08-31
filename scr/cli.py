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

    pk = sub.add_parser("package")
    pksub = pk.add_subparsers(dest="pcmd", required=True)
    pv = pksub.add_parser("verify")
    pv.add_argument("package"); pv.add_argument("--trust", help="file of trusted public keys (hex, one per line)")
    pv.set_defaults(func=cmd_package_verify)

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
