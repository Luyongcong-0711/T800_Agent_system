from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LogQueryResponse(BaseModel):
    workspace_id: str
    items: list[dict[str, Any]]
    next_cursor: str | None = None
    truncated: bool = False
    redacted: bool = True


class LogSummaryResponse(BaseModel):
    workspace_id: str
    items: list[str]
    next_cursor: str | None = None
    truncated: bool = False
    redacted: bool = True


class LogArtifactResponse(BaseModel):
    workspace_id: str
    object_key: str
    file_name: str
    content_type: str
    artifact_type: str
    size_bytes: int
    sha256: str
    text: str | None = None
    parsed_json: Any | None = None
    base64: str | None = None
    truncated: bool = False
    redacted: bool = True


class CreateDiagnosticBundleRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    trace_id: str | None = Field(default=None, max_length=160)
    run_id: str | None = Field(default=None, max_length=160)
    component: str | None = Field(default=None, max_length=120)
    components: list[str] = Field(default_factory=list)
    include_summary: bool = True
    include_errors: bool = True
    include_component_logs: bool = True
    notes: str | None = Field(default=None, max_length=4000)
    limit: int = Field(default=100, ge=1, le=1000)
    request_id: str | None = Field(default=None, max_length=160)


class CreateLogArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str | None = Field(default=None, max_length=32)
    runtime_instance_id: str | None = Field(default=None, max_length=120)
    request_id: str | None = Field(default=None, max_length=160)


class DiagnosticBundleResponse(BaseModel):
    schema_version: int = 1
    bundle_id: str
    workspace_id: str
    created_by: str
    created_at: str
    runtime_instance_id: str
    filters: dict[str, Any]
    manifest_object_key: str | None = None
    object_key: str | None = None
    package_object_key: str | None = None
    package_sha256: str | None = None
    package_bytes: int | None = None
    related_job_id: str | None = None
    job_id: str
    job_status: str
    item_counts: dict[str, int] = Field(default_factory=dict)
    redacted: bool = True


class LogArchiveJobResponse(BaseModel):
    schema_version: int = 1
    workspace_id: str
    date: str
    runtime_instance_id: str
    manifest_object_key: str | None = None
    related_job_id: str
    job_id: str
    job_status: str
    redacted: bool = True
