"""Credential vault (design §3.2, §7). Nothing secret ever lands on disk in
plaintext.

Windows: DPAPI (CryptProtectData / CryptUnprotectData, user scope). The vault
stores the DPAPI ciphertext blob per secret name under the SCR home; only the
logged-in user (or the machine, if extended) can decrypt.
POSIX: the `keyring` library (libsecret / Keychain). Skip-marked in tests
where keyring is unavailable.

The interface is backend-agnostic: store_secret / get_secret / delete_secret.
"""
from __future__ import annotations

import os
from typing import Optional


class VaultError(Exception):
    pass


# ------------------------------------------------------------ Windows DPAPI
if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> _DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _blob_bytes(blob: _DATA_BLOB) -> bytes:
        return ctypes.string_at(blob.pbData, blob.cbData)

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def dpapi_protect(data: bytes, entropy: bytes = b"scr-vault") -> bytes:
        out = _DATA_BLOB()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(_blob(data)), "scr-secret", ctypes.byref(_blob(entropy)),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise VaultError(f"CryptProtectData failed: {ctypes.get_last_error()}")
        try:
            return _blob_bytes(out)
        finally:
            _kernel32.LocalFree(out.pbData)

    def dpapi_unprotect(data: bytes, entropy: bytes = b"scr-vault") -> bytes:
        out = _DATA_BLOB()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(_blob(data)), None, ctypes.byref(_blob(entropy)),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise VaultError(f"CryptUnprotectData failed: {ctypes.get_last_error()}")
        try:
            return _blob_bytes(out)
        finally:
            _kernel32.LocalFree(out.pbData)


class Vault:
    def __init__(self, home: str):
        self.dir = os.path.join(home, "vault")
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "-_.")
        return os.path.join(self.dir, f"{safe}.blob")

    def store_secret(self, name: str, value: str) -> None:
        if os.name == "nt":
            blob = dpapi_protect(value.encode("utf-8"))
            from .atomic import atomic_write_bytes
            atomic_write_bytes(self._path(name), blob)
        else:
            self._keyring().set_password("scr", name, value)

    def get_secret(self, name: str) -> Optional[str]:
        if os.name == "nt":
            path = self._path(name)
            if not os.path.exists(path):
                return None
            with open(path, "rb") as f:
                return dpapi_unprotect(f.read()).decode("utf-8")
        return self._keyring().get_password("scr", name)

    def delete_secret(self, name: str) -> None:
        if os.name == "nt":
            path = self._path(name)
            if os.path.exists(path):
                os.unlink(path)
        else:
            try:
                self._keyring().delete_password("scr", name)
            except Exception:
                pass

    def _keyring(self):
        try:
            import keyring
        except ImportError as e:  # pragma: no cover - POSIX-only path
            raise VaultError("keyring not installed; POSIX vault unavailable") from e
        return keyring
