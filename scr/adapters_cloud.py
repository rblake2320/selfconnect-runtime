"""Cloud model adapters (design §3.2): AWS Bedrock (SigV4) and Azure OpenAI.

Both honor the gateway contract: build_request() is pure and offline-testable,
complete() does the network call. SigV4 is implemented directly (stdlib
hmac/hashlib) — no boto3 — and its signing-key derivation is checked against
AWS's published example vector in the tests.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import urllib.request
from typing import Any

from .gateway import ModelResponse, ToolCall, ToolDef


# ------------------------------------------------------------------ SigV4
def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def derive_signing_key(secret_key: str, date_stamp: str, region: str,
                       service: str) -> bytes:
    """AWS SigV4 signing key. Verified against AWS's documented example."""
    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BedrockAdapter:
    """AWS Bedrock InvokeModel for Anthropic-family models (Messages shape)."""

    def __init__(self, access_key: str, secret_key: str, region: str,
                 model_id: str, session_token: str = "", timeout: float = 120.0,
                 max_tokens: int = 4096):
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.model_id = model_id
        self.session_token = session_token
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.service = "bedrock"

    def _now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc)

    def build_request(self, messages: list[dict[str, str]], tools: list[ToolDef],
                      now: datetime.datetime | None = None
                      ) -> tuple[str, dict[str, str], dict[str, Any]]:
        now = now or self._now()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        host = f"bedrock-runtime.{self.region}.amazonaws.com"
        path = f"/model/{self.model_id}/invoke"

        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        body: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.max_tokens,
            "messages": convo,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description,
                              "input_schema": t.parameters} for t in tools]
        payload = json.dumps(body).encode("utf-8")
        payload_hash = _sha256_hex(payload)

        canonical_headers = (f"host:{host}\n"
                             f"x-amz-content-sha256:{payload_hash}\n"
                             f"x-amz-date:{amz_date}\n")
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join([
            "POST", path, "", canonical_headers, signed_headers, payload_hash])

        scope = f"{date_stamp}/{self.region}/{self.service}/aws4_request"
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            _sha256_hex(canonical_request.encode("utf-8"))])
        signing_key = derive_signing_key(self.secret_key, date_stamp,
                                         self.region, self.service)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"),
                             hashlib.sha256).hexdigest()

        authorization = (f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
                         f"SignedHeaders={signed_headers}, Signature={signature}")
        headers = {
            "Content-Type": "application/json",
            "X-Amz-Date": amz_date,
            "X-Amz-Content-Sha256": payload_hash,
            "Authorization": authorization,
        }
        if self.session_token:
            headers["X-Amz-Security-Token"] = self.session_token
        return f"https://{host}{path}", headers, body

    def parse_response(self, payload: dict[str, Any]) -> ModelResponse:
        text_parts, calls = [], []
        for block in payload.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=block["id"], name=block["name"],
                                      arguments=block.get("input") or {}))
        usage = payload.get("usage") or {}
        return ModelResponse("".join(text_parts), tuple(calls),
                             usage.get("input_tokens", 0),
                             usage.get("output_tokens", 0))

    def complete(self, messages, tools) -> ModelResponse:
        url, headers, body = self.build_request(messages, tools)
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return self.parse_response(json.loads(resp.read().decode()))


class AzureOpenAIAdapter:
    """Azure OpenAI Chat Completions (deployment + api-version + api-key)."""

    def __init__(self, endpoint: str, api_key: str, deployment: str,
                 api_version: str = "2024-10-21", timeout: float = 120.0):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.deployment = deployment
        self.api_version = api_version
        self.timeout = timeout

    def build_request(self, messages: list[dict[str, str]], tools: list[ToolDef]
                      ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = (f"{self.endpoint}/openai/deployments/{self.deployment}"
               f"/chat/completions?api-version={self.api_version}")
        headers = {"Content-Type": "application/json", "api-key": self.api_key}
        body: dict[str, Any] = {"messages": messages}
        if tools:
            body["tools"] = [{"type": "function",
                              "function": {"name": t.name, "description": t.description,
                                           "parameters": t.parameters}} for t in tools]
        return url, headers, body

    def parse_response(self, payload: dict[str, Any]) -> ModelResponse:
        choice = payload["choices"][0]["message"]
        calls = []
        for tc in choice.get("tool_calls") or []:
            calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"],
                                  arguments=json.loads(tc["function"]["arguments"] or "{}")))
        usage = payload.get("usage") or {}
        return ModelResponse(choice.get("content") or "", tuple(calls),
                             usage.get("prompt_tokens", 0),
                             usage.get("completion_tokens", 0))

    def complete(self, messages, tools) -> ModelResponse:
        url, headers, body = self.build_request(messages, tools)
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return self.parse_response(json.loads(resp.read().decode()))
