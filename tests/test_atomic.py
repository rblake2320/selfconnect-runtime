import os

import pytest

from scr.atomic import atomic_write_bytes, atomic_write_text


def test_creates_new_file(tmp_path):
    p = tmp_path / "a.bin"
    atomic_write_bytes(str(p), b"hello")
    assert p.read_bytes() == b"hello"


def test_replaces_existing_file(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"old")
    atomic_write_bytes(str(p), b"new-content")
    assert p.read_bytes() == b"new-content"


def test_crash_before_replace_preserves_original(tmp_path, monkeypatch):
    """Fault injection: crash between temp-write and rename. Original intact,
    no torn write visible at the destination path."""
    p = tmp_path / "a.bin"
    p.write_bytes(b"original")

    def boom(src, dst):
        raise OSError("simulated power loss before rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(str(p), b"never-lands")
    assert p.read_bytes() == b"original"


def test_crash_cleans_up_temp_file(tmp_path, monkeypatch):
    p = tmp_path / "a.bin"
    monkeypatch.setattr(os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        atomic_write_bytes(str(p), b"x")
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith(".scr-tmp-")]
    assert leftovers == []


def test_crlf_bytes_survive_verbatim(tmp_path):
    """CRLF-safety: no newline translation ever."""
    p = tmp_path / "a.txt"
    atomic_write_text(str(p), "line1\r\nline2\nline3\r\n")
    assert p.read_bytes() == b"line1\r\nline2\nline3\r\n"


def test_binary_content_with_null_bytes(tmp_path):
    p = tmp_path / "a.bin"
    payload = bytes(range(256)) * 4
    atomic_write_bytes(str(p), payload)
    assert p.read_bytes() == payload
