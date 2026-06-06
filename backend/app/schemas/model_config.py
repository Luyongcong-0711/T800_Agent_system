from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ModelConfigId = Literal[
    "main_chat",
    "graphrag_llm",
    "embedding",
    "rerank",
    "compression",
    "fallback",
]
PublicModelProvider = Literal["openai_compatible", "anthropic"]
ModelConfigPurpose = Literal["chat", "embedding", "rerank", "compression", "fallback"]
ModelConfigStatus = Literal["configured", "missing_secret", "disabled"]
ModelConfigSource = Literal["stored", "default_env"]


class ModelConfigResponse(BaseModel):
    schema_version: int = 1
    workspace_id: str
    config_id: ModelConfigId
    display_name: str
    purpose: ModelConfigPurpose
    provider: PublicModelProvider
    model: str
    base_url: str | None = None
    api_key_ref: str | None = None
    context_window_tokens: int = Field(default=200000, ge=1)
    max_output_tokens: int = Field(default=8192, ge=1)
    timeout_ms: int = Field(default=60000, ge=1000)
    supports_tool_calling: bool = True
    enabled: bool = True
    status: ModelConfigStatus
    source: ModelConfigSource
    updated_at: str
    revision: int = 1


class ListModelConfigsResponse(BaseModel):
    workspace_id: str
    configs: list[ModelConfigResponse]


class UpdateModelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: PublicModelProvider
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    api_key_ref: str | None = Field(default=None, min_length=1, max_length=200)
    context_window_tokens: int = Field(default=200000, ge=1)
    max_output_tokens: int = Field(default=8192, ge=1)
    timeout_ms: int = Field(default=60000, ge=1000)
    supports_tool_calling: bool = True
    enabled: bool = True

    @model_validator(mode="after")
    def validate_token_budget(self) -> UpdateModelConfigRequest:
        if self.max_output_tokens > self.context_window_tokens:
            raise ValueError("max_output_tokens must not exceed context_window_tokens.")
        return self


class TestModelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(default="Reply with pong.", min_length=1, max_length=2000)
    max_output_tokens: int = Field(default=32, ge=1, le=8192)
    config: UpdateModelConfigRequest | None = None


class TestModelConfigResponse(BaseModel):
    workspace_id: str
    config_id: ModelConfigId
    ok: bool
    provider: PublicModelProvider
    model: str
    latency_ms: int
    content_preview: str | None = None
    usage: dict[str, int | bool] | None = None
    error_type: str | None = None
    retryable: bool = False
    redacted: bool = True
