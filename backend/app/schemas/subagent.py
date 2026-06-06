from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SubAgentMode = Literal["readonly", "write"]
SubAgentStatus = Literal["created", "queued", "running", "completed", "failed", "reviewed"]
ReviewDecision = Literal["accepted", "rejected", "needs_revision"]


class SubAgentTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_run_id: str | None = Field(default=None, min_length=1)
    parent_thread_id: str | None = None
    agent_type: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=1, max_length=4000)
    mode: SubAgentMode = "readonly"
    read_scope: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=300000, ge=1000, le=3600000)
    token_budget: int = Field(default=12000, ge=1000, le=200000)
    expected_output: str = Field(default="", max_length=4000)


class SubAgentFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str = Field(default="info", max_length=40)
    title: str = Field(min_length=1, max_length=240)
    evidence: str = Field(default="", max_length=2000)
    recommendation: str = Field(default="", max_length=2000)


class SubAgentCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["completed", "failed"] = "completed"
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[SubAgentFinding] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    created_job_id: str | None = None
    error_type: str | None = None


class SubAgentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision: ReviewDecision | None = None
    accepted: bool | None = None
    reviewer: str | None = None
    reviewer_notes: str = Field(default="", max_length=4000)
    review_notes: str | None = Field(default=None, max_length=4000)


class SubAgentTaskSummary(BaseModel):
    task_id: str
    workspace_id: str
    parent_run_id: str
    parent_thread_id: str | None = None
    agent_type: str
    objective: str
    mode: SubAgentMode
    read_scope: list[str]
    write_scope: list[str]
    allowed_tools: list[str]
    forbidden_tools: list[str]
    timeout_ms: int
    token_budget: int
    status: SubAgentStatus
    needs_main_review: bool = True
    requires_main_review: bool = True
    output_schema: str = "SubAgentResult"
    created_at: str
    updated_at: str


class SubAgentTaskDetail(SubAgentTaskSummary):
    schema_version: int = 1
    expected_output: str = ""
    result: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    object_keys: dict[str, str] = Field(default_factory=dict)


class ListSubAgentTasksResponse(BaseModel):
    workspace_id: str
    tasks: list[SubAgentTaskSummary]


class SubAgentResultResponse(BaseModel):
    schema_version: int = 1
    task_id: str
    workspace_id: str
    parent_run_id: str
    agent_type: str
    status: Literal["completed", "failed"]
    summary: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    created_job_id: str | None = None
    error_type: str | None = None
    execution: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    needs_main_review: bool = True
    can_directly_finalize: bool = False
    created_at: str


class SubAgentReviewResponse(BaseModel):
    schema_version: int = 1
    task_id: str
    workspace_id: str
    parent_run_id: str
    decision: ReviewDecision
    review_status: str
    reviewer_notes: str
    reviewed_subagent_result: dict[str, Any]
    reviewed_at: str
