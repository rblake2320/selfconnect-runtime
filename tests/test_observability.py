"""JSON logging with correlation ids + off-by-default metrics."""
import json
import logging

from scr.observability import (
    JsonLogFormatter,
    MetricsRegistry,
    configure_json_logging,
    correlation_id,
)


def test_json_log_has_correlation_id():
    logger = logging.getLogger("scr.test.obs")
    buf = []

    class _H(logging.Handler):
        def emit(self, record):
            buf.append(self.format(record))

    h = _H()
    h.setFormatter(JsonLogFormatter())
    logger.handlers.clear()
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

    token = correlation_id.set("session-42")
    try:
        logger.info("hello %s", "world")
    finally:
        correlation_id.reset(token)

    rec = json.loads(buf[0])
    assert rec["correlation_id"] == "session-42"
    assert rec["msg"] == "hello world"
    assert rec["level"] == "INFO"


def test_metrics_off_by_default_inert():
    m = MetricsRegistry()               # disabled
    m.inc("runs_total")
    m.observe("queue_depth", 5)
    assert m.render_prometheus() == ""  # nothing exposed


def test_metrics_enabled_counts_and_renders():
    m = MetricsRegistry(enabled=True)
    m.inc("runs_total")
    m.inc("runs_total", 2)
    m.observe("queue_depth", 7)
    text = m.render_prometheus()
    assert "runs_total 3.0" in text
    assert "queue_depth 7" in text
    assert "# TYPE runs_total counter" in text


def test_configure_json_logging_redacts():
    logger = logging.getLogger("scr.test.obs.redact")
    configure_json_logging(logger, redaction_secrets=["topsecret"])
    buf = []

    class _H(logging.Handler):
        def emit(self, record):
            buf.append(record.getMessage())

    # configure_json_logging set a StreamHandler; add a capture handler too
    cap = _H()
    logger.addHandler(cap)
    logger.setLevel(logging.INFO)
    logger.info("value is topsecret")
    assert all("topsecret" not in m for m in buf)
