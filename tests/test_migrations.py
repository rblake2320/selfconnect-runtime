"""Forward-only migrations with pre-migration snapshot + auto-restore (§6)."""
import sqlite3

import pytest

from scr.migrations import (
    DEFAULT_MIGRATIONS,
    Migration,
    MigrationError,
    current_version,
    migrate,
)
from scr.state import Store


def _add_table(name):
    return lambda c: c.execute(f"CREATE TABLE {name}(id INTEGER PRIMARY KEY)")


def test_applies_in_order_and_advances_version(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    migs = [Migration(1, "t1", _add_table("t1")),
            Migration(2, "t2", _add_table("t2"))]
    applied = migrate(store, migs)
    assert applied == [(1, "t1"), (2, "t2")]
    assert current_version(store) == 2
    # both tables exist
    names = {r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"t1", "t2"} <= names


def test_idempotent_rerun_is_noop(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    migs = [Migration(1, "t1", _add_table("t1"))]
    assert migrate(store, migs) == [(1, "t1")]
    assert migrate(store, migs) == []          # nothing pending
    assert current_version(store) == 1


def test_forward_only_target_below_current_is_noop(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    migs = [Migration(1, "t1", _add_table("t1")),
            Migration(2, "t2", _add_table("t2"))]
    migrate(store, migs)                        # now at 2
    assert migrate(store, migs, target=1) == []  # can't go back
    assert current_version(store) == 2


def test_failed_migration_auto_restores_and_holds_version(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    store.conn.execute("CREATE TABLE keep(id INTEGER)")
    store.conn.execute("INSERT INTO keep VALUES (42)")

    def boom(c):
        c.execute("CREATE TABLE half(id INTEGER)")   # partial work
        raise RuntimeError("mid-migration failure")

    migs = [Migration(1, "good", _add_table("good")),
            Migration(2, "bad", boom)]
    with pytest.raises(MigrationError):
        migrate(store, migs)

    # v1 committed; v2 failed and was restored → version stays at 1
    assert current_version(store) == 1
    names = {r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "good" in names          # v1 applied
    assert "half" not in names      # v2's partial work reverted
    # pre-existing data intact
    assert store.conn.execute("SELECT id FROM keep").fetchone()[0] == 42


def test_snapshot_restore_primitive_reverts_live_changes(tmp_path):
    """Directly exercise the snapshot/restore path: a change auto-committed
    outside a transaction is still reverted by the snapshot restore."""
    from scr.migrations import _restore, _snapshot
    store = Store(str(tmp_path / "m.db"))
    store.conn.execute("CREATE TABLE orig(id INTEGER)")
    snap = _snapshot(store, "test")
    # mutate the live DB AFTER the snapshot (auto-committed)
    store.conn.execute("CREATE TABLE injected(id INTEGER)")
    store.conn.execute("DROP TABLE orig")
    _restore(store, snap)
    names = {r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "orig" in names and "injected" not in names   # snapshot won


def test_duplicate_version_rejected(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    with pytest.raises(MigrationError):
        migrate(store, [Migration(1, "a", _add_table("a")),
                        Migration(1, "b", _add_table("b"))])


def test_default_migrations_apply(tmp_path):
    store = Store(str(tmp_path / "m.db"))
    migrate(store, DEFAULT_MIGRATIONS)
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "priority" in cols
