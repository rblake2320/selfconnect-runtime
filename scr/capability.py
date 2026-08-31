"""Capability kernel: deny-by-default policy enforcement at the tool boundary.

The model may REQUEST anything; only what the effective manifest PERMITS
executes. Manifests are data. Delegation is monotonic: a child's effective
capabilities are the intersection of its own manifest with its parent's
effective set — capabilities can only shrink down a delegation chain.

Path rules:
  * every path is resolved (symlinks followed) BEFORE the containment check,
    so a symlink inside an allowed root that points outside it is denied;
  * traversal sequences ('..') cannot escape because containment is checked
    on the fully resolved path;
  * NTFS alternate data streams ('name:stream') are rejected on Windows
    semantics (exposed for testing via the `windows` flag).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional


class CapabilityDenied(Exception):
    pass


# Classification ceilings (§3.3). Ordered least→most sensitive. A manifest's
# ceiling bounds the classification of any tool it may invoke; deny-by-default:
# an unknown level is rejected rather than assumed low.
_CLASS_ORDER = {"public": 0, "internal": 1, "confidential": 2, "secret": 3}


def classification_rank(level: str) -> int:
    if level not in _CLASS_ORDER:
        raise CapabilityDenied(f"unknown classification level: {level!r}")
    return _CLASS_ORDER[level]


def _reject_ads(path: str, windows: bool) -> None:
    """Reject NTFS alternate-data-stream syntax under Windows semantics.
    A drive-letter colon ('C:') is legal; any other ':' in a component is not."""
    if not windows:
        return
    norm = path.replace("/", "\\")
    parts = norm.split("\\")
    for i, part in enumerate(parts):
        if ":" in part:
            if i == 0 and re.fullmatch(r"[A-Za-z]:", part):
                continue
            raise CapabilityDenied(f"alternate data stream syntax rejected: {part!r}")


def resolve_within(path: str, roots: list[str], windows: Optional[bool] = None) -> str:
    """Resolve `path` and require the RESOLVED path to sit under one of the
    resolved `roots`. Returns the resolved path or raises CapabilityDenied."""
    if windows is None:
        windows = os.name == "nt"
    _reject_ads(path, windows)
    resolved = os.path.realpath(os.path.abspath(path))
    for root in roots:
        r = os.path.realpath(os.path.abspath(root))
        try:
            if os.path.commonpath([resolved, r]) == r:
                return resolved
        except ValueError:
            # different drives on Windows
            continue
    raise CapabilityDenied(f"path outside allowed roots: {path!r}")


@dataclass(frozen=True)
class ExecRule:
    binary: str                     # exact binary name, e.g. "git"
    arg_pattern: str = r".*"        # regex the FULL arg string must match

    def allows(self, binary: str, args: list[str]) -> bool:
        if binary != self.binary:
            return False
        joined = " ".join(args)
        return re.fullmatch(self.arg_pattern, joined) is not None


@dataclass(frozen=True)
class CapabilityManifest:
    tools: frozenset[str] = frozenset()
    fs_read_roots: tuple[str, ...] = ()
    fs_write_roots: tuple[str, ...] = ()
    net_hosts: frozenset[str] = frozenset()
    exec_rules: tuple[ExecRule, ...] = ()
    max_budget_usd: float = 0.0
    # Highest tool classification this manifest may invoke. Default "secret"
    # (no restriction) for backward compatibility; an admin/policy lowers it.
    classification_ceiling: str = "secret"

    # -- checks (all deny-by-default) ----------------------------------
    def check_tool(self, name: str) -> None:
        if name not in self.tools:
            raise CapabilityDenied(f"tool not permitted: {name!r}")

    def check_read(self, path: str, windows: Optional[bool] = None) -> str:
        if not self.fs_read_roots:
            raise CapabilityDenied("no filesystem read capability")
        return resolve_within(path, list(self.fs_read_roots), windows)

    def check_write(self, path: str, windows: Optional[bool] = None) -> str:
        if not self.fs_write_roots:
            raise CapabilityDenied("no filesystem write capability")
        return resolve_within(path, list(self.fs_write_roots), windows)

    def check_net(self, host: str) -> None:
        if host not in self.net_hosts:
            raise CapabilityDenied(f"network host not permitted: {host!r}")

    def check_exec(self, binary: str, args: list[str]) -> None:
        for rule in self.exec_rules:
            if rule.allows(binary, args):
                return
        raise CapabilityDenied(f"exec not permitted: {binary} {' '.join(args)}")

    def check_classification(self, level: str) -> None:
        if classification_rank(level) > classification_rank(self.classification_ceiling):
            raise CapabilityDenied(
                f"classification {level!r} exceeds ceiling "
                f"{self.classification_ceiling!r}")


def _roots_contained(child_roots: tuple[str, ...], parent_roots: tuple[str, ...]) -> tuple[str, ...]:
    """Keep only child roots that sit under some parent root."""
    kept = []
    for c in child_roots:
        rc = os.path.realpath(os.path.abspath(c))
        for p in parent_roots:
            rp = os.path.realpath(os.path.abspath(p))
            try:
                if os.path.commonpath([rc, rp]) == rp:
                    kept.append(c)
                    break
            except ValueError:
                continue
    return tuple(kept)


def attenuate(parent: CapabilityManifest, child: CapabilityManifest) -> CapabilityManifest:
    """Effective = child ∩ parent. Monotonic: never grants beyond parent."""
    exec_rules = tuple(
        r for r in child.exec_rules
        if any(r.binary == pr.binary and r.arg_pattern == pr.arg_pattern
               for pr in parent.exec_rules)
    )
    # Ceiling attenuates to the MORE restrictive (lower-ranked) of the two.
    lower_ceiling = min((parent.classification_ceiling, child.classification_ceiling),
                        key=classification_rank)
    return CapabilityManifest(
        tools=child.tools & parent.tools,
        fs_read_roots=_roots_contained(child.fs_read_roots, parent.fs_read_roots),
        fs_write_roots=_roots_contained(child.fs_write_roots, parent.fs_write_roots),
        net_hosts=child.net_hosts & parent.net_hosts,
        exec_rules=exec_rules,
        max_budget_usd=min(child.max_budget_usd, parent.max_budget_usd),
        classification_ceiling=lower_ceiling,
    )
