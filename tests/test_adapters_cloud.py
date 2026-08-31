"""Bedrock (SigV4) + Azure OpenAI adapter build/parse — offline."""
import datetime

from scr.adapters_cloud import AzureOpenAIAdapter, BedrockAdapter, derive_signing_key
from scr.gateway import ToolDef


def test_sigv4_signing_key_matches_aws_example():
    # AWS-documented SigV4 example vector.
    key = derive_signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                             "20150830", "us-east-1", "iam")
    assert key.hex() == "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9"


def test_bedrock_build_request_deterministic_and_signed():
    a = BedrockAdapter("AKIDEXAMPLE", "secret", "us-east-1",
                       "anthropic.claude-3-sonnet")
    now = datetime.datetime(2026, 8, 31, 12, 0, 0, tzinfo=datetime.timezone.utc)
    url1, h1, b1 = a.build_request([{"role": "user", "content": "hi"}], [], now=now)
    url2, h2, b2 = a.build_request([{"role": "user", "content": "hi"}], [], now=now)
    assert url1 == url2 and h1 == h2   # deterministic given a fixed clock
    assert "bedrock-runtime.us-east-1.amazonaws.com" in url1
    assert h1["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "SignedHeaders=host;x-amz-content-sha256;x-amz-date" in h1["Authorization"]
    assert h1["X-Amz-Date"] == "20260831T120000Z"
    assert b1["anthropic_version"] == "bedrock-2023-05-31"


def test_bedrock_session_token_header():
    a = BedrockAdapter("AK", "sk", "us-west-2", "m", session_token="tok123")
    _, headers, _ = a.build_request([{"role": "user", "content": "x"}], [])
    assert headers["X-Amz-Security-Token"] == "tok123"


def test_bedrock_parse_response():
    a = BedrockAdapter("AK", "sk", "us-east-1", "m")
    resp = a.parse_response({
        "content": [{"type": "text", "text": "hello"},
                    {"type": "tool_use", "id": "t1", "name": "fs_read",
                     "input": {"path": "x"}}],
        "usage": {"input_tokens": 10, "output_tokens": 5}})
    assert resp.text == "hello"
    assert resp.tool_calls[0].name == "fs_read"
    assert resp.input_tokens == 10 and resp.output_tokens == 5


def test_azure_build_request():
    a = AzureOpenAIAdapter("https://x.openai.azure.com", "key123", "gpt4o",
                           api_version="2024-10-21")
    url, headers, body = a.build_request(
        [{"role": "user", "content": "hi"}],
        [ToolDef("fs_read", "read", {"type": "object", "properties": {}})])
    assert "deployments/gpt4o/chat/completions?api-version=2024-10-21" in url
    assert headers["api-key"] == "key123"
    assert body["tools"][0]["function"]["name"] == "fs_read"


def test_azure_parse_response():
    a = AzureOpenAIAdapter("https://x", "k", "d")
    resp = a.parse_response({
        "choices": [{"message": {"content": "hi",
                                 "tool_calls": [{"id": "1",
                                                 "function": {"name": "f", "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
    assert resp.text == "hi" and resp.tool_calls[0].name == "f"
    assert resp.input_tokens == 3 and resp.output_tokens == 2
