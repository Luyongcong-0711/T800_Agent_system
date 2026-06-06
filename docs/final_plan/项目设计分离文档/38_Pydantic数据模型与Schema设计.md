# Pydantic 数据模型与 Schema 设计

状态：P0 开发前 Schema 总表  
更新时间：2026-05-30

## 定位

本文件定义后端 Pydantic v2 schema 的命名、分层和核心字段。实现时可继续拆分到 `backend/app/schemas/*.py`。

## 通用模型

```python
from pydantic import BaseModel, Field
from typing import Any, Literal


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error_type: str
    message_for_user: str
    retryable: bool = False
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PageRequest(BaseModel):
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class PageResponse(BaseModel):
    items: list[Any]
    next_cursor: str | None = None
```

## Identity

```python
class RuntimeIdentity(BaseModel):
    user_id: str = "default_user"
    role: Literal["owner", "admin", "editor", "viewer"] = "owner"
    workspace_id: str = "default"
    workspace_role: Literal["owner", "admin", "editor", "viewer"] = "owner"
```

## ToolResult

```python
class ToolResult(BaseModel):
    ok: bool
    tool_name: str
    tool_call_id: str | None = None
    data: dict[str, Any] | None = None
    error_type: str | None = None
    message_for_user: str | None = None
    retryable: bool = False
    requires_approval: bool = False
    approval_id: str | None = None
    trace_id: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
```

## Model

```python
class ModelConfig(BaseModel):
    config_id: str
    provider: Literal["openai_compatible", "anthropic"]
    base_url: str | None = None
    api_key_ref: str
    model: str
    context_window_tokens: int = 200000
    max_output_tokens: int = 8192
    timeout_ms: int = 60000
    supports_streaming: bool = True
    supports_tool_calling: bool = True
    supports_parallel_tool_calls: bool = False
    supports_vision: bool = False
    json_mode: bool = False


class ModelRequest(BaseModel):
    request_id: str
    model_config_id: str
    provider: str
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: str | dict[str, Any] = "auto"
    max_output_tokens: int = 8192
    stream: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_estimated: bool = False
```

## Thread / Run / Event

```python
class ThreadManifest(BaseModel):
    thread_id: str
    workspace_id: str
    user_id: str
    title: str
    status: Literal["active", "archived", "soft_deleted"] = "active"
    created_at: str
    updated_at: str
    current_leaf_run_id: str | None = None


class RunManifest(BaseModel):
    run_id: str
    thread_id: str
    workspace_id: str
    user_id: str
    status: Literal["created", "running", "waiting_approval", "succeeded", "failed", "cancelled"]
    trace_id: str
    idempotency_key: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RunEvent(BaseModel):
    event_id: str
    event_seq: int
    run_id: str
    thread_id: str
    workspace_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
```

## Job

```python
JobStatus = Literal[
    "created",
    "queued",
    "running",
    "waiting_retry",
    "succeeded",
    "failed",
    "cancelled",
    "unknown_outcome",
    "recovering",
]


class JobManifest(BaseModel):
    job_id: str
    workspace_id: str | None = None
    job_type: str
    status: JobStatus
    target_scope: dict[str, str] = Field(default_factory=dict)
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    owner_id: str | None = None
    fencing_token: str | None = None
    retry_of_job_id: str | None = None
    related_run_id: str | None = None
    trace_id: str
    created_at: str
    updated_at: str


class JobEvent(BaseModel):
    event_id: str
    event_seq: int
    job_id: str
    workspace_id: str | None = None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class JobSummary(BaseModel):
    job_id: str
    job_type: str
    status: JobStatus
    title: str
    progress_percent: float = 0
    current_stage: str | None = None
    target_scope: dict[str, str] = Field(default_factory=dict)
    updated_at: str
```

## Document / Chunk

```python
class DocumentManifest(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    doc_id: str
    doc_version_id: str
    source_file_name: str
    mime_type: str
    file_sha256: str
    parser_name: str | None = None
    parser_quality: Literal["full", "degraded", "failed"] | None = None
    ingestion_status: str
    chunk_total: int = 0
    chunk_embedded: int = 0
    chunk_failed: int = 0
    graph_status: str = "pending"
    search_available: bool = False
    graphrag_available: bool = False
    last_job_id: str | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class ChunkRecord(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    doc_id: str
    doc_version_id: str
    chunk_id: str
    parent_chunk_id: str | None = None
    chunk_index: int
    chunk_type: str
    text: str
    section_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    source_block_ids: list[str] = Field(default_factory=list)
    token_count: int
    text_hash: str
    metadata_filter: dict[str, Any] = Field(default_factory=dict)
```

## Graph

```python
class EntityRecord(BaseModel):
    entity_id: str
    workspace_id: str
    knowledge_base_id: str
    name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)


class RelationFactRecord(BaseModel):
    relation_fact_id: str
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["active", "disabled", "conflict"] = "active"
```

## Memory

```python
class MemoryRecord(BaseModel):
    memory_id: str
    workspace_id: str | None = None
    user_id: str
    scope: Literal["global", "workspace"]
    type: Literal["user_profile", "user_preference", "project_fact", "project_rule", "correction", "relationship_fact"]
    field: str | None = None
    value: str | None = None
    summary: str
    content: str
    source: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    enabled_for_model_context: bool = True
    frontend_visible: bool = True
    requires_approval: bool = False
    created_at: str
    updated_at: str


class MemorySnapshot(BaseModel):
    memory_snapshot_id: str
    workspace_id: str
    user_id: str
    thread_id: str
    included_memory_ids: list[str]
    profile: dict[str, Any] = Field(default_factory=dict)
    preferences: list[str] = Field(default_factory=list)
    project_memories: list[str] = Field(default_factory=list)
    created_at: str
```

## Skill

```python
class SkillEntrypoint(BaseModel):
    name: str
    type: Literal["prompt_workflow", "script"]
    runtime: str | None = None
    path: str | None = None
    args_schema: dict[str, Any]
    risk_level: Literal["low", "medium", "high", "critical"]
    sandbox_profile: str | None = None
    timeout_ms: int = 30000
    write_mode: Literal["none", "staged_patch"] = "none"


class SkillManifest(BaseModel):
    skill_id: str
    display_name: str
    version: str
    description: str
    owner: str = "user"
    enabled: bool = False
    entrypoints: list[SkillEntrypoint] = Field(default_factory=list)
    dependencies: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, Any] = Field(default_factory=dict)
    checksums: dict[str, str] = Field(default_factory=dict)


class SkillProposal(BaseModel):
    proposal_id: str
    workspace_id: str
    display_name: str
    description: str
    when_to_use: list[str]
    workflow_steps: list[str]
    entrypoints: list[dict[str, Any]]
    script_required: bool = False
    risk_level: str
    approval_id: str | None = None
```

## MCP

```python
class McpServerSummary(BaseModel):
    server_name: str
    transport: Literal["stdio", "streamable_http"]
    enabled: bool
    status: str
    last_seen: str | None = None
    tool_count: int = 0
    stale: bool = False


class McpToolSummary(BaseModel):
    server_name: str
    name: str
    normalized_name: str
    description: str
    input_schema_hash: str
    enabled: bool
    risk_level: str
    name_conflict: bool = False
```

## Secret

```python
class SecretRecord(BaseModel):
    secret_id: str
    workspace_id: str
    type: str
    display_name: str
    status: Literal["active", "disabled", "deleted"]
    masked: str
    key_version: str
    ciphertext: str
    nonce: str
    tag: str
    created_at: str
    updated_at: str
```

## Schema 开发规则

- Pydantic schema 不直接依赖数据库 SDK 类型。
- 所有外部响应先转内部 schema，再写事件或返回前端。
- schema 中的时间第一版统一 ISO string，后续可替换为 aware datetime。
- 所有 ID 生成集中在 `core/ids.py`。
- `workspace_id`、`user_id`、`trace_id` 必须贯穿所有可审计对象。

