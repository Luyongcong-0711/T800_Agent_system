from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RiskLevel = Literal["low", "medium", "high", "critical"]
SkillStatus = Literal["enabled", "disabled"]
ProposalStatus = Literal["pending_approval", "materialized"]
EntrypointType = Literal["prompt_workflow", "script"]


class SkillPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_read: list[str] = Field(default_factory=list)
    file_write: list[str] = Field(default_factory=list)
    database_read: list[str] = Field(default_factory=list)
    database_write: list[str] = Field(default_factory=list)
    network: bool = False


class SkillSource(BaseModel):
    model_config = ConfigDict(extra="allow")

    thread_id: str | None = None
    message_ids: list[str] = Field(default_factory=list)
    run_id: str | None = None


class SkillEntrypointSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    type: EntrypointType = "prompt_workflow"
    runtime: str | None = None
    args_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = "low"
    script_required: bool = False
    script_content: str | None = Field(default=None, max_length=20000)
    sandbox_profile: str | None = None
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    write_mode: Literal["none", "staged_patch"] = "none"
    file_write: list[str] = Field(default_factory=list)


class SkillProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    when_to_use: list[str] = Field(default_factory=list)
    workflow_steps: list[str] = Field(min_length=1)
    knowledge_notes: list[str] = Field(default_factory=list)
    entrypoints: list[SkillEntrypointSpec] = Field(default_factory=list)
    scripts: list[dict[str, Any]] = Field(default_factory=list)
    permissions: SkillPermissions = Field(default_factory=SkillPermissions)
    script_required: bool = False
    source: SkillSource = Field(default_factory=SkillSource)


class SkillCreateFromProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    skill_id: str | None = Field(default=None, min_length=1, max_length=120)
    version: str = Field(default="0.1.0", min_length=1, max_length=40)


class SkillActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)
    version: str | None = None


class SkillDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class SkillValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str | None = None


class SkillProposalResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    proposal_id: str
    workspace_id: str
    display_name: str
    description: str
    when_to_use: list[str]
    workflow_steps: list[str]
    knowledge_notes: list[str]
    entrypoints: list[dict[str, Any]]
    permissions: dict[str, Any]
    source: dict[str, Any]
    script_required: bool
    risk_level: RiskLevel
    approval_required: bool
    approval_id: str
    status: ProposalStatus
    created_at: str
    updated_at: str


class SkillSummary(BaseModel):
    skill_id: str
    workspace_id: str
    display_name: str
    version: str
    description: str
    when_to_use: list[str] = Field(default_factory=list)
    entrypoint_count: int = 0
    risk_level: RiskLevel = "low"
    status: SkillStatus = "enabled"
    enabled: bool = True
    requires_activation: bool = True
    requires_validation: bool = False
    updated_at: str


class SkillDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    skill_id: str
    workspace_id: str
    version: str
    display_name: str
    description: str
    summary: str
    workflow_summary: list[str]
    knowledge_sections: list[dict[str, Any]]
    entrypoints: list[dict[str, Any]]
    permissions: dict[str, Any]
    status: SkillStatus
    enabled: bool
    risk_level: RiskLevel
    requires_activation: bool
    requires_validation: bool
    validation_status: str
    created_at: str
    updated_at: str


class ListSkillsResponse(BaseModel):
    workspace_id: str
    skills: list[SkillSummary]


class SkillSearchResponse(BaseModel):
    workspace_id: str
    items: list[SkillSummary]


class SkillActivationResponse(BaseModel):
    schema_version: int = 1
    workspace_id: str
    run_id: str
    thread_id: str
    skill_id: str
    version: str
    reason: str
    activated_entrypoint_tools: list[str]
    context_block_object_key: str
    created_at: str
