"""Ed25519 signing, key pinning, and signed revocation lists (design §3.4).

The signature is detached and covers the Merkle root of the package. Trust is
by key pinning: only signatures from a key in the Keystore are honored
(publisher key pinned at install; customers may add their own). Revocation is
a signed list — its own signature must verify against a trusted key before it
is honored, so a forged revocation list cannot deny-of-service a good package.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def key_id(public_key_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:16]


def generate_keypair() -> tuple[str, str]:
    """Return (private_hex, public_hex) raw Ed25519 key bytes as hex."""
    priv = Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes_raw()
    pub_bytes = priv.public_key().public_bytes_raw()
    return priv_bytes.hex(), pub_bytes.hex()


def sign(private_key_hex: str, message: bytes) -> str:
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    return priv.sign(message).hex()


def verify(public_key_hex: str, signature_hex: str, message: bytes) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(signature_hex), message)
        return True
    except (InvalidSignature, ValueError):
        return False


@dataclass
class Keystore:
    """Trusted public keys, by key_id. Deny-by-default: a key not present is
    not trusted."""
    keys: dict[str, str] = field(default_factory=dict)  # key_id -> public_hex

    def add(self, public_key_hex: str) -> str:
        kid = key_id(public_key_hex)
        self.keys[kid] = public_key_hex
        return kid

    def trusts(self, public_key_hex: str) -> bool:
        kid = key_id(public_key_hex)
        return self.keys.get(kid) == public_key_hex


@dataclass
class RevocationList:
    """Signed list of revoked (package, version) pairs."""
    revoked: tuple[tuple[str, str], ...] = ()
    public_key: str = ""
    signature: str = ""

    def _message(self) -> bytes:
        return json.dumps(
            {"revoked": [list(r) for r in sorted(self.revoked)]},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def create(revoked: list[tuple[str, str]], private_key_hex: str,
               public_key_hex: str) -> "RevocationList":
        rl = RevocationList(revoked=tuple(revoked), public_key=public_key_hex)
        rl.signature = sign(private_key_hex, rl._message())
        return rl

    def is_valid(self, keystore: Keystore) -> bool:
        """A revocation list is honored only if signed by a trusted key."""
        if not self.signature or not keystore.trusts(self.public_key):
            return False
        return verify(self.public_key, self.signature, self._message())

    def is_revoked(self, package: str, version: str) -> bool:
        return (package, version) in set(self.revoked)
