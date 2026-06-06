from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RuntimeStatus = Literal[
    "created",
    "model_called",
    "tools_completed",
    "waiting_approval",
    "completed",
    "failed",
]


class RuntimeSmokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str = Field(
        default="Run the runtime smoke graph.",
        min_length=1,
        max_length=4000,
    )
    thread_id: str | None = None


class RuntimeToolResultResponse(BaseModel):
    tool_call_id: str
    name: str
    ok: bool
    content: dict[str, Any]
    error_type: str | None = None


class RuntimeSmokeResponse(BaseModel):
    run_id: str
    thread_id: str
    workspace_id: str
    status: RuntimeStatus
    model_error: str | None = None
    requires_approval: bool
    context_usage: dict[str, Any]
    memory_snapshot: dict[str, Any] = Field(default_factory=dict)
    compaction: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    messages: list[dict[str, Any]]
    tool_results: list[RuntimeToolResultResponse]
    tool_specs: list[dict[str, Any]]
