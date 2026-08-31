"""Build a gateway adapter from a config model entry + vault secret.

Shared by `scr run` and `scr model test`. The secret is fetched from the vault
by the entry's `secret_ref`; it is never read from config or logged.
"""
from __future__ import annotations

from typing import Optional

from .adapters_cloud import AzureOpenAIAdapter, BedrockAdapter
from .gateway import (
    Adapter,
    AnthropicAdapter,
    OllamaAdapter,
    OpenAICompatAdapter,
)


class ModelConfigError(Exception):
    pass


def build_adapter(model_cfg: dict, secret: Optional[str]) -> Adapter:
    kind = model_cfg.get("adapter", "")
    model = model_cfg.get("model", "")
    base_url = model_cfg.get("base_url", "")
    if kind == "ollama":
        return OllamaAdapter(base_url=base_url or "http://127.0.0.1:11434", model=model)
    if kind == "openai-compat":
        if not secret:
            raise ModelConfigError("openai-compat requires a stored secret")
        return OpenAICompatAdapter(base_url or "https://api.openai.com/v1", secret, model)
    if kind == "anthropic":
        if not secret:
            raise ModelConfigError("anthropic requires a stored secret")
        return AnthropicAdapter(secret, model,
                                base_url=base_url or "https://api.anthropic.com")
    if kind == "azure":
        if not secret:
            raise ModelConfigError("azure requires a stored secret")
        return AzureOpenAIAdapter(base_url, secret, model)
    if kind == "bedrock":
        # secret_ref holds "access_key:secret_key:region" for the CLI path.
        parts = (secret or "").split(":")
        if len(parts) < 3:
            raise ModelConfigError("bedrock secret must be 'access:secret:region'")
        return BedrockAdapter(parts[0], parts[1], parts[2], model)
    raise ModelConfigError(f"unknown adapter kind: {kind!r}")
