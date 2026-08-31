"""Multi-agent orchestration (design §3.1).

Team topology comes from a package's agents/. Every delegation edge attenuates
capability: a child's effective manifest is capability.attenuate(parent, child)
— enforced here by the runtime, not by convention, so a subagent can never
exceed the delegator. Delegation depth is bounded. Inter-agent messages are
persisted in the mailbox and delivered in order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .capability import CapabilityManifest, attenuate
from .state import Store


class DelegationError(Exception):
    pass


@dataclass(frozen=True)
class AgentNode:
    name: str
    manifest: CapabilityManifest


@dataclass
class Team:
    """A tree of agents; each node carries a manifest. Effective manifests are
    computed by attenuation down the delegation path. Revoking any node severs
    the delegation chain for it and all descendants (§3.3) — a child cannot
    outlive the authority it was delegated from."""
    root: AgentNode
    edges: dict[str, list[str]] = field(default_factory=dict)   # parent -> children
    nodes: dict[str, AgentNode] = field(default_factory=dict)
    max_depth: int = 4
    revoked: set = field(default_factory=set)

    def revoke(self, name: str) -> None:
        """Revoke an agent. Any descendant whose delegation path passes through
        it is invalidated (chain severed)."""
        self.revoked.add(name)

    def is_severed(self, name: str) -> bool:
        return any(n in self.revoked for n in self.path_to(name))

    def add(self, parent: str, child: AgentNode) -> None:
        if parent not in self.nodes and parent != self.root.name:
            raise DelegationError(f"unknown parent {parent!r}")
        self.nodes[child.name] = child
        self.edges.setdefault(parent, []).append(child.name)

    def _node(self, name: str) -> AgentNode:
        if name == self.root.name:
            return self.root
        return self.nodes[name]

    def path_to(self, name: str) -> list[str]:
        """Delegation path root..name (raises if unreachable / too deep)."""
        parent_of = {}
        for parent, children in self.edges.items():
            for c in children:
                parent_of[c] = parent
        chain = [name]
        while chain[-1] != self.root.name:
            if chain[-1] not in parent_of:
                raise DelegationError(f"{name!r} not reachable from root")
            chain.append(parent_of[chain[-1]])
            if len(chain) > self.max_depth + 1:
                raise DelegationError(f"delegation depth exceeds {self.max_depth}")
        chain.reverse()
        return chain

    def effective_manifest(self, name: str) -> CapabilityManifest:
        """Attenuate the manifest down the whole delegation path. A revoked
        node anywhere on the path severs the chain — deny-all."""
        path = self.path_to(name)
        if any(n in self.revoked for n in path):
            raise DelegationError(
                f"delegation chain to {name!r} severed by a revoked ancestor")
        eff = self.root.manifest
        for node_name in path[1:]:
            eff = attenuate(eff, self._node(node_name).manifest)
        return eff


class Mailbox:
    """Persisted inter-agent messaging within a session."""

    def __init__(self, store: Store, session_id: str):
        self.store = store
        self.session_id = session_id

    def send(self, from_agent: str, to_agent: str, body: str) -> None:
        self.store.mailbox_send(self.session_id, from_agent, to_agent, body)

    def inbox(self, agent: str) -> list[dict]:
        return self.store.mailbox_inbox(self.session_id, agent)
