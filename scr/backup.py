"""Encrypted backup/restore (design §3.5, §3.8, §5).

A backup is an AES-256-GCM-encrypted snapshot of the state DB + config. The
GCM auth tag makes any tampered or wrong-key archive fail closed. Restore is
atomic: it stages into a temp dir and only swaps into place on full success.

Key handling (§3.5 "key wrapped by DPAPI/keyring"):
  * DEFAULT (no key supplied): a random AES-256 key is generated per backup and
    WRAPPED with DPAPI (Windows); only the wrapped blob is stored in the
    archive — the AES key is never on disk in plaintext, and only the same
    user/machine can unwrap it. Same-machine backup/restore needs no key
    management.
  * EXPLICIT key (air-gapped / cross-machine): the caller supplies a 32-byte
    key; the archive stores no wrapped key and restore requires the same key.

Archive format: MAGIC(8) ‖ wrapped_len(4, big-endian) ‖ wrapped_key ‖ nonce(12)
‖ ciphertext.  wrapped_len == 0 ⇒ explicit-key mode.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MAGIC = b"SCRBAK02"


class BackupError(Exception):
    pass


def _dpapi_available() -> bool:
    return os.name == "nt"


def _wrap_key(key: bytes) -> bytes:
    """Wrap the AES key so it is never stored in plaintext. Windows: DPAPI
    (user scope). Elsewhere: raises (caller must use explicit-key mode)."""
    if os.name == "nt":
        from .vault import dpapi_protect
        return dpapi_protect(key, entropy=b"scr-backup-key")
    raise BackupError("DPAPI key wrapping is Windows-only; supply an explicit key")


def _unwrap_key(wrapped: bytes) -> bytes:
    if os.name == "nt":
        from .vault import dpapi_unprotect
        return dpapi_unprotect(wrapped, entropy=b"scr-backup-key")
    raise BackupError("DPAPI key unwrapping is Windows-only")


def _collect(home: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in ("config.json", "scr.db"):
            full = os.path.join(home, rel)
            if os.path.exists(full):
                z.write(full, rel)
        vault_dir = os.path.join(home, "vault")
        if os.path.isdir(vault_dir):
            for name in sorted(os.listdir(vault_dir)):
                z.write(os.path.join(vault_dir, name), f"vault/{name}")
    return buf.getvalue()


def create_backup(home: str, key: Optional[bytes], out_path: str) -> None:
    """Create an encrypted backup. key=None → generate + DPAPI-wrap a key
    (default, same-machine). key=<32 bytes> → explicit-key mode (portable)."""
    if key is None:
        key = os.urandom(32)
        wrapped = _wrap_key(key)          # never store the raw key
    else:
        if len(key) != 32:
            raise BackupError("key must be 32 bytes (AES-256)")
        wrapped = b""                     # explicit-key mode: no wrapped key
    plaintext = _collect(home)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, _MAGIC)
    header = _MAGIC + len(wrapped).to_bytes(4, "big") + wrapped + nonce
    from .atomic import atomic_write_bytes
    atomic_write_bytes(out_path, header + ct)


def restore_backup(archive_path: str, key: Optional[bytes], dest_home: str) -> None:
    """Restore a backup. If the archive carries a DPAPI-wrapped key it is
    unwrapped automatically (key ignored); otherwise the explicit key is
    required."""
    with open(archive_path, "rb") as f:
        blob = f.read()
    if blob[:8] != _MAGIC:
        raise BackupError("not an SCR backup archive")
    wrapped_len = int.from_bytes(blob[8:12], "big")
    off = 12
    wrapped = blob[off:off + wrapped_len]; off += wrapped_len
    nonce = blob[off:off + 12]; off += 12
    ct = blob[off:]

    if wrapped_len > 0:
        try:
            key = _unwrap_key(wrapped)    # DPAPI: only this user/machine
        except Exception as e:  # noqa: BLE001
            raise BackupError(f"could not unwrap backup key: {e}") from e
    else:
        if key is None or len(key) != 32:
            raise BackupError("this backup needs an explicit 32-byte key")
    try:
        plaintext = AESGCM(key).decrypt(nonce, ct, _MAGIC)
    except InvalidTag as e:
        raise BackupError("decryption failed (wrong key or tampered archive)") from e

    # atomic: extract to a temp dir, then swap files into dest
    os.makedirs(dest_home, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest_home) as staging:
        with zipfile.ZipFile(io.BytesIO(plaintext)) as z:
            z.extractall(staging)
        for root, _dirs, names in os.walk(staging):
            for name in names:
                src = os.path.join(root, name)
                rel = os.path.relpath(src, staging)
                target = os.path.join(dest_home, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(src, target)
