"""Vault: secrets encrypted at rest, never plaintext on disk."""
import os

import pytest

from scr.vault import Vault


@pytest.mark.skipif(os.name != "nt",
                    reason="DPAPI backend is Windows; POSIX keyring path skip-marked")
def test_dpapi_roundtrip_and_ciphertext_at_rest(tmp_path):
    v = Vault(str(tmp_path))
    secret = "sk-super-secret-value-1234567890"
    v.store_secret("model:openai", secret)
    assert v.get_secret("model:openai") == secret

    # The on-disk blob must NOT contain the plaintext.
    blob_dir = tmp_path / "vault"
    blobs = list(blob_dir.glob("*.blob"))
    assert blobs, "no vault blob written"
    raw = blobs[0].read_bytes()
    assert secret.encode() not in raw
    assert len(raw) > 0


@pytest.mark.skipif(os.name != "nt", reason="DPAPI backend is Windows")
def test_missing_secret_returns_none(tmp_path):
    v = Vault(str(tmp_path))
    assert v.get_secret("nope") is None


@pytest.mark.skipif(os.name != "nt", reason="DPAPI backend is Windows")
def test_delete_secret(tmp_path):
    v = Vault(str(tmp_path))
    v.store_secret("k", "value")
    v.delete_secret("k")
    assert v.get_secret("k") is None
