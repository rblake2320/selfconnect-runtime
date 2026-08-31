"""Publisher-side signer (`python -m scr.signer`). NEVER shipped in the
customer installer — it holds the code path that produces signatures. The
loader/service never import this module.

Usage:
  python -m scr.signer keygen                       -> prints priv/pub hex
  python -m scr.signer sign <src_dir> <out.scpkg> --name N --version V \\
        --key <private_hex>
"""
from __future__ import annotations

import argparse
import sys

from .merkle import merkle_root
from .package import build_manifest, write_package
from .signing import generate_keypair, key_id, sign
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _public_of(private_hex: str) -> str:
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    return priv.public_key().public_bytes_raw().hex()


def sign_package(src_dir: str, out_path: str, name: str, version: str,
                 private_key_hex: str) -> dict:
    manifest = build_manifest(src_dir, name, version)
    root = merkle_root(manifest["files"])
    public_hex = _public_of(private_key_hex)
    signature = {
        "algorithm": "ed25519",
        "package": name,
        "version": version,
        "merkle_root": root,
        "public_key": public_hex,
        "key_id": key_id(public_hex),
        "signature": sign(private_key_hex, root.encode("ascii")),
    }
    write_package(src_dir, out_path, manifest, signature)
    return signature


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="scr-sign")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen")
    s = sub.add_parser("sign")
    s.add_argument("src_dir")
    s.add_argument("out_path")
    s.add_argument("--name", required=True)
    s.add_argument("--version", required=True)
    s.add_argument("--key", required=True, help="private key hex")
    args = p.parse_args(argv)

    if args.cmd == "keygen":
        priv, pub = generate_keypair()
        print(f"private {priv}")
        print(f"public  {pub}")
        print(f"key_id  {key_id(pub)}")
        return 0
    if args.cmd == "sign":
        sig = sign_package(args.src_dir, args.out_path, args.name,
                           args.version, args.key)
        print(f"signed {args.out_path} root={sig['merkle_root']} key_id={sig['key_id']}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
