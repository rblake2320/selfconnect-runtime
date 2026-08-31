"""Staged side-by-side updater (design §6). A new version installs alongside
the current one; a health probe runs against it; only on success is `current`
switched. A failing probe rolls back — `current` never points at a bad build.
Offline update files are supported (the caller supplies the staged directory).

`current` is a small pointer file (not a symlink) so the mechanism is
identical on Windows and POSIX.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

from .atomic import atomic_write_text


@dataclass
class UpdateResult:
    ok: bool
    active_version: str
    rolled_back: bool = False
    detail: str = ""


class Updater:
    def __init__(self, root: str):
        self.root = root
        self.versions_dir = os.path.join(root, "versions")
        self.pointer = os.path.join(root, "current")
        os.makedirs(self.versions_dir, exist_ok=True)

    def active(self) -> Optional[str]:
        if not os.path.exists(self.pointer):
            return None
        with open(self.pointer, "r", encoding="utf-8") as f:
            return f.read().strip() or None

    def install_initial(self, version: str) -> None:
        os.makedirs(os.path.join(self.versions_dir, version), exist_ok=True)
        atomic_write_text(self.pointer, version)

    def stage(self, version: str) -> str:
        """Create (or reuse) the staged version directory; return its path.
        Real installs unpack the update payload here (offline file supported)."""
        path = os.path.join(self.versions_dir, version)
        os.makedirs(path, exist_ok=True)
        return path

    def apply(self, version: str,
              health_probe: Callable[[str], bool]) -> UpdateResult:
        """Switch to `version` only if its health probe passes; otherwise roll
        back to the previously-active version."""
        previous = self.active()
        staged = self.stage(version)
        try:
            healthy = health_probe(staged)
        except Exception as e:  # noqa: BLE001 — a throwing probe = unhealthy
            healthy = False
            probe_err = str(e)[:200]
        else:
            probe_err = ""
        if healthy:
            atomic_write_text(self.pointer, version)
            return UpdateResult(True, version, detail="switched")
        # rollback: pointer stays at previous (or unset if none)
        if previous is not None:
            atomic_write_text(self.pointer, previous)
        return UpdateResult(False, previous or "",
                            rolled_back=True,
                            detail=f"health probe failed; rolled back. {probe_err}")
