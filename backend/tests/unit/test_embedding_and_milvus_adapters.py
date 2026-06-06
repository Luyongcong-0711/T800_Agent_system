from __future__ import annotations

import json

import httpx
import pytest

from app.embedding.client import OpenAICompatibleEmbeddingClient
from app.vector_store.milvus_http import MilvusHttpVectorStore, MilvusVectorStoreError


def test_openai_compatible_embedding_client_posts_embeddings_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [0.1, 0.2], "index": 0},
                    {"embedding": [0.3, 0.4], "index": 1},
                ]
            },
        )

    client = OpenAICompatibleEmbeddingClient(
        base_url="https://llm.example.com/v1",
        api_key="secret-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    vectors = client.embed_documents(
        texts=["hello", "world"],
        model="text-embedding-test",
        dimension=2,
    )

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert requests[0].url == "https://llm.example.com/v1/embeddings"
    assert requests[0].headers["authorization"] == "Bearer secret-key"
    payload = json.loads(requests[0].content)
    assert payload == {
        "dimensions": 2,
        "input": ["hello", "world"],
        "model": "text-embedding-test",
    }


def test_milvus_http_vector_store_creates_collection_and_upserts_records() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        requests.append((request.url.path, payload))
        if request.url.path.endswith("/collections/describe"):
            return httpx.Response(404, json={"message": "collection not found"})
        return httpx.Response(200, json={"code": 0, "data": []})

    store = MilvusHttpVectorStore(
        uri="http://milvus.local",
        token="milvus-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    store.ensure_collection(collection="kb_default_v1", dimension=3)
    store.upsert(
        collection="kb_default_v1",
        records=[
            {
                "chunk_id": "chk_001",
                "doc_id": "doc_001",
                "doc_version_id": "docv_001",
                "knowledge_base_id": "kb_default",
                "metadata": {"doc_type": "contract"},
                "object_key": "chunks/chk_001.json",
                "text": "Party B must deliver.",
                "vector": [0.1, 0.2, 0.3],
                "workspace_id": "default",
            }
        ],
    )

    assert [path for path, _ in requests] == [
        "/v2/vectordb/collections/describe",
        "/v2/vectordb/collections/create",
        "/v2/vectordb/entities/upsert",
    ]
    assert requests[1][1]["collectionName"] == "kb_default_v1"
    assert requests[1][1]["dimension"] == 3
    assert requests[2][1]["data"][0]["chunk_id"] == "chk_001"
    assert requests[2][1]["data"][0]["vector"] == [0.1, 0.2, 0.3]


def test_milvus_http_vector_store_reports_partial_upsert_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/vectordb/entities/upsert"
        return httpx.Response(200, json={"code": 0, "upsertCount": 0})

    store = MilvusHttpVectorStore(
        uri="http://milvus.local",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = store.upsert(
        collection="kb_default_v1",
        records=[
            {
                "chunk_id": "chk_001",
                "vector": [0.1, 0.2, 0.3],
            }
        ],
    )

    assert result["ok"] is False
    assert result["upserted_count"] == 0


def test_milvus_http_vector_store_search_normalizes_hits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/vectordb/entities/search"
        payload = json.loads(request.content)
        assert payload["filter"] == 'doc_type == "contract" and workspace_id == "default"'
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "chunk_id": "chk_001",
                        "distance": 0.12,
                        "doc_id": "doc_001",
                        "doc_version_id": "docv_001",
                        "object_key": "chunks/chk_001.json",
                    }
                ]
            },
        )

    store = MilvusHttpVectorStore(
        uri="http://milvus.local",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    hits = store.search(
        collection="kb_default_v1",
        vector=[0.1, 0.2, 0.3],
        top_k=5,
        filters={"doc_type": "contract", "workspace_id": "default"},
    )

    assert hits[0].chunk_id == "chk_001"
    assert hits[0].score == 0.12
    assert hits[0].object_key == "chunks/chk_001.json"


def test_milvus_http_vector_store_deletes_by_primary_ids() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        return httpx.Response(200, json={"code": 0})

    store = MilvusHttpVectorStore(
        uri="http://milvus.local",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = store.delete_by_ids(collection="default_memory", ids=["mem_001", "mem_002"])

    assert result["ok"] is True
    assert requests == [
        (
            "/v2/vectordb/entities/delete",
            {
                "collectionName": "default_memory",
                "filter": 'chunk_id in ["mem_001", "mem_002"]',
            },
        )
    ]


def test_milvus_http_vector_store_marks_timeout_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow milvus", request=request)

    store = MilvusHttpVectorStore(
        uri="http://milvus.local",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(MilvusVectorStoreError) as exc_info:
        store.search(collection="kb_default_v1", vector=[0.1, 0.2, 0.3], top_k=5)

    assert exc_info.value.error_type == "timeout"
    assert exc_info.value.retryable is True


def test_milvus_http_vector_store_marks_connection_error_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    store = MilvusHttpVectorStore(
        uri="http://milvus.local",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(MilvusVectorStoreError) as exc_info:
        store.upsert(collection="kb_default_v1", records=[])

    assert exc_info.value.error_type == "network_error"
    assert exc_info.value.retryable is True


def test_milvus_http_vector_store_marks_missing_collection_non_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "collection kb_default_v1 not found"})

    store = MilvusHttpVectorStore(
        uri="http://milvus.local",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(MilvusVectorStoreError) as exc_info:
        store.search(collection="kb_default_v1", vector=[0.1, 0.2, 0.3], top_k=5)

    assert exc_info.value.error_type == "collection_not_found"
    assert exc_info.value.retryable is False


def test_milvus_http_vector_store_marks_dimension_mismatch_non_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 1100, "message": "vector dimension mismatch"},
        )

    store = MilvusHttpVectorStore(
        uri="http://milvus.local",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(MilvusVectorStoreError) as exc_info:
        store.upsert(
            collection="kb_default_v1",
            records=[
                {
                    "chunk_id": "chk_001",
                    "vector": [0.1, 0.2],
                }
            ],
        )

    assert exc_info.value.error_type == "dimension_mismatch"
    assert exc_info.value.retryable is False
