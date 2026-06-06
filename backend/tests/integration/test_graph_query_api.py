from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import graph as graph_api
from app.api.dependencies import get_graph_query_service, get_identity, get_object_store
from app.graph_pipeline.neo4j_readonly import Neo4jReadOnlyQueryAdapter
from app.graph_pipeline.paths import graph_index_key
from app.main import app
from app.rag_pipeline.paths import active_embedding_key, document_chunk_key, search_index_key
from app.schemas.identity import RuntimeIdentity
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def embed_query(self, **kwargs) -> list[float]:
        self.calls.append(kwargs)
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def __init__(self, object_key: str) -> None:
        self.calls: list[dict[str, object]] = []
        self.object_key = object_key

    def search(self, **kwargs) -> list[dict[str, object]]:
        self.calls.append(kwargs)
        return [
            {
                "chunk_id": "chk_001",
                "doc_id": "doc_001",
                "doc_version_id": "docv_001",
                "object_key": self.object_key,
                "score": 0.99,
            }
        ]


class _FakeGraphQueryService:
    def __init__(self) -> None:
        self.entity_search_calls: list[dict[str, object]] = []

    def entity_search(self, *args, **kwargs) -> list[dict[str, object]]:
        self.entity_search_calls.append({"args": args, **kwargs})
        return [
            {
                "entity_id": "ent_from_neo4j",
                "name": "Neo4j Entity",
                "entity_type": "Concept",
                "score": 1.0,
            }
        ]


class _FakeCypherDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute_query(self, query: str, **kwargs):
        self.calls.append({"query": query, "kwargs": kwargs})
        return [{"name": "Party B"}], None, ["name"]


@pytest.fixture()
def object_store(tmp_path) -> LocalObjectStore:
    store = LocalObjectStore(tmp_path / "objects")
    chunk_key = document_chunk_key("default", "kb_default", "doc_001", "chk_001")
    JsonObjectStore(store).write_json(
        chunk_key,
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "doc_id": "doc_001",
            "doc_version_id": "docv_001",
            "chunk_id": "chk_001",
            "text": "Party B must deliver the equipment before the delivery deadline.",
            "source": {"source_file_name": "contract.md", "page_start": 3},
            "metadata_filter": {
                "workspace_id": "default",
                "knowledge_base_id": "kb_default",
                "doc_id": "doc_001",
                "chunk_id": "chk_001",
            },
        },
    )
    JsonObjectStore(store).write_json(
        active_embedding_key("default", "kb_default"),
        {
            "schema_version": 1,
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "version_id": "embv_real",
            "provider": "openai_compatible",
            "model": "mimo-embedding-test",
            "dimension": 3,
            "collection": "kb_default_real_v1",
            "status": "active",
            "revision": 1,
        },
    )
    JsonObjectStore(store).write_json(
        search_index_key("default", "kb_default"),
        {
            "schema_version": 1,
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "backend": "object_store_lexical",
            "records": [
                {
                    "chunk_id": "chk_001",
                    "doc_id": "doc_001",
                    "doc_version_id": "docv_001",
                    "workspace_id": "default",
                    "knowledge_base_id": "kb_default",
                    "text": "Party B must deliver the equipment before the delivery deadline.",
                    "object_key": chunk_key,
                    "metadata": {
                        "workspace_id": "default",
                        "knowledge_base_id": "kb_default",
                    },
                    "source": {"source_file_name": "contract.md", "page_start": 3},
                    "term_counts": {
                        "party": 1,
                        "b": 1,
                        "must": 1,
                        "deliver": 1,
                        "equipment": 1,
                        "delivery": 1,
                        "deadline": 1,
                    },
                    "token_count": 9,
                }
            ],
            "revision": 1,
        },
    )
    JsonObjectStore(store).write_json(
        graph_index_key("default", "kb_default"),
        {
            "schema_version": 1,
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "entities": [
                {
                    "entity_id": "ent_party_b",
                    "name": "Party B",
                    "aliases": ["Supplier"],
                    "entity_type": "Organization",
                    "evidence_count": 1,
                },
                {
                    "entity_id": "ent_equipment",
                    "name": "Equipment",
                    "aliases": [],
                    "entity_type": "Asset",
                    "evidence_count": 1,
                },
                {
                    "entity_id": "ent_delivery",
                    "name": "Delivery",
                    "aliases": [],
                    "entity_type": "Event",
                    "evidence_count": 1,
                },
            ],
            "mentions": [],
            "relation_facts": [
                {
                    "fact_id": "fact_001",
                    "predicate": "DELIVERS",
                    "subject_entity_id": "ent_party_b",
                    "object_entity_id": "ent_delivery",
                    "confidence": 0.91,
                    "relation_strength": 0.8,
                    "evidence_ids": ["ev_001"],
                },
                {
                    "fact_id": "fact_002",
                    "predicate": "TARGETS",
                    "subject_entity_id": "ent_delivery",
                    "object_entity_id": "ent_equipment",
                    "confidence": 0.89,
                    "relation_strength": 0.7,
                    "evidence_ids": ["ev_002"],
                },
            ],
            "evidence": [
                {
                    "evidence_id": "ev_001",
                    "fact_id": "fact_001",
                    "source_chunk_id": "chk_001",
                    "chunk_object_key": chunk_key,
                    "evidence_text": "Party B must deliver the equipment.",
                    "source": {"source_file_name": "contract.md"},
                },
                {
                    "evidence_id": "ev_002",
                    "fact_id": "fact_002",
                    "source_chunk_id": "chk_001",
                    "chunk_object_key": chunk_key,
                    "evidence_text": "The delivery target is the equipment.",
                    "source": {"source_file_name": "contract.md"},
                },
            ],
        },
    )
    return store


@pytest.fixture()
def client(object_store: LocalObjectStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()


def test_graph_query_api_search_expand_and_evidence(client: TestClient) -> None:
    search_response = client.post(
        "/workspaces/default/graph/entities/search",
        json={"knowledge_base_id": "kb_default", "query": "Supplier"},
    )
    expand_response = client.post(
        "/workspaces/default/graph/entities/ent_party_b/expand",
        json={"knowledge_base_id": "kb_default", "depth": 99, "include_evidence": True},
    )
    evidence_response = client.post(
        "/workspaces/default/graph/evidence",
        json={
            "knowledge_base_id": "kb_default",
            "fact_ids": ["fact_001"],
            "evidence_ids": ["ev_001"],
        },
    )

    assert search_response.status_code == 200
    assert search_response.json()["entities"][0]["entity_id"] == "ent_party_b"
    assert "using_object_store_graph_fallback" in search_response.json()["warnings"]
    assert expand_response.status_code == 200
    paths = expand_response.json()["paths"]
    assert paths
    assert all(path["depth"] <= 2 for path in paths)
    assert paths[0]["relationships"][0]["fact_id"] == "fact_001"
    assert evidence_response.status_code == 200
    evidence = evidence_response.json()["evidence"]
    assert evidence[0]["evidence_id"] == "ev_001"
    assert evidence[0]["evidence_text"] == "Party B must deliver the equipment."
    assert evidence[0]["chunk_text"] == (
        "Party B must deliver the equipment before the delivery deadline."
    )
    assert evidence[0]["source"]["page_start"] == 3


def test_graph_evidence_api_respects_chunk_text_controls(client: TestClient) -> None:
    response = client.post(
        "/workspaces/default/graph/evidence",
        json={
            "knowledge_base_id": "kb_default",
            "evidence_ids": ["ev_001"],
            "include_chunk_text": True,
            "max_chars_per_chunk": 12,
        },
    )
    without_text_response = client.post(
        "/workspaces/default/graph/evidence",
        json={
            "knowledge_base_id": "kb_default",
            "evidence_ids": ["ev_001"],
            "include_chunk_text": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["evidence"][0]["chunk_text"] == "Party B must"
    assert without_text_response.status_code == 200
    assert "chunk_text" not in without_text_response.json()["evidence"][0]


def test_graphrag_search_api_returns_text_and_graph_evidence(client: TestClient) -> None:
    response = client.post(
        "/workspaces/default/graph/search",
        json={
            "knowledge_base_id": "kb_default",
            "query": "deliver equipment",
            "top_k": 5,
            "final_top_k": 3,
            "graph_depth": 2,
            "include_sources": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["text_evidence"]
    assert body["text_evidence"][0]["chunk_id"] == "chk_001"
    assert "deliver the equipment" in body["text_evidence"][0]["text"]
    assert body["graph_evidence"]
    assert body["graph_evidence"][0]["evidence_id"] in {"ev_001", "ev_002"}
    assert body["graph_evidence"][0]["chunk_text"]
    assert "using_object_store_lexical_fallback" in body["warnings"]
    assert "using_object_store_graph_fallback" in body["warnings"]


def test_graphrag_search_api_uses_active_embedding_collection(
    client: TestClient,
    monkeypatch,
) -> None:
    chunk_key = document_chunk_key("default", "kb_default", "doc_001", "chk_001")
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore(chunk_key)
    monkeypatch.setattr(graph_api, "_optional_embedding_client", lambda *args: embedding_client)
    monkeypatch.setattr(graph_api, "_optional_vector_store", lambda *args: vector_store)

    response = client.post(
        "/workspaces/default/graph/search",
        json={
            "knowledge_base_id": "kb_default",
            "query": "deliver equipment",
            "top_k": 5,
            "final_top_k": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["text_evidence"][0]["score"] == 0.99
    assert embedding_client.calls[0]["model"] == "mimo-embedding-test"
    assert vector_store.calls[0]["collection"] == "kb_default_real_v1"


def test_graph_paths_api_clamps_requested_depth_to_two(client: TestClient) -> None:
    response = client.post(
        "/workspaces/default/graph/paths/find",
        json={
            "knowledge_base_id": "kb_default",
            "source_entity": "Party B",
            "target_entity": "Equipment",
            "max_depth": 99,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["paths"]
    assert all(path["depth"] <= 2 for path in body["paths"])
    assert body["paths"][0]["target_entity_id"] == "ent_equipment"


def test_graph_relationship_api_returns_direct_edges(client: TestClient) -> None:
    response = client.post(
        "/workspaces/default/graph/relationships/find",
        json={
            "include_evidence": True,
            "knowledge_base_id": "kb_default",
            "relationship_allowlist": ["DELIVERS"],
            "source_entity": "Party B",
            "target_entity": "Delivery",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["empty"] is False
    assert body["source"]["entity_id"] == "ent_party_b"
    assert body["target"]["entity_id"] == "ent_delivery"
    assert body["relationships"][0]["fact_id"] == "fact_001"
    assert body["relationships"][0]["evidence_ids"] == ["ev_001"]


def test_graph_api_uses_configured_query_service_for_read_path(
    object_store: LocalObjectStore,
) -> None:
    fake_graph_query = _FakeGraphQueryService()
    app.dependency_overrides[get_object_store] = lambda: object_store
    app.dependency_overrides[get_graph_query_service] = lambda: fake_graph_query
    try:
        response = TestClient(app).post(
            "/workspaces/default/graph/entities/search",
            json={"knowledge_base_id": "kb_default", "query": "anything"},
        )
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    assert response.status_code == 200
    assert response.json()["entities"][0]["entity_id"] == "ent_from_neo4j"
    assert fake_graph_query.entity_search_calls[0]["args"][0] == "default"


def test_graph_readonly_cypher_api_rejects_write_before_driver_call(
    object_store: LocalObjectStore,
) -> None:
    driver = _FakeCypherDriver()
    adapter = Neo4jReadOnlyQueryAdapter(driver=driver)
    app.dependency_overrides[get_object_store] = lambda: object_store
    app.dependency_overrides[get_graph_query_service] = lambda: adapter
    app.dependency_overrides[get_identity] = lambda: RuntimeIdentity(
        user_id="admin_user",
        role="admin",
        workspace_id="default",
        workspace_role="admin",
    )
    try:
        response = TestClient(app).post(
            "/workspaces/default/graph/cypher/read",
            json={
                "query": "MATCH (n) CREATE (m:GraphEntity) RETURN n",
                "parameters": {},
            },
        )
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_type"] == "neo4j_readonly_query_rejected"
    assert driver.calls == []


def test_graph_readonly_cypher_api_rejects_viewer_before_driver_call(
    object_store: LocalObjectStore,
) -> None:
    driver = _FakeCypherDriver()
    adapter = Neo4jReadOnlyQueryAdapter(driver=driver)
    app.dependency_overrides[get_object_store] = lambda: object_store
    app.dependency_overrides[get_graph_query_service] = lambda: adapter
    app.dependency_overrides[get_identity] = lambda: RuntimeIdentity(
        user_id="viewer_user",
        role="viewer",
        workspace_id="default",
        workspace_role="viewer",
    )
    try:
        response = TestClient(app).post(
            "/workspaces/default/graph/cypher/read",
            json={
                "query": "MATCH (n) RETURN n",
                "parameters": {},
            },
        )
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    assert response.status_code == 403
    assert driver.calls == []
