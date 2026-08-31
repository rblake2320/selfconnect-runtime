"""Log redaction scrubs secrets and key-shaped tokens before emission."""
import logging

from scr.redaction import RedactionFilter


def _capture(record_msg, *args, secrets=None):
    logger = logging.getLogger("scr.test.redact")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    buf = []

    class _H(logging.Handler):
        def emit(self, record):
            buf.append(self.format(record))

    h = _H()
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)
    logger.addFilter(RedactionFilter(secrets or []))
    logger.info(record_msg, *args)
    return "\n".join(buf)


def test_registered_secret_scrubbed():
    out = _capture("token is %s here", "hunter2superSECRETvalue", secrets=["hunter2superSECRETvalue"])
    assert "hunter2superSECRETvalue" not in out
    assert "REDACTED" in out


def test_api_key_pattern_scrubbed():
    out = _capture("using key sk-ABCDEF0123456789ABCDEF for the call")
    assert "sk-ABCDEF0123456789ABCDEF" not in out
    assert "REDACTED" in out


def test_long_hex_scrubbed():
    out = _capture("hmac=" + "a" * 40)
    assert "a" * 40 not in out


def test_bearer_scrubbed():
    out = _capture("Authorization: Bearer abcDEF123456ghijkl")
    assert "abcDEF123456ghijkl" not in out


def test_non_secret_passes_through():
    out = _capture("session %s started with %d tools", "abc", 3)
    assert "session abc started with 3 tools" == out
