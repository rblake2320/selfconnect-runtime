"""Seat enforcement in team mode (§7)."""
import pytest

from scr.license import License, check
from scr.seats import SeatManager, SeatsExhausted
from scr.signing import generate_keypair
from scr.state import Store


def test_within_seats_ok():
    sm = SeatManager(Store(":memory:"), seats=2)
    sm.acquire("alice")
    sm.acquire("bob")
    assert sm.active() == 2


def test_exceeding_seats_denied():
    sm = SeatManager(Store(":memory:"), seats=2)
    sm.acquire("alice")
    sm.acquire("bob")
    with pytest.raises(SeatsExhausted):
        sm.acquire("carol")


def test_same_subject_reacquire_is_free():
    sm = SeatManager(Store(":memory:"), seats=1)
    sm.acquire("alice")
    sm.acquire("alice")            # idempotent, no new seat
    assert sm.active() == 1


def test_release_frees_a_seat():
    sm = SeatManager(Store(":memory:"), seats=2)
    sm.acquire("alice")
    sm.acquire("bob")
    with pytest.raises(SeatsExhausted):
        sm.acquire("carol")
    sm.release("alice")
    sm.acquire("carol")            # now fits
    assert sm.holds("carol") and not sm.holds("alice")


def test_seat_count_driven_by_license():
    """The seat limit is the license's seat field end-to-end."""
    priv, pub = generate_keypair()
    lic = License.issue("acme", seats=1, features=["run"],
                        not_after=2_000_000_000.0, private_key_hex=priv,
                        public_key_hex=pub)
    status = check(lic.to_text(), pub, now=1_700_000_000.0)
    assert status.state == "valid"
    parsed = License.parse(lic.to_text())
    sm = SeatManager(Store(":memory:"), seats=parsed.seats)
    sm.acquire("alice")
    with pytest.raises(SeatsExhausted):
        sm.acquire("bob")          # license granted only 1 seat
