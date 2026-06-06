from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.rag_pipeline.models import DocumentChunk

CHUNKER_VERSION = "structured-deterministic-v1"
TOKEN_RE = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+")


def chunk_document(
    document: dict[str, Any],
    *,
    chunk_size: int = 900,
    chunk_overlap: int = 120,
) -> list[dict[str, Any]]:
    if chunk_size < 50:
        raise ValueError("chunk_size must be at least 50.")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size.")

    source_file_name = str(document.get("source_file_name") or "")
    mime_type = str(document.get("mime_type") or "text/plain")
    language = str(document.get("language") or "unknown")
    doc_type = str(document.get("doc_type") or "general")

    chunks: list[DocumentChunk] = []
    current_blocks: list[dict[str, Any]] = []
    current_len = 0
    for block in document.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if current_blocks and current_len + len(text) + 1 > chunk_size:
            chunks.append(
                _build_chunk(
                    document=document,
                    blocks=current_blocks,
                    chunk_index=len(chunks),
                    source_file_name=source_file_name,
                    mime_type=mime_type,
                    language=language,
                    doc_type=doc_type,
                )
            )
            current_blocks = _overlap_tail(current_blocks, chunk_overlap)
            current_len = sum(len(str(item.get("text") or "")) + 1 for item in current_blocks)
        current_blocks.append(block)
        current_len += len(text) + 1

    if current_blocks:
        chunks.append(
            _build_chunk(
                document=document,
                blocks=current_blocks,
                chunk_index=len(chunks),
                source_file_name=source_file_name,
                mime_type=mime_type,
                language=language,
                doc_type=doc_type,
            )
        )

    return [chunk.to_record() for chunk in chunks]


def _overlap_tail(blocks: list[dict[str, Any]], chunk_overlap: int) -> list[dict[str, Any]]:
    if chunk_overlap <= 0:
        return []
    selected: list[dict[str, Any]] = []
    total = 0
    for block in reversed(blocks):
        text = str(block.get("text") or "")
        if selected and total + len(text) > chunk_overlap:
            break
        selected.append(block)
        total += len(text)
    selected.reverse()
    return selected


def _build_chunk(
    *,
    document: dict[str, Any],
    blocks: list[dict[str, Any]],
    chunk_index: int,
    source_file_name: str,
    mime_type: str,
    language: str,
    doc_type: str,
) -> DocumentChunk:
    workspace_id = str(document["workspace_id"])
    knowledge_base_id = str(document["knowledge_base_id"])
    doc_id = str(document["doc_id"])
    doc_version_id = str(document["doc_version_id"])
    text = "\n".join(str(block.get("text") or "").strip() for block in blocks).strip()
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    section_path = _section_path(blocks, document)
    parent_chunk_id = _stable_id(
        "pchk",
        {
            "doc_version_id": doc_version_id,
            "section_path": section_path,
            "chunker_version": CHUNKER_VERSION,
        },
    )
    chunk_id = _stable_id(
        "chk",
        {
            "doc_version_id": doc_version_id,
            "chunk_index": chunk_index,
            "text_hash": text_hash,
            "chunker_version": CHUNKER_VERSION,
        },
    )
    source_block_ids = [str(block.get("block_id")) for block in blocks if block.get("block_id")]
    page_start = _min_int(blocks, "page_start")
    page_end = _max_int(blocks, "page_end")
    char_start = _min_int(blocks, "char_start")
    char_end = _max_int(blocks, "char_end")
    metadata_filter = {
        "workspace_id": workspace_id,
        "knowledge_base_id": knowledge_base_id,
        "doc_id": doc_id,
        "doc_version_id": doc_version_id,
        "chunk_id": chunk_id,
        "source_file_name": source_file_name,
        "mime_type": mime_type,
        "language": language,
        "doc_type": doc_type,
    }
    return DocumentChunk(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        doc_id=doc_id,
        doc_version_id=doc_version_id,
        chunk_id=chunk_id,
        parent_chunk_id=parent_chunk_id,
        chunk_index=chunk_index,
        chunk_type=_chunk_type(blocks),
        text=text,
        section_path=section_path,
        page_start=page_start,
        page_end=page_end,
        char_start=char_start,
        char_end=char_end,
        source_block_ids=source_block_ids,
        token_count=max(1, len(TOKEN_RE.findall(text))),
        text_hash=text_hash,
        metadata_filter=metadata_filter,
        source={
            "source_file_name": source_file_name,
            "mime_type": mime_type,
            "page_start": page_start,
            "page_end": page_end,
            "char_start": char_start,
            "char_end": char_end,
            "section_path": section_path,
        },
    )


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


def _section_path(blocks: list[dict[str, Any]], document: dict[str, Any]) -> list[str]:
    for block in blocks:
        value = block.get("section_path")
        if isinstance(value, list) and value:
            return [str(item) for item in value]
    title = document.get("title") or document.get("source_file_name") or "Untitled"
    return [str(title)]


def _chunk_type(blocks: list[dict[str, Any]]) -> str:
    types = {str(block.get("type") or "paragraph") for block in blocks}
    if types == {"heading"}:
        return "heading"
    if "code" in types:
        return "code"
    if "table" in types:
        return "table"
    return "paragraph"


def _min_int(blocks: list[dict[str, Any]], key: str) -> int | None:
    values = [int(block[key]) for block in blocks if block.get(key) is not None]
    return min(values) if values else None


def _max_int(blocks: list[dict[str, Any]], key: str) -> int | None:
    values = [int(block[key]) for block in blocks if block.get(key) is not None]
    return max(values) if values else None
