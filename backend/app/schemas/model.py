from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ModelProvider = Literal["openai_compatible", "anthropic", "fake"]
ModelErrorType = Literal[
    "auth_failed",
    "model_not_found",
    "context_overflow",
    "rate_limit",
    "provider_5xx",
    "timeout",
    "connection_lost",
    "stream_ended_before_terminal",
    "invalid_request",
    "unsupported_feature",
    "unknown",
]
ModelStreamEventType = Literal[
    "message_start",
    "content_delta",
    "tool_call_start",
    "tool_call_delta",
    "tool_call_completed",
    "usage_delta",
    "message_completed",
    "provider_error",
    "stream_closed",
]


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_id: str = "default"
    provider: ModelProvider = "fake"
    model: str = "fake-runtime-smoke"
    base_url: str | None = None
    api_key_ref: str | None = None
    context_window_tokens: int = Field(default=200000, ge=1)
    max_output_tokens: int = Field(default=8192, ge=1)
    timeout_ms: int = Field(default=60000, ge=1000)
    supports_tool_calling: bool = True


class ModelToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ModelMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    messages: list[ModelMessage]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    max_output_tokens: int


class ModelUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    usage_estimated: bool = False


class ToolCallDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = 0
    tool_call_id: str | None = None
    name: str | None = None
    args_delta: str = ""
    args: dict[str, Any] | None = None


class ModelStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ModelStreamEventType
    request_id: str
    delta: str = ""
    tool_call_delta: ToolCallDelta | None = None
    finish_reason: str | None = None
    usage: ModelUsage | None = None
    raw_provider: str | None = None


class ModelResult(BaseModel):
    content: str
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    raw_provider: str | None = None


class ModelError(Exception):
    def __init__(
        self,
        error_type: ModelErrorType,
        message: str,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        self.error_type = error_type
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)
