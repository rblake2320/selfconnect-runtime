"""Minimal semantic-version parsing and constraint checks for package
manifests (§3.4: semver, min-runtime, dependency constraints).

Supports `major.minor.patch` and the operators >= > <= < == (and a bare
version, treated as ==). Pre-release/build metadata is ignored for ordering.
"""
from __future__ import annotations

import re

_OPS = (">=", "<=", "==", ">", "<")
_VER_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


class SemverError(Exception):
    pass


def parse(version: str) -> tuple[int, int, int]:
    m = _VER_RE.match(version or "")
    if not m:
        raise SemverError(f"not a semantic version: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def satisfies(version: str, constraint: str) -> bool:
    """True if `version` satisfies `constraint` (e.g. '>=1.2.0')."""
    constraint = constraint.strip()
    op = "=="
    rest = constraint
    for candidate in _OPS:
        if constraint.startswith(candidate):
            op, rest = candidate, constraint[len(candidate):]
            break
    v, target = parse(version), parse(rest)
    if op == ">=":
        return v >= target
    if op == "<=":
        return v <= target
    if op == ">":
        return v > target
    if op == "<":
        return v < target
    return v == target
