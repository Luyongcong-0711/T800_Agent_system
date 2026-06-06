from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryType = Literal[
    "user_profile",
    "user_preference",
    "project_fact",
    "project_rule",
    "tool_usage_preference",
    "correction",
    "safety_boundary",
    "relationship_fact",
]
MemoryScope = Literal["global", "workspace"]
MemoryStatus = Literal["active", "disabled", "deleted", "pending_approval", "rejected"]


class MemorySource(BaseModel):
    model_config = ConfigDict(extra="allow")

    thread_id: str | None = None
    message_id: str | None = None
    run_id: str | None = None
    evidence: str | None = Field(default=None, max_length=2000)


class MemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    memory_id: str
    workspace_id: str | None = None
    user_id: str
    scope: MemoryScope
    type: MemoryType
    field: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=4000)
    summary: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=8000)
    content_object_key: str
    visibility: Literal["user_visible"] = "user_visible"
    status: MemoryStatus = "active"
    sensitive: bool = False
    source: MemorySource = Field(default_factory=MemorySource)
    confidence: float = Field(default=1.0, ge=0, le=1)
    enabled_for_model_context: bool = True
    frontend_visible: bool = True
    requires_approval: bool = False
    created_at: str
    updated_at: str
    deleted_at: str | None = None
    revision: int = 1


class UpsertMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str | None = Field(default=None, min_length=1, max_length=160)
    scope: MemoryScope | None = None
    type: MemoryType
    field: str | None = Field(default=None, max_length=120)
    value: str | None = Field(default=None, max_length=4000)
    summary: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=8000)
    source: MemorySource = Field(default_factory=MemorySource)
    confidence: float = Field(default=1.0, ge=0, le=1)
    enabled_for_model_context: bool = True


class PatchMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    content: str | None = Field(default=None, min_length=1, max_length=8000)
    field: str | None = Field(default=None, max_length=120)
    scope: MemoryScope | None = None
    value: str | None = Field(default=None, max_length=4000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    enabled_for_model_context: bool | None = None
    status: Literal["active", "disabled"] | None = None


class MemorySummary(BaseModel):
    memory_id: str
    workspace_id: str | None = None
    user_id: str
    scope: MemoryScope
    type: MemoryType
    field: str | None = None
    summary: str
    sensitive: bool = False
    status: MemoryStatus
    enabled_for_model_context: bool
    frontend_visible: bool
    requires_approval: bool
    confidence: float
    created_at: str
    updated_at: str


class ListMemoriesResponse(BaseModel):
    workspace_id: str
    memories: list[MemorySummary]


class MemoryDetailResponse(MemoryRecord):
    pass


class MemorySearchResponse(BaseModel):
    workspace_id: str
    hits: list[dict[str, Any]]


class MemorySyncStateResponse(BaseModel):
    schema_version: int = 1
    workspace_id: str
    pending_targets: list[dict[str, Any]] = Field(default_factory=list)
    last_event_id: str | None = None
    last_event_seq: int = 0
    last_enqueue: dict[str, Any] | None = None
    last_processed_at: str | None = None
    last_result: dict[str, Any] | None = None
    updated_at: str | None = None
    revision: int = 0


class MemorySnapshotResponse(BaseModel):
    schema_version: int = 1
    memory_snapshot_id: str
    workspace_id: str
    user_id: str
    thread_id: str
    included_memory_ids: list[str]
    profile: dict[str, Any] = Field(default_factory=dict)
    preferences: list[str] = Field(default_factory=list)
    project_facts: list[str] = Field(default_factory=list)
    project_rules: list[str] = Field(default_factory=list)
    created_at: str
