"""Deterministic SHA-256 Merkle tree over a package's file set.

Leaves are domain-separated (`leaf:` prefix) and sorted by path so the root
is a pure function of {path: filehash} independent of input order. Internal
nodes use a distinct (`node:`) prefix so a leaf can never be reinterpreted as
an internal node (second-preimage / node-substitution defense). An odd node
at any level is promoted unchanged to the next level.
"""
from __future__ import annotations

import hashlib


def _leaf(path: str, file_hash_hex: str) -> bytes:
    h = hashlib.sha256()
    h.update(b"leaf:")
    h.update(path.encode("utf-8"))
    h.update(b":")
    h.update(bytes.fromhex(file_hash_hex))
    return h.digest()


def _node(left: bytes, right: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(b"node:")
    h.update(left)
    h.update(right)
    return h.digest()


def merkle_root(files: dict[str, str]) -> str:
    """Return the hex Merkle root of {path: sha256hex}. Empty set → all-zero."""
    if not files:
        return "0" * 64
    level = [_leaf(p, files[p]) for p in sorted(files)]
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_node(level[i], level[i + 1]))
            else:
                nxt.append(level[i])  # odd node promoted
        level = nxt
    return level[0].hex()
