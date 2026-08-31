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


def build_native_tools(manifest: CapabilityManifest,
                       runner: SandboxRunner) -> dict[str, ToolSpec]:
    def fs_read(args: dict[str, Any]) -> str:
        try:
            resolved = manifest.check_read(args["path"])
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "fs_read", "path": resolved,
             "read_roots": list(manifest.fs_read_roots)},
            cwd=_work_dir(manifest)))

    def fs_write(args: dict[str, Any]) -> str:
        try:
            resolved = manifest.check_write(args["path"])
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "fs_write", "path": resolved, "content": args["content"],
             "write_roots": list(manifest.fs_write_roots)},
            cwd=_work_dir(manifest)))

    def fs_list(args: dict[str, Any]) -> str:
        try:
            resolved = manifest.check_read(args["path"])
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "fs_list", "path": resolved,
             "read_roots": list(manifest.fs_read_roots)},
            cwd=_work_dir(manifest)))

    def http_get(args: dict[str, Any]) -> str:
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
        binary, argv = args["binary"], list(args.get("args", []))
        try:
            manifest.check_exec(binary, argv)
            cwd = _work_dir(manifest)
        except CapabilityDenied as e:
            return _denied(e)
        return _fold(runner.run_worker(
            {"op": "proc_exec", "binary": binary, "args": argv, "cwd": cwd},
            cwd=cwd))

    def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required}

    return {
        "fs_read": ToolSpec("fs_read", fs_read, idempotent=True,
                            description="Read a file inside the workspace.",
                            parameters=_obj({"path": {"type": "string"}}, ["path"])),
        "fs_write": ToolSpec("fs_write", fs_write, idempotent=False,
                             description="Atomically write a file inside the workspace.",
                             parameters=_obj({"path": {"type": "string"},
                                              "content": {"type": "string"}},
                                             ["path", "content"])),
        "fs_list": ToolSpec("fs_list", fs_list, idempotent=True,
                            description="List a directory inside the workspace.",
                            parameters=_obj({"path": {"type": "string"}}, ["path"])),
        "http_get": ToolSpec("http_get", http_get, idempotent=True,
                             description="HTTP GET an allowlisted host.",
                             parameters=_obj({"url": {"type": "string"}}, ["url"])),
        "proc_exec": ToolSpec("proc_exec", proc_exec, idempotent=False,
                              description="Run an allowlisted binary in the workspace.",
                              parameters=_obj({"binary": {"type": "string"},
                                               "args": {"type": "array",
                                                        "items": {"type": "string"}}},
                                              ["binary"])),
    }
