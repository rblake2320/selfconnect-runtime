import json

from scr.ledger import GENESIS, Ledger
from scr.state import Store


def make(tmp_path, n=5):
    store = Store(str(tmp_path / "l.db"))
    ledger = Ledger(store)
    sid = store.create_session()
    for i in range(n):
        ledger.append(sid, {"type": "event", "i": i})
    return store, ledger, sid


def test_clean_chain_verifies(tmp_path):
    store, ledger, sid = make(tmp_path)
    r = ledger.verify(sid)
    assert r.ok and r.count == 5 and r.head != GENESIS


def test_empty_chain_verifies(tmp_path):
    store = Store(str(tmp_path / "l.db"))
    ledger = Ledger(store)
    sid = store.create_session()
    r = ledger.verify(sid)
    assert r.ok and r.count == 0 and r.head == GENESIS


def test_bit_flip_in_event_detected(tmp_path):
    store, ledger, sid = make(tmp_path)
    store.conn.execute(
        "UPDATE ledger SET event=? WHERE session_id=? AND seq=3",
        (json.dumps({"type": "event", "i": 99}, sort_keys=True, separators=(",", ":")), sid),
    )
    r = ledger.verify(sid)
    assert not r.ok and "chain break at seq 3" in r.error


def test_reorder_detected(tmp_path):
    store, ledger, sid = make(tmp_path)
    # swap events at seq 2 and 4
    e2 = store.conn.execute("SELECT event FROM ledger WHERE session_id=? AND seq=2", (sid,)).fetchone()["event"]
    e4 = store.conn.execute("SELECT event FROM ledger WHERE session_id=? AND seq=4", (sid,)).fetchone()["event"]
    store.conn.execute("UPDATE ledger SET event=? WHERE session_id=? AND seq=2", (e4, sid))
    store.conn.execute("UPDATE ledger SET event=? WHERE session_id=? AND seq=4", (e2, sid))
    r = ledger.verify(sid)
    assert not r.ok


def test_mid_chain_deletion_detected(tmp_path):
    store, ledger, sid = make(tmp_path)
    store.conn.execute("DELETE FROM ledger WHERE session_id=? AND seq=3", (sid,))
    r = ledger.verify(sid)
    assert not r.ok and "sequence gap" in r.error


def test_splice_forged_event_with_recomputed_hash_detected(tmp_path):
    """Attacker replaces an event AND recomputes that row's hash, but cannot
    fix downstream rows without rewriting the whole chain — verify catches
    the break at the next row."""
    from scr.ledger import chain_hash
    store, ledger, sid = make(tmp_path)
    rows = store.conn.execute(
        "SELECT seq, hash FROM ledger WHERE session_id=? ORDER BY seq", (sid,)
    ).fetchall()
    prev = rows[1]["hash"]  # hash at seq 2
    forged = {"type": "event", "i": 777}
    forged_hash = chain_hash(prev, forged)
    store.conn.execute(
        "UPDATE ledger SET event=?, hash=? WHERE session_id=? AND seq=3",
        (json.dumps(forged, sort_keys=True, separators=(",", ":")), forged_hash, sid),
    )
    r = ledger.verify(sid)
    assert not r.ok and "chain break at seq 4" in r.error


def test_truncation_after_seal_detected(tmp_path):
    store, ledger, sid = make(tmp_path)
    key = b"k" * 32
    ledger.seal(sid, key)
    store.conn.execute("DELETE FROM ledger WHERE session_id=? AND seq=5", (sid,))
    r = ledger.verify(sid, key)
    assert not r.ok and "seal mismatch" in r.error


def test_extension_after_seal_detected(tmp_path):
    store, ledger, sid = make(tmp_path)
    key = b"k" * 32
    ledger.seal(sid, key)
    ledger.append(sid, {"type": "sneaky", "i": 100})
    r = ledger.verify(sid, key)
    assert not r.ok and "seal mismatch" in r.error


def test_seal_forgery_wrong_key_detected(tmp_path):
    store, ledger, sid = make(tmp_path)
    ledger.seal(sid, b"attacker-key-000000000000000000000")
    r = ledger.verify(sid, b"real-key-0000000000000000000000000")
    assert not r.ok and "seal HMAC invalid" in r.error


def test_seal_valid_key_verifies(tmp_path):
    store, ledger, sid = make(tmp_path)
    key = b"real-key-0000000000000000000000000"
    ledger.seal(sid, key)
    r = ledger.verify(sid, key)
    assert r.ok


def test_unparseable_event_detected(tmp_path):
    store, ledger, sid = make(tmp_path)
    store.conn.execute(
        "UPDATE ledger SET event='not-json{' WHERE session_id=? AND seq=2", (sid,)
    )
    r = ledger.verify(sid)
    assert not r.ok and "unparseable" in r.error
