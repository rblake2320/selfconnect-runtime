"""Evidence bundle export + verification (design §3.5).

An exported `.scevidence` is a single zip holding:
  bundle.json   runtime version, package name/version, session id, the full
                ledger (events as {seq, canonical-event, hash}), session seal
  bundle.hmac   HMAC-SHA256(key, canonical(bundle.json)) — detects any edit
  verify.py     the exact _evidence_verifier source, so the bundle verifies
                itself on a machine with nothing but Python stdlib installed

`export_bundle` and `verify_bundle` both route through `_evidence_verifier`,
so the SCR-side path and the embedded standalone can never diverge.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import zipfile
from dataclasses import dataclass
from typing import Optional

from . import __version__
from . import _evidence_verifier as verifier
from .state import Store


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_session_events(store: Store, session_id: str) -> list[dict]:
    rows = store.conn.execute(
        "SELECT seq, event, hash FROM ledger WHERE session_id=? ORDER BY seq",
        (session_id,),
    ).fetchall()
    return [{"seq": r["seq"], "event": r["event"], "hash": r["hash"]} for r in rows]


def _read_seal(store: Store, session_id: str) -> Optional[dict]:
    row = store.conn.execute(
        "SELECT head, count, hmac FROM seals WHERE session_id=?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    return {"head": row["head"], "count": row["count"], "hmac": row["hmac"]}


def seal_on_close(store: Store, session_id: str, key: bytes) -> str:
    """Per-session seal at close. Mirrors Ledger.seal via the store."""
    from .ledger import Ledger
    return Ledger(store).seal(session_id, key)


def build_bundle(store: Store, session_id: str, package: Optional[dict] = None) -> dict:
    return {
        "runtime_version": __version__,
        "package": package or {},
        "session_id": session_id,
        "events": _read_session_events(store, session_id),
        "seal": _read_seal(store, session_id) or {},
    }


def export_bundle(store: Store, session_id: str, key: bytes, out_path: str,
                  package: Optional[dict] = None) -> str:
    """Write a self-verifying .scevidence bundle. Returns bundle.hmac hex."""
    bundle = build_bundle(store, session_id, package)
    bundle_bytes = _canonical(bundle)
    bundle_hmac = hmac_mod.new(key, bundle_bytes, hashlib.sha256).hexdigest()

    verifier_src = _load_verifier_source()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        # store bundle.json in the SAME canonical bytes the hmac covers
        z.writestr("bundle.json", bundle_bytes)
        z.writestr("bundle.hmac", bundle_hmac)
        z.writestr("verify.py", verifier_src)
        z.writestr("README.txt",
                   "Verify offline with only Python stdlib:\n"
                   "  python verify.py bundle.json --key <hex>\n"
                   "Exit 0 = VERIFIED, 1 = TAMPERED/INVALID.\n")
    return bundle_hmac


def build_team_bundle(store: Store, team_id: str,
                      package: Optional[dict] = None) -> dict:
    members = store.team_members(team_id)
    sessions = []
    for m in members:
        sid = m["session_id"]
        sessions.append({
            "session_id": sid, "agent": m["agent"],
            "events": _read_session_events(store, sid),
            "seal": _read_seal(store, sid) or {},
        })
    return {
        "runtime_version": __version__,
        "package": package or {},
        "team_id": team_id,
        "delegation_tree": [
            {"agent": m["agent"], "session_id": m["session_id"],
             "parent_session": m["parent_session"], "depth": m["depth"]}
            for m in members],
        "sessions": sessions,
    }


def export_team_bundle(store: Store, team_id: str, key: bytes, out_path: str,
                       package: Optional[dict] = None) -> str:
    """Export a whole team run (all delegation sessions + the tree) as one
    self-verifying .scevidence bundle. Seals each session first."""
    for m in store.team_members(team_id):
        seal_on_close(store, m["session_id"], key)
    bundle = build_team_bundle(store, team_id, package)
    bundle_bytes = _canonical(bundle)
    bundle_hmac = hmac_mod.new(key, bundle_bytes, hashlib.sha256).hexdigest()
    verifier_src = _load_verifier_source()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bundle.json", bundle_bytes)
        z.writestr("bundle.hmac", bundle_hmac)
        z.writestr("verify.py", verifier_src)
        z.writestr("README.txt",
                   "Team evidence bundle. Verify offline with only Python stdlib:\n"
                   "  python verify.py bundle.json --key <hex>\n")
    return bundle_hmac


def _load_verifier_source() -> str:
    """Read the standalone verifier's source to embed in the bundle. Works both
    from the source tree AND from a frozen (PyInstaller) exe, where the source
    is bundled as package data rather than sitting next to the bytecode."""
    # 1) packaged resource — resolves inside a frozen bundle when the .py was
    #    added as data (see the PyInstaller --add-data flag).
    try:
        from importlib.resources import files
        res = files("scr").joinpath("_evidence_verifier.py")
        if res.is_file():
            return res.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError, OSError, TypeError):
        pass
    # 2) source-tree fallback
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_evidence_verifier.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@dataclass
class BundleReport:
    ok: bool
    report: dict
    text: str


def verify_bundle(path: str, key: bytes) -> BundleReport:
    """Verify a .scevidence bundle from disk (SCR-side path)."""
    with zipfile.ZipFile(path, "r") as z:
        bundle = json.loads(z.read("bundle.json"))
        bundle_hmac = z.read("bundle.hmac").decode("ascii").strip()
    report = verifier.verify_bundle_obj(bundle, bundle_hmac, key)
    return BundleReport(report["ok"], report, verifier.human_report(report))
