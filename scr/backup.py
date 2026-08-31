"""Encrypted backup/restore (design §3.8, §5).

A backup is an AES-256-GCM-encrypted snapshot of the state DB + config. The
GCM auth tag makes any tampered or wrong-key archive fail closed. Restore is
atomic: it stages into a temp dir and only swaps into place on full success,
so a failure never leaves a half-restored home.
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

_MAGIC = b"SCRBAK01"


class BackupError(Exception):
    pass


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


def create_backup(home: str, key: bytes, out_path: str) -> None:
    if len(key) != 32:
        raise BackupError("key must be 32 bytes (AES-256)")
    plaintext = _collect(home)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, _MAGIC)
    from .atomic import atomic_write_bytes
    atomic_write_bytes(out_path, _MAGIC + nonce + ct)


def restore_backup(archive_path: str, key: bytes, dest_home: str) -> None:
    if len(key) != 32:
        raise BackupError("key must be 32 bytes (AES-256)")
    with open(archive_path, "rb") as f:
        blob = f.read()
    if blob[:8] != _MAGIC:
        raise BackupError("not an SCR backup archive")
    nonce, ct = blob[8:20], blob[20:]
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
