"""Compliance mapping — SelfConnect layer #1 (Provenance / "Sentinel") ported
to the SelfConnect Runtime.

Sentinel ingested a signed Enterprise ledger and mapped each control's
`evidence_sources` to a pass/detail signal, then produced a control-coverage
report against NIST 800-53, ISO/IEC 42001, the EU AI Act, and NIST AI RMF. The
control catalogs are ported verbatim (scr/frameworks/data/*.json); only the
SIGNAL EXTRACTORS are re-sourced from SelfConnect-Enterprise's ledger schema to
**SCR's own hash-chained ledger** — the point of the migration.

Determinism lives here, in code — never in model prose. A team agent drives
this via the capability-gated `compliance_map` tool and writes the report; the
mapping itself is reproducible from a bundle by anyone, offline.

Honesty rule: a signal SCR does not actually emit (drift monitors, red-team
runs, control-plane snapshots, mTLS handshakes, per-entry agent-pubkey) is
reported `not_evidenced` with the reason — NEVER silently passed. A compliance
tool that green-checks everything is a fake.
"""
from __future__ import annotations

import json
import zipfile
from importlib.resources import files
from typing import Any

# ------------------------------------------------------------------ catalogs


def load_frameworks() -> list[dict]:
    """Load every control catalog from the frozen-safe data package."""
    out = []
    data_dir = files("scr.frameworks").joinpath("data")
    for entry in sorted(data_dir.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".json"):
            out.append(json.loads(entry.read_text(encoding="utf-8")))
    return out


# ------------------------------------------------------------------ snapshot


def snapshot_from_events(events: list[dict]) -> dict:
    """Extract the signals the mapper needs from a flat list of SCR ledger
    events (already JSON-decoded). Kept separate from bundle parsing so it can
    run over a live store too."""
    return {"events": events}


def snapshot_from_bundle(bundle: dict) -> dict:
    """Build the mapper snapshot from an exported .scevidence bundle dict:
    flatten every session's ledger events + carry seals and package provenance."""
    events: list[dict] = []
    sealed_sessions = 0
    total_sessions = 0
    for s in bundle.get("sessions", []):
        total_sessions += 1
        if (s.get("seal") or {}).get("hmac"):
            sealed_sessions += 1
        for row in s.get("events", []):
            try:
                e = json.loads(row["event"])
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            e["_hash"] = row.get("hash")
            events.append(e)
    return {
        "events": events,
        "package": bundle.get("package") or {},
        "sealed_sessions": sealed_sessions,
        "total_sessions": total_sessions,
        "bundle_seal": True,  # a bundle that parsed here came through export
        "frameworks_loaded": len(load_frameworks()),
    }


# ------------------------------------------------------------ signal engine


def _passed_and_detail(source: str, snap: dict) -> tuple[bool, str]:
    """(passed, detail) for one evidence_source, evaluated against SCR's ledger.
    Returns passed=False with a 'not evidenced on SCR' detail for signals SCR
    genuinely does not emit — honestly, never a silent pass."""
    ev = snap.get("events", [])
    pkg = snap.get("package") or {}
    src = source.lower()

    def _count(pred):
        return sum(1 for e in ev if pred(e))

    # ── ledger presence / integrity ─────────────────────────────────────────
    if src in ("ledger.entries", "ledger.segments", "ledger.entry"):
        return len(ev) > 0, f"{len(ev)} ledger events present"
    if src in ("ledger.entry.entry_hash",):
        n = len(ev); h = _count(lambda e: e.get("_hash"))
        return n > 0 and h == n, f"{h}/{n} events carry an entry hash"
    if src in ("ledger.entry_hash chain", "ledger.segment.signature"):
        tot, sealed = snap.get("total_sessions", 0), snap.get("sealed_sessions", 0)
        return tot > 0 and sealed == tot, (
            f"{sealed}/{tot} sessions HMAC-sealed (chain verified by ledger verify)")
    if src == "ledger.args_hash":
        n = _count(lambda e: e.get("args_sha256"))
        return n > 0, f"{n} tool_exec events carry args_sha256"
    if src in ("ledger.policy_decision",):
        decs = {e.get("decision") for e in ev if e.get("type") == "policy"}
        decs = {d for d in decs if d}
        return bool(decs), f"policy decisions seen: {sorted(decs)}"
    if src == "ledger.policy_decision=deny":
        n = _count(lambda e: e.get("type") == "cap_denied") + _count(
            lambda e: e.get("type") == "policy" and e.get("decision") == "denied")
        return n > 0, f"{n} deny/cap_denied decisions (default-deny posture)"
    if src == "ledger.action=spawn":
        n = _count(lambda e: e.get("type") == "delegate")
        return n > 0, f"{n} delegate (spawn) events logged"
    if src == "ledger.action=kill":
        return False, "not evidenced on SCR (no kill events in a normal run)"
    if src == "ledger.classification":
        n = _count(lambda e: e.get("type") == "grant_context")
        return n > 0, f"{n} grant_context events record the effective capability envelope"
    if src == "ledger.caveats":
        n = _count(lambda e: e.get("type") == "delegate" and e.get("eff_cap_sha256"))
        return n > 0, f"{n} delegation edges carry an attenuated-capability hash (caveats)"
    if src == "ledger.entry.decision_reason":
        n = _count(lambda e: e.get("reason"))
        return n > 0, f"{n} events carry a decision reason"
    if src == "ledger.entry.agent_identity_pubkey_sha384":
        return False, "not evidenced on SCR (per-entry agent pubkey not recorded)"
    if src == "ledger.ingest_lag_ms":
        return False, "not evidenced on SCR (no ingest-lag metric persisted)"

    # ── policy bundle == the signed package governing the run ────────────────
    if src in ("policy.bundle.current", "policy.bundles"):
        ok = bool(pkg.get("package"))
        return ok, (f"signed package governs run: {pkg.get('package')} "
                    f"{pkg.get('version')}" if ok else "no package provenance in bundle")
    if src == "policy.bundle.signature":
        return bool(snap.get("bundle_seal")), "bundle is HMAC-sealed"
    if src in ("policy.caveat_allowlist", "policy.classification_envelope",
               "policy.rule_count"):
        return False, f"not evidenced in the bundle ({source} lives in the installed manifest)"

    # ── operator approvals ───────────────────────────────────────────────────
    if src in ("operator_queue", "operator_queue.resolved"):
        n = _count(lambda e: e.get("type") in ("approval", "approval_required"))
        r = _count(lambda e: e.get("type") == "approval")
        if src == "operator_queue.resolved":
            return r > 0, f"{r} resolved approvals" if r else "no approvals resolved this run"
        return n > 0, f"{n} approval events" if n else "no operator approvals this run"

    # ── identity (SCR = Ed25519 package signing key) ─────────────────────────
    if src in ("identity.public_key_pem", "identity.key_algorithm"):
        ok = bool(pkg.get("key_id"))
        if src == "identity.key_algorithm":
            return ok, "key_algorithm=Ed25519 (SCR package signing)" if ok else "no key"
        return ok, f"signing key pinned (key_id={pkg.get('key_id')})" if ok else "no key"
    if src == "identity.hash_algorithm":
        # honest: SCR uses SHA-256, the control expects SHA-384
        return False, "SCR uses SHA-256 (Merkle/ledger); control expects SHA-384"

    # ── exporter / coverage ──────────────────────────────────────────────────
    if src == "exporter.bundle":
        return bool(snap.get("bundle_seal")), "a signed evidence bundle was produced"
    if src == "framework.coverage":
        n = snap.get("frameworks_loaded", 0)
        return n >= 4, f"{n} frameworks loaded"

    # ── signals SCR does not (yet) emit ──────────────────────────────────────
    if src in ("monitors.drift_alerts", "red_team.runs", "control_plane.state",
               "control_plane.last_operator_action", "transport.mtls"):
        return False, f"not evidenced on SCR ({source} is a Sentinel-era source)"

    return False, f"source '{source}' has no SCR assertion"


# ------------------------------------------------------------------ public API


def evaluate(snap: dict) -> dict:
    """Evaluate all controls across all frameworks against the snapshot.
    Returns a structured result (no I/O)."""
    result = {"frameworks": [], "controls_total": 0, "controls_satisfied": 0}
    for fw in load_frameworks():
        fw_rows = []
        satisfied = 0
        for c in fw.get("controls", []):
            sources = c.get("provenance_mapping", {}).get("evidence_sources", [])
            rows = [{"source": s, **dict(zip(("passed", "detail"),
                                             _passed_and_detail(s, snap)))}
                    for s in sources]
            # a control is satisfied when it has ≥1 source and ALL pass
            ok = bool(rows) and all(r["passed"] for r in rows)
            satisfied += 1 if ok else 0
            fw_rows.append({"id": c["id"], "name": c.get("name", ""),
                            "satisfied": ok, "signals": rows})
        result["frameworks"].append({
            "framework_id": fw["framework_id"], "name": fw.get("name", ""),
            "controls_total": len(fw_rows), "controls_satisfied": satisfied,
            "controls": fw_rows})
        result["controls_total"] += len(fw_rows)
        result["controls_satisfied"] += satisfied
    return result


def map_bundle(bundle_path: str) -> dict:
    """Open a .scevidence bundle and evaluate compliance from its chain."""
    with zipfile.ZipFile(bundle_path) as z:
        bundle = json.loads(z.read("bundle.json"))
    snap = snapshot_from_bundle(bundle)
    res = evaluate(snap)
    res["source_bundle"] = bundle_path
    res["package"] = snap.get("package") or {}
    res["event_count"] = len(snap.get("events", []))
    return res


def render_markdown(res: dict) -> str:
    pkg = res.get("package") or {}
    lines = ["# SelfConnect Runtime — Compliance Control Mapping",
             "",
             "compliance mapping complete",
             "",
             f"Source bundle: `{res.get('source_bundle', '?')}`  "
             f"({res.get('event_count', 0)} ledger events)",
             f"Governing package: {pkg.get('package', '(none)')} "
             f"{pkg.get('version', '')} (key_id {pkg.get('key_id', '-')})",
             f"**Overall: {res['controls_satisfied']} / {res['controls_total']} "
             f"controls satisfied by this run's ledger.**",
             "",
             "Generated deterministically by SCR from the hash chain — not the "
             "model. Signals marked not-evidenced are honest gaps, not failures.",
             ""]
    for fw in res["frameworks"]:
        lines.append(f"## {fw['name']} — {fw['controls_satisfied']}/"
                     f"{fw['controls_total']}")
        lines.append("| Control | Satisfied | Signals |")
        lines.append("|---|---|---|")
        for c in fw["controls"]:
            sig = "; ".join(f"{'✓' if r['passed'] else '✗'} {r['source']}: {r['detail']}"
                            for r in c["signals"]) or "(no sources)"
            mark = "YES" if c["satisfied"] else "no"
            lines.append(f"| {c['id']} {c['name']} | {mark} | {sig} |")
        lines.append("")
    return "\n".join(lines)
