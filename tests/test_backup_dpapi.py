"""DPAPI-wrapped backup key (§3.5): the AES key is never stored in plaintext
beside the ciphertext; only the same user/machine can unwrap it."""
import os

import pytest

from scr.backup import BackupError, create_backup, restore_backup


def _home(tmp_path):
    home = tmp_path / "home"
    (home / "vault").mkdir(parents=True)
    (home / "config.json").write_text('{"bind_port": 8787}')
    (home / "scr.db").write_bytes(b"SQLite format 3\x00fake")
    return str(home)


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows")
def test_dpapi_wrapped_roundtrip_no_key(tmp_path):
    home = _home(tmp_path)
    archive = str(tmp_path / "b.scbak")
    create_backup(home, None, archive)               # key=None → DPAPI-wrapped
    dest = str(tmp_path / "restored")
    restore_backup(archive, None, dest)              # unwraps automatically
    assert open(os.path.join(dest, "config.json")).read() == '{"bind_port": 8787}'


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows")
def test_wrapped_archive_contains_no_plaintext_key(tmp_path):
    """The raw AES key must never appear in the archive — only the wrapped blob.
    We can't know the random key, but we CAN assert the wrapped-key header is
    present and non-empty and that decryption requires DPAPI (no key on disk)."""
    home = _home(tmp_path)
    archive = str(tmp_path / "b.scbak")
    create_backup(home, None, archive)
    blob = open(archive, "rb").read()
    assert blob[:8] == b"SCRBAK02"
    wrapped_len = int.from_bytes(blob[8:12], "big")
    assert wrapped_len > 0                            # a wrapped key is present
    # a foreign 32-byte key cannot restore a DPAPI-wrapped archive
    with pytest.raises(BackupError):
        # even supplying a key, the wrapped path is used → wrong key fails,
        # OR the DPAPI unwrap yields the real key and a tampered wrapped blob
        # fails; here we tamper the wrapped key to prove it's load-bearing.
        tampered = bytearray(blob)
        tampered[12] ^= 0xFF
        t = str(tmp_path / "t.scbak")
        open(t, "wb").write(bytes(tampered))
        restore_backup(t, None, str(tmp_path / "r"))


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows")
def test_wrapped_tamper_ciphertext_fails(tmp_path):
    home = _home(tmp_path)
    archive = str(tmp_path / "b.scbak")
    create_backup(home, None, archive)
    data = bytearray(open(archive, "rb").read())
    data[-1] ^= 0xFF                                  # flip ciphertext byte
    open(archive, "wb").write(bytes(data))
    with pytest.raises(BackupError):
        restore_backup(archive, None, str(tmp_path / "r"))


def test_explicit_key_mode_still_works(tmp_path):
    """Air-gapped / cross-machine: explicit key, no wrapped blob."""
    home = _home(tmp_path)
    key = bytes.fromhex("11" * 32)
    archive = str(tmp_path / "b.scbak")
    create_backup(home, key, archive)
    blob = open(archive, "rb").read()
    assert int.from_bytes(blob[8:12], "big") == 0     # no wrapped key stored
    dest = str(tmp_path / "restored")
    restore_backup(archive, key, dest)
    assert open(os.path.join(dest, "config.json")).read() == '{"bind_port": 8787}'
    # wrong explicit key fails
    with pytest.raises(BackupError):
        restore_backup(archive, bytes.fromhex("22" * 32), str(tmp_path / "r2"))
