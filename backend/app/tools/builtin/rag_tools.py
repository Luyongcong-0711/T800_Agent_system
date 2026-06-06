from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.rag_pipeline.ingestion import DEFAULT_ACTIVE_EMBEDDING, DocumentIngestionService
from app.rag_pipeline.paths import document_chunk_key
from app.storage.object_store import JsonObjectStore


class RagSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    top_k: int = Field(default=50, ge=1, le=100)
    final_top_k: int = Field(default=10, ge=1, le=20)
    filters: dict[str, Any] = Field(default_factory=dict)
    max_chars_per_chunk: int = Field(default=1200, ge=1, le=4000)


class DocumentChunkGetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    doc_id: str | None = None
    chunk_id: str | None = None
    object_key: str | None = None
    max_chars: int = Field(default=1200, ge=1, le=4000)


class WorkspaceKnowledgeBaseStore:
    def __init__(self, document_service: DocumentIngestionService, workspace_id: str) -> None:
        self.document_service = document_service
        self.workspace_id = workspace_id

    def get_active_embedding(self, knowledge_base_id: str) -> dict[str, Any]:
        return self.document_service.get_active_embedding(self.workspace_id, knowledge_base_id)


def build_rag_search_tool(
    *,
    object_store: Any | None = None,
    embedding_client: Any | None = None,
    milvus: Any | None = None,
    vector_store: Any | None = None,
    kb_store: Any | None = None,
    knowledge_base_store: Any | None = None,
) -> StructuredTool:
    active_vector_store = vector_store or milvus
    active_kb_store = knowledge_base_store or kb_store

    def rag_search(
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        top_k: int = 50,
        final_top_k: int = 10,
        filters: dict[str, Any] | None = None,
        max_chars_per_chunk: int = 1200,
    ) -> dict[str, Any]:
        """Search document chunks and return source-backed text evidence."""
        scoped_filters = {
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            **(filters or {}),
        }
        warnings: list[str] = []
        evidence: list[dict[str, Any]]
        if active_vector_store is not None and embedding_client is not None:
            active_embedding = _active_embedding(active_kb_store, knowledge_base_id)
            query_vector = embedding_client.embed_query(
                text=query,
                provider=active_embedding.get("provider"),
                model=active_embedding.get("model"),
                dimension=active_embedding.get("dimension"),
            )
            hits = active_vector_store.search(
                collection=active_embedding.get("collection"),
                vector=query_vector,
                top_k=top_k,
                filters=scoped_filters,
            )
            evidence = [
                _evidence_from_hit(hit, object_store, max_chars_per_chunk)
                for hit in hits[:final_top_k]
            ]
        elif object_store is not None:
            service = DocumentIngestionService(object_store)
            results = service.search(
                workspace_id,
                knowledge_base_id,
                query,
                limit=min(top_k, final_top_k),
                filters=scoped_filters,
            )
            evidence = [
                _evidence_from_search_result(result, object_store, max_chars_per_chunk)
                for result in results[:final_top_k]
            ]
            warnings.append("using_object_store_lexical_fallback")
        else:
            evidence = []
            warnings.append("rag_backend_not_configured")
        return {"ok": True, "data": {"text_evidence": evidence, "warnings": warnings}}

    return StructuredTool.from_function(
        func=rag_search,
        name="rag_search",
        description="Search a knowledge base and return source-backed document chunk evidence.",
        args_schema=RagSearchArgs,
    )


def build_document_chunk_get_tool(*, object_store: Any) -> StructuredTool:
    def document_chunk_get(
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str | None = None,
        chunk_id: str | None = None,
        object_key: str | None = None,
        max_chars: int = 1200,
    ) -> dict[str, Any]:
        """Read one document chunk body by chunk id or known object key."""
        if object_key:
            chunk = _read_json(object_store, object_key)
        elif doc_id and chunk_id:
            chunk = _read_json(
                object_store,
                document_chunk_key(workspace_id, knowledge_base_id, doc_id, chunk_id),
            )
        else:
            service = DocumentIngestionService(object_store)
            chunk = service.get_chunk(
                workspace_id,
                knowledge_base_id,
                chunk_id=chunk_id,
                object_key=object_key,
            )
        normalized = _normalize_chunk(chunk)
        normalized["text"] = str(normalized.get("text") or "")[:max_chars]
        return {"ok": True, "data": {"chunk": normalized, "warnings": []}}

    return StructuredTool.from_function(
        func=document_chunk_get,
        name="document_chunk_get",
        description="Read source text for one retrieved document chunk.",
        args_schema=DocumentChunkGetArgs,
    )


def _active_embedding(kb_store: Any | None, knowledge_base_id: str) -> dict[str, Any]:
    if kb_store is None:
        return dict(DEFAULT_ACTIVE_EMBEDDING)
    value = kb_store.get_active_embedding(knowledge_base_id)
    if isinstance(value, dict):
        return value
    return {
        "provider": getattr(value, "provider", None),
        "model": getattr(value, "model", None),
        "dimension": getattr(value, "dimension", None),
        "collection": getattr(value, "collection", None),
    }


def _evidence_from_hit(hit: Any, object_store: Any, max_chars: int) -> dict[str, Any]:
    object_key = _value(hit, "object_key")
    chunk = _read_json(object_store, object_key) if object_key else {}
    normalized = _normalize_chunk({**chunk, "object_key": object_key})
    return {
        "chunk_id": _value(hit, "chunk_id") or normalized.get("chunk_id"),
        "doc_id": _value(hit, "doc_id") or normalized.get("doc_id"),
        "doc_version_id": _value(hit, "doc_version_id") or normalized.get("doc_version_id"),
        "score": _value(hit, "score"),
        "object_key": object_key,
        "text": str(normalized.get("text") or "")[:max_chars],
        "source": normalized.get("source") or {},
        "metadata_filter": normalized.get("metadata_filter") or {},
    }


def _evidence_from_search_result(
    result: dict[str, Any],
    object_store: Any,
    max_chars: int,
) -> dict[str, Any]:
    object_key = result.get("object_key")
    chunk = _read_json(object_store, object_key) if object_key else result
    normalized = _normalize_chunk({**chunk, "object_key": object_key})
    return {
        "chunk_id": normalized.get("chunk_id") or result.get("chunk_id"),
        "doc_id": normalized.get("doc_id") or result.get("doc_id"),
        "doc_version_id": normalized.get("doc_version_id") or result.get("doc_version_id"),
        "score": result.get("score"),
        "object_key": object_key,
        "text": str(normalized.get("text") or result.get("text") or "")[:max_chars],
        "source": normalized.get("source") or result.get("source") or {},
        "metadata_filter": normalized.get("metadata_filter") or result.get("metadata") or {},
    }


def _read_json(object_store: Any, key: str) -> dict[str, Any]:
    if hasattr(object_store, "read_json"):
        value = object_store.read_json(key)
        return dict(value)
    return JsonObjectStore(object_store).read_json(key)


def _value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _normalize_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    if "doc_id" not in chunk and "document_id" in chunk:
        chunk = {**chunk, "doc_id": chunk["document_id"]}
    metadata_filter = dict(chunk.get("metadata_filter") or {})
    if "workspace_id" not in chunk:
        chunk = {**chunk, "workspace_id": metadata_filter.get("workspace_id")}
    if "knowledge_base_id" not in chunk:
        chunk = {**chunk, "knowledge_base_id": metadata_filter.get("knowledge_base_id")}
    if "doc_version_id" not in chunk:
        chunk = {**chunk, "doc_version_id": metadata_filter.get("doc_version_id")}
    return chunk


rag_search_tool = build_rag_search_tool()
