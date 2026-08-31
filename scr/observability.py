"""Structured logging + metrics (design §3.8).

JSON logs carry a correlation id from a contextvar so a session/run/tool-call
can be traced across records. The metrics registry is a tiny counter/histogram
store rendered in Prometheus text format — OFF BY DEFAULT; nothing is exposed
until explicitly enabled.
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "scr_correlation_id", default="-")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": correlation_id.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True)


@dataclass
class MetricsRegistry:
    enabled: bool = False
    _counters: dict[str, float] = field(default_factory=dict)
    _gauges: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, name: str, amount: float = 1.0) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + amount

    def observe(self, name: str, value: float) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._gauges[name] = value

    def render_prometheus(self) -> str:
        if not self.enabled:
            return ""
        lines = []
        with self._lock:
            for name, val in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {val}")
            for name, val in sorted(self._gauges.items()):
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {val}")
        return "\n".join(lines) + ("\n" if lines else "")


def configure_json_logging(logger: logging.Logger,
                           redaction_secrets: Optional[list[str]] = None) -> None:
    from .redaction import RedactionFilter
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.addFilter(RedactionFilter(redaction_secrets or []))
