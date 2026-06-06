from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GraphEntitySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = "kb_default"
    query: str = Field(min_length=1)
    entity_types: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=20)
    include_aliases: bool = True


class GraphExpandEntityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = "kb_default"
    depth: int = Field(default=1, ge=1)
    relationship_allowlist: list[str] = Field(default_factory=list)
    limit: int = Field(default=30, ge=1, le=50)
    include_evidence: bool = True


class GraphFindRelationshipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = "kb_default"
    source_entity: str = Field(min_length=1)
    target_entity: str = Field(min_length=1)
    relationship_allowlist: list[str] = Field(default_factory=list)
    include_evidence: bool = True


class GraphFindPathsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = "kb_default"
    source_entity: str = Field(min_length=1)
    target_entity: str = Field(min_length=1)
    max_depth: int = Field(default=2, ge=1)
    relationship_allowlist: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=20)


class GraphGetEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = "kb_default"
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    include_chunk_text: bool = True
    max_chars_per_chunk: int = Field(default=1200, ge=1, le=4000)


class GraphReadonlyCypherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=50, ge=1, le=200)


class GraphRagSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str = "kb_default"
    query: str = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=50, ge=1, le=100)
    final_top_k: int = Field(default=10, ge=1, le=20)
    graph_depth: int = Field(default=2, ge=1)
    relationship_allowlist: list[str] = Field(default_factory=list)
    include_sources: bool = True


class GraphBuildResponse(BaseModel):
    ok: bool = True
    workspace_id: str
    knowledge_base_id: str
    doc_id: str
    job_id: str
    result: dict[str, Any]
