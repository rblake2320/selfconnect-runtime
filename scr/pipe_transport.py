r"""Windows named-pipe transport for the runtime control channel (§3.7:
"REST + WebSocket on localhost by default (named pipe option on Windows)").

A minimal, real request/response transport over `\\.\pipe\<name>`: the server
reads a newline-framed JSON request, dispatches it to a handler, and writes a
newline-framed JSON response. Byte mode + newline framing avoids message-mode
edge cases. POSIX has no named pipes in this sense — uvicorn's Unix-domain
socket is the equivalent there; this module is Windows-only.

Hardening note: the pipe is created in the per-session local namespace. An
owner-only security descriptor is the recommended lockdown for multi-user
hosts (documented; not built here since the default local-namespace ACL
already scopes the pipe to the interactive session).
"""
from __future__ import annotations

import json
import os
import threading
from typing import Callable

if os.name == "nt":
    import ctypes as C
    import ctypes.wintypes as W

    _k32 = C.WinDLL("kernel32", use_last_error=True)
    _k32.CreateNamedPipeW.restype = W.HANDLE
    _k32.CreateNamedPipeW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD,
                                      W.DWORD, W.DWORD, W.DWORD, C.c_void_p]
    _k32.CreateFileW.restype = W.HANDLE
    _k32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                                 W.DWORD, W.DWORD, W.HANDLE]
    _k32.ConnectNamedPipe.argtypes = [W.HANDLE, C.c_void_p]
    _k32.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD,
                              C.POINTER(W.DWORD), C.c_void_p]
    _k32.WriteFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD,
                               C.POINTER(W.DWORD), C.c_void_p]
    _k32.FlushFileBuffers.argtypes = [W.HANDLE]
    _k32.DisconnectNamedPipe.argtypes = [W.HANDLE]
    _k32.CloseHandle.argtypes = [W.HANDLE]

    _INVALID = C.c_void_p(-1).value
    _PIPE_ACCESS_DUPLEX = 0x3
    _PIPE_UNLIMITED_INSTANCES = 255
    _GENERIC_RW = 0x80000000 | 0x40000000
    _OPEN_EXISTING = 3
    _ERROR_PIPE_CONNECTED = 535


def pipe_path(name: str) -> str:
    return rf"\\.\pipe\{name}"


class NamedPipeServer:
    """Serves newline-framed JSON requests to `handler(dict) -> dict`."""

    def __init__(self, name: str, handler: Callable[[dict], dict]):
        if os.name != "nt":
            raise RuntimeError("named-pipe transport is Windows-only")
        self.name = name
        self.handler = handler
        self._stop = threading.Event()
        self._thread = None

    def _read_line(self, h) -> bytes:
        buf = bytearray()
        one = (C.c_char * 1)()
        read = W.DWORD(0)
        while True:
            ok = _k32.ReadFile(h, one, 1, C.byref(read), None)
            if not ok or read.value == 0:
                break
            b = bytes(one[:1])
            if b == b"\n":
                break
            buf += b
        return bytes(buf)

    def serve_one(self, timeout_ms: int = 5000) -> bool:
        """Create one pipe instance, accept one client, handle one request."""
        h = _k32.CreateNamedPipeW(
            pipe_path(self.name), _PIPE_ACCESS_DUPLEX, 0,
            _PIPE_UNLIMITED_INSTANCES, 65536, 65536, timeout_ms, None)
        if h == _INVALID:
            raise OSError(C.get_last_error(), "CreateNamedPipeW failed")
        try:
            connected = _k32.ConnectNamedPipe(h, None)
            if not connected and C.get_last_error() not in (0, _ERROR_PIPE_CONNECTED):
                return False
            raw = self._read_line(h)
            try:
                req = json.loads(raw.decode("utf-8"))
                resp = self.handler(req)
            except Exception as e:  # noqa: BLE001
                resp = {"ok": False, "error": type(e).__name__, "detail": str(e)[:200]}
            payload = (json.dumps(resp) + "\n").encode("utf-8")
            written = W.DWORD(0)
            _k32.WriteFile(h, payload, len(payload), C.byref(written), None)
            _k32.FlushFileBuffers(h)
            return True
        finally:
            _k32.DisconnectNamedPipe(h)
            _k32.CloseHandle(h)

    def serve_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.serve_one()
            except OSError:
                break

    def start(self) -> "NamedPipeServer":
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        # unblock a pending ConnectNamedPipe by connecting once
        try:
            pipe_client_request(self.name, {"op": "_stop"}, timeout_s=1)
        except Exception:
            pass


def pipe_client_request(name: str, request: dict, timeout_s: float = 5.0) -> dict:
    """Connect to the pipe, send one JSON request, return the JSON response."""
    if os.name != "nt":
        raise RuntimeError("named-pipe transport is Windows-only")
    import time
    deadline = time.monotonic() + timeout_s
    h = _INVALID
    while time.monotonic() < deadline:
        h = _k32.CreateFileW(pipe_path(name), _GENERIC_RW, 0, None,
                             _OPEN_EXISTING, 0, None)
        if h != _INVALID:
            break
        time.sleep(0.02)
    if h == _INVALID:
        raise OSError(C.get_last_error(), "could not open pipe")
    try:
        payload = (json.dumps(request) + "\n").encode("utf-8")
        written = W.DWORD(0)
        _k32.WriteFile(h, payload, len(payload), C.byref(written), None)
        _k32.FlushFileBuffers(h)
        buf = bytearray()
        one = (C.c_char * 1)()
        read = W.DWORD(0)
        while True:
            ok = _k32.ReadFile(h, one, 1, C.byref(read), None)
            if not ok or read.value == 0:
                break
            b = bytes(one[:1])
            if b == b"\n":
                break
            buf += b
        return json.loads(bytes(buf).decode("utf-8"))
    finally:
        _k32.CloseHandle(h)
