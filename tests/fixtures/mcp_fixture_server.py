"""Minimal MCP stdio server for tests. JSON-RPC 2.0 over newline-delimited
stdin/stdout. Tools: echo, add, env_echo. Optional scripted crash after N
tool calls via SCR_FIXTURE_CRASH_AFTER.

Not part of the product — a test double standing in for a real MCP server.
"""
import json
import os
import sys

TOOLS = [
    {"name": "echo", "description": "Echo text back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "add", "description": "Add two integers.",
     "inputSchema": {"type": "object",
                     "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                     "required": ["a", "b"]}},
    {"name": "env_echo", "description": "Report one environment variable.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}},
                     "required": ["name"]}},
]


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _text(s):
    return {"content": [{"type": "text", "text": s}]}


def main():
    crash_after = int(os.environ.get("SCR_FIXTURE_CRASH_AFTER", "0"))
    calls = 0
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        msg = json.loads(raw)
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            out = _result(rid, {"protocolVersion": "2024-11-05",
                                "capabilities": {"tools": {}},
                                "serverInfo": {"name": "fixture", "version": "1.0"}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            out = _result(rid, {"tools": TOOLS})
        elif method == "tools/call":
            calls += 1
            if crash_after and calls > crash_after:
                sys.exit(7)  # scripted crash mid-stream
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "echo":
                out = _result(rid, _text(args.get("text", "")))
            elif name == "add":
                out = _result(rid, _text(str(int(args["a"]) + int(args["b"]))))
            elif name == "env_echo":
                out = _result(rid, _text(os.environ.get(args["name"], "<unset>")))
            else:
                out = {"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32601, "message": f"no tool {name}"}}
        else:
            out = {"jsonrpc": "2.0", "id": rid,
                   "error": {"code": -32601, "message": f"no method {method}"}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
