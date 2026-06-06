from __future__ import annotations

from typing import Any, Literal, TypedDict

from langchain_core.messages import BaseMessage

RuntimeStatus = Literal[
    "created",
    "model_called",
    "tools_completed",
    "waiting_approval",
    "completed",
    "failed",
]


class ContextUsage(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_estimated: bool


class PendingToolCall(TypedDict):
    tool_call_id: str
    name: str
    args: dict[str, Any]


class RuntimeToolResult(TypedDict):
    tool_call_id: str
    name: str
    ok: bool
    content: dict[str, Any]
    error_type: str | None


class AgentState(TypedDict, total=False):
    run_id: str
    trace_id: str
    thread_id: str
    workspace_id: str
    user_id: str
    role: str
    messages: list[BaseMessage]
    tool_results: list[RuntimeToolResult]
    status: RuntimeStatus
    context_usage: ContextUsage
    memory_snapshot: dict[str, Any]
    skill_context: dict[str, Any]
    compaction: dict[str, Any] | None
    warnings: list[str]
    context_window_tokens: int
    max_output_tokens: int
    requires_approval: bool
    pending_tool_calls: list[PendingToolCall]
    tool_iteration_count: int
    max_tool_iterations: int
    model_error: str | None
    context_overflow_retried: bool
