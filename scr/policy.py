"""Policy layer: HITL approval rules and admin tightening (design §3.3).

A policy is DATA loaded from YAML. It can:
  * mark action classes as require_approval (match by tool name, optionally
    narrowed by a regex on a named argument);
  * TIGHTEN a capability manifest — intersection only. A policy may never
    widen: naming a tool or host the base manifest does not grant is a
    PolicyError, not a silent grant.

Tightening reuses the monotonic `attenuate` semantics conceptually: the
result is always a subset of the base manifest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from .capability import CapabilityManifest
from .gateway import ToolCall


class PolicyError(Exception):
    """A policy tried to widen authority (grant beyond the base manifest)."""


@dataclass(frozen=True)
class ApprovalRule:
    tool: str                                  # exact tool name or "*"
    arg_match: tuple[tuple[str, str], ...] = ()  # (arg_name, regex) pairs, all must match

    def matches(self, call: ToolCall) -> bool:
        if self.tool != "*" and self.tool != call.name:
            return False
        for name, pattern in self.arg_match:
            val = call.arguments.get(name)
            if val is None or re.search(pattern, str(val)) is None:
                return False
        return True


@dataclass(frozen=True)
class Policy:
    approval_rules: tuple[ApprovalRule, ...] = ()
    tighten_tools: Optional[frozenset[str]] = None      # None = no tools restriction
    tighten_net_hosts: Optional[frozenset[str]] = None

    # ---------------------------------------------------------------- load
    @staticmethod
    def from_yaml(text: str) -> "Policy":
        data = yaml.safe_load(text) or {}
        rules = []
        for r in data.get("require_approval", []) or []:
            if isinstance(r, str):
                rules.append(ApprovalRule(tool=r))
                continue
            arg_match = tuple((k, v) for k, v in (r.get("arg_match") or {}).items())
            rules.append(ApprovalRule(tool=r["tool"], arg_match=arg_match))
        tighten = data.get("tighten") or {}
        tt = tighten.get("tools")
        th = tighten.get("net_hosts")
        return Policy(
            approval_rules=tuple(rules),
            tighten_tools=frozenset(tt) if tt is not None else None,
            tighten_net_hosts=frozenset(th) if th is not None else None,
        )

    @staticmethod
    def from_file(path: str) -> "Policy":
        with open(path, "r", encoding="utf-8") as f:
            return Policy.from_yaml(f.read())

    # ----------------------------------------------------------- approval
    def requires_approval(self, call: ToolCall) -> bool:
        return any(rule.matches(call) for rule in self.approval_rules)

    # ---------------------------------------------------------- tightening
    def validate_tightening(self, base: CapabilityManifest) -> None:
        """Reject any attempt to grant beyond the base manifest."""
        if self.tighten_tools is not None:
            extra = self.tighten_tools - base.tools
            if extra:
                raise PolicyError(f"policy widens tools beyond manifest: {sorted(extra)}")
        if self.tighten_net_hosts is not None:
            extra = self.tighten_net_hosts - base.net_hosts
            if extra:
                raise PolicyError(f"policy widens net_hosts beyond manifest: {sorted(extra)}")

    def tighten(self, base: CapabilityManifest) -> CapabilityManifest:
        """Return base ∩ policy. Raises PolicyError on any widening attempt."""
        self.validate_tightening(base)
        tools = base.tools if self.tighten_tools is None else (base.tools & self.tighten_tools)
        hosts = (base.net_hosts if self.tighten_net_hosts is None
                 else (base.net_hosts & self.tighten_net_hosts))
        return CapabilityManifest(
            tools=tools,
            fs_read_roots=base.fs_read_roots,
            fs_write_roots=base.fs_write_roots,
            net_hosts=hosts,
            exec_rules=base.exec_rules,
            max_budget_usd=base.max_budget_usd,
        )
