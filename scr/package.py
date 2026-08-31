"""`.scpkg` package format (design §3.4).

Layout inside the zip:
  manifest.json         name, version, files{path: sha256hex}
  agents/ skills/ tools/ mcp/ policies/ tests/   payload
  SIGNATURE             detached Ed25519 signature over the Merkle root

The manifest hashes every payload file; SIGNATURE and manifest.json are
themselves excluded from `files` (the manifest cannot hash itself; the
signature covers the Merkle root derived from the manifest's file map).
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from typing import Optional

MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "SIGNATURE"
_PAYLOAD_DIRS = ("agents", "skills", "tools", "mcp", "policies", "tests")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_files_map(src_dir: str) -> dict[str, str]:
    """Hash every payload file under src_dir (posix-relative paths)."""
    files: dict[str, str] = {}
    for root, _dirs, names in os.walk(src_dir):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, src_dir).replace(os.sep, "/")
            if rel in (MANIFEST_NAME, SIGNATURE_NAME):
                continue
            with open(full, "rb") as f:
                files[rel] = _sha256_bytes(f.read())
    return files


def build_manifest(src_dir: str, name: str, version: str) -> dict:
    return {"name": name, "version": version, "files": build_files_map(src_dir)}


def write_package(src_dir: str, out_path: str, manifest: dict,
                  signature: dict) -> None:
    """Write a .scpkg zip: manifest + payload files + SIGNATURE."""
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST_NAME,
                   json.dumps(manifest, sort_keys=True, separators=(",", ":")))
        for rel in sorted(manifest["files"]):
            full = os.path.join(src_dir, rel.replace("/", os.sep))
            with open(full, "rb") as f:
                z.writestr(rel, f.read())
        z.writestr(SIGNATURE_NAME,
                   json.dumps(signature, sort_keys=True, separators=(",", ":")))


class Package:
    """Read-only view over a .scpkg. Reads members in-memory by exact name;
    never extracts arbitrary member paths to disk (path-traversal safe)."""

    def __init__(self, path: str):
        self.path = path
        self._zip = zipfile.ZipFile(path, "r")
        self.manifest = json.loads(self._read(MANIFEST_NAME))
        self.signature: Optional[dict] = None
        if SIGNATURE_NAME in self._zip.namelist():
            self.signature = json.loads(self._read(SIGNATURE_NAME))

    def _read(self, name: str) -> bytes:
        return self._zip.read(name)

    def read_member(self, name: str) -> bytes:
        return self._read(name)

    def member_names(self) -> list[str]:
        return [n for n in self._zip.namelist()
                if n not in (MANIFEST_NAME, SIGNATURE_NAME)]

    def actual_file_hashes(self) -> dict[str, str]:
        return {n: _sha256_bytes(self._read(n)) for n in self.member_names()}

    def close(self) -> None:
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
