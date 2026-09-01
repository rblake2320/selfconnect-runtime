"""Native tools, capability-mediated and sandbox-executed.

The parent performs every capability check BEFORE any process is spawned:
a denied call never creates a worker. The worker then re-validates inside
the sandbox (defense in depth). Denials are returned as tool-result text so
the kernel folds them back to the model, matching the kernel's own pattern.

Idempotency declarations drive crash recovery (design §3.1):
  fs_read / fs_list / http_get  -> idempotent=True  (safe_reissue)
  fs_write / proc_exec          -> idempotent=False (quarantine)
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

from .capability import CapabilityDenied, CapabilityManifest
from .kernel import ToolSpec
from .sandbox import SandboxRunner


def _work_dir(manifest: CapabilityManifest) -> str:
    roots = manifest.fs_write_roots or manifest.fs_read_roots
    if not roots:
        raise CapabilityDenied("no filesystem roots to anchor a worker jail")
    return roots[0]


def _denied(e: CapabilityDenied) -> str:
    return f"DENIED by capability kernel: {e}"


def _fold(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return json.dumps(result)
    return f"TOOL ERROR [{result.get('error')}]: {result.get('detail', '')}"


def _missing(args: dict, *keys: str):
    """RUN-E: malformed model tool calls (missing/mistyped args) must fold to
    a corrective error, not raise."""
    bad = [k for k in keys if not isinstance(args.get(k), str)]
    if bad:
        return (f"TOOL ERROR [bad_args]: missing or non-string argument(s) "
                f"{bad} — supply them and retry")
    return None


def build_native_tools(manifest: CapabilityManifest,
                       runner: SandboxRunner) -> dict[str, ToolSpec]:
    def fs_read(args: dict[str, Any]) -> str:
        err = _missing(args, "path")
        if err:
            return err
        try:
            resolved = manifest.check_read(args["path"])
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "fs_read", "path": resolved,
             "read_roots": list(manifest.fs_read_roots)},
            cwd=_work_dir(manifest)))

    def fs_write(args: dict[str, Any]) -> str:
        err = _missing(args, "path", "content")
        if err:
            return err
        try:
            resolved = manifest.check_write(args["path"])
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "fs_write", "path": resolved, "content": args["content"],
             "write_roots": list(manifest.fs_write_roots)},
            cwd=_work_dir(manifest)))

    def fs_list(args: dict[str, Any]) -> str:
        err = _missing(args, "path")
        if err:
            return err
        try:
            resolved = manifest.check_read(args["path"])
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "fs_list", "path": resolved,
             "read_roots": list(manifest.fs_read_roots)},
            cwd=_work_dir(manifest)))

    def http_get(args: dict[str, Any]) -> str:
        err = _missing(args, "url")
        if err:
            return err
        host = urllib.parse.urlsplit(args["url"]).hostname or ""
        try:
            manifest.check_net(host)
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "http_get", "url": args["url"],
             "net_hosts": sorted(manifest.net_hosts)},
            cwd=_work_dir(manifest)))

    def proc_exec(args: dict[str, Any]) -> str:
        err = _missing(args, "binary")
        if err:
            return err
        binary, argv = args["binary"], list(args.get("args", []))
        try:
            manifest.check_exec(binary, argv)
            cwd = _work_dir(manifest)
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "proc_exec", "binary": binary, "args": argv, "cwd": cwd},
            cwd=cwd))

    def compliance_map(args: dict[str, Any]) -> str:
        """Layer #1 (Provenance/Sentinel): map an SCR evidence bundle's ledger
        to compliance controls and write a report. Capability-gated: the bundle
        must be under a readable root and the output under a writable root."""
        err = _missing(args, "bundle", "out")
        if err:
            return err
        from . import compliance
        try:
            bundle = manifest.check_read(args["bundle"])
            out = manifest.check_write(args["out"])
        except CapabilityDenied as e:
            return _denied(e)
        try:
            res = compliance.map_bundle(bundle)
        except (OSError, KeyError, ValueError, __import__("zipfile").BadZipFile) as e:
            return f"TOOL ERROR [compliance_map]: {type(e).__name__}: {e}"
        with open(out, "w", encoding="utf-8") as f:
            f.write(compliance.render_markdown(res))
        return (f"compliance mapping complete: {res['controls_satisfied']}/"
                f"{res['controls_total']} controls satisfied across "
                f"{len(res['frameworks'])} frameworks; report written to {out}")

    def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required}

    # Read-only, idempotent tools are declared parallel_safe: they have no
    # shared mutable state, so a batch of them runs concurrently (§3.1).
    # Side-effecting tools (fs_write, proc_exec) are never parallel_safe.
    return {
        "fs_read": ToolSpec("fs_read", fs_read, idempotent=True, parallel_safe=True,
                            description="Read a file inside the workspace.",
                            parameters=_obj({"path": {"type": "string"}}, ["path"])),
        "fs_write": ToolSpec("fs_write", fs_write, idempotent=False,
                             description="Atomically write a file inside the workspace.",
                             parameters=_obj({"path": {"type": "string"},
                                              "content": {"type": "string"}},
                                             ["path", "content"])),
        "fs_list": ToolSpec("fs_list", fs_list, idempotent=True, parallel_safe=True,
                            description="List a directory inside the workspace.",
                            parameters=_obj({"path": {"type": "string"}}, ["path"])),
        "http_get": ToolSpec("http_get", http_get, idempotent=True, parallel_safe=True,
                             description="HTTP GET an allowlisted host.",
                             parameters=_obj({"url": {"type": "string"}}, ["url"])),
        "proc_exec": ToolSpec("proc_exec", proc_exec, idempotent=False,
                              description="Run an allowlisted binary in the workspace.",
                              parameters=_obj({"binary": {"type": "string"},
                                               "args": {"type": "array",
                                                        "items": {"type": "string"}}},
                                              ["binary"])),
        "compliance_map": ToolSpec(
            "compliance_map", compliance_map, idempotent=True,
            description="Map an SCR evidence bundle's ledger to NIST/ISO/EU-AI-Act "
                        "compliance controls and write a report.",
            parameters=_obj({"bundle": {"type": "string"},
                             "out": {"type": "string"}}, ["bundle", "out"])),
    }
