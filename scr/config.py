"""Layered configuration (design §3.2). Precedence: defaults → config file →
runtime overrides. Model endpoints persist a VAULT REFERENCE for their
credential, never the secret itself.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from .atomic import atomic_write_text

_DEFAULTS: dict[str, Any] = {
    "bind_host": "127.0.0.1",
    "bind_port": 8787,
    "tls": False,
    "models": {},        # name -> {adapter, base_url, model, secret_ref}
    "default_model": None,
}


def scr_home() -> str:
    home = os.environ.get("SCR_HOME")
    if home:
        return home
    if os.name == "nt":
        base = os.environ.get("ProgramData", os.path.expanduser("~"))
        return os.path.join(base, "SelfConnect", "SCR")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "scr")


class Config:
    def __init__(self, home: Optional[str] = None):
        self.home = home or scr_home()
        os.makedirs(self.home, exist_ok=True)
        self.path = os.path.join(self.home, "config.json")
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._overrides: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = {**_DEFAULTS, **json.load(f)}

    def save(self) -> None:
        atomic_write_text(self.path,
                          json.dumps(self._data, sort_keys=True, indent=2))

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def override(self, key: str, value: Any) -> None:
        self._overrides[key] = value

    def add_model(self, name: str, adapter: str, model: str,
                  base_url: str = "", secret_ref: str = "",
                  timeout: Optional[float] = None,
                  num_ctx: Optional[int] = None) -> None:
        """Record a model endpoint. secret_ref names a vault entry; the secret
        itself is NEVER stored here."""
        models = dict(self._data.get("models", {}))
        models[name] = {"adapter": adapter, "model": model,
                        "base_url": base_url, "secret_ref": secret_ref}
        if timeout is not None:
            models[name]["timeout"] = float(timeout)
        if num_ctx is not None:
            models[name]["num_ctx"] = int(num_ctx)
        self._data["models"] = models
        if self._data.get("default_model") is None:
            self._data["default_model"] = name

    def models(self) -> dict[str, Any]:
        return dict(self._data.get("models", {}))
