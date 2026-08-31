"""SCR service host — the entry point frozen into `scr-service.exe`.

Two modes:
  * console  — `scr-service run [--host H --port P]`: runs the FastAPI app
    under uvicorn directly (used for local testing and non-Windows). Ctrl-C /
    SIGTERM triggers graceful shutdown.
  * service  — when launched by the Windows Service Control Manager (no args),
    it registers a service control handler, runs uvicorn in a thread, and on
    SERVICE_CONTROL_STOP shuts the server down gracefully: it stops accepting
    work, closes the store, and releases the workspace lock. Implemented with
    ctypes (no pywin32 dependency); the SCM path is exercised by the elevated
    install (see docs/CLEAN_BOX_TEST.md), the console path by tests.

Graceful shutdown always: close sessions cleanly (store.close) and release the
single-writer workspace lock so a restart is never blocked.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading

SERVICE_NAME = "SelfConnectRuntime"


def _build_app():
    """Build the FastAPI app over a real store + a model-backed kernel factory
    from the SCR home config."""
    from .capability import CapabilityManifest
    from .config import Config
    from .kernel import Kernel
    from .model_factory import build_adapter
    from .service import create_app
    from .state import Store
    from .vault import Vault

    cfg = Config()
    store = Store(os.path.join(cfg.home, "scr.db"))

    def factory(s, sid):
        name = cfg.get("default_model")
        if name and name in cfg.models():
            mc = cfg.models()[name]
            secret = Vault(cfg.home).get_secret(mc["secret_ref"]) if mc.get("secret_ref") else None
            return Kernel(s, build_adapter(mc, secret), {}, CapabilityManifest())
        # no model configured yet: a kernel with no adapter is only used once a
        # model is added; the service still starts and serves status/ledger.
        from .gateway import MockAdapter, ModelResponse
        return Kernel(s, MockAdapter([ModelResponse("no model configured")]),
                      {}, CapabilityManifest())

    return cfg, store, create_app(store, factory)


class _Runner:
    """Owns the uvicorn server + the workspace lock; supports graceful stop."""

    def __init__(self, host: str, port: int):
        import uvicorn

        from .locks import WorkspaceLock
        self.cfg, self.store, app = _build_app()
        self.lock = WorkspaceLock(os.path.join(self.cfg.home, "service.lock"))
        self.lock.acquire()                       # single service instance
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(config)

    def serve(self) -> None:
        try:
            self.server.run()
        finally:
            self._cleanup()

    def serve_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.serve, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self.server.should_exit = True            # uvicorn graceful shutdown

    def _cleanup(self) -> None:
        try:
            self.store.close()                    # close sessions/state cleanly
        finally:
            self.lock.release()                   # release the workspace lock


def run_console(argv=None) -> int:
    p = argparse.ArgumentParser(prog="scr-service")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=8787)
    args = p.parse_args(argv)
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8787)
    from .service import check_bind
    check_bind(host, tls=False, auth=True)        # refuse non-loopback w/o TLS
    runner = _Runner(host, port)
    runner.serve()                                # blocks; Ctrl-C → graceful
    return 0


# ------------------------------------------------------------- SCM (Windows)
def _run_as_service() -> bool:
    """Attempt to run under the Windows SCM. Returns False if not launched by
    the SCM (caller should fall back to console mode)."""
    if os.name != "nt":
        return False
    import ctypes as C
    import ctypes.wintypes as W

    a32 = C.WinDLL("advapi32", use_last_error=True)

    SERVICE_WIN32_OWN_PROCESS = 0x10
    SERVICE_CONTROL_STOP = 0x1
    SERVICE_CONTROL_SHUTDOWN = 0x5
    SERVICE_RUNNING = 0x4
    SERVICE_STOP_PENDING = 0x3
    SERVICE_STOPPED = 0x1
    SERVICE_START_PENDING = 0x2
    SERVICE_ACCEPT_STOP = 0x1
    SERVICE_ACCEPT_SHUTDOWN = 0x4
    ERROR_FAILED_SERVICE_CONTROLLER_CONNECT = 1063

    class SERVICE_STATUS(C.Structure):
        _fields_ = [("dwServiceType", W.DWORD), ("dwCurrentState", W.DWORD),
                    ("dwControlsAccepted", W.DWORD), ("dwWin32ExitCode", W.DWORD),
                    ("dwServiceSpecificExitCode", W.DWORD), ("dwCheckPoint", W.DWORD),
                    ("dwWaitHint", W.DWORD)]

    HANDLER = C.WINFUNCTYPE(W.DWORD, W.DWORD, W.DWORD, C.c_void_p, C.c_void_p)
    MAIN = C.WINFUNCTYPE(None, W.DWORD, C.POINTER(C.c_wchar_p))

    state = {"handle": None, "runner": None, "status": SERVICE_STATUS()}
    state["status"].dwServiceType = SERVICE_WIN32_OWN_PROCESS

    def set_state(current, accepts=0, wait_hint=0):
        st = state["status"]
        st.dwCurrentState = current
        st.dwControlsAccepted = accepts
        st.dwWaitHint = wait_hint
        a32.SetServiceStatus(state["handle"], C.byref(st))

    def handler(control, event_type, event_data, context):
        if control in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
            set_state(SERVICE_STOP_PENDING, 0, 5000)
            if state["runner"]:
                state["runner"].stop()            # graceful uvicorn shutdown
            set_state(SERVICE_STOPPED)
        return 0

    handler_cb = HANDLER(handler)

    def service_main(argc, argv):
        state["handle"] = a32.RegisterServiceCtrlHandlerExW(
            SERVICE_NAME, handler_cb, None)
        set_state(SERVICE_START_PENDING, 0, 5000)
        runner = _Runner("127.0.0.1", 8787)
        state["runner"] = runner
        t = runner.serve_in_thread()
        set_state(SERVICE_RUNNING, SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN)
        t.join()                                  # until stop() flips should_exit
        set_state(SERVICE_STOPPED)

    main_cb = MAIN(service_main)

    class SERVICE_TABLE_ENTRY(C.Structure):
        _fields_ = [("lpServiceName", C.c_wchar_p), ("lpServiceProc", MAIN)]

    table = (SERVICE_TABLE_ENTRY * 2)()
    table[0].lpServiceName = SERVICE_NAME
    table[0].lpServiceProc = main_cb
    table[1].lpServiceName = None
    table[1].lpServiceProc = C.cast(None, MAIN)

    if not a32.StartServiceCtrlDispatcherW(table):
        if C.get_last_error() == ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
            return False                          # not launched by SCM
        raise OSError(C.get_last_error(), "StartServiceCtrlDispatcherW failed")
    return True


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Explicit console subcommand, or non-Windows → console mode.
    if argv[:1] == ["run"] or os.name != "nt":
        return run_console(argv)
    # No args on Windows: try SCM; fall back to console if not under the SCM.
    if _run_as_service():
        return 0
    return run_console(["run"])


if __name__ == "__main__":
    sys.exit(main())
