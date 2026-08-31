from scr.gateway import (
    AnthropicAdapter,
    MockAdapter,
    ModelResponse,
    OllamaAdapter,
    OpenAICompatAdapter,
    ToolCall,
    ToolDef,
)

TOOLS = [ToolDef("read_file", "Read a file", {"type": "object", "properties": {"path": {"type": "string"}}})]
MSGS = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "do it"},
]


# ------------------------------------------------------------------- mock
def test_mock_adapter_plays_script():
    m = MockAdapter([ModelResponse("done")])
    r = m.complete(MSGS, TOOLS)
    assert r.text == "done" and m.calls[0][1]["content"] == "do it"


def test_mock_adapter_can_simulate_crash():
    import pytest
    m = MockAdapter([ConnectionError("net down")])
    with pytest.raises(ConnectionError):
        m.complete(MSGS, TOOLS)


# ---------------------------------------------------------- openai-compat
def test_openai_request_shape():
    a = OpenAICompatAdapter("https://gw.corp/v1", "sk-cust", "gpt-x")
    url, headers, body = a.build_request(MSGS, TOOLS)
    assert url == "https://gw.corp/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-cust"
    assert body["model"] == "gpt-x"
    assert body["tools"][0]["function"]["name"] == "read_file"
    assert body["messages"] == MSGS


def test_openai_parse_tool_call():
    a = OpenAICompatAdapter("https://gw", "k", "m")
    payload = {
        "choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "c1", "function": {"name": "read_file",
                                                      "arguments": '{"path": "a.txt"}'}}],
        }}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    r = a.parse_response(payload)
    assert r.tool_calls == (ToolCall("c1", "read_file", {"path": "a.txt"}),)
    assert r.input_tokens == 10 and r.output_tokens == 5


def test_openai_parse_plain_text():
    a = OpenAICompatAdapter("https://gw", "k", "m")
    r = a.parse_response({"choices": [{"message": {"content": "hi"}}]})
    assert r.text == "hi" and r.tool_calls == ()


# ----------------------------------------------------------------- ollama
def test_ollama_request_shape_no_credential():
    a = OllamaAdapter(model="qwen3")
    url, headers, body = a.build_request(MSGS, TOOLS)
    assert url == "http://127.0.0.1:11434/api/chat"
    assert "Authorization" not in headers
    assert body["stream"] is False and body["model"] == "qwen3"
    assert body["tools"][0]["function"]["name"] == "read_file"


def test_ollama_parse_tool_call():
    a = OllamaAdapter()
    payload = {"message": {"content": "", "tool_calls": [
        {"function": {"name": "read_file", "arguments": {"path": "a.txt"}}}]},
        "prompt_eval_count": 7, "eval_count": 3}
    r = a.parse_response(payload)
    assert r.tool_calls[0].name == "read_file"
    assert r.tool_calls[0].arguments == {"path": "a.txt"}
    assert r.input_tokens == 7


# -------------------------------------------------------------- anthropic
def test_anthropic_request_shape_system_lifted():
    a = AnthropicAdapter("k-cust", "claude-x", base_url="https://gw.corp")
    url, headers, body = a.build_request(MSGS, TOOLS)
    assert url == "https://gw.corp/v1/messages"
    assert headers["x-api-key"] == "k-cust"
    assert body["system"] == "sys"
    assert all(m["role"] != "system" for m in body["messages"])
    assert body["tools"][0]["input_schema"] == TOOLS[0].parameters


def test_anthropic_parse_mixed_content():
    a = AnthropicAdapter("k", "m")
    payload = {"content": [
        {"type": "text", "text": "Working. "},
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}},
    ], "usage": {"input_tokens": 12, "output_tokens": 4}}
    r = a.parse_response(payload)
    assert r.text == "Working. "
    assert r.tool_calls == (ToolCall("t1", "read_file", {"path": "a"}),)


def test_all_adapters_share_one_internal_schema():
    """The kernel-facing contract: every adapter yields ModelResponse with
    the same fields — this is what makes packages portable across models."""
    oc = OpenAICompatAdapter("https://g", "k", "m").parse_response(
        {"choices": [{"message": {"content": "x"}}]})
    ol = OllamaAdapter().parse_response({"message": {"content": "x"}})
    an = AnthropicAdapter("k", "m").parse_response(
        {"content": [{"type": "text", "text": "x"}]})
    for r in (oc, ol, an):
        assert isinstance(r, ModelResponse) and r.text == "x" and r.tool_calls == ()
