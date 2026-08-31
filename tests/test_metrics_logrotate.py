"""/metrics off-by-default (§3.8) and rotating JSON logs (§3.8, §5)."""
import glob
import json
import logging
import os

from fastapi.testclient import TestClient

from scr.capability import CapabilityManifest
from scr.gateway import MockAdapter, ModelResponse
from scr.kernel import Kernel
from scr.observability import MetricsRegistry, configure_rotating_json_logging
from scr.service import create_app
from scr.state import Store


def _factory():
    def make(store, sid):
        return Kernel(store, MockAdapter([ModelResponse("hi")]), {},
                      CapabilityManifest())
    return make


def _tokens(store):
    store.token_put("op", "op@x", "operator")


def test_metrics_disabled_by_default_returns_404():
    store = Store(":memory:"); _tokens(store)
    c = TestClient(create_app(store, _factory()))          # no metrics → disabled
    assert c.get("/metrics").status_code == 404


def test_metrics_enabled_exposes_prometheus_after_run():
    store = Store(":memory:"); _tokens(store)
    reg = MetricsRegistry(enabled=True)
    c = TestClient(create_app(store, _factory(), metrics=reg))
    c.post("/runs", json={"user_text": "x", "idem_key": "k1"},
           headers={"Authorization": "Bearer op"})
    r = c.get("/metrics")
    assert r.status_code == 200
    assert "scr_runs_total 1" in r.text
    assert "scr_run_completed_total 1" in r.text


def test_rotating_log_creates_backups(tmp_path):
    logpath = str(tmp_path / "scr.log")
    logger = logging.getLogger("scr.test.rotate")
    logger.setLevel(logging.INFO)
    # tiny cap so a handful of lines forces rotation
    configure_rotating_json_logging(logger, logpath, max_bytes=400, backup_count=3)
    for i in range(50):
        logger.info("event number %d with some padding text", i)
    files = glob.glob(logpath + "*")
    assert logpath in files                       # active log exists
    assert any(f != logpath for f in files)       # at least one rotated backup
    # active log lines are valid JSON
    with open(logpath, encoding="utf-8") as f:
        line = f.readline().strip()
    if line:
        rec = json.loads(line)
        assert "msg" in rec and "correlation_id" in rec


def test_rotating_log_redacts_secrets(tmp_path):
    logpath = str(tmp_path / "scr.log")
    logger = logging.getLogger("scr.test.rotate.redact")
    logger.setLevel(logging.INFO)
    configure_rotating_json_logging(logger, logpath, max_bytes=10000,
                                    redaction_secrets=["topsecretvalue"])
    logger.info("using token topsecretvalue now")
    with open(logpath, encoding="utf-8") as f:
        contents = f.read()
    assert "topsecretvalue" not in contents
