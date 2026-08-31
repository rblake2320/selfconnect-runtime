"""Encrypted backup/restore: round-trip, wrong key, tamper, atomicity."""
import os

import pytest

from scr.backup import BackupError, create_backup, restore_backup

KEY = bytes.fromhex("11" * 32)


def _home(tmp_path):
    home = tmp_path / "home"
    (home / "vault").mkdir(parents=True)
    (home / "config.json").write_text('{"bind_port": 8787}')
    (home / "scr.db").write_bytes(b"SQLite format 3\x00fake-db-bytes")
    (home / "vault" / "model_openai.blob").write_bytes(b"\x01\x02encrypted")
    return str(home)


def test_backup_restore_roundtrip(tmp_path):
    home = _home(tmp_path)
    archive = str(tmp_path / "b.scbak")
    create_backup(home, KEY, archive)
    dest = str(tmp_path / "restored")
    restore_backup(archive, KEY, dest)
    assert open(os.path.join(dest, "config.json")).read() == '{"bind_port": 8787}'
    assert open(os.path.join(dest, "scr.db"), "rb").read().startswith(b"SQLite format 3")
    assert os.path.exists(os.path.join(dest, "vault", "model_openai.blob"))


def test_wrong_key_fails(tmp_path):
    home = _home(tmp_path)
    archive = str(tmp_path / "b.scbak")
    create_backup(home, KEY, archive)
    with pytest.raises(BackupError):
        restore_backup(archive, bytes.fromhex("22" * 32), str(tmp_path / "r"))


def test_tampered_archive_fails(tmp_path):
    home = _home(tmp_path)
    archive = str(tmp_path / "b.scbak")
    create_backup(home, KEY, archive)
    data = bytearray(open(archive, "rb").read())
    data[-1] ^= 0xFF                      # flip a ciphertext byte
    open(archive, "wb").write(bytes(data))
    with pytest.raises(BackupError):
        restore_backup(archive, KEY, str(tmp_path / "r"))


def test_restore_atomic_on_failure(tmp_path):
    home = _home(tmp_path)
    archive = str(tmp_path / "b.scbak")
    create_backup(home, KEY, archive)
    dest = str(tmp_path / "dest")
    os.makedirs(dest)
    # tamper → restore must fail and leave no partial staged content behind
    data = bytearray(open(archive, "rb").read())
    data[25] ^= 0x01
    open(archive, "wb").write(bytes(data))
    with pytest.raises(BackupError):
        restore_backup(archive, KEY, dest)
    assert not os.path.exists(os.path.join(dest, "config.json"))
    # no leftover temp staging dirs
    assert os.listdir(dest) == []


def test_key_length_validated(tmp_path):
    with pytest.raises(BackupError):
        create_backup(_home(tmp_path), b"short", str(tmp_path / "x"))
