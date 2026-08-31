"""Durable state store: SQLite in WAL mode.

Holds sessions, conversation messages, the kernel write-ahead journal,
the tool-result idempotency table, the hash-chained ledger rows, and seals.

Every kernel side effect is journaled BEFORE it happens (intent record)
and confirmed after (done record). Recovery reads the journal tail and
classifies what a crash interrupted.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS journal(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  state TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(session_id, seq)
);
CREATE TABLE IF NOT EXISTS tool_results(
  idem_key TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event TEXT NOT NULL,
  hash TEXT NOT NULL,
  UNIQUE(session_id, seq)
);
CREATE TABLE IF NOT EXISTS seals(
  session_id TEXT PRIMARY KEY,
  head TEXT NOT NULL,
  count INTEGER NOT NULL,
  hmac TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals(
  approval_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL,            -- approved | denied
  approver TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=FULL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- sessions -------------------------------------------------------
    def create_session(self) -> str:
        sid = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO sessions(id, created_at) VALUES(?,?)", (sid, time.time())
        )
        return sid

    def set_session_status(self, session_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE sessions SET status=? WHERE id=?", (status, session_id)
        )

    def session_status(self, session_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT status FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return row["status"] if row else None

    # -- messages -------------------------------------------------------
    def add_message(self, session_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
            (session_id, role, content, time.time()),
        )

    def get_messages(self, session_id: str) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    # -- write-ahead journal -------------------------------------------
    def journal_append(self, session_id: str, state: str, payload: dict[str, Any]) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM journal WHERE session_id=?",
            (session_id,),
        ).fetchone()
        seq = int(row["m"]) + 1
        self.conn.execute(
            "INSERT INTO journal(session_id, seq, state, payload, created_at)"
            " VALUES(?,?,?,?,?)",
            (session_id, seq, state, json.dumps(payload, sort_keys=True), time.time()),
        )
        return seq

    def journal_tail(self, session_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT seq, state, payload FROM journal WHERE session_id=?"
            " ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "seq": row["seq"],
            "state": row["state"],
            "payload": json.loads(row["payload"]),
        }

    def journal_all(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT seq, state, payload FROM journal WHERE session_id=? ORDER BY seq",
            (session_id,),
        ).fetchall()
        return [
            {"seq": r["seq"], "state": r["state"], "payload": json.loads(r["payload"])}
            for r in rows
        ]

    # -- idempotency ----------------------------------------------------
    def tool_result_get(self, idem_key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT result FROM tool_results WHERE idem_key=?", (idem_key,)
        ).fetchone()
        return row["result"] if row else None

    def tool_result_put(
        self, idem_key: str, session_id: str, tool: str, result: str
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO tool_results(idem_key, session_id, tool, result,"
            " created_at) VALUES(?,?,?,?,?)",
            (idem_key, session_id, tool, result, time.time()),
        )

    # -- approvals (HITL gate) -----------------------------------------
    def approval_put(
        self, approval_id: str, session_id: str, status: str, approver: str
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO approvals(approval_id, session_id, status,"
            " approver, created_at) VALUES(?,?,?,?,?)",
            (approval_id, session_id, status, approver, time.time()),
        )

    def approval_get(self, approval_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT status, approver FROM approvals WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            return None
        return {"status": row["status"], "approver": row["approver"]}
