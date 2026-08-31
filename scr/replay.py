"""Deterministic replay (§3.1).

The kernel is a deterministic state machine: given the same session id, the
same user input, the same model responses, and the same (deterministic) tools,
it produces a byte-identical hash-chained ledger. Replay re-executes a session
into a fresh store under the SAME session id and compares the resulting ledger
head to the original. Equal heads = faithful reproduction; unequal heads =
divergence (nondeterminism, tampering, or a changed input) — surfaced, not
hidden.

The idempotency key binds (session_id, journal seq, tool, args), so the SAME
session id is required to reproduce identical tool-exec hashes; that is why the
store now accepts an explicit session id.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .kernel import Kernel
from .ledger import Ledger
from .state import Store


@dataclass
class ReplayResult:
    matches: bool
    source_head: str
    replay_head: str
    source_count: int
    replay_count: int


# A factory that builds a fresh, deterministic Kernel over a given store+sid.
KernelBuilder = Callable[[Store, str], Kernel]


def run_and_replay(session_id: str, user_text: str,
                   build: KernelBuilder) -> ReplayResult:
    """Run a session once, then replay it into a fresh in-memory store under the
    same session id, and compare ledger heads. `build(store, sid)` must return a
    kernel with a DETERMINISTIC adapter + tools (same script each call)."""
    src = Store(":memory:")
    sid = src.create_session(session_id)
    build(src, sid).run(sid, user_text)
    src_head, src_count = Ledger(src).head(sid)

    rep = Store(":memory:")
    rep.create_session(session_id)
    build(rep, sid).run(sid, user_text)
    rep_head, rep_count = Ledger(rep).head(sid)

    return ReplayResult(
        matches=(src_head == rep_head and src_count == rep_count),
        source_head=src_head, replay_head=rep_head,
        source_count=src_count, replay_count=rep_count)


def replay_matches(source_store: Store, session_id: str, user_text: str,
                   build: KernelBuilder) -> bool:
    """Replay a session recorded in `source_store` into a fresh store under the
    same id; True iff the replayed ledger head matches the source's."""
    src_head, src_count = Ledger(source_store).head(session_id)
    rep = Store(":memory:")
    rep.create_session(session_id)
    build(rep, session_id).run(session_id, user_text)
    rep_head, rep_count = Ledger(rep).head(session_id)
    return src_head == rep_head and src_count == rep_count
