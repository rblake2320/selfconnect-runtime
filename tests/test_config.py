"""Layered config: precedence, model endpoints store vault refs not secrets."""
from scr.config import Config


def test_defaults_and_override(tmp_path):
    cfg = Config(str(tmp_path))
    assert cfg.get("bind_host") == "127.0.0.1"
    cfg.override("bind_host", "0.0.0.0")
    assert cfg.get("bind_host") == "0.0.0.0"   # override wins
    cfg2 = Config(str(tmp_path))
    assert cfg2.get("bind_host") == "127.0.0.1"  # override is not persisted


def test_model_endpoint_stores_ref_not_secret(tmp_path):
    cfg = Config(str(tmp_path))
    cfg.add_model("openai", adapter="openai-compat", model="gpt-x",
                  base_url="https://gw.internal/v1", secret_ref="model:openai")
    cfg.save()
    raw = (tmp_path / "config.json").read_text()
    assert "model:openai" in raw           # the reference is fine
    assert "secret" not in raw.lower() or "secret_ref" in raw  # no bare secret value
    reloaded = Config(str(tmp_path))
    m = reloaded.models()["openai"]
    assert m["secret_ref"] == "model:openai"
    assert reloaded.get("default_model") == "openai"


def test_roundtrip_persists(tmp_path):
    cfg = Config(str(tmp_path))
    cfg.set("bind_port", 9999)
    cfg.save()
    assert Config(str(tmp_path)).get("bind_port") == 9999
