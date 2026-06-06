from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Any

import pytest


@dataclass(frozen=True)
class _Hit:
    chunk_id: str
    doc_id: str
    doc_version_id: str
    score: float
    object_key: str


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def embed_query(self, **kwargs: Any) -> list[float]:
        self.calls.append(kwargs)
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> list[_Hit]:
        self.calls.append(kwargs)
        return [
            _Hit(
                chunk_id="chk_001",
                doc_id="doc_001",
                doc_version_id="docv_001",
                score=0.91,
                object_key=(
                    "workspaces/default/knowledge_bases/kb_default/documents/"
                    "doc_001/chunks/chk_001.json"
                ),
            ),
            _Hit(
                chunk_id="chk_002",
                doc_id="doc_001",
                doc_version_id="docv_001",
                score=0.72,
                object_key=(
                    "workspaces/default/knowledge_bases/kb_default/documents/"
                    "doc_001/chunks/chk_002.json"
                ),
            ),
        ]


class _FakeObjectStore:
    def __init__(self) -> None:
        self.objects = {
            "workspaces/default/knowledge_bases/kb_default/documents/doc_001/chunks/chk_001.json": {
                "chunk_id": "chk_001",
                "doc_id": "doc_001",
                "doc_version_id": "docv_001",
                "text": "Party B must deliver equipment before 2026-06-30.",
                "source": {"source_file_name": "contract.md", "page_start": 2},
                "metadata_filter": {
                    "workspace_id": "default",
                    "knowledge_base_id": "kb_default",
                    "chunk_id": "chk_001",
                },
            },
            "workspaces/default/knowledge_bases/kb_default/documents/doc_001/chunks/chk_002.json": {
                "chunk_id": "chk_002",
                "doc_id": "doc_001",
                "doc_version_id": "docv_001",
                "text": "This lower ranked chunk should be trimmed by final_top_k.",
                "source": {"source_file_name": "contract.md", "page_start": 3},
                "metadata_filter": {
                    "workspace_id": "default",
                    "knowledge_base_id": "kb_default",
                    "chunk_id": "chk_002",
                },
            },
        }

    def read_json(self, key: str) -> dict[str, Any]:
        return self.objects[key]


class _FakeKnowledgeBaseStore:
    def get_active_embedding(self, knowledge_base_id: str) -> Any:
        assert knowledge_base_id == "kb_default"
        return {
            "model": "fake-embedding",
            "dimension": 3,
            "collection": "kb_default_chunks",
        }


def _tools_module():
    candidates = (
        "app.tools.builtin.rag_tools",
        "app.rag_pipeline.tools",
        "app.runtime.rag_tools",
    )
    for module_name in candidates:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    pytest.fail(
        "Phase G requires a RAG tool module at app.tools.builtin.rag_tools "
        "or an equivalent tested module."
    )


def _make_tool(module: Any, builder_name: str, tool_name: str, **deps: Any) -> Any:
    builder = getattr(module, builder_name, None)
    if builder is not None:
        signature = inspect.signature(builder)
        kwargs = {
            name: value
            for name, value in deps.items()
            if name in signature.parameters
        }
        return builder(**kwargs)

    tool = getattr(module, tool_name, None)
    if tool is None:
        pytest.fail(f"Phase G tool contract requires {builder_name}() or {tool_name}.")
    return tool


def _invoke(tool: Any, args: dict[str, Any]) -> dict[str, Any]:
    if hasattr(tool, "invoke"):
        result = tool.invoke(args)
    else:
        result = tool(**args)
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    assert isinstance(result, dict)
    return result


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result.get("ok", True) is True
    data = result.get("data", result)
    assert isinstance(data, dict)
    return data


def test_rag_search_uses_injected_embedding_vector_store_and_object_store() -> None:
    module = _tools_module()
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore()
    object_store = _FakeObjectStore()
    kb_store = _FakeKnowledgeBaseStore()
    tool = _make_tool(
        module,
        "build_rag_search_tool",
        "rag_search_tool",
        embedding_client=embedding_client,
        milvus=vector_store,
        vector_store=vector_store,
        object_store=object_store,
        kb_store=kb_store,
        knowledge_base_store=kb_store,
    )

    result = _invoke(
        tool,
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "query": "When must Party B deliver?",
            "top_k": 50,
            "final_top_k": 1,
            "filters": {"doc_type": "contract"},
            "max_chars_per_chunk": 24,
        },
    )

    data = _data(result)
    evidence = data["text_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["chunk_id"] == "chk_001"
    assert evidence[0]["doc_id"] == "doc_001"
    assert evidence[0]["score"] == 0.91
    assert len(evidence[0]["text"]) <= 24
    assert evidence[0]["source"]["source_file_name"] == "contract.md"
    assert data.get("warnings", []) == []
    assert embedding_client.calls
    assert embedding_client.calls[0]["text"] == "When must Party B deliver?"
    assert vector_store.calls
    assert vector_store.calls[0]["top_k"] == 50
    assert vector_store.calls[0]["filters"]["workspace_id"] == "default"
    assert vector_store.calls[0]["filters"]["knowledge_base_id"] == "kb_default"
    assert vector_store.calls[0]["filters"]["doc_type"] == "contract"


def test_document_chunk_get_returns_source_chunk_body() -> None:
    module = _tools_module()
    object_store = _FakeObjectStore()
    tool = _make_tool(
        module,
        "build_document_chunk_get_tool",
        "document_chunk_get_tool",
        object_store=object_store,
    )

    result = _invoke(
        tool,
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "doc_id": "doc_001",
            "chunk_id": "chk_001",
            "max_chars": 18,
        },
    )

    data = _data(result)
    chunk = data.get("chunk", data)
    assert chunk["chunk_id"] == "chk_001"
    assert chunk["doc_id"] == "doc_001"
    assert len(chunk["text"]) <= 18
    assert chunk["source"]["source_file_name"] == "contract.md"
    assert chunk["metadata_filter"]["workspace_id"] == "default"
    assert chunk["metadata_filter"]["knowledge_base_id"] == "kb_default"


def test_runtime_default_registry_can_expose_rag_tools_without_secret_surface(tmp_path) -> None:
    from app.runtime.tools import build_default_tool_registry
    from app.storage.local_object_store import LocalObjectStore

    registry = build_default_tool_registry(LocalObjectStore(tmp_path / "objects"))
    specs = registry.model_safe_specs()
    names = {spec["name"] for spec in specs}

    assert {"rag_search", "document_chunk_get"} <= names
    serialized = str(specs).lower()
    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "plaintext" not in serialized
    assert "ciphertext" not in serialized
