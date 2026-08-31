"""Reduce the privilege of a sandbox worker process (§3.6).

Applied by the worker to ITSELF at startup — this avoids rewriting the tested
Popen/Job-Object spawn path (using CreateProcessAsUserW with a restricted token
would fight the pipe-based worker IPC; see docs/DECISIONS.md ADR-012).

Windows: `AdjustTokenPrivileges(DisableAllPrivileges=TRUE)` disables every
privilege in the worker's token — the standard restricted-sandbox hardening.
Combined with the non-admin account and the capability kernel's path jail, the
worker cannot use any privileged operation and cannot write protected system
paths.
POSIX: `prctl(PR_SET_NO_NEW_PRIVS)` so the worker can never gain privileges via
setuid/file capabilities (seccomp syscall filtering is the deeper residual).

Read isolation of arbitrary paths at the OS level (beyond the capability
kernel's mediation) requires AppContainer on Windows / Landlock on Linux —
documented as a residual (ADR-012); low-integrity tokens were ruled out because
they block the worker's own temp/jail writes and Medium-IL pipe replies.
"""
from __future__ import annotations

import os

SE_PRIVILEGE_ENABLED = 0x2
_TOKEN_QUERY = 0x0008
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_TokenPrivileges = 3


def _win_token_handle(access):
    import ctypes as C
    import ctypes.wintypes as W
    k32 = C.WinDLL("kernel32", use_last_error=True)
    a32 = C.WinDLL("advapi32", use_last_error=True)
    k32.GetCurrentProcess.restype = W.HANDLE
    a32.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    h = W.HANDLE()
    if not a32.OpenProcessToken(k32.GetCurrentProcess(), access, C.byref(h)):
        raise OSError(C.get_last_error(), "OpenProcessToken failed")
    return a32, h


def enabled_privilege_count() -> int:
    """How many privileges are currently ENABLED on this process's token."""
    if os.name != "nt":
        return 0
    import ctypes as C
    import ctypes.wintypes as W
    a32, h = _win_token_handle(_TOKEN_QUERY)
    a32.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD,
                                        C.POINTER(W.DWORD)]
    size = W.DWORD(0)
    a32.GetTokenInformation(h, _TokenPrivileges, None, 0, C.byref(size))
    buf = (C.c_byte * size.value)()
    if not a32.GetTokenInformation(h, _TokenPrivileges, buf, size, C.byref(size)):
        raise OSError(C.get_last_error(), "GetTokenInformation failed")
    count = int.from_bytes(bytes(buf[:4]), "little")   # PrivilegeCount (DWORD)
    enabled = 0
    # each LUID_AND_ATTRIBUTES = 8-byte LUID + 4-byte Attributes = 12 bytes
    off = 4
    for _ in range(count):
        attrs = int.from_bytes(bytes(buf[off + 8:off + 12]), "little")
        if attrs & SE_PRIVILEGE_ENABLED:
            enabled += 1
        off += 12
    return enabled


def _windows_disable_all_privileges() -> bool:
    import ctypes as C
    import ctypes.wintypes as W
    a32, h = _win_token_handle(_TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY)
    a32.AdjustTokenPrivileges.argtypes = [W.HANDLE, W.BOOL, C.c_void_p, W.DWORD,
                                          C.c_void_p, C.c_void_p]
    # DisableAllPrivileges = TRUE → disables every privilege in the token.
    ok = a32.AdjustTokenPrivileges(h, True, None, 0, None, None)
    return bool(ok)


def _posix_no_new_privs() -> bool:
    import ctypes
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    PR_SET_NO_NEW_PRIVS = 38
    return libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0


def harden_current_process() -> bool:
    """Drop privilege for the current (worker) process. Best-effort: returns
    True on success, False if the platform mechanism was unavailable."""
    try:
        if os.name == "nt":
            return _windows_disable_all_privileges()
        return _posix_no_new_privs()
    except OSError:
        return False
