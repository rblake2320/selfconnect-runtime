"""Installed-package registry — closes G3 ("verified at load AND at each
execution").

Install verifies a `.scpkg` and copies it into the SCR home. Every run then
re-verifies the *stored* package before executing anything, so a package that
is tampered on disk after install, or whose version is later revoked, is
refused at session start — not trusted on the strength of a one-time install
check.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from .atomic import atomic_write_text
from .loader import LoadResult, verify_package
from .signing import Keystore, RevocationList


class RegistryError(Exception):
    pass


def check_requirements(manifest: dict, runtime_version: str,
                       installed_versions: dict[str, str],
                       model_caps: Optional[dict] = None) -> list[str]:
    """Validate a package manifest's compatibility declarations (§3.4). Returns
    a list of unmet-requirement messages (empty = compatible). Deny-by-default:
    an unparseable constraint is treated as unmet.

    Recognized manifest keys (all optional):
      runtime: {min: "<semver>"}                 — min SCR runtime version
      requires: {pkg: "<constraint>", ...}       — dependency packages
      model_requirements: {min_context: int, tool_calls: bool}
    """
    from .semver import SemverError, satisfies
    problems: list[str] = []

    rt = (manifest.get("runtime") or {}).get("min")
    if rt:
        try:
            if not satisfies(runtime_version, f">={rt}"):
                problems.append(
                    f"requires runtime >= {rt}, have {runtime_version}")
        except SemverError as e:
            problems.append(f"bad runtime constraint: {e}")

    for dep, constraint in (manifest.get("requires") or {}).items():
        have = installed_versions.get(dep)
        if have is None:
            problems.append(f"missing dependency {dep!r} ({constraint})")
            continue
        try:
            if not satisfies(have, constraint):
                problems.append(
                    f"dependency {dep} {have} does not satisfy {constraint}")
        except SemverError as e:
            problems.append(f"bad dependency constraint for {dep}: {e}")

    reqs = manifest.get("model_requirements") or {}
    caps = model_caps or {}
    if "min_context" in reqs:
        if int(caps.get("context", 0)) < int(reqs["min_context"]):
            problems.append(
                f"model context {caps.get('context', 0)} < required {reqs['min_context']}")
    if reqs.get("tool_calls") and not caps.get("tool_calls", False):
        problems.append("model does not support tool calls")
    return problems


@dataclass
class InstalledPackage:
    name: str
    version: str
    path: str
    key_id: str


@dataclass
class UpdateOutcome:
    promoted: bool
    version: Optional[str]
    reason: str
    selftests: Optional[dict]


class PackageRegistry:
    def __init__(self, home: str, keystore: Keystore,
                 revocations: Optional[RevocationList] = None):
        self.home = home
        self.dir = os.path.join(home, "packages")
        os.makedirs(self.dir, exist_ok=True)
        self.index_path = os.path.join(self.dir, "installed.json")
        self.keystore = keystore
        self.revocations = revocations

    # -------------------------------------------------------------- index
    def _load_index(self) -> dict:
        if not os.path.exists(self.index_path):
            return {}
        with open(self.index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_index(self, idx: dict) -> None:
        atomic_write_text(self.index_path, json.dumps(idx, sort_keys=True, indent=2))

    # ------------------------------------------------------------- install
    def install(self, scpkg_path: str) -> LoadResult:
        """Verify then store. Refuses to install anything that does not verify
        (unsigned / untrusted / tampered / revoked)."""
        res = verify_package(scpkg_path, self.keystore, self.revocations)
        if not res.ok:
            return res
        dest = os.path.join(self.dir, f"{res.package}-{res.version}.scpkg")
        shutil.copyfile(scpkg_path, dest)
        # record the merkle root so a later swap of the stored file is caught
        from .package import Package
        with Package(dest) as pkg:
            key_id = (pkg.signature or {}).get("key_id", "")
        idx = self._load_index()
        idx[res.package] = {"version": res.version, "path": dest, "key_id": key_id}
        self._save_index(idx)
        return res

    def list_installed(self) -> list[InstalledPackage]:
        return [InstalledPackage(name, m["version"], m["path"], m.get("key_id", ""))
                for name, m in sorted(self._load_index().items())]

    def get(self, name: str) -> Optional[InstalledPackage]:
        m = self._load_index().get(name)
        if m is None:
            return None
        return InstalledPackage(name, m["version"], m["path"], m.get("key_id", ""))

    # -------------------------------------------- verify-at-execution (G3)
    def verify_installed(self, name: str) -> LoadResult:
        """Re-verify the STORED package. Called at every session start."""
        inst = self.get(name)
        if inst is None:
            return LoadResult(False, "not_installed", f"no installed package {name!r}")
        if not os.path.exists(inst.path):
            return LoadResult(False, "missing_on_disk",
                              f"installed package file gone: {inst.path}")
        return verify_package(inst.path, self.keystore, self.revocations)

    # ---------------------------------------- shadow-install updates (§6)
    def shadow_update(self, scpkg_path: str, adapter) -> "UpdateOutcome":
        """Update a package the safe way: verify → shadow-install → run the
        package self-tests against the model → promote only on pass. On any
        failure the currently-installed version stays active (rollback) and the
        shadow is discarded."""
        res = verify_package(scpkg_path, self.keystore, self.revocations)
        if not res.ok:
            return UpdateOutcome(False, None, f"verify failed: {res.error}", None)

        shadow = os.path.join(self.dir, f"{res.package}-{res.version}.scpkg.shadow")
        shutil.copyfile(scpkg_path, shadow)
        from .loader import run_selftests
        try:
            st = run_selftests(shadow, adapter, self.keystore, self.revocations)
        except Exception as e:  # noqa: BLE001
            os.unlink(shadow)
            return UpdateOutcome(False, None, f"self-test error: {e}", None)
        if not st.get("ok"):
            os.unlink(shadow)          # discard shadow; active version unchanged
            return UpdateOutcome(False, res.version, "self-tests failed", st)

        # promote: atomically replace, update the index to the new version
        dest = os.path.join(self.dir, f"{res.package}-{res.version}.scpkg")
        os.replace(shadow, dest)
        from .package import Package
        with Package(dest) as pkg:
            key_id_v = (pkg.signature or {}).get("key_id", "")
        idx = self._load_index()
        idx[res.package] = {"version": res.version, "path": dest, "key_id": key_id_v}
        self._save_index(idx)
        return UpdateOutcome(True, res.version, "promoted", st)

    def reload(self, name: str) -> tuple[bool, Optional[dict]]:
        """Hot-reload (§3.4): re-verify the installed package and return its
        fresh manifest WITHOUT a service restart. A package re-signed/updated
        on disk is picked up; a tampered one is refused (no stale content
        served)."""
        res = self.verify_installed(name)
        if not res.ok:
            return False, None
        inst = self.get(name)
        from .package import Package
        with Package(inst.path) as pkg:
            return True, dict(pkg.manifest)

    def session_guard(self, name: str):
        """Return a zero-arg callable that re-verifies the package; a
        SessionManager calls it before running a job bound to this package."""
        def _guard() -> LoadResult:
            return self.verify_installed(name)
        return _guard
