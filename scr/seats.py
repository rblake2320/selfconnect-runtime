"""Seat enforcement in team mode (§7): session accounting in the tenant DB
bounds the number of distinct concurrent seat holders to the license seat
count. A holder already occupying a seat may re-acquire freely (idempotent);
a new holder beyond the limit is refused.
"""
from __future__ import annotations

import time

from .state import Store


class SeatsExhausted(Exception):
    pass


class SeatManager:
    def __init__(self, store: Store, seats: int):
        self.store = store
        self.seats = seats

    def active(self) -> int:
        return int(self.store.conn.execute(
            "SELECT COUNT(*) FROM seat_holders").fetchone()[0])

    def holds(self, subject: str) -> bool:
        return self.store.conn.execute(
            "SELECT 1 FROM seat_holders WHERE subject=?", (subject,)).fetchone() is not None

    def acquire(self, subject: str) -> None:
        """Occupy a seat for `subject`. No-op if already held. Raises
        SeatsExhausted if a NEW holder would exceed the licensed seat count."""
        if self.holds(subject):
            return
        if self.active() >= self.seats:
            raise SeatsExhausted(
                f"license seat limit reached ({self.seats}); cannot admit {subject!r}")
        self.store.conn.execute(
            "INSERT OR IGNORE INTO seat_holders(subject, acquired_at) VALUES(?,?)",
            (subject, time.time()))

    def release(self, subject: str) -> None:
        self.store.conn.execute(
            "DELETE FROM seat_holders WHERE subject=?", (subject,))
