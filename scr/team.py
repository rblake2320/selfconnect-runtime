"""Multi-agent team execution (design §3.1, §3.7).

A team is a tree of agents loaded from a package's agents/. The entry agent is
the orchestrator; it delegates subtasks to its declared children via a kernel
`delegate` tool. Every delegation edge enforces capability.attenuate (a child's
effective manifest is the intersection down its whole path — it can never
exceed the parent), is journaled, and is recorded in the ledger. Subtasks and
results flow through the persisted mailbox, whose deliveries are hash-recorded
in the ledger so a tampered stored message is detected on fold.

Each agent instance runs in its OWN session (own messages/journal/ledger),
linked by team_id + parent_session, so per-subagent crash recovery is the
existing single-session recovery applied per agent (see team_recover, Commit B).
Delegation is sequential and synchronous (the orchestrator's delegate tool runs
the child to completion before continuing), which keeps the single-dangling-
intent recovery model intact per session.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

import yaml

from .capability import CapabilityManifest, ExecRule, attenuate
from .gateway import Adapter
from .kernel import Kernel, RunResult, ToolSpec
from .ledger import Ledger, canonical
from .orchestration import AgentNode, DelegationError, Mailbox, Team
from .state import Store


class TeamLoadError(Exception):
    pass


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    system_prompt: str
    manifest: CapabilityManifest
    delegates: tuple[str, ...] = ()


@dataclass
class LoadedTeam:
    specs: dict[str, AgentSpec]
    team: Team
    aliases: dict[str, str] = field(default_factory=dict)   # alias -> entry agent

    def entry_for(self, name: str) -> str:
        if name in self.aliases:
            return self.aliases[name]
        if name in self.specs:
            return name
        raise TeamLoadError(
            f"unknown team/agent {name!r}. Available: "
            f"agents={sorted(self.specs)} teams={sorted(self.aliases)}")


# ------------------------------------------------------------------ loading
def _manifest_from_caps(caps: dict, workspace: str) -> CapabilityManifest:
    def roots(key):
        return tuple(str(r).replace("${WORKSPACE}", workspace)
                     for r in (caps.get(key) or []))
    exec_rules = tuple(ExecRule(r["binary"], r.get("arg_pattern", r".*"))
                       for r in (caps.get("exec_rules") or []))
    return CapabilityManifest(
        tools=frozenset(caps.get("tools") or []),
        fs_read_roots=roots("fs_read_roots"),
        fs_write_roots=roots("fs_write_roots"),
        net_hosts=frozenset(caps.get("net_hosts") or []),
        exec_rules=exec_rules,
        max_budget_usd=float(caps.get("max_budget_usd", 0.0) or 0.0),
        classification_ceiling=caps.get("classification_ceiling", "secret"),
    )


def _parse_agents(agent_docs: list[dict], workspace: str) -> dict[str, AgentSpec]:
    specs: dict[str, AgentSpec] = {}
    for d in agent_docs:
        name = d["name"]
        specs[name] = AgentSpec(
            name=name, role=d.get("role", ""),
            system_prompt=(d.get("system_prompt") or f"You are the {name} agent.").strip(),
            manifest=_manifest_from_caps(d.get("capabilities") or {}, workspace),
            delegates=tuple(d.get("delegates") or ()),
        )
    return specs


def _validate_topology(specs: dict[str, AgentSpec]) -> tuple[str, dict]:
    """Return (root, edges). Rejects unknown delegates, multiple parents,
    cycles, and any child that WIDENS beyond its parent."""
    edges = {n: list(s.delegates) for n, s in specs.items()}
    # unknown delegate target
    for parent, kids in edges.items():
        for k in kids:
            if k not in specs:
                raise TeamLoadError(f"{parent!r} delegates to unknown agent {k!r}")
    # single parent
    parent_of: dict[str, str] = {}
    for parent, kids in edges.items():
        for k in kids:
            if k in parent_of:
                raise TeamLoadError(f"{k!r} has multiple parents "
                                    f"({parent_of[k]!r}, {parent!r})")
            parent_of[k] = parent
    roots = [n for n in specs if n not in parent_of]
    if len(roots) != 1:
        raise TeamLoadError(f"team must have exactly one root orchestrator; found {roots}")
    root = roots[0]
    # cycle detection (DFS)
    seen, stack = set(), [root]
    order = []
    while stack:
        n = stack.pop()
        if n in seen:
            raise TeamLoadError(f"cycle in delegation topology at {n!r}")
        seen.add(n); order.append(n)
        stack.extend(edges.get(n, []))
    if len(seen) != len(specs):
        raise TeamLoadError("topology is not a single connected tree")
    # no-widening: every child's declared caps ⊆ its parent's declared caps
    for parent, kids in edges.items():
        pm = specs[parent].manifest
        for k in kids:
            _reject_widening(parent, k, pm, specs[k].manifest)
    return root, edges


def _reject_widening(parent: str, child: str, pm: CapabilityManifest,
                     cm: CapabilityManifest) -> None:
    extra_tools = cm.tools - pm.tools
    if extra_tools:
        raise TeamLoadError(
            f"{child!r} widens tools beyond parent {parent!r}: {sorted(extra_tools)}")
    extra_hosts = cm.net_hosts - pm.net_hosts
    if extra_hosts:
        raise TeamLoadError(
            f"{child!r} widens net_hosts beyond {parent!r}: {sorted(extra_hosts)}")
    # fs roots: each child root must sit under some parent root
    from .capability import _roots_contained
    for label in ("fs_read_roots", "fs_write_roots"):
        child_roots = getattr(cm, label)
        kept = _roots_contained(child_roots, getattr(pm, label))
        if set(kept) != set(child_roots):
            raise TeamLoadError(
                f"{child!r} widens {label} beyond parent {parent!r}")


def load_team_from_dir(src_dir: str, workspace: str) -> LoadedTeam:
    agents_dir = os.path.join(src_dir, "agents")
    docs = []
    for name in sorted(os.listdir(agents_dir)):
        if name.endswith((".yaml", ".yml")):
            with open(os.path.join(agents_dir, name), "r", encoding="utf-8") as f:
                docs.append(yaml.safe_load(f))
    aliases = {}
    team_file = os.path.join(src_dir, "team.yaml")
    if os.path.exists(team_file):
        with open(team_file, "r", encoding="utf-8") as f:
            aliases = (yaml.safe_load(f) or {}).get("teams", {}) or {}
    return _build_loaded(docs, aliases, workspace)


def load_team_from_package(package_path: str, workspace: str) -> LoadedTeam:
    from .package import Package
    docs, aliases = [], {}
    with Package(package_path) as pkg:
        for member in pkg.member_names():
            if member.startswith("agents/") and member.endswith((".yaml", ".yml")):
                docs.append(yaml.safe_load(pkg.read_member(member).decode("utf-8")))
            elif member == "team.yaml":
                aliases = (yaml.safe_load(pkg.read_member(member).decode("utf-8")) or {}).get("teams", {}) or {}
    return _build_loaded(docs, aliases, workspace)


def _build_loaded(docs: list[dict], aliases: dict, workspace: str) -> LoadedTeam:
    if not docs:
        raise TeamLoadError("package declares no agents/")
    specs = _parse_agents(docs, workspace)
    root, edges = _validate_topology(specs)
    team = Team(root=AgentNode(root, specs[root].manifest))
    for parent, kids in edges.items():
        for k in kids:
            team.add(parent, AgentNode(k, specs[k].manifest))
    return LoadedTeam(specs=specs, team=team, aliases=aliases)


# --------------------------------------------------------------- execution
AdapterFactory = Callable[[str], Adapter]
ToolsFactory = Callable[[CapabilityManifest], dict]


def _eff_hash(m: CapabilityManifest) -> str:
    payload = canonical({
        "tools": sorted(m.tools), "read": sorted(m.fs_read_roots),
        "write": sorted(m.fs_write_roots), "net": sorted(m.net_hosts),
        "exec": sorted((r.binary, r.arg_pattern) for r in m.exec_rules),
        "ceiling": m.classification_ceiling,
    })
    return hashlib.sha256(payload).hexdigest()


class TeamRunner:
    def __init__(self, store: Store, loaded: LoadedTeam,
                 adapter_factory: AdapterFactory, tools_factory: ToolsFactory,
                 policy=None, max_depth: int = 4, sandbox=None):
        self.store = store
        self.loaded = loaded
        self.adapter_factory = adapter_factory
        self.tools_factory = tools_factory
        self.policy = policy
        self.max_depth = max_depth
        self.sandbox = sandbox                 # shared SandboxRunner (for cancel)
        self.sessions: dict[str, str] = {}     # agent -> its session (last run)
        self.last_team_id: Optional[str] = None
        self._cancel = threading.Event()

    def revoke(self, agent: str) -> None:
        self.loaded.team.revoke(agent)

    def cancel(self) -> None:
        """G5 at team scope: stop the whole team cooperatively AND kill every
        in-flight subagent process tree — no orphans."""
        self._cancel.set()
        if self.sandbox is not None:
            self.sandbox.kill_all()

    def run(self, entry_name: str, task: str, team_id: Optional[str] = None,
            parent_session: Optional[str] = None, depth: int = 0) -> RunResult:
        entry = self.loaded.entry_for(entry_name)
        team_id = team_id or uuid.uuid4().hex
        if depth == 0:
            self.last_team_id = team_id
        eff = self.loaded.team.effective_manifest(entry)   # raises if severed/deep
        sid = self.store.create_session()
        self.store.team_session_add(team_id, sid, entry, parent_session, depth)
        self.sessions[entry] = sid

        tools = dict(self.tools_factory(eff))
        if self.loaded.team.edges.get(entry):
            # Grant the framework `delegate` tool to an orchestrator (it is not a
            # declared capability, so it is exempt from the widening check).
            import dataclasses
            eff = dataclasses.replace(eff, tools=eff.tools | {"delegate"})
            tools["delegate"] = self._delegate_tool(entry, team_id, depth, sid)
        kernel = Kernel(self.store, self.adapter_factory(entry), tools, eff,
                        system_prompt=self.loaded.specs[entry].system_prompt,
                        policy=self.policy)
        kernel.cancel_check = self._cancel.is_set    # team cancel reaches every level
        result = kernel.run(sid, task)
        result.session_id = sid
        return result

    def _delegate_tool(self, parent: str, team_id: str, depth: int,
                       parent_session: str) -> ToolSpec:
        led = Ledger(self.store)
        mb = Mailbox(self.store, team_id)

        def fn(args: dict) -> str:
            if self._cancel.is_set():
                return "CANCELLED: team run cancelled; no further delegation"
            child = str(args.get("agent", ""))
            subtask = str(args.get("task", ""))
            allowed = self.loaded.team.edges.get(parent, [])
            if child not in allowed:
                led.append(parent_session, {"type": "delegate_denied", "parent": parent,
                                            "child": child, "reason": "not_a_delegate"})
                return (f"DENIED: {child!r} is not a delegate of {parent!r}. "
                        f"Available: {allowed}")
            if depth + 1 > self.max_depth:
                led.append(parent_session, {"type": "delegate_denied", "parent": parent,
                                            "child": child, "reason": "depth_limit"})
                return f"DENIED: delegation depth limit ({self.max_depth}) reached"
            try:
                eff = self.loaded.team.effective_manifest(child)
            except DelegationError as e:
                led.append(parent_session, {"type": "delegate_denied", "parent": parent,
                                            "child": child, "reason": f"severed: {e}"})
                return f"DENIED: delegation to {child!r} refused — {e}"

            led.append(parent_session, {"type": "delegate", "parent": parent,
                                        "child": child, "team_id": team_id,
                                        "eff_cap_sha256": _eff_hash(eff), "depth": depth + 1})
            self._deliver(led, mb, parent_session, parent, child, subtask, "task")
            child_result = self.run(child, subtask, team_id, parent_session, depth + 1)
            text = child_result.final_text
            self._deliver(led, mb, parent_session, child, parent, text, "result")
            return text

        return ToolSpec("delegate", fn, idempotent=False,
                        description="Delegate a subtask to a named team member; returns their result.",
                        parameters={"type": "object", "properties": {
                            "agent": {"type": "string"}, "task": {"type": "string"}},
                            "required": ["agent", "task"]})

    def _deliver(self, led: Ledger, mb: Mailbox, session: str, frm: str, to: str,
                 body: str, direction: str) -> None:
        mb.send(frm, to, body)
        led.append(session, {"type": "mailbox", "from": frm, "to": to,
                             "direction": direction,
                             "body_sha256": hashlib.sha256(body.encode()).hexdigest()})


def team_recover(store: Store, team_id: str) -> list[dict]:
    """Team-level crash recovery: classify EACH subagent session individually
    via the kernel's single-session recovery (resume / safe_reissue /
    quarantine / reissue_model_call / clean). Completed children keep their
    results (cached); a child killed mid non-idempotent side effect is
    quarantined; the orchestrator's dangling delegate is quarantined too until
    a human resolves it. Uses throwaway kernels — recover only reads the
    journal tail + idempotency cache."""
    from .capability import CapabilityManifest
    from .gateway import MockAdapter
    reports = []
    for m in store.team_members(team_id):
        k = Kernel(store, MockAdapter([]), {}, CapabilityManifest())
        rep = k.recover(m["session_id"])
        reports.append({"agent": m["agent"], "session": m["session_id"],
                        "depth": m["depth"], "status": rep.status, "detail": rep.detail})
    return reports


def verify_team_mailbox(store: Store, team_id: str) -> tuple[bool, list[str]]:
    """Detect tampering of stored mailbox messages by recomputing each body's
    SHA-256 and comparing to the ledgered delivery hash. Any mismatch = tamper."""
    # collect ledgered delivery hashes across all team sessions
    ledgered: list[str] = []
    for m in store.team_members(team_id):
        rows = store.conn.execute(
            "SELECT event FROM ledger WHERE session_id=? ORDER BY seq",
            (m["session_id"],)).fetchall()
        for r in rows:
            try:
                e = json.loads(r["event"])
            except (json.JSONDecodeError, TypeError):
                continue
            if e.get("type") == "mailbox":
                ledgered.append(e["body_sha256"])
    # recompute from the stored mailbox rows
    actual = [hashlib.sha256(r["body"].encode()).hexdigest()
              for r in store.conn.execute(
                  "SELECT body FROM mailbox WHERE session_id=? ORDER BY id", (team_id,)).fetchall()]
    problems = []
    if sorted(actual) != sorted(ledgered):
        problems.append("mailbox integrity violation: stored messages do not "
                        "match ledgered delivery hashes (tamper or loss)")
    return (not problems), problems
