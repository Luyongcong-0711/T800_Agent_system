from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal[
    "created",
    "queued",
    "running",
    "waiting_retry",
    "succeeded",
    "partial_success",
    "failed",
    "cancelled",
    "unknown_outcome",
    "recovering",
]


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: str = Field(min_length=1, max_length=120)
    priority: Literal["low", "normal", "high"] = "normal"
    title: str | None = Field(default=None, max_length=180)
    target_scope: dict[str, str] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)
    related_run_id: str | None = None
    related_thread_id: str | None = None
    trace_id: str | None = Field(default=None, max_length=160)


class RetryJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)
    trace_id: str | None = Field(default=None, max_length=160)


class JobSummary(BaseModel):
    job_id: str
    workspace_id: str
    job_type: str
    status: JobStatus
    priority: str
    title: str
    target_scope: dict[str, str] = Field(default_factory=dict)
    target_scope_key: str | None = None
    progress_percent: float = 0
    current_stage: str | None = None
    idempotency_key: str
    related_run_id: str | None = None
    related_thread_id: str | None = None
    last_event_id: str | None = None
    last_event_seq: int = 0
    manifest_object_key: str | None = None
    event_index_object_key: str | None = None
    leaf_state_object_key: str | None = None
    created_by: str | None = None
    created_at: str
    updated_at: str


class JobDetailResponse(JobSummary):
    manifest: dict[str, Any]
    leaf_state: dict[str, Any]


class ListJobsResponse(BaseModel):
    workspace_id: str
    jobs: list[JobSummary]
    next_cursor: str | None = None


class JobEvent(BaseModel):
    schema_version: int = 1
    event_seq: int
    event_id: str
    workspace_id: str
    job_id: str
    type: str
    created_at: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ListJobEventsResponse(BaseModel):
    workspace_id: str
    job_id: str
    events: list[JobEvent]
    next_after_event_id: str | None = None
    job_status: JobStatus


class CancelJobResponse(BaseModel):
    job_id: str
    workspace_id: str
    status: JobStatus
