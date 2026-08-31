"""Offline licensing: valid, expired→grace (read-only), tampered/wrong-key."""
from scr.license import License, check
from scr.signing import generate_keypair

NOW = 1_700_000_000.0
FUTURE = NOW + 86_400
PAST = NOW - 86_400


def _issue(not_after):
    priv, pub = generate_keypair()
    lic = License.issue("acme", 5, ["run", "evidence"], not_after, priv, pub)
    return lic.to_text(), pub


def test_valid_license():
    text, pub = _issue(FUTURE)
    status = check(text, pub, NOW)
    assert status.state == "valid"
    assert status.may_run and status.may_read_evidence


def test_expired_license_grace_readonly():
    text, pub = _issue(PAST)
    status = check(text, pub, NOW)
    assert status.state == "grace"
    assert not status.may_run              # runs denied
    assert status.may_read_evidence        # never bricks — evidence still readable


def test_wrong_trusted_key_rejected():
    text, _ = _issue(FUTURE)
    _, other_pub = generate_keypair()
    status = check(text, other_pub, NOW)
    assert status.state == "invalid"


def test_tampered_field_rejected():
    text, pub = _issue(FUTURE)
    import json
    d = json.loads(text)
    d["seats"] = 9999                       # tamper after signing
    status = check(json.dumps(d), pub, NOW)
    assert status.state == "invalid"


def test_malformed_license_rejected():
    assert check("not json", "00" * 32, NOW).state == "invalid"
