"""Offline licensing (design §7). Ed25519-signed license files, no phone-home.

Expiry is graceful: an expired license degrades to READ-ONLY EVIDENCE ACCESS
(export/verify ledgers) and denies new runs — it never bricks the product.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .signing import sign, verify


@dataclass
class License:
    subject: str
    seats: int
    features: tuple[str, ...]
    not_after: float          # unix epoch seconds
    public_key: str = ""
    signature: str = ""

    def _payload(self) -> bytes:
        return json.dumps(
            {"subject": self.subject, "seats": self.seats,
             "features": sorted(self.features), "not_after": self.not_after},
            sort_keys=True, separators=(",", ":")).encode("utf-8")

    def to_text(self) -> str:
        return json.dumps({
            "subject": self.subject, "seats": self.seats,
            "features": list(self.features), "not_after": self.not_after,
            "public_key": self.public_key, "signature": self.signature,
        }, sort_keys=True)

    @staticmethod
    def issue(subject: str, seats: int, features: list[str], not_after: float,
              private_key_hex: str, public_key_hex: str) -> "License":
        lic = License(subject, seats, tuple(features), not_after,
                      public_key=public_key_hex)
        lic.signature = sign(private_key_hex, lic._payload())
        return lic

    @staticmethod
    def parse(text: str) -> "License":
        d = json.loads(text)
        return License(d["subject"], int(d["seats"]), tuple(d.get("features", [])),
                       float(d["not_after"]), d.get("public_key", ""),
                       d.get("signature", ""))


@dataclass
class LicenseStatus:
    state: str            # valid | grace | invalid
    reason: str = ""

    @property
    def may_run(self) -> bool:
        return self.state == "valid"

    @property
    def may_read_evidence(self) -> bool:
        return self.state in ("valid", "grace")


def check(text: str, trusted_public_key_hex: str, now: float) -> LicenseStatus:
    """Verify signature + trust, then expiry. Expired but authentic → grace."""
    try:
        lic = License.parse(text)
    except (json.JSONDecodeError, KeyError, ValueError):
        return LicenseStatus("invalid", "malformed license")
    if lic.public_key != trusted_public_key_hex:
        return LicenseStatus("invalid", "license not signed by the trusted key")
    if not verify(lic.public_key, lic.signature, lic._payload()):
        return LicenseStatus("invalid", "signature does not verify")
    if now > lic.not_after:
        return LicenseStatus("grace",
                             "license expired — read-only evidence access only")
    return LicenseStatus("valid", "")
