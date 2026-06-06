from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Protocol

from app.rag_pipeline.models import DocumentChunk, SearchResult
from app.rag_pipeline.paths import search_index_key
from app.storage.object_store import JsonObjectStore, ObjectStore

TOKEN_RE = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+")


class SearchIndex(Protocol):
    def upsert(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        chunks: list[DocumentChunk],
    ) -> None:
        raise NotImplementedError

    def search(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        raise NotImplementedError


class InMemorySearchIndex:
    def __init__(self) -> None:
        self._records_by_scope: dict[str, dict[str, dict[str, object]]] = {}

    def upsert(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        chunks: list[DocumentChunk],
    ) -> None:
        records = self._records_by_scope.setdefault(_scope(workspace_id, knowledge_base_id), {})
        for chunk in chunks:
            records[chunk.chunk_id] = _chunk_record(chunk)

    def search(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        records = self._records_by_scope.get(_scope(workspace_id, knowledge_base_id), {})
        return _rank_records(records.values(), query, limit, filters or {})


class ObjectStoreSearchIndex:
    """Small persisted lexical index for local and MinIO-backed fallback retrieval."""

    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)

    def upsert(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        chunks: list[DocumentChunk],
    ) -> None:
        key = search_index_key(workspace_id, knowledge_base_id)
        index = self.json_store.read_json_or_default(
            key,
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "backend": "object_store_lexical",
                "records": [],
                "revision": 0,
            },
        )
        by_chunk_id = {
            str(record["chunk_id"]): record
            for record in index.get("records", [])
            if isinstance(record, dict) and record.get("chunk_id")
        }
        for chunk in chunks:
            by_chunk_id[chunk.chunk_id] = _chunk_record(chunk)
        index["records"] = sorted(by_chunk_id.values(), key=lambda item: str(item["chunk_id"]))
        index["revision"] = int(index.get("revision", 0)) + 1
        self.json_store.write_json(key, index)

    def search(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        index = self.json_store.read_json_or_default(
            search_index_key(workspace_id, knowledge_base_id),
            {"records": []},
        )
        return _rank_records(index.get("records", []), query, limit, filters or {})


def _scope(workspace_id: str, knowledge_base_id: str) -> str:
    return f"{workspace_id}:{knowledge_base_id}"


def _chunk_record(chunk: DocumentChunk) -> dict[str, object]:
    tokens = _tokens(chunk.text)
    return {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "doc_version_id": chunk.doc_version_id,
        "workspace_id": chunk.workspace_id,
        "knowledge_base_id": chunk.knowledge_base_id,
        "chunk_index": chunk.chunk_index,
        "text": chunk.text,
        "object_key": chunk.object_key,
        "metadata": chunk.metadata_filter,
        "source": chunk.source,
        "term_counts": dict(sorted(Counter(tokens).items())),
        "token_count": len(tokens),
    }


def _rank_records(
    records: object,
    query: str,
    limit: int,
    filters: dict[str, Any],
) -> list[SearchResult]:
    if not isinstance(records, list) and not hasattr(records, "__iter__"):
        return []
    query_terms = Counter(_tokens(query))
    if not query_terms:
        return []
    scored: list[tuple[float, str, SearchResult]] = []
    for record in records:
        if not isinstance(record, dict) or not _matches_filters(record, filters):
            continue
        term_counts = record.get("term_counts")
        if not isinstance(term_counts, dict):
            continue
        score = _score(query_terms, term_counts, int(record.get("token_count") or 0))
        if score <= 0:
            continue
        chunk_id = str(record["chunk_id"])
        scored.append(
            (
                score,
                chunk_id,
                SearchResult(
                    chunk_id=chunk_id,
                    doc_id=str(record["doc_id"]),
                    doc_version_id=str(record["doc_version_id"]),
                    workspace_id=str(record["workspace_id"]),
                    knowledge_base_id=str(record["knowledge_base_id"]),
                    score=score,
                    text=str(record["text"]),
                    object_key=str(record.get("object_key") or ""),
                    metadata=dict(record.get("metadata") or {}),
                    source=dict(record.get("source") or {}),
                ),
            )
        )
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in scored[: max(1, min(limit, 100))]]


def _matches_filters(record: dict[str, Any], filters: dict[str, Any]) -> bool:
    metadata = dict(record.get("metadata") or {})
    for key, value in filters.items():
        if key in {"workspace_id", "knowledge_base_id"}:
            continue
        if metadata.get(key) != value and record.get(key) != value:
            return False
    return True


def _tokens(value: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(value)]


def _score(query_terms: Counter[str], term_counts: dict[object, object], token_count: int) -> float:
    score = 0.0
    normalization = math.sqrt(max(token_count, 1))
    for term, query_count in query_terms.items():
        raw_count = term_counts.get(term, 0)
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 0
        score += (count * query_count) / normalization
    return round(score, 6)
