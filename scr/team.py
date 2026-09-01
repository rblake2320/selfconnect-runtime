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
    # Delegation policy (runtime-enforced, every decision ledgered):
    #   required_children: [names]          finalize refused until each completes
    #   max_delegations_per_child: int      excess attempts folded as denials
    #   no_redelegate_after_denial: bool    re-delegating a same-task all-denied
    #                                       child is blocked (team cycle guard)
    policy: dict = field(default_factory=dict)


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
            system_prompt=(d.get("system_prompt") or f"You are the {name} agent.")
            .strip().replace("${WORKSPACE}", workspace),
            manifest=_manifest_from_caps(d.get("capabilities") or {}, workspace),
            delegates=tuple(d.get("delegates") or ()),
            policy=dict(d.get("delegation_policy") or {}),
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
    # delegation policy must only reference DECLARED delegates of that agent
    for name, spec in specs.items():
        for ref in (spec.policy.get("required_children") or []):
            if ref not in edges.get(name, []):
                raise TeamLoadError(
                    f"{name!r} delegation policy requires undeclared child {ref!r} "
                    f"(declared delegates: {edges.get(name, [])})")
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


def _caps_context(m: CapabilityManifest) -> str:
    """Render an agent's EFFECTIVE grants as a context block appended to its
    system prompt. RUN-B live finding: agents were granted real fs roots but
    never TOLD them, so the model blind-guessed container paths (/, /workspace)
    and every call was denied. Deny-by-default only works if the model can see
    what IS granted — derived from the attenuated manifest (the truth), never
    authored prose."""
    def fmt(items):
        return ", ".join(sorted(items)) if items else "(none)"
    return (
        "[capability grant — authoritative, from your signed manifest]\n"
        f"tools: {fmt(m.tools)}\n"
        f"readable roots: {fmt(m.fs_read_roots)}\n"
        f"writable roots: {fmt(m.fs_write_roots)}\n"
        f"network hosts: {fmt(m.net_hosts)}\n"
        "Anything outside these grants is denied. Use these exact root paths "
        "in fs_list/fs_read — do not guess other paths."
    )


def team_execution_summary(store: Store, team_id: str) -> str:
    """Ledger-derived execution summary — the runtime's FACTS printed after the
    model's prose on every team run. Motivated by three-for-three live runs in
    which the model confabulated causes ("worker crashes") or work ("Auditor
    Risk Assessment") the ledger disproves. Generated ONLY from the hash chain;
    the model has no hand in it."""
    members = store.team_members(team_id)
    agents: dict[str, int] = {}
    delegations: list[str] = []
    files: list[str] = []
    tool_counts: dict[str, int] = {}
    denials: list[str] = []
    policy_lines: list[str] = []
    empty_returns = 0
    for m in members:
        agents[m["agent"]] = agents.get(m["agent"], 0) + 1
        for row in store.conn.execute(
                "SELECT event FROM ledger WHERE session_id=? ORDER BY seq",
                (m["session_id"],)).fetchall():
            e = json.loads(row["event"])
            t = e.get("type")
            if t == "delegate":
                delegations.append(f'{e.get("parent")} -> {e.get("child")}')
            elif t == "tool_exec":
                tool_counts[e.get("tool", "?")] = tool_counts.get(e.get("tool", "?"), 0) + 1
                if e.get("path"):
                    files.append(e["path"])
            elif t == "tool_error":
                denials.append(f'{e.get("tool")} ERROR[{e.get("class")}]: '
                               f'{e.get("detail", "")[:60]}')
            elif t == "cap_denied":
                denials.append(f'{e.get("tool")}: {e.get("reason", "")[:80]}')
            elif t == "policy":
                rule, dec = e.get("rule", "?"), e.get("decision", "?")
                if dec == "not_counted":
                    empty_returns += 1
                policy_lines.append(f'{rule}: {dec} ({e.get("child") or e.get("agent") or ""})')
    uniq_files = sorted(set(files))
    lines = ["=" * 68,
             "RUNTIME EXECUTION SUMMARY (ledger-derived; generated by SCR, not the model)",
             "=" * 68,
             f"agents invoked: " + ", ".join(f"{a} x{n}" for a, n in sorted(agents.items())),
             f"delegations ({len(delegations)}): " + ("; ".join(delegations) or "(none)"),
             f"tool executions: " + (", ".join(f"{t} x{n}" for t, n in sorted(tool_counts.items())) or "(none)")]
    if uniq_files:
        shown = uniq_files[:20]
        more = f" (+{len(uniq_files) - 20} more)" if len(uniq_files) > 20 else ""
        lines.append(f"files touched ({len(uniq_files)}):" + more)
        lines.extend(f"  {f}" for f in shown)
    else:
        lines.append("files touched: NONE — no filesystem path was actually read")
    lines.append(f"denials / tool errors ({len(denials)}): "
                 + ("; ".join(denials[:10]) or "(none)"))
    if empty_returns:
        lines.append(f"empty child returns not counted as completed: {empty_returns}")
    lines.append(f"policy decisions ({len(policy_lines)}): "
                 + ("; ".join(policy_lines[:15]) or "(none)"))
    lines.append("Any claim in the report above that conflicts with these facts "
                 "is unsupported by the ledger.")
    return "\n".join(lines)


class TeamRunner:
    def __init__(self, store: Store, loaded: LoadedTeam,
                 adapter_factory: AdapterFactory, tools_factory: ToolsFactory,
                 policy=None, max_depth: int = 4, sandbox=None, on_event=None,
                 provenance: Optional[dict] = None):
        self.store = store
        self.loaded = loaded
        self.adapter_factory = adapter_factory
        self.tools_factory = tools_factory
        self.policy = policy
        self.max_depth = max_depth
        self.sandbox = sandbox                 # shared SandboxRunner (for cancel)
        self.on_event = on_event or (lambda msg: None)   # progress callback
        self.sessions: dict[str, str] = {}     # agent -> its session (last run)
        self.last_team_id: Optional[str] = None
        self._cancel = threading.Event()
        # Which signed package governs this run (name/version/content hash).
        # Ledgered into the lead session so the evidence bundle can prove it.
        self.provenance = dict(provenance or {})

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
            self.on_event(f"> team run: {entry_name} (orchestrator {entry})")
        eff = self.loaded.team.effective_manifest(entry)   # raises if severed/deep
        sid = self.store.create_session()
        self.store.team_session_add(team_id, sid, entry, parent_session, depth)
        self.sessions[entry] = sid
        if depth == 0 and self.provenance:
            # Provenance INSIDE the lead session's hash chain — tamper-evident,
            # so the bundle can prove which signed package governed the run.
            Ledger(self.store).append(sid, {"type": "provenance",
                                            **self.provenance})

        tools = dict(self.tools_factory(eff))
        kernel_policy_state = {"count": {}, "completed": set(), "denied": set()}
        spec_policy = self.loaded.specs[entry].policy
        if self.loaded.team.edges.get(entry):
            # Grant the framework `delegate` tool to an orchestrator (it is not a
            # declared capability, so it is exempt from the widening check).
            import dataclasses
            eff = dataclasses.replace(eff, tools=eff.tools | {"delegate"})
            tools["delegate"] = self._delegate_tool(entry, team_id, depth, sid,
                                                    spec_policy, kernel_policy_state)
        # Tell the model its EFFECTIVE grants (post-attenuation, incl. the
        # delegate grant) — derived from the manifest, so it cannot overstate.
        grant_block = _caps_context(eff)
        sys_prompt = (self.loaded.specs[entry].system_prompt
                      + "\n\n" + grant_block)
        # Ledger WHAT THE AGENT WAS TOLD next to WHAT IS ENFORCED, in the same
        # chain: a divergence between the two is the prompt-injection surface
        # an auditor asks about. (Owner directive, 2026-09-01.)
        Ledger(self.store).append(sid, {
            "type": "grant_context", "agent": entry,
            "eff_cap_sha256": _eff_hash(eff),
            "grant_block_sha256": hashlib.sha256(grant_block.encode()).hexdigest(),
            "system_prompt_sha256": hashlib.sha256(sys_prompt.encode()).hexdigest(),
        })
        kernel = Kernel(self.store, self.adapter_factory(entry), tools, eff,
                        system_prompt=sys_prompt, policy=self.policy)
        kernel.cancel_check = self._cancel.is_set    # team cancel reaches every level
        if spec_policy.get("required_children"):
            kernel.finalize_guard = self._finalize_guard(spec_policy, kernel_policy_state)
        result = kernel.run(sid, task)
        result.session_id = sid
        if depth == 0 and result.final_text is not None:
            # Standing product feature: the model's prose is followed by the
            # runtime's FACTS, generated from the hash chain — the "auditor
            # heading with no auditor" exhibit built in. Derived output, not
            # stored (regenerable from the ledger at any time).
            result.final_text = (result.final_text.rstrip() + "\n\n"
                                 + team_execution_summary(self.store, team_id))
        return result

    def _finalize_guard(self, policy: dict, state: dict):
        required = list(policy.get("required_children") or [])

        def guard(_session_id: str) -> Optional[str]:
            missing = [c for c in required if c not in state["completed"]]
            if missing:
                return (f"POLICY: cannot finalize yet — required team members have "
                        f"not completed successfully: {missing}. Delegate the task to "
                        f"each of them (use the delegate tool) before finalizing.")
            return None
        return guard

    def _child_all_denied(self, child_session: str) -> bool:
        """True if the child made tool calls and EVERY one was capability-denied
        (no successful tool_exec) — the team-level analogue of a stuck loop."""
        denied = execed = 0
        for r in self.store.conn.execute(
                "SELECT event FROM ledger WHERE session_id=? ORDER BY seq",
                (child_session,)).fetchall():
            try:
                e = json.loads(r["event"])
            except (json.JSONDecodeError, TypeError):
                continue
            if e.get("type") == "cap_denied":
                denied += 1
            elif e.get("type") == "tool_exec":
                execed += 1
        return denied > 0 and execed == 0

    def _delegate_tool(self, parent: str, team_id: str, depth: int,
                       parent_session: str, policy: dict, state: dict) -> ToolSpec:
        led = Ledger(self.store)
        mb = Mailbox(self.store, team_id)
        max_per = policy.get("max_delegations_per_child")
        no_redelegate = bool(policy.get("no_redelegate_after_denial"))
        need_nonempty = bool(policy.get("require_nonempty_result"))

        def _policy(child, rule, decision, reason):
            led.append(parent_session, {"type": "policy", "parent": parent,
                                        "child": child, "rule": rule,
                                        "decision": decision, "reason": reason})

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
            task_key = (child, hashlib.sha256(subtask.encode()).hexdigest())
            # POLICY: no re-delegate after an all-denied run of the same task
            if no_redelegate and task_key in state["denied"]:
                _policy(child, "no_redelegate_after_denial", "denied",
                        "child previously failed this exact task (all tool calls denied)")
                self.on_event(f"  x policy: {child} not re-delegated (prior all-denied)")
                return (f"DENIED by policy: {child!r} already ran this task and every "
                        f"tool call was denied; retrying will not help. Adjust the task "
                        f"or the capability grant, or proceed without it.")
            # POLICY: max delegations per child
            if max_per is not None and state["count"].get(child, 0) >= int(max_per):
                _policy(child, "max_delegations_per_child", "denied",
                        f"max {max_per} delegations to {child} reached")
                self.on_event(f"  x policy: max delegations to {child} reached")
                return f"DENIED by policy: max {max_per} delegations to {child!r} reached"
            try:
                eff = self.loaded.team.effective_manifest(child)
            except DelegationError as e:
                led.append(parent_session, {"type": "delegate_denied", "parent": parent,
                                            "child": child, "reason": f"severed: {e}"})
                return f"DENIED: delegation to {child!r} refused — {e}"

            led.append(parent_session, {"type": "delegate", "parent": parent,
                                        "child": child, "team_id": team_id,
                                        "eff_cap_sha256": _eff_hash(eff), "depth": depth + 1})
            self.on_event(f"  -> {parent} delegates to {child}: {subtask[:70]}")
            self._deliver(led, mb, parent_session, parent, child, subtask, "task")
            child_result = self.run(child, subtask, team_id, parent_session, depth + 1)
            text = child_result.final_text
            if not text and child_result.stopped_reason != "completed":
                # Abnormal stop: tell the parent WHY (a model_error child once
                # handed back None/"" and the raw len() crashed the delegate
                # tool). An empty COMPLETED result stays "" — that is a
                # different fact require_nonempty_result must still see.
                text = (f"CHILD STOPPED [{child_result.stopped_reason}]: "
                        f"{child!r} ended without a result")
            state["count"][child] = state["count"].get(child, 0) + 1
            if self._child_all_denied(child_result.session_id):
                state["denied"].add(task_key)
                _policy(child, "no_redelegate_after_denial", "marked_denied",
                        "child run ended with all tool calls denied")
                self.on_event(f"  ! {child} run was all-denied (fs/tool access)")
            elif need_nonempty and not text.strip():
                # RUN-B live finding: a child returned 0 chars yet satisfied
                # required_children — a model can complete a requirement
                # empty-handed. Zero output is a mechanical ledger fact, so it
                # is enforceable: don't count it, and say so in the ledger.
                _policy(child, "require_nonempty_result", "not_counted",
                        "child completed but returned an empty result")
                self.on_event(f"  ! {child} returned empty - not counted as completed")
            else:
                state["completed"].add(child)
                _policy(child, "required_children", "completed", "child completed")
            self.on_event(f"  <- {child} returned ({len(text)} chars)")
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
