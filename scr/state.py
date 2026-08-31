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
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  idem_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,            -- queued | running | done | cancelled | needs_review
  user_text TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mailbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_tokens(
  token TEXT PRIMARY KEY,
  subject TEXT NOT NULL,
  role TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seat_holders(
  subject TEXT PRIMARY KEY,
  acquired_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_sessions(
  team_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  parent_session TEXT,
  depth INTEGER NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(session_id)
);
"""


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # check_same_thread=False: the FastAPI service dispatches sync routes on
        # a threadpool, so the connection is used from worker threads. WAL mode
        # + synchronous=FULL + busy_timeout keep this safe for the single-tenant
        # self-hosted service; SQLite serializes access internally.
        self.conn = sqlite3.connect(db_path, isolation_level=None,
                                    check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=FULL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- sessions -------------------------------------------------------
    def create_session(self, session_id: Optional[str] = None) -> str:
        # An explicit id supports deterministic replay (§3.1): re-running into a
        # fresh store under the SAME session id reproduces the same idem keys
        # and therefore the same ledger hash chain.
        sid = session_id or uuid.uuid4().hex
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

    # -- jobs (durable queue) ------------------------------------------
    def job_upsert(self, job_id: str, session_id: str, idem_key: str,
                   status: str, user_text: str) -> dict[str, Any]:
        """Insert a job, or return the existing one for this idem_key (dedupe)."""
        existing = self.conn.execute(
            "SELECT job_id, session_id, status FROM jobs WHERE idem_key=?",
            (idem_key,),
        ).fetchone()
        if existing is not None:
            return {"job_id": existing["job_id"], "session_id": existing["session_id"],
                    "status": existing["status"], "deduped": True}
        self.conn.execute(
            "INSERT INTO jobs(job_id, session_id, idem_key, status, user_text,"
            " created_at) VALUES(?,?,?,?,?,?)",
            (job_id, session_id, idem_key, status, user_text, time.time()),
        )
        return {"job_id": job_id, "session_id": session_id, "status": status,
                "deduped": False}

    def job_set_status(self, job_id: str, status: str) -> None:
        self.conn.execute("UPDATE jobs SET status=? WHERE job_id=?", (status, job_id))

    def job_get(self, job_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT job_id, session_id, idem_key, status, user_text FROM jobs"
            " WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def jobs_by_status(self, status: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT job_id, session_id, idem_key, status, user_text FROM jobs"
            " WHERE status=? ORDER BY created_at", (status,)).fetchall()
        return [dict(r) for r in rows]

    def jobs_all(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT job_id, session_id, status FROM jobs ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # -- mailbox (inter-agent) -----------------------------------------
    def mailbox_send(self, session_id: str, from_agent: str, to_agent: str,
                     body: str) -> None:
        self.conn.execute(
            "INSERT INTO mailbox(session_id, from_agent, to_agent, body, created_at)"
            " VALUES(?,?,?,?,?)", (session_id, from_agent, to_agent, body, time.time()))

    def mailbox_inbox(self, session_id: str, to_agent: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT from_agent, to_agent, body FROM mailbox WHERE session_id=?"
            " AND to_agent=? ORDER BY id", (session_id, to_agent)).fetchall()
        return [dict(r) for r in rows]

    # -- auth tokens ----------------------------------------------------
    def token_put(self, token: str, subject: str, role: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO agent_tokens(token, subject, role) VALUES(?,?,?)",
            (token, subject, role))

    def token_get(self, token: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT subject, role FROM agent_tokens WHERE token=?", (token,)).fetchone()
        return dict(row) if row else None

    # -- team sessions (delegation tree) --------------------------------
    def team_session_add(self, team_id: str, session_id: str, agent: str,
                         parent_session: Optional[str], depth: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO team_sessions(team_id, session_id, agent,"
            " parent_session, depth, created_at) VALUES(?,?,?,?,?,?)",
            (team_id, session_id, agent, parent_session, depth, time.time()))

    def team_members(self, team_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT session_id, agent, parent_session, depth FROM team_sessions"
            " WHERE team_id=? ORDER BY created_at", (team_id,)).fetchall()
        return [dict(r) for r in rows]

    def team_id_for_session(self, session_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT team_id FROM team_sessions WHERE session_id=?",
            (session_id,)).fetchone()
        return row["team_id"] if row else None
