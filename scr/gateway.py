"""Model gateway: one internal schema, pluggable vendor adapters.

The kernel speaks ONLY the internal schema. Adapters translate.
Customer supplies the endpoint + credential; SCR never ships vendor keys.

Adapters here expose build_request() (pure, unit-testable offline) and
complete() (network). Live conformance runs happen in CI against real
endpoints; this module is tested via build_request + MockAdapter.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


# ---------------------------------------------------------------- schema
@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0


class Adapter(Protocol):
    def complete(
        self, messages: list[dict[str, str]], tools: list[ToolDef]
    ) -> ModelResponse: ...


# ------------------------------------------------------------------ mock
class MockAdapter:
    """Scripted adapter for deterministic kernel tests. Optionally raises
    at a scripted step to simulate a crash mid-model-call."""

    def __init__(self, script: list[ModelResponse | Exception]):
        self.script = list(script)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages, tools) -> ModelResponse:
        self.calls.append([dict(m) for m in messages])
        if not self.script:
            raise RuntimeError("MockAdapter script exhausted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


# --------------------------------------------------------- openai-compat
class OpenAICompatAdapter:
    """Covers OpenAI, Azure-compatible gateways, vLLM, LM Studio, etc."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def build_request(
        self, messages: list[dict[str, str]], tools: list[ToolDef]
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        return url, headers, body

    def parse_response(self, payload: dict[str, Any]) -> ModelResponse:
        choice = payload["choices"][0]["message"]
        calls = []
        for tc in choice.get("tool_calls") or []:
            calls.append(
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=json.loads(tc["function"]["arguments"] or "{}"),
                )
            )
        usage = payload.get("usage") or {}
        return ModelResponse(
            text=choice.get("content") or "",
            tool_calls=tuple(calls),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )

    def complete(self, messages, tools) -> ModelResponse:
        url, headers, body = self.build_request(messages, tools)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return self.parse_response(json.loads(resp.read().decode()))


# ---------------------------------------------------------------- ollama
class OllamaAdapter:
    """Local models — fully offline path. No credential required."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "llama3.1",
                 timeout: float = 600.0,    # local reasoning models can think a long time
                 num_ctx: int = 16384):
        # num_ctx MUST be explicit: Ollama's runtime default (~4k) silently
        # truncates the prompt from the top (system prompt first) and caps
        # generation — a 262k-capable qwen3.6 was strangled to 4k in live RUN
        # A-C, cutting the researcher off MID-THINKING (empty content, no tool
        # calls). Silent truncation is the same failure class as a mangled
        # workspace: the run proceeds against nothing.
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_ctx = int(num_ctx)

    def build_request(
        self, messages: list[dict[str, str]], tools: list[ToolDef]
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = f"{self.base_url}/api/chat"
        headers = {"Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": self.num_ctx},
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        return url, headers, body

    def parse_response(self, payload: dict[str, Any]) -> ModelResponse:
        if payload.get("error"):
            # A server-side error (e.g. runner crash / model failed to load)
            # must surface, never masquerade as an empty completion.
            raise RuntimeError(f"ollama error: {payload['error']}")
        msg = payload.get("message") or {}
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            calls.append(
                ToolCall(id=f"ollama-{i}", name=fn.get("name", ""),
                         arguments=fn.get("arguments") or {})
            )
        return ModelResponse(
            text=msg.get("content") or "",
            tool_calls=tuple(calls),
            input_tokens=payload.get("prompt_eval_count", 0),
            output_tokens=payload.get("eval_count", 0),
        )

    def complete(self, messages, tools) -> ModelResponse:
        url, headers, body = self.build_request(messages, tools)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return self.parse_response(json.loads(resp.read().decode()))


# ------------------------------------------------------------- anthropic
class AnthropicAdapter:
    """Customer's Anthropic endpoint (direct or enterprise gateway URL)."""

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://api.anthropic.com", timeout: float = 120.0,
                 max_tokens: int = 4096):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def build_request(
        self, messages: list[dict[str, str]], tools: list[ToolDef]
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": convo,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description,
                 "input_schema": t.parameters}
                for t in tools
            ]
        return url, headers, body

    def parse_response(self, payload: dict[str, Any]) -> ModelResponse:
        text_parts, calls = [], []
        for block in payload.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(
                    ToolCall(id=block["id"], name=block["name"],
                             arguments=block.get("input") or {})
                )
        usage = payload.get("usage") or {}
        return ModelResponse(
            text="".join(text_parts),
            tool_calls=tuple(calls),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    def complete(self, messages, tools) -> ModelResponse:
        url, headers, body = self.build_request(messages, tools)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return self.parse_response(json.loads(resp.read().decode()))
