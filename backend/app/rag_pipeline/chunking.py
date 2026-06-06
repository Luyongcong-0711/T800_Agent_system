from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.rag_pipeline.chunker import CHUNKER_VERSION, chunk_document
from app.rag_pipeline.models import DocumentChunk, ParsedDocument


@dataclass(frozen=True)
class ChunkingConfig:
    max_chars: int = 900
    overlap_chars: int = 120

    def __post_init__(self) -> None:
        if self.max_chars < 50:
            raise ValueError("max_chars must be at least 50.")
        if self.overlap_chars < 0 or self.overlap_chars >= self.max_chars:
            raise ValueError("overlap_chars must be non-negative and smaller than max_chars.")


class DeterministicChunker:
    version = CHUNKER_VERSION

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        doc_version_id: str,
        parsed_document: ParsedDocument,
        metadata: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        document = {
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "doc_id": doc_id,
            "doc_version_id": doc_version_id,
            "source_file_name": (metadata or {}).get("source_file_name")
            or parsed_document.metadata.get("filename"),
            "mime_type": (metadata or {}).get("mime_type")
            or parsed_document.metadata.get("content_type")
            or "text/plain",
            "language": (metadata or {}).get("language") or "unknown",
            "doc_type": (metadata or {}).get("doc_type") or "general",
            "title": (metadata or {}).get("title")
            or parsed_document.metadata.get("filename")
            or "Untitled",
            "blocks": parsed_document.blocks,
        }
        records = chunk_document(
            document,
            chunk_size=self.config.max_chars,
            chunk_overlap=self.config.overlap_chars,
        )
        return [
            DocumentChunk(
                workspace_id=str(record["workspace_id"]),
                knowledge_base_id=str(record["knowledge_base_id"]),
                doc_id=str(record["doc_id"]),
                doc_version_id=str(record["doc_version_id"]),
                chunk_id=str(record["chunk_id"]),
                parent_chunk_id=str(record["parent_chunk_id"]),
                chunk_index=int(record["chunk_index"]),
                chunk_type=str(record["chunk_type"]),
                text=str(record["text"]),
                section_path=[str(item) for item in record.get("section_path", [])],
                page_start=record.get("page_start"),
                page_end=record.get("page_end"),
                char_start=record.get("char_start"),
                char_end=record.get("char_end"),
                source_block_ids=[str(item) for item in record.get("source_block_ids", [])],
                token_count=int(record["token_count"]),
                text_hash=str(record["text_hash"]),
                metadata_filter=dict(record.get("metadata_filter") or {}),
                source=dict(record.get("source") or {}),
                object_key=record.get("object_key"),
            )
            for record in records
        ]
