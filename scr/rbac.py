"""Role-based access control (design §3.1). Deny-by-default: an action is
permitted only if the subject's role is in its allowed set.

Roles:
  admin    — everything (token/user management, run, cancel, read)
  operator — run, cancel, approve/deny, read
  auditor  — read ledger/evidence + status only
  viewer   — read status only
"""
from __future__ import annotations

from typing import Optional

ROLES = ("admin", "operator", "auditor", "viewer")

# action -> roles permitted
_MATRIX: dict[str, frozenset[str]] = {
    "run": frozenset({"admin", "operator"}),
    "cancel": frozenset({"admin", "operator"}),
    "approve": frozenset({"admin", "operator"}),
    "read_status": frozenset({"admin", "operator", "auditor", "viewer"}),
    "read_ledger": frozenset({"admin", "operator", "auditor"}),
    "export_evidence": frozenset({"admin", "operator", "auditor"}),
    "manage_tokens": frozenset({"admin"}),
    "install_package": frozenset({"admin"}),
}


class AccessDenied(Exception):
    pass


def permitted(role: str, action: str) -> bool:
    return role in _MATRIX.get(action, frozenset())


def require(role: Optional[str], action: str) -> None:
    if role is None:
        raise AccessDenied("no authenticated subject")
    if role not in ROLES:
        raise AccessDenied(f"unknown role: {role!r}")
    if not permitted(role, action):
        raise AccessDenied(f"role {role!r} may not {action!r}")
