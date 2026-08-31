"""Tamper-evident evidence ledger.

Each event E_n stores hash_n = SHA-256(hash_{n-1} || canonical(E_n)).
Canonical form: JSON with sorted keys, compact separators, UTF-8.
Session close seals the chain with HMAC-SHA256(key, head || count).

verify() detects: content modification (bit flips), reordering,
splicing, deletion mid-chain, truncation (against seal count),
head substitution, and seal forgery with the wrong key.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
from dataclasses import dataclass
from typing import Any, Optional

from .state import Store

GENESIS = "0" * 64


def canonical(event: dict[str, Any]) -> bytes:
    return json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")


def chain_hash(prev_hash: str, event: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(canonical(event))
    return h.hexdigest()


@dataclass
class VerifyResult:
    ok: bool
    count: int
    head: str
    error: Optional[str] = None


class Ledger:
    def __init__(self, store: Store):
        self.store = store

    def head(self, session_id: str) -> tuple[str, int]:
        row = self.store.conn.execute(
            "SELECT hash, seq FROM ledger WHERE session_id=? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return GENESIS, 0
        return row["hash"], row["seq"]

    def append(self, session_id: str, event: dict[str, Any]) -> str:
        prev, seq = self.head(session_id)
        new_hash = chain_hash(prev, event)
        self.store.conn.execute(
            "INSERT INTO ledger(session_id, seq, event, hash) VALUES(?,?,?,?)",
            (session_id, seq + 1, canonical(event).decode("utf-8"), new_hash),
        )
        return new_hash

    def seal(self, session_id: str, key: bytes) -> str:
        head, count = self.head(session_id)
        mac = hmac_mod.new(
            key, f"{head}:{count}".encode("ascii"), hashlib.sha256
        ).hexdigest()
        self.store.conn.execute(
            "INSERT OR REPLACE INTO seals(session_id, head, count, hmac)"
            " VALUES(?,?,?,?)",
            (session_id, head, count, mac),
        )
        return mac

    def verify(self, session_id: str, key: Optional[bytes] = None) -> VerifyResult:
        rows = self.store.conn.execute(
            "SELECT seq, event, hash FROM ledger WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
        prev = GENESIS
        expected_seq = 1
        for r in rows:
            if r["seq"] != expected_seq:
                return VerifyResult(
                    False, len(rows), prev,
                    f"sequence gap: expected {expected_seq}, found {r['seq']}",
                )
            try:
                event = json.loads(r["event"])
            except json.JSONDecodeError:
                return VerifyResult(False, len(rows), prev, f"unparseable event at seq {r['seq']}")
            recomputed = chain_hash(prev, event)
            if recomputed != r["hash"]:
                return VerifyResult(
                    False, len(rows), prev, f"chain break at seq {r['seq']}"
                )
            prev = recomputed
            expected_seq += 1
        head, count = prev, len(rows)

        seal_row = self.store.conn.execute(
            "SELECT head, count, hmac FROM seals WHERE session_id=?", (session_id,)
        ).fetchone()
        if seal_row is not None:
            if seal_row["head"] != head or seal_row["count"] != count:
                return VerifyResult(
                    False, count, head, "seal mismatch: chain truncated or extended after sealing"
                )
            if key is not None:
                expect = hmac_mod.new(
                    key, f"{head}:{count}".encode("ascii"), hashlib.sha256
                ).hexdigest()
                if not hmac_mod.compare_digest(expect, seal_row["hmac"]):
                    return VerifyResult(False, count, head, "seal HMAC invalid (wrong key or forged seal)")
        return VerifyResult(True, count, head)
