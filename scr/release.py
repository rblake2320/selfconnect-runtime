"""Supply-chain artifacts (§8, DoD "signed release artifacts + SBOM"):
a CycloneDX SBOM generated from the actually-pinned dependencies, and Ed25519
detached signatures over release artifacts (Authenticode for the MSI is layered
on later with the code-signing cert).
"""
from __future__ import annotations

import hashlib
import os
import tomllib
from typing import Optional

from .signing import Keystore, key_id, sign, verify

CYCLONEDX_SPEC = "1.5"


def parse_pinned_deps(pyproject_path: str) -> dict[str, str]:
    """Return {name: version} for every exactly-pinned dependency (name==ver)
    in [project].dependencies and the dev extra."""
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    deps: dict[str, str] = {}
    project = data.get("project", {})
    reqs = list(project.get("dependencies", []))
    for extra in (project.get("optional-dependencies", {}) or {}).values():
        reqs += list(extra)
    for req in reqs:
        if "==" in req:
            name, ver = req.split("==", 1)
            deps[name.strip()] = ver.strip()
    return deps


def generate_sbom(name: str, version: str, dependencies: dict[str, str]) -> dict:
    """A CycloneDX 1.5 SBOM document for the runtime + its pinned deps."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC,
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": name,
                          "version": version, "purl": f"pkg:pypi/{name}@{version}"},
        },
        "components": [
            {"type": "library", "name": n, "version": v,
             "purl": f"pkg:pypi/{n}@{v}",
             "bom-ref": f"pkg:pypi/{n}@{v}"}
            for n, v in sorted(dependencies.items())
        ],
    }


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sign_artifact(path: str, private_key_hex: str, public_key_hex: str) -> dict:
    """Detached Ed25519 signature over the artifact's SHA-256 digest."""
    digest = _sha256_file(path)
    return {
        "artifact": os.path.basename(path),
        "sha256": digest,
        "algorithm": "ed25519",
        "public_key": public_key_hex,
        "key_id": key_id(public_key_hex),
        "signature": sign(private_key_hex, bytes.fromhex(digest)),
    }


def verify_artifact(path: str, manifest: dict, keystore: Keystore) -> tuple[bool, str]:
    """Verify an artifact against its signature manifest, requiring the signing
    key to be pinned. Fail-closed."""
    digest = _sha256_file(path)
    if digest != manifest.get("sha256"):
        return False, "artifact digest does not match signature manifest"
    pub = manifest.get("public_key", "")
    if not verify(pub, manifest.get("signature", ""), bytes.fromhex(digest)):
        return False, "Ed25519 signature does not verify"
    if not keystore.trusts(pub):
        return False, "signing key is not pinned"
    return True, "verified"
