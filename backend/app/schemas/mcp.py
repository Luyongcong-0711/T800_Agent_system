from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

McpTransport = Literal["stdio", "http", "streamable_http", "sse"]
McpServerStatus = Literal[
    "configured",
    "starting",
    "initializing",
    "connected",
    "stopped",
    "failed",
    "restarting",
    "disconnected",
    "auth_failed",
    "tool_list_failed",
]
McpRiskLevel = Literal["low", "medium", "high", "critical"]
McpToolDisabledReason = Literal[
    "server_disabled",
    "tool_disabled_by_user",
    "name_conflict",
    "schema_changed",
    "unsafe_schema",
]


class McpServerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workspace_id: str
    server_name: str
    transport: McpTransport
    enabled: bool = True
    status: McpServerStatus = "configured"
    config_version: int = 1
    scope: Literal["workspace", "system"] = "workspace"
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    secret_env_refs: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    public_headers: dict[str, str] = Field(default_factory=dict)
    headers_ref: str | None = None
    auth_type: str | None = None
    oauth_credential_ref: str | None = None
    server_info: dict[str, Any] = Field(default_factory=dict)
    last_seen: str | None = None
    last_snapshot_hash: str | None = None
    created_at: str
    updated_at: str
    revision: int = 1


class McpServerSummary(BaseModel):
    server_name: str
    transport: McpTransport
    enabled: bool
    status: str
    last_seen: str | None = None
    tool_count: int = 0
    stale: bool = False
    last_snapshot_hash: str | None = None
    updated_at: str | None = None


class McpRawToolDescriptor(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class McpCapabilityTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_name: str
    name: str
    normalized_name: str
    original_tool_name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    input_schema_hash: str
    enabled: bool
    risk_level: McpRiskLevel = "medium"
    requires_approval: bool = False
    side_effect: bool = False
    name_conflict: bool = False
    schema_changed: bool = False
    disabled_reason: McpToolDisabledReason | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class McpToolSummary(BaseModel):
    server_name: str
    name: str
    normalized_name: str
    description: str
    input_schema_hash: str
    enabled: bool
    risk_level: str
    name_conflict: bool = False
    schema_changed: bool = False
    disabled_reason: str | None = None


class McpResourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str
    name: str | None = None
    description: str = ""
    mime_type: str | None = None


class McpPromptSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    arguments_schema: dict[str, Any] = Field(default_factory=dict)


class McpCapabilitySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workspace_id: str
    server_name: str
    transport: McpTransport
    status: McpServerStatus
    server_enabled: bool = True
    stale: bool = False
    stale_reason: str | None = None
    runtime_configured: bool = False
    capability_source: Literal["mcp_runtime", "fallback_unconfigured"] = "mcp_runtime"
    snapshot_hash: str
    server_info: dict[str, Any] = Field(default_factory=dict)
    tools: list[McpCapabilityTool] = Field(default_factory=list)
    resources: list[McpResourceSummary] = Field(default_factory=list)
    prompts: list[McpPromptSummary] = Field(default_factory=list)
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    updated_at: str
    revision: int = 1


class McpToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workspace_id: str
    server_name: str
    tool_name: str
    normalized_name: str
    enabled: bool
    risk_level: McpRiskLevel = "medium"
    updated_by: str
    updated_at: str
    policy_version: int = 1
    input_schema_hash: str | None = None
    disabled_reason: McpToolDisabledReason | None = None


class McpServerDetail(BaseModel):
    manifest: McpServerManifest
    capability_snapshot: McpCapabilitySnapshot | None = None
    summary: McpServerSummary


class McpToolPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_name: str
    tool_name: str
    enabled: bool
    risk_level: McpRiskLevel = "medium"
    input_schema_hash: str | None = None


class McpToolPolicyUpdateResponse(BaseModel):
    workspace_id: str
    server_name: str
    tool_name: str
    model_name: str | None = None
    enabled: bool
    risk_level: McpRiskLevel | str
    updated_by: str
    updated_at: str
    policy_version: int


class McpRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_reason: str = Field(default="manual", min_length=1, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)
    capability_override: dict[str, Any] | None = None


class McpReconnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)


class McpServerConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: McpTransport = "stdio"
    enabled: bool = True
    scope: Literal["workspace", "system"] = "workspace"
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    command: str | None = Field(default=None, max_length=1000)
    args: list[str] = Field(default_factory=list, max_length=64)
    cwd: str | None = Field(default=None, max_length=1000)
    env: dict[str, str] = Field(default_factory=dict)
    secret_env_refs: dict[str, str] = Field(default_factory=dict)
    url: str | None = Field(default=None, max_length=2000)
    public_headers: dict[str, str] = Field(default_factory=dict)
    headers_ref: str | None = Field(default=None, max_length=240)
    auth_type: str | None = Field(default=None, max_length=80)
    oauth_credential_ref: str | None = Field(default=None, max_length=240)


class McpRefreshResponse(BaseModel):
    workspace_id: str
    server_name: str
    server: dict[str, Any]
    snapshot: dict[str, Any]
    refresh_job: dict[str, Any]
    job_id: str


class McpReconnectResponse(McpRefreshResponse):
    health: dict[str, Any]


class McpServerHealthResponse(BaseModel):
    workspace_id: str
    server_name: str
    enabled: bool
    transport: str
    status: str
    runtime_configured: bool
    connected: bool
    stale: bool
    tool_count: int = 0
    last_seen: str | None = None
    last_error: dict[str, Any] | None = None
    snapshot_hash: str | None = None
    snapshot_updated_at: str | None = None
    next_action: str
    live_probe: dict[str, Any] | None = None
    reconnect: dict[str, Any]


class McpServersResponse(BaseModel):
    workspace_id: str
    servers: list[dict[str, Any]]


class McpServerDetailResponse(BaseModel):
    workspace_id: str
    server_name: str
    server: dict[str, Any]
    snapshot: dict[str, Any] | None = None


class McpToolsResponse(BaseModel):
    workspace_id: str
    server_name: str
    tools: list[dict[str, Any]]


class ToolInventoryResponse(BaseModel):
    workspace_id: str
    tools: list[dict[str, Any]]
    created_at: str
