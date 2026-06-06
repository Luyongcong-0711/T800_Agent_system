from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ThreadStatus = Literal["active", "archived", "soft_deleted"]
MessageRole = Literal["user", "assistant", "system", "tool"]
RunStatus = Literal["created", "running", "waiting_approval", "completed", "failed", "cancelled"]


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=160)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)


class PatchThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=160)
    pinned: bool | None = None
    status: ThreadStatus | None = None


class ThreadSummary(BaseModel):
    thread_id: str
    workspace_id: str
    user_id: str
    title: str
    status: ThreadStatus
    pinned: bool = False
    current_run_id: str | None = None
    current_run_status: RunStatus | None = None
    last_message_id: str | None = None
    last_message_preview: str | None = None
    last_message_at: str | None = None
    message_count: int = 0
    run_count: int = 0
    created_at: str
    updated_at: str


class ThreadDetailResponse(ThreadSummary):
    current_run: dict[str, Any] | None = None


class ListThreadsResponse(BaseModel):
    workspace_id: str
    threads: list[ThreadSummary]


class ConversationMessage(BaseModel):
    message_id: str
    workspace_id: str
    thread_id: str
    role: MessageRole
    content: str
    run_id: str | None = None
    created_at: str


class ListMessagesResponse(BaseModel):
    workspace_id: str
    thread_id: str
    messages: list[ConversationMessage]


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str = Field(min_length=1, max_length=20000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    trace_id: str | None = Field(default=None, min_length=1, max_length=160)
    stream: bool = False


class RunSummary(BaseModel):
    run_id: str
    workspace_id: str
    thread_id: str
    status: RunStatus
    idempotency_key: str
    user_message_id: str | None = None
    last_event_id: str | None = None
    last_event_seq: int = 0
    assistant_message_id: str | None = None
    model_error: str | None = None
    trace_id: str | None = None
    created_at: str
    updated_at: str


class RunDetailResponse(RunSummary):
    leaf_state: dict[str, Any]


class RunEvent(BaseModel):
    event_seq: int
    event_id: str
    workspace_id: str
    thread_id: str
    run_id: str
    trace_id: str | None = None
    type: str
    created_at: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ListRunEventsResponse(BaseModel):
    workspace_id: str
    run_id: str
    events: list[RunEvent]
    next_after_event_id: str | None = None
    run_status: RunStatus


class CancelRunResponse(BaseModel):
    run_id: str
    workspace_id: str
    thread_id: str
    status: RunStatus


class RunApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)


class RunApprovalDecisionResponse(BaseModel):
    run_id: str
    workspace_id: str
    thread_id: str
    approval_id: str
    decision: Literal["approved", "rejected"]
    status: str
    run_status: RunStatus
    operation_plan_object_key: str
    skill_run_id: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)
    updated_at: str


class RunOperationRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rollback_token: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)


class RunOperationRollbackResponse(BaseModel):
    run_id: str
    workspace_id: str
    thread_id: str
    operation_id: str
    rollback_token: str
    status: str
    restored_files: list[dict[str, Any]] = Field(default_factory=list)
    event_id: str | None = None
    updated_at: str
