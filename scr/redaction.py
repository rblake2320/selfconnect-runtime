"""Log redaction (design §3.8). A logging.Filter that scrubs registered
secret values and high-entropy key-shaped tokens from every record before it
is emitted, so a credential can never reach a log file or console.
"""
from __future__ import annotations

import logging
import re

# Key-shaped tokens: long base64/hex-ish runs, and common secret prefixes.
_PATTERNS = [
    re.compile(r"\b(sk|pk|rk|api|key|tok)[-_][A-Za-z0-9]{16,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),                 # long hex (keys, hmacs)
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}\b"),
]
_MASK = "«REDACTED»"


class RedactionFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None):
        super().__init__()
        self._secrets: set[str] = set(s for s in (secrets or []) if s)

    def add_secret(self, value: str) -> None:
        if value:
            self._secrets.add(value)

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret and secret in text:
                text = text.replace(secret, _MASK)
        for pat in _PATTERNS:
            text = pat.sub(_MASK, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.msg = self._scrub(msg)
        record.args = ()  # already folded into msg by getMessage()
        return True
