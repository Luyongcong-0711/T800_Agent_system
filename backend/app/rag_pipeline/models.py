from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

DocumentFormat = Literal[
    "text",
    "markdown",
    "code",
    "html",
    "csv",
    "pdf",
    "docx",
    "excel",
    "image",
]


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    document_format: DocumentFormat
    source_sha256: str
    byte_size: int
    blocks: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentChunk:
    workspace_id: str
    knowledge_base_id: str
    doc_id: str
    doc_version_id: str
    chunk_id: str
    parent_chunk_id: str
    chunk_index: int
    chunk_type: str
    text: str
    section_path: list[str]
    page_start: int | None
    page_end: int | None
    char_start: int | None
    char_end: int | None
    source_block_ids: list[str]
    token_count: int
    text_hash: str
    metadata_filter: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    object_key: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "workspace_id": self.workspace_id,
            "knowledge_base_id": self.knowledge_base_id,
            "doc_id": self.doc_id,
            "doc_version_id": self.doc_version_id,
            "chunk_id": self.chunk_id,
            "parent_chunk_id": self.parent_chunk_id,
            "chunk_index": self.chunk_index,
            "chunk_type": self.chunk_type,
            "text": self.text,
            "section_path": self.section_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "source_block_ids": self.source_block_ids,
            "token_count": self.token_count,
            "text_hash": self.text_hash,
            "metadata_filter": self.metadata_filter,
            "source": self.source,
            "object_key": self.object_key,
        }


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    doc_id: str
    doc_version_id: str
    workspace_id: str
    knowledge_base_id: str
    score: float
    text: str
    object_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_version_id": self.doc_version_id,
            "workspace_id": self.workspace_id,
            "knowledge_base_id": self.knowledge_base_id,
            "score": self.score,
            "text": self.text,
            "object_key": self.object_key,
            "metadata": self.metadata,
            "source": self.source,
        }
