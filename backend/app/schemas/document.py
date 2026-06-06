from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateKnowledgeBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = Field(default="kb_default", min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=180)


class KnowledgeBaseResponse(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    name: str
    status: str = "active"
    manifest_object_key: str | None = None
    updated_at: str


class ListKnowledgeBasesResponse(BaseModel):
    workspace_id: str
    knowledge_bases: list[KnowledgeBaseResponse]


class UploadDocumentJsonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = Field(default="kb_default", min_length=1, max_length=128)
    source_file_name: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1)
    mime_type: str | None = Field(default="text/plain", max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)


class EmbeddingReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    dimension: int | None = Field(default=None, ge=1, le=65536)
    collection: str | None = Field(default=None, min_length=1, max_length=180)
    config_id: str | None = Field(default=None, max_length=120)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)


class EmbeddingReindexResponse(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    job_id: str
    job_type: str = "embedding_reindex_job"
    job_status: str
    active_embedding: dict[str, Any]


class ActiveEmbeddingResponse(BaseModel):
    schema_version: int = 1
    workspace_id: str
    knowledge_base_id: str
    version_id: str
    provider: str
    model: str
    dimension: int
    collection: str
    status: str
    previous_version_id: str | None = None
    previous_collection: str | None = None
    chunk_count: int = 0
    active: bool = True
    manifest_object_key: str | None = None
    job_id: str | None = None
    updated_at: str | None = None
    revision: int | None = None


class DocumentResponse(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    doc_id: str
    doc_version_id: str
    current_doc_version_id: str | None = None
    source_file_name: str
    mime_type: str
    size_bytes: int | None = None
    file_sha256: str | None = None
    title: str | None = None
    parser_quality: str | None = None
    ingestion_status: str
    parse_status: str | None = None
    chunk_status: str | None = None
    embedding_status: str | None = None
    graph_status: str | None = None
    chunk_total: int = 0
    chunk_embedded: int = 0
    chunk_failed: int = 0
    search_available: bool = False
    graphrag_available: bool = False
    retryable: bool = False
    failure_strategy: str | None = None
    last_error: dict[str, Any] | None = None
    last_job_id: str | None = None
    job_id: str | None = None
    job_type: str | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    object_keys: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


class ListDocumentsResponse(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    documents: list[DocumentResponse]
    next_cursor: str | None = None


class ChunkResponse(BaseModel):
    workspace_id: str | None = None
    knowledge_base_id: str | None = None
    doc_id: str
    doc_version_id: str | None = None
    chunk_id: str
    parent_chunk_id: str | None = None
    chunk_index: int | None = None
    chunk_type: str | None = None
    text: str
    section_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    source_block_ids: list[str] = Field(default_factory=list)
    token_count: int | None = None
    text_hash: str | None = None
    chunk_status: str | None = None
    search_index_status: str | None = None
    embedding_status: str | None = None
    graph_status: str | None = None
    retryable: bool = False
    last_error: dict[str, Any] | None = None
    metadata_filter: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    object_key: str | None = None


class ListChunksResponse(BaseModel):
    workspace_id: str
    knowledge_base_id: str
    doc_id: str
    chunks: list[ChunkResponse]
