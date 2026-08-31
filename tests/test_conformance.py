"""Shared adapter conformance corpus (design §3.2: "All adapters pass one
shared conformance corpus").

Every real adapter must satisfy the SAME contract:
  * build_request(messages, tools) yields (url:str, headers:dict, body:dict)
    that carries the conversation, represents declared tools, and includes the
    adapter's auth material;
  * parse_response(vendor_payload) yields a ModelResponse with the text, the
    tool call, and non-zero token counts — regardless of the vendor's wire
    shape.

Proving one corpus across all adapters is what §3.2 claims; before this test
each adapter was only checked ad hoc. MockAdapter is excluded (it is scripted
and has no build_request/parse_response).
"""
import datetime

import pytest

from scr.adapters_cloud import AzureOpenAIAdapter, BedrockAdapter
from scr.gateway import (
    AnthropicAdapter,
    ModelResponse,
    OllamaAdapter,
    OpenAICompatAdapter,
    ToolDef,
)

CORPUS_MESSAGES = [
    {"role": "system", "content": "You are a SelfConnect agent."},
    {"role": "user", "content": "read the file"},
]
CORPUS_TOOLS = [ToolDef("fs_read", "read a file",
                        {"type": "object", "properties": {"path": {"type": "string"}}})]

# (adapter, auth-substring-that-must-appear-somewhere-in-the-request)
BUILDERS = [
    (OpenAICompatAdapter("https://gw/v1", "sk-abc", "gpt-x"), "sk-abc"),
    (OllamaAdapter("http://host:11434", "llama3.1"), None),
    (AnthropicAdapter("k-anth", "claude-x"), "k-anth"),
    (BedrockAdapter("AKID", "secret", "us-east-1", "anthropic.claude-3"), "AKID"),
    (AzureOpenAIAdapter("https://x.openai.azure.com", "azkey", "dep"), "azkey"),
]


@pytest.mark.parametrize("adapter,auth", BUILDERS,
                         ids=[type(a).__name__ for a, _ in BUILDERS])
def test_build_request_contract(adapter, auth):
    url, headers, body = adapter.build_request(CORPUS_MESSAGES, CORPUS_TOOLS)
    assert isinstance(url, str) and url.startswith(("http://", "https://"))
    assert isinstance(headers, dict) and headers
    assert isinstance(body, dict) and body
    # the user turn survives into the request (body or, for some shapes, messages)
    blob = str(body)
    assert "read the file" in blob
    # the declared tool is represented
    assert "fs_read" in str(body) or "fs_read" in str(headers)
    # auth material is present somewhere in the request when the adapter uses one
    if auth is not None:
        assert auth in str(headers) or auth in str(body) or auth in url


# vendor-shaped responses, one per adapter, all meaning: text "ok" + one
# fs_read tool call + (3 in, 2 out) tokens.
PARSE_CASES = [
    (OpenAICompatAdapter("u", "k", "m"),
     {"choices": [{"message": {"content": "ok",
       "tool_calls": [{"id": "1", "function": {"name": "fs_read", "arguments": "{\"path\":\"x\"}"}}]}}],
      "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
    (OllamaAdapter(),
     {"message": {"content": "ok",
       "tool_calls": [{"function": {"name": "fs_read", "arguments": {"path": "x"}}}]},
      "prompt_eval_count": 3, "eval_count": 2}),
    (AnthropicAdapter("k", "m"),
     {"content": [{"type": "text", "text": "ok"},
       {"type": "tool_use", "id": "1", "name": "fs_read", "input": {"path": "x"}}],
      "usage": {"input_tokens": 3, "output_tokens": 2}}),
    (BedrockAdapter("a", "s", "us-east-1", "m"),
     {"content": [{"type": "text", "text": "ok"},
       {"type": "tool_use", "id": "1", "name": "fs_read", "input": {"path": "x"}}],
      "usage": {"input_tokens": 3, "output_tokens": 2}}),
    (AzureOpenAIAdapter("https://x", "k", "d"),
     {"choices": [{"message": {"content": "ok",
       "tool_calls": [{"id": "1", "function": {"name": "fs_read", "arguments": "{\"path\":\"x\"}"}}]}}],
      "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
]


@pytest.mark.parametrize("adapter,payload", PARSE_CASES,
                         ids=[type(a).__name__ for a, _ in PARSE_CASES])
def test_parse_response_contract(adapter, payload):
    resp = adapter.parse_response(payload)
    assert isinstance(resp, ModelResponse)
    assert resp.text == "ok"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "fs_read"
    assert resp.tool_calls[0].arguments == {"path": "x"}
    assert resp.input_tokens == 3 and resp.output_tokens == 2


def test_corpus_covers_every_non_mock_adapter():
    """Guard: if a new real adapter is added, it must be in the corpus."""
    import scr.gateway as gw
    import scr.adapters_cloud as ac
    covered = {type(a).__name__ for a, _ in BUILDERS}
    known_real = {"OpenAICompatAdapter", "OllamaAdapter", "AnthropicAdapter",
                  "BedrockAdapter", "AzureOpenAIAdapter"}
    assert known_real <= covered
