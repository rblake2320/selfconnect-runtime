"""MCP client host: launch/supervise stdio MCP servers and connect
streamable-HTTP servers from declarative config, projecting their tools into
the kernel as deny-by-default ToolSpecs.

Protocol: JSON-RPC 2.0. stdio transport is newline-delimited JSON objects.
A reader thread drains stdout into a queue so reads can time out on Windows
(no select() on pipes). Handshake: initialize -> initialized notification ->
tools/list. Calls are tools/call.

Security:
  * scoped env only — a server sees the restricted env plus its own
    configured vars; nothing from the parent's ambient environment leaks;
  * per-server capability scope — a projected tool name must ALSO be present
    in the kernel manifest's tool set, so deny-by-default still holds even
    if a server advertises a tool the operator never granted;
  * idempotent defaults to False in config, so an interrupted MCP call is
    quarantined by the kernel, never silently replayed.

Supervision: process liveness + protocol responsiveness = health; restart
with capped exponential backoff.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .capability import CapabilityManifest
from .kernel import ToolSpec
from .sandbox import restricted_env


class MCPError(Exception):
    pass


class _TransportError(MCPError):
    """Stream closed / timed out at the transport layer — restartable."""


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str                       # "stdio" | "http"
    command: tuple[str, ...] = ()        # stdio: argv
    url: str = ""                        # http: endpoint
    env: dict[str, str] = field(default_factory=dict)   # scoped extra env
    idempotent_tools: frozenset[str] = frozenset()      # tools safe to reissue
    call_timeout: float = 60.0
    max_restarts: int = 5


# ------------------------------------------------------------ stdio client
class _StdioClient:
    def __init__(self, cfg: MCPServerConfig):
        self.cfg = cfg
        self._proc: Optional[subprocess.Popen] = None
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._next_id = 0
        self._restarts = 0

    def start(self) -> None:
        env = restricted_env(self.cfg.env)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = subprocess.Popen(
            list(self.cfg.command),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, bufsize=0, creationflags=creationflags,
        )
        self._q = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self._handshake()

    def _pump(self) -> None:
        assert self._proc and self._proc.stdout
        for raw in self._proc.stdout:
            self._q.put(raw.decode("utf-8", "replace").strip())
        self._q.put(None)  # EOF sentinel

    def _send(self, obj: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("server not running")
        self._proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self._proc.stdin.flush()

    def _recv(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _TransportError("timeout awaiting server response")
            try:
                line = self._q.get(timeout=remaining)
            except queue.Empty:
                raise _TransportError("timeout awaiting server response")
            if line is None:
                raise _TransportError("server closed stream")
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore non-JSON log lines on stdout
            if "id" in msg or "error" in msg:
                return msg

    def _rpc(self, method: str, params: dict[str, Any], timeout: float) -> Any:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        msg = self._recv(timeout)
        if "error" in msg:
            raise MCPError(f"{method}: {msg['error']}")
        return msg.get("result")

    def _handshake(self) -> None:
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "scr", "version": "0.2.0"},
        }, timeout=self.cfg.call_timeout)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized",
                    "params": {}})

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {}, timeout=self.cfg.call_timeout)
        return result.get("tools", []) if isinstance(result, dict) else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            result = self._rpc("tools/call", {"name": name, "arguments": arguments},
                               timeout=self.cfg.call_timeout)
        except _TransportError:
            # Transport dropped (server crashed or hung) — restart and retry once.
            self._restart()
            result = self._rpc("tools/call", {"name": name, "arguments": arguments},
                               timeout=self.cfg.call_timeout)
        if isinstance(result, dict):
            parts = [c.get("text", "") for c in result.get("content", [])
                     if c.get("type") == "text"]
            return "".join(parts) if parts else json.dumps(result)
        return json.dumps(result)

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _restart(self) -> None:
        if self._restarts >= self.cfg.max_restarts:
            raise MCPError(f"server {self.cfg.name} exceeded max restarts")
        backoff = min(2.0 ** self._restarts, 30.0) * 0.01  # fast in tests
        self._restarts += 1
        time.sleep(backoff)
        self.stop()
        self.start()

    def stop(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None


# ------------------------------------------------------------- http client
class _HttpClient:
    def __init__(self, cfg: MCPServerConfig):
        self.cfg = cfg
        self._next_id = 0

    def start(self) -> None:
        self.list_tools()  # probe

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        import urllib.request
        self._next_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._next_id,
                           "method": method, "params": params}).encode()
        req = urllib.request.Request(
            self.cfg.url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.cfg.call_timeout) as resp:
            msg = json.loads(resp.read().decode())
        if "error" in msg:
            raise MCPError(f"{method}: {msg['error']}")
        return msg.get("result")

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list", {})
        return result.get("tools", []) if isinstance(result, dict) else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if isinstance(result, dict):
            parts = [c.get("text", "") for c in result.get("content", [])
                     if c.get("type") == "text"]
            return "".join(parts) if parts else json.dumps(result)
        return json.dumps(result)

    def stop(self) -> None:
        pass


class MCPHost:
    """Owns a set of MCP servers and projects their tools as ToolSpecs."""

    def __init__(self, manifest: CapabilityManifest):
        self.manifest = manifest
        self.clients: dict[str, Any] = {}

    def add_server(self, cfg: MCPServerConfig) -> None:
        client = _StdioClient(cfg) if cfg.transport == "stdio" else _HttpClient(cfg)
        client.start()
        self.clients[cfg.name] = client

    def stop_all(self) -> None:
        for c in self.clients.values():
            c.stop()

    def tool_specs(self) -> dict[str, ToolSpec]:
        """Project every advertised tool, scoped by the manifest tool set."""
        specs: dict[str, ToolSpec] = {}
        for name, client in self.clients.items():
            cfg: MCPServerConfig = client.cfg
            for tool in client.list_tools():
                tname = tool.get("name", "")
                projected = f"mcp__{name}__{tname}"
                idem = tname in cfg.idempotent_tools
                specs[projected] = self._make_spec(
                    client, projected, tname, tool.get("description", ""),
                    tool.get("inputSchema") or {"type": "object", "properties": {}},
                    idem)
        return specs

    def _make_spec(self, client: Any, projected: str, remote: str,
                   desc: str, schema: dict[str, Any], idem: bool) -> ToolSpec:
        def fn(args: dict[str, Any]) -> str:
            try:
                return client.call_tool(remote, args)
            except MCPError as e:
                return f"MCP ERROR [{projected}]: {e}"
        return ToolSpec(projected, fn, idempotent=idem, description=desc,
                        parameters=schema)
