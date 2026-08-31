"""Forward-only schema migrations with a pre-migration snapshot and automatic
restore on failure (design §6: "migrations are versioned, forward-only, with
pre-migration snapshot; failed migration = automatic restore").

Each migration runs inside a transaction AND behind a snapshot taken via the
SQLite backup API. If a migration raises, the transaction is rolled back and
the DB is additionally restored from the snapshot — so a partially-applied or
non-transactional change (VACUUM, an auto-committed PRAGMA) can never leave a
customer DB corrupted or half-migrated. The schema version is `PRAGMA
user_version`; it only advances when a migration fully commits.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

from .state import Store


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int                       # strictly increasing, forward-only
    name: str
    apply: Callable[[sqlite3.Connection], None]


def current_version(store: Store) -> int:
    return int(store.conn.execute("PRAGMA user_version").fetchone()[0])


def _snapshot(store: Store, tag: str) -> str:
    """Consistent snapshot of the live DB via the backup API."""
    path = f"{store.db_path}.premigration-{tag}"
    if store.db_path == ":memory:":
        # in-memory: snapshot into a temp on-disk file
        path = os.path.join(os.getcwd(), f".scr-memsnap-{tag}.db")
    snap = sqlite3.connect(path)
    try:
        store.conn.backup(snap)
    finally:
        snap.close()
    return path


def _restore(store: Store, snap_path: str) -> None:
    """Overwrite the live DB content from the snapshot, in place (the live
    connection stays open — safe on Windows where the file is locked)."""
    snap = sqlite3.connect(snap_path)
    try:
        snap.backup(store.conn)
    finally:
        snap.close()


def _cleanup(path: str) -> None:
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass


def migrate(store: Store, migrations: list[Migration],
            target: Optional[int] = None) -> list[tuple[int, str]]:
    """Apply pending migrations in version order up to `target` (all if None).
    Returns the list of (version, name) applied. Forward-only: versions at or
    below the current version are skipped; a target below current is a no-op."""
    ordered = sorted(migrations, key=lambda m: m.version)
    # reject a non-monotonic registry early
    seen = set()
    for m in ordered:
        if m.version in seen:
            raise MigrationError(f"duplicate migration version {m.version}")
        seen.add(m.version)

    applied: list[tuple[int, str]] = []
    for m in ordered:
        cur = current_version(store)
        if m.version <= cur:
            continue
        if target is not None and m.version > target:
            break
        snap = _snapshot(store, str(m.version))
        try:
            store.conn.execute("BEGIN")
            m.apply(store.conn)
            store.conn.execute(f"PRAGMA user_version = {m.version}")
            store.conn.execute("COMMIT")
        except Exception as e:  # noqa: BLE001 — any failure = restore
            try:
                store.conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            _restore(store, snap)          # belt-and-suspenders beyond the txn
            _cleanup(snap)
            raise MigrationError(
                f"migration {m.version} ({m.name}) failed; DB restored: {e}") from e
        _cleanup(snap)
        applied.append((m.version, m.name))
    return applied


# ---------------------------------------------------------- default set
# Real product migrations register here as the schema evolves. v0 = the
# baseline schema created by Store; the first real migration is v1.
DEFAULT_MIGRATIONS: list[Migration] = [
    Migration(
        version=1, name="add_jobs_priority",
        apply=lambda c: c.execute(
            "ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"),
    ),
]
