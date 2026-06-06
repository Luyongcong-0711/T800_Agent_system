from __future__ import annotations

import hashlib
import importlib
from typing import Any

import pytest


def _chunker_module():
    try:
        return importlib.import_module("app.rag_pipeline.chunker")
    except ModuleNotFoundError:
        pytest.fail("Phase G requires app.rag_pipeline.chunker with chunk_document().")


def _chunk_document(document: dict[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    module = _chunker_module()
    chunk_document = getattr(module, "chunk_document", None)
    if chunk_document is None:
        pytest.fail("Phase G chunker contract requires chunk_document(document, **options).")

    chunks = chunk_document(document, **kwargs)
    if hasattr(chunks, "model_dump"):
        chunks = chunks.model_dump()
    if isinstance(chunks, dict):
        chunks = chunks.get("chunks", chunks.get("items"))
    assert isinstance(chunks, list)
    return [chunk.model_dump() if hasattr(chunk, "model_dump") else chunk for chunk in chunks]


def _sample_document() -> dict[str, Any]:
    return {
        "workspace_id": "default",
        "knowledge_base_id": "kb_default",
        "doc_id": "doc_contract_001",
        "doc_version_id": "docv_contract_001",
        "source_file_name": "contract.md",
        "mime_type": "text/markdown",
        "language": "zh",
        "doc_type": "contract",
        "parser_quality": "full",
        "title": "采购合同",
        "blocks": [
            {
                "block_id": "blk_001",
                "type": "heading",
                "level": 1,
                "text": "第一条 合同主体",
                "page_start": 1,
                "page_end": 1,
                "char_start": 0,
                "char_end": 8,
                "section_path": ["采购合同", "第一条 合同主体"],
            },
            {
                "block_id": "blk_002",
                "type": "paragraph",
                "text": "甲方为广州星河科技有限公司，乙方为深圳蓝海贸易有限公司。",
                "page_start": 1,
                "page_end": 1,
                "char_start": 9,
                "char_end": 40,
                "section_path": ["采购合同", "第一条 合同主体"],
            },
            {
                "block_id": "blk_003",
                "type": "heading",
                "level": 1,
                "text": "第二条 合同金额",
                "page_start": 2,
                "page_end": 2,
                "char_start": 41,
                "char_end": 49,
                "section_path": ["采购合同", "第二条 合同金额"],
            },
            {
                "block_id": "blk_004",
                "type": "paragraph",
                "text": "本合同金额为人民币100万元，乙方应于2026年6月30日前完成设备交付。",
                "page_start": 2,
                "page_end": 2,
                "char_start": 50,
                "char_end": 91,
                "section_path": ["采购合同", "第二条 合同金额"],
            },
        ],
    }


def _chunk_id_for(chunk: dict[str, Any]) -> str:
    return str(chunk["chunk_id"])


def test_chunk_document_preserves_structure_source_and_stable_ids() -> None:
    document = _sample_document()

    first = _chunk_document(document, chunk_size=80, chunk_overlap=20)
    second = _chunk_document(document, chunk_size=80, chunk_overlap=20)

    assert first
    assert [_chunk_id_for(chunk) for chunk in first] == [_chunk_id_for(chunk) for chunk in second]
    for index, chunk in enumerate(first):
        assert chunk["doc_id"] == "doc_contract_001"
        assert chunk["doc_version_id"] == "docv_contract_001"
        assert chunk["chunk_index"] == index
        assert chunk["text"].strip()
        assert chunk["source_block_ids"]
        assert chunk["section_path"][0] == "采购合同"
        assert chunk["page_start"] <= chunk["page_end"]
        assert chunk["token_count"] > 0
        assert chunk["parent_chunk_id"]
        assert chunk["metadata_filter"]["workspace_id"] == "default"
        assert chunk["metadata_filter"]["knowledge_base_id"] == "kb_default"
        assert chunk["metadata_filter"]["chunk_id"] == chunk["chunk_id"]
        expected_hash = hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()
        assert chunk["text_hash"] == expected_hash


def test_chunk_document_filters_empty_blocks_without_losing_errors() -> None:
    document = _sample_document()
    document["blocks"].append(
        {
            "block_id": "blk_empty",
            "type": "paragraph",
            "text": "   \n\t",
            "page_start": 3,
            "page_end": 3,
            "char_start": 92,
            "char_end": 95,
            "section_path": ["采购合同", "空白段"],
        }
    )

    chunks = _chunk_document(document, chunk_size=80, chunk_overlap=20)

    assert all(chunk["text"].strip() for chunk in chunks)
    assert all("blk_empty" not in chunk["source_block_ids"] for chunk in chunks)


def test_chunk_document_rejects_invalid_overlap() -> None:
    with pytest.raises((ValueError, AssertionError), match="overlap|chunk"):
        _chunk_document(_sample_document(), chunk_size=80, chunk_overlap=80)
