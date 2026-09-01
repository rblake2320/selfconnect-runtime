"""Standalone evidence verifier — PURE STDLIB (hashlib, hmac, json only).

Imports NOTHING from the scr package. This is the single source of truth for
evidence verification and is embedded verbatim into every exported bundle so
an auditor can verify offline on a machine with nothing else installed:

    python verify.py bundle.json --key <hex>

The hash-chain formula mirrors the runtime ledger exactly:
    hash_n = SHA-256( hash_{n-1}(ascii) || event_canonical_n(utf-8) )
with GENESIS = "0" * 64, and events stored as their exact canonical strings.
"""
from __future__ import annotations

import hashlib
import hmac
import json

GENESIS = "0" * 64


def chain_hash(prev_hash: str, event_canonical: str) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(event_canonical.encode("utf-8"))
    return h.hexdigest()


def recompute_chain(events: list) -> dict:
    """events: [{seq, event, hash}] with event as the canonical string.
    Returns {ok, head, count, error}."""
    prev = GENESIS
    expected_seq = 1
    for row in events:
        if row.get("seq") != expected_seq:
            return {"ok": False, "head": prev, "count": expected_seq - 1,
                    "error": f"sequence gap: expected {expected_seq}, found {row.get('seq')}"}
        recomputed = chain_hash(prev, row["event"])
        if recomputed != row.get("hash"):
            return {"ok": False, "head": prev, "count": expected_seq - 1,
                    "error": f"chain break at seq {row.get('seq')}"}
        prev = recomputed
        expected_seq += 1
    return {"ok": True, "head": prev, "count": len(events), "error": None}


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_bundle_obj(bundle: dict, bundle_hmac_hex: str, key: bytes) -> dict:
    """Full verification. Handles a single-session bundle (events[]/seal) and a
    TEAM bundle (sessions[] + delegation_tree). Returns a structured report."""
    if "sessions" in bundle:
        return _verify_team_bundle(bundle, bundle_hmac_hex, key)
    events = bundle.get("events", [])
    chain = recompute_chain(events)
    report = {
        "chain_ok": chain["ok"],
        "chain_error": chain["error"],
        "count": chain["count"],
        "head": chain["head"],
        "session_seal_ok": False,
        "bundle_seal_ok": False,
        "session_id": bundle.get("session_id"),
        "runtime_version": bundle.get("runtime_version"),
        "package": bundle.get("package"),
    }
    if not chain["ok"]:
        report["ok"] = False
        return report

    seal = bundle.get("seal") or {}
    if seal:
        if seal.get("head") == chain["head"] and seal.get("count") == chain["count"]:
            expect = hmac.new(key, f"{chain['head']}:{chain['count']}".encode("ascii"),
                              hashlib.sha256).hexdigest()
            report["session_seal_ok"] = hmac.compare_digest(expect, seal.get("hmac", ""))
        else:
            report["session_seal_ok"] = False
    else:
        report["session_seal_ok"] = None  # unsealed session

    expect_bundle = hmac.new(key, _canonical(bundle), hashlib.sha256).hexdigest()
    report["bundle_seal_ok"] = hmac.compare_digest(expect_bundle, bundle_hmac_hex or "")

    report["ok"] = (report["chain_ok"]
                    and report["bundle_seal_ok"]
                    and report["session_seal_ok"] in (True, None))
    return report


def _verify_team_bundle(bundle: dict, bundle_hmac_hex: str, key: bytes) -> dict:
    sessions = bundle.get("sessions", [])
    tree = bundle.get("delegation_tree", [])
    per = []
    all_ok = True
    for s in sessions:
        chain = recompute_chain(s.get("events", []))
        seal = s.get("seal") or {}
        seal_ok = None
        if seal:
            if seal.get("head") == chain["head"] and seal.get("count") == chain["count"]:
                expect = hmac.new(key, f"{chain['head']}:{chain['count']}".encode("ascii"),
                                  hashlib.sha256).hexdigest()
                seal_ok = hmac.compare_digest(expect, seal.get("hmac", ""))
            else:
                seal_ok = False
        ok = chain["ok"] and (seal_ok in (True, None))
        all_ok = all_ok and ok
        per.append({"agent": s.get("agent"), "session": s.get("session_id"),
                    "chain_ok": chain["ok"], "seal_ok": seal_ok,
                    "count": chain["count"], "error": chain["error"]})
    expect_bundle = hmac.new(key, _canonical(bundle), hashlib.sha256).hexdigest()
    bundle_seal_ok = hmac.compare_digest(expect_bundle, bundle_hmac_hex or "")
    return {
        "team": True, "team_id": bundle.get("team_id"),
        "runtime_version": bundle.get("runtime_version"), "package": bundle.get("package"),
        "delegation_tree": tree, "sessions": per,
        "bundle_seal_ok": bundle_seal_ok,
        "ok": all_ok and bundle_seal_ok,
    }


def human_report(report: dict) -> str:
    if report.get("team"):
        lines = ["SelfConnect Team Evidence Verification",
                 "======================================",
                 f"team:     {report.get('team_id')}",
                 f"runtime:  {report.get('runtime_version')}",
                 f"package:  {report.get('package')}",
                 "delegation tree:"]
        for n in sorted(report.get("delegation_tree", []), key=lambda x: (x.get("depth", 0), x.get("agent", ""))):
            indent = "  " * (int(n.get("depth", 0)) + 1)
            lines.append(f"{indent}{n.get('agent')}  (session {str(n.get('session_id'))[:8]}.., depth {n.get('depth')})")
        lines.append("sessions:")
        for s in report.get("sessions", []):
            status = "OK" if (s["chain_ok"] and s["seal_ok"] in (True, None)) else "FAIL"
            lines.append(f"  [{status}] {s['agent']}  {s['count']} events"
                         + ("" if s["chain_ok"] else f" — {s['error']}"))
        lines.append(f"bundle seal:  {'OK' if report.get('bundle_seal_ok') else 'FAIL'}")
        lines.append("")
        lines.append(f"RESULT: {'VERIFIED' if report.get('ok') else 'TAMPERED / INVALID'}")
        return "\n".join(lines)

    lines = [
        "SelfConnect Evidence Verification",
        "=================================",
        f"session:  {report.get('session_id')}",
        f"runtime:  {report.get('runtime_version')}",
        f"package:  {report.get('package')}",
        f"events:   {report.get('count')}",
        f"head:     {report.get('head')}",
        f"chain:        {'OK' if report.get('chain_ok') else 'FAIL — ' + str(report.get('chain_error'))}",
        f"session seal: {'OK' if report.get('session_seal_ok') else ('n/a (unsealed)' if report.get('session_seal_ok') is None else 'FAIL')}",
        f"bundle seal:  {'OK' if report.get('bundle_seal_ok') else 'FAIL'}",
        "",
        f"RESULT: {'VERIFIED' if report.get('ok') else 'TAMPERED / INVALID'}",
    ]
    return "\n".join(lines)


def _main(argv=None) -> int:
    import argparse
    import os

    p = argparse.ArgumentParser(prog="verify.py",
                                description="Verify a SelfConnect evidence bundle offline.")
    p.add_argument("bundle_json", help="path to bundle.json")
    p.add_argument("--hmac", help="path to bundle.hmac (default: alongside bundle.json)")
    p.add_argument("--key", help="hex sealing key (or set SCR_EVIDENCE_KEY)")
    args = p.parse_args(argv)

    with open(args.bundle_json, "r", encoding="utf-8") as f:
        bundle = json.load(f)
    hmac_path = args.hmac or (args.bundle_json.rsplit(".", 1)[0] + ".hmac")
    try:
        with open(hmac_path, "r", encoding="utf-8") as f:
            bundle_hmac = f.read().strip()
    except OSError:
        bundle_hmac = ""
    key_hex = args.key or os.environ.get("SCR_EVIDENCE_KEY", "")
    key = bytes.fromhex(key_hex) if key_hex else b""

    report = verify_bundle_obj(bundle, bundle_hmac, key)
    print(human_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
