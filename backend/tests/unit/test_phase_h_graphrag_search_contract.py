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
    def embed_query(self, **_kwargs: Any) -> list[float]:
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def search(self, **_kwargs: Any) -> list[_Hit]:
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
            )
        ]


class _FakeObjectStore:
    def __init__(self) -> None:
        self.objects = {
            "workspaces/default/knowledge_bases/kb_default/documents/doc_001/chunks/chk_001.json": {
                "chunk_id": "chk_001",
                "doc_id": "doc_001",
                "doc_version_id": "docv_001",
                "text": "Lin and Jia both participated in the poetry society.",
                "source": {"source_file_name": "novel-notes.md", "page_start": 7},
                "metadata_filter": {
                    "workspace_id": "default",
                    "knowledge_base_id": "kb_default",
                },
            }
        }

    def read_json(self, key: str) -> dict[str, Any]:
        return self.objects[key]


class _FakeKnowledgeBaseStore:
    def get_active_embedding(self, knowledge_base_id: str) -> dict[str, Any]:
        assert knowledge_base_id == "kb_default"
        return {
            "provider": "fake",
            "model": "fake-embedding",
            "dimension": 3,
            "collection": "kb_default_chunks",
        }


class _FakeGraphQuery:
    def entities_by_chunk_id(self, **kwargs: Any) -> list[dict[str, Any]]:
        assert kwargs["chunk_id"] == "chk_001"
        return [{"entity_id": "ent_lin", "name": "Lin"}]

    def expand_entity(self, **kwargs: Any) -> list[dict[str, Any]]:
        assert kwargs["depth"] <= 2
        return [
            {
                "path_id": "path_001",
                "depth": 1,
                "nodes": [
                    {"entity_id": "ent_lin", "name": "Lin"},
                    {"entity_id": "ent_poetry", "name": "Poetry Society"},
                ],
                "relationships": [
                    {
                        "type": "PARTICIPATED_IN",
                        "direction": "outgoing",
                        "fact_id": "fact_001",
                        "evidence_ids": ["ev_001"],
                    }
                ],
            }
        ]

    def get_evidence_refs(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "evidence_id": "ev_001",
                "fact_id": "fact_001",
                "source_chunk_id": "chk_001",
                "chunk_id": "chk_001",
                "chunk_object_key": (
                    "workspaces/default/knowledge_bases/kb_default/documents/"
                    "doc_001/chunks/chk_001.json"
                ),
                "evidence_text": "Lin joined the poetry society.",
                "source": {"source_file_name": "novel-notes.md", "page_start": 7},
            }
        ]


def _graphrag_module() -> Any:
    candidates = (
        "app.tools.builtin.graph_tools",
        "app.graph_pipeline.graphrag",
        "app.graph_pipeline.tools",
        "app.runtime.graph_tools",
    )
    for module_name in candidates:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    pytest.fail(
        "Phase H requires graphrag_search in app.tools.builtin.graph_tools "
        "or an equivalent tested module."
    )


def _make_tool(module: Any, builder_name: str, tool_name: str, **deps: Any) -> Any:
    builder = getattr(module, builder_name, None)
    if builder is not None:
        signature = inspect.signature(builder)
        kwargs = {name: value for name, value in deps.items() if name in signature.parameters}
        return builder(**kwargs)
    tool = getattr(module, tool_name, None)
    if tool is None:
        pytest.fail(f"Phase H GraphRAG contract requires {builder_name}() or {tool_name}.")
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


def test_graphrag_search_returns_text_and_graph_evidence_with_source_chunks() -> None:
    module = _graphrag_module()
    tool = _make_tool(
        module,
        "build_graphrag_search_tool",
        "graphrag_search_tool",
        object_store=_FakeObjectStore(),
        embedding_client=_FakeEmbeddingClient(),
        milvus=_FakeVectorStore(),
        vector_store=_FakeVectorStore(),
        kb_store=_FakeKnowledgeBaseStore(),
        knowledge_base_store=_FakeKnowledgeBaseStore(),
        graph_query=_FakeGraphQuery(),
    )

    result = _invoke(
        tool,
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "query": "What connects Lin and the poetry society?",
            "filters": {},
            "top_k": 10,
            "final_top_k": 5,
            "graph_depth": 2,
            "relationship_allowlist": ["PARTICIPATED_IN"],
            "include_sources": True,
        },
    )

    data = _data(result)
    assert data["text_evidence"]
    assert data["graph_evidence"]
    assert data["text_evidence"][0]["chunk_id"] == "chk_001"
    assert data["text_evidence"][0]["source"]["source_file_name"] == "novel-notes.md"
    graph_evidence = data["graph_evidence"][0]
    assert (
        graph_evidence.get("source_chunk_id") == "chk_001"
        or graph_evidence.get("chunk_id") == "chk_001"
    )
    assert graph_evidence.get("source") or graph_evidence.get("chunk_text")
    assert "fact_001" in str(graph_evidence)
    assert data.get("warnings", []) == []
