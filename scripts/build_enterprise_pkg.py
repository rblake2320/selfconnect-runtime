"""Build + sign the selfconnect-enterprise package, then verify it loads.

Usage: python scripts/build_enterprise_pkg.py [out_dir]

Generates a fresh publisher keypair (for a real release the key comes from the
publisher's offline signer, not here), signs the package, verifies it through
the loader, and prints the trusted public key so a customer can pin it.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scr.loader import verify_package
from scr.signer import sign_package
from scr.signing import Keystore, generate_keypair

SRC = os.path.join(_ROOT, "packages", "selfconnect-enterprise")
VERSION = "1.0.0"


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"selfconnect-enterprise-{VERSION}.scpkg")

    priv, pub = generate_keypair()
    sig = sign_package(SRC, out, "selfconnect-enterprise", VERSION, priv)

    ks = Keystore()
    ks.add(pub)
    res = verify_package(out, ks)
    # Write the pin next to the package so install --trust always matches THIS
    # build (a stale publisher_key.txt silently rejects a fresh package).
    pin_path = os.path.join(out_dir, "publisher_key.txt")
    with open(pin_path, "w") as f:
        f.write(pub + "\n")
    print(f"built:  {out}")
    print(f"signed: root={sig['merkle_root']} key_id={sig['key_id']}")
    print(f"verify: {'OK' if res.ok else 'FAIL ' + str(res.error)}")
    print(f"pin this publisher key: {pub} (written to {pin_path})")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
