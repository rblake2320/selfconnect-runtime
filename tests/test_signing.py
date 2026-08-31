"""Ed25519 signing, key pinning, and signed revocation lists."""
from scr.signing import (
    Keystore,
    RevocationList,
    generate_keypair,
    key_id,
    sign,
    verify,
)


def test_sign_verify_roundtrip():
    priv, pub = generate_keypair()
    msg = b"the merkle root"
    sig = sign(priv, msg)
    assert verify(pub, sig, msg)


def test_wrong_key_fails():
    priv, pub = generate_keypair()
    _, other_pub = generate_keypair()
    sig = sign(priv, b"root")
    assert not verify(other_pub, sig, b"root")


def test_tampered_message_fails():
    priv, pub = generate_keypair()
    sig = sign(priv, b"root")
    assert not verify(pub, sig, b"root-tampered")


def test_key_id_derivation_stable():
    _, pub = generate_keypair()
    assert key_id(pub) == key_id(pub)
    assert len(key_id(pub)) == 16


def test_keystore_deny_by_default():
    _, pub = generate_keypair()
    ks = Keystore()
    assert not ks.trusts(pub)
    ks.add(pub)
    assert ks.trusts(pub)


def test_revocation_list_must_be_signed_by_trusted_key():
    priv, pub = generate_keypair()
    ks = Keystore()
    ks.add(pub)
    rl = RevocationList.create([("pkg", "1.0.0")], priv, pub)
    assert rl.is_valid(ks)
    assert rl.is_revoked("pkg", "1.0.0")
    assert not rl.is_revoked("pkg", "1.0.1")


def test_revocation_list_from_untrusted_key_not_honored():
    priv, pub = generate_keypair()      # not added to keystore
    ks = Keystore()                     # empty → trusts nobody
    rl = RevocationList.create([("pkg", "1.0.0")], priv, pub)
    assert not rl.is_valid(ks)          # fail closed


def test_forged_revocation_signature_rejected():
    priv, pub = generate_keypair()
    ks = Keystore()
    ks.add(pub)
    rl = RevocationList.create([("pkg", "1.0.0")], priv, pub)
    rl.signature = "00" * 64            # forged
    assert not rl.is_valid(ks)
