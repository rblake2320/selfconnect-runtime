"""Package loader/verifier (design §3.4). Fail-closed: any format, hash,
signature, trust, or revocation problem is a rejection, never a pass. Runs at
install AND at session start (Phase 6 wires the session-start call).

Verification order (each step localizes its failure):
  1. every packaged member's hash matches the manifest (names the bad file);
  2. no extra / missing members vs the manifest (manifest/content mismatch);
  3. recomputed Merkle root equals the SIGNATURE's root;
  4. Ed25519 signature over the root verifies with the SIGNATURE's public key;
  5. that key is pinned in the keystore (deny-by-default trust);
  6. (name, version) is not revoked by a validly-signed revocation list.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from .merkle import merkle_root
from .package import Package
from .signing import Keystore, RevocationList, verify


@dataclass
class LoadResult:
    ok: bool
    error: Optional[str] = None
    detail: str = ""
    package: Optional[str] = None
    version: Optional[str] = None


def verify_package(path: str, keystore: Keystore,
                   revocations: Optional[RevocationList] = None) -> LoadResult:
    try:
        pkg = Package(path)
    except Exception as e:  # noqa: BLE001 — malformed zip / manifest = reject
        return LoadResult(False, "malformed_package", str(e)[:200])
    try:
        manifest = pkg.manifest
        name = manifest.get("name")
        version = manifest.get("version")
        declared = manifest.get("files", {})
        actual = pkg.actual_file_hashes()

        # 1 + 2: manifest vs content, with localization
        declared_set, actual_set = set(declared), set(actual)
        missing = declared_set - actual_set
        extra = actual_set - declared_set
        if missing:
            return LoadResult(False, "missing_files",
                              f"declared but absent: {sorted(missing)}", name, version)
        if extra:
            return LoadResult(False, "unexpected_files",
                              f"present but not in manifest: {sorted(extra)}", name, version)
        for rel in sorted(declared):
            if actual[rel] != declared[rel]:
                return LoadResult(False, "tampered_file",
                                  f"hash mismatch in {rel!r}", name, version)

        # 3: signature present + root match
        if pkg.signature is None:
            return LoadResult(False, "unsigned", "no SIGNATURE member", name, version)
        sig = pkg.signature
        recomputed_root = merkle_root(declared)
        if sig.get("merkle_root") != recomputed_root:
            return LoadResult(False, "root_mismatch",
                              "SIGNATURE root != recomputed Merkle root", name, version)

        # 4: cryptographic signature over the root
        pub = sig.get("public_key", "")
        if not verify(pub, sig.get("signature", ""),
                      recomputed_root.encode("ascii")):
            return LoadResult(False, "bad_signature",
                              "Ed25519 signature does not verify", name, version)

        # 5: key pinning
        if not keystore.trusts(pub):
            return LoadResult(False, "untrusted_key",
                              "signing key is not pinned in the keystore", name, version)

        # 6: revocation (only honored if the list is validly signed)
        if revocations is not None and revocations.is_valid(keystore):
            if revocations.is_revoked(name, version):
                return LoadResult(False, "revoked",
                                  f"{name} {version} is revoked", name, version)

        return LoadResult(True, None, "verified", name, version)
    finally:
        pkg.close()


def integrity_check(path: str) -> LoadResult:
    """Verify a package's on-disk integrity WITHOUT asserting trust: checks
    file hashes → manifest, Merkle root, and the signature against the
    package's OWN embedded key. Detects tamper of a stored package (for
    `scr doctor`); it does not prove the key is pinned."""
    from .signing import Keystore
    try:
        pkg = Package(path)
        pub = (pkg.signature or {}).get("public_key", "")
        pkg.close()
    except Exception as e:  # noqa: BLE001
        return LoadResult(False, "malformed_package", str(e)[:200])
    ks = Keystore()
    if pub:
        ks.add(pub)
    return verify_package(path, ks)


def run_selftests(path: str, adapter, keystore: Keystore,
                  revocations: Optional[RevocationList] = None) -> dict:
    """Verify the package, then run its tests/*.yaml scenarios through the
    kernel against `adapter`. Each scenario: {name, prompt, expect_contains}.
    Returns {ok, verified, results:[{name, passed, detail}]}."""
    import yaml

    from .capability import CapabilityManifest
    from .kernel import Kernel
    from .state import Store

    v = verify_package(path, keystore, revocations)
    if not v.ok:
        return {"ok": False, "verified": False, "error": v.error, "detail": v.detail,
                "results": []}

    import tempfile

    results = []
    with Package(path) as pkg:
        scenarios = [n for n in pkg.member_names()
                     if n.startswith("tests/") and n.endswith(".yaml")]
        for member in sorted(scenarios):
            spec = yaml.safe_load(pkg.read_member(member).decode("utf-8")) or {}
            want = spec.get("expect_contains", "")
            store = Store(":memory:")
            if spec.get("team"):
                # Exercise the REAL multi-agent team from the package's agents/.
                from .team import TeamRunner, load_team_from_package
                with tempfile.TemporaryDirectory() as ws:
                    out_dir = os.path.join(ws, "scr-selftest-out")
                    os.makedirs(out_dir, exist_ok=True)
                    loaded = load_team_from_package(path, ws, out_dir)
                    runner = TeamRunner(store, loaded, lambda a: adapter,
                                        lambda m: {})
                    run = runner.run(spec["team"], spec.get("prompt", ""))
            else:
                sid = store.create_session()
                run = Kernel(store, adapter, {}, CapabilityManifest(tools=frozenset())
                             ).run(sid, spec.get("prompt", ""))
            passed = want in run.final_text
            results.append({"name": spec.get("name", member),
                            "passed": passed, "team": bool(spec.get("team")),
                            "detail": "" if passed else f"missing {want!r} in output"})
            store.close()
    return {"ok": all(r["passed"] for r in results), "verified": True,
            "results": results}
