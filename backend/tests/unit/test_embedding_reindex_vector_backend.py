from __future__ import annotations

from typing import Any

import pytest

from app.rag_pipeline.ingestion import DocumentIngestionService
from app.storage.local_object_store import LocalObjectStore


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def embed_documents(self, **kwargs: Any) -> list[list[float]]:
        self.calls.append(kwargs)
        texts = kwargs["texts"]
        return [[float(index), 0.1, 0.2] for index, _ in enumerate(texts)]


class _FakeVectorStore:
    def __init__(
        self,
        *,
        fail_upsert: bool = False,
        failed_count: int | None = None,
        ok: bool = True,
        upserted_count: int | None = None,
    ) -> None:
        self.collections: list[dict[str, Any]] = []
        self.upserts: list[dict[str, Any]] = []
        self.fail_upsert = fail_upsert
        self.failed_count = failed_count
        self.ok = ok
        self.upserted_count = upserted_count

    def ensure_collection(self, **kwargs: Any) -> None:
        self.collections.append(kwargs)

    def upsert(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_upsert:
            raise RuntimeError("milvus unavailable")
        self.upserts.append(kwargs)
        return {
            "error_type": "vector_store_upsert_partial_failure",
            "failed_count": self.failed_count,
            "ok": self.ok,
            "retryable": True,
            "upserted_count": self.upserted_count
            if self.upserted_count is not None
            else len(kwargs["records"]),
        }


def _indexed_service(tmp_path, **deps: Any) -> tuple[DocumentIngestionService, dict[str, Any]]:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = DocumentIngestionService(object_store, **deps)
    manifest = service.create_uploaded_document(
        workspace_id="default",
        knowledge_base_id="kb_default",
        content="# Contract\nParty B must deliver before June.",
        source_file_name="contract.md",
        mime_type="text/markdown",
    )
    indexed = service.ingest_document_content(
        workspace_id="default",
        knowledge_base_id="kb_default",
        doc_id=manifest["doc_id"],
        content="# Contract\nParty B must deliver before June.",
        filename="contract.md",
        content_type="text/markdown",
    )
    return service, indexed


def test_reindex_embeddings_writes_vectors_before_switching_active_embedding(tmp_path) -> None:
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore()
    service, manifest = _indexed_service(
        tmp_path,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )

    result = service.reindex_embeddings(
        workspace_id="default",
        knowledge_base_id="kb_default",
        provider="openai_compatible",
        model="mimo-embedding-test",
        dimension=3,
        collection="kb_default_mimo_v1",
        job_id="job_reindex_001",
    )

    active = service.get_active_embedding("default", "kb_default")
    chunks = service.get_chunks("default", "kb_default", manifest["doc_id"])
    assert active["collection"] == "kb_default_mimo_v1"
    assert result["chunk_count"] == len(chunks)
    assert embedding_client.calls[0]["texts"] == [chunk["text"] for chunk in chunks]
    assert embedding_client.calls[0]["model"] == "mimo-embedding-test"
    assert vector_store.collections == [
        {"collection": "kb_default_mimo_v1", "dimension": 3}
    ]
    records = vector_store.upserts[0]["records"]
    assert vector_store.upserts[0]["collection"] == "kb_default_mimo_v1"
    assert {record["chunk_id"] for record in records} == {chunk["chunk_id"] for chunk in chunks}
    assert all(record["vector"] for record in records)
    assert {
        chunk["metadata_filter"]["embedding_collection"]
        for chunk in chunks
    } == {"kb_default_mimo_v1"}


def test_reindex_embeddings_keeps_previous_active_embedding_when_vector_upsert_fails(
    tmp_path,
) -> None:
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore(fail_upsert=True)
    service, manifest = _indexed_service(
        tmp_path,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )
    before = service.get_active_embedding("default", "kb_default")

    with pytest.raises(RuntimeError, match="milvus unavailable"):
        service.reindex_embeddings(
            workspace_id="default",
            knowledge_base_id="kb_default",
            provider="openai_compatible",
            model="mimo-embedding-test",
            dimension=3,
            collection="kb_default_mimo_v1",
            job_id="job_reindex_001",
        )

    after = service.get_active_embedding("default", "kb_default")
    chunks = service.get_chunks("default", "kb_default", manifest["doc_id"])
    assert after["collection"] == before["collection"]
    assert "kb_default_mimo_v1" not in {
        chunk.get("metadata_filter", {}).get("embedding_collection") for chunk in chunks
    }


def test_reindex_embeddings_requires_vector_backend_without_switching_active(
    tmp_path,
) -> None:
    service, _ = _indexed_service(tmp_path)
    before = service.get_active_embedding("default", "kb_default")

    with pytest.raises(ValueError, match="Embedding reindex requires"):
        service.reindex_embeddings(
            workspace_id="default",
            knowledge_base_id="kb_default",
            provider="openai_compatible",
            model="mimo-embedding-test",
            dimension=3,
            collection="kb_default_mimo_v1",
            job_id="job_reindex_missing_backend",
        )

    after = service.get_active_embedding("default", "kb_default")
    assert after["collection"] == before["collection"]


def test_reindex_embeddings_rejects_vector_dimension_mismatch_without_switching_active(
    tmp_path,
) -> None:
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore()
    service, _ = _indexed_service(
        tmp_path,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )
    before = service.get_active_embedding("default", "kb_default")

    with pytest.raises(ValueError, match="Embedding vector dimension"):
        service.reindex_embeddings(
            workspace_id="default",
            knowledge_base_id="kb_default",
            provider="openai_compatible",
            model="mimo-embedding-test",
            dimension=4,
            collection="kb_default_mimo_v1",
        )

    active = service.get_active_embedding("default", "kb_default")
    assert active["collection"] == before["collection"]


def test_reindex_embeddings_rejects_partial_vector_upsert_without_switching_active(
    tmp_path,
) -> None:
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore(upserted_count=0)
    service, _ = _indexed_service(
        tmp_path,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )
    before = service.get_active_embedding("default", "kb_default")

    with pytest.raises(ValueError, match="Vector store upsert count"):
        service.reindex_embeddings(
            workspace_id="default",
            knowledge_base_id="kb_default",
            provider="openai_compatible",
            model="mimo-embedding-test",
            dimension=3,
            collection="kb_default_mimo_v1",
        )

    active = service.get_active_embedding("default", "kb_default")
    assert active["collection"] == before["collection"]


def test_reindex_embeddings_rejects_vector_adapter_reported_partial_failure(
    tmp_path,
) -> None:
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore(failed_count=1)
    service, _ = _indexed_service(
        tmp_path,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )
    before = service.get_active_embedding("default", "kb_default")

    with pytest.raises(ValueError, match="Vector store upsert completed with failures"):
        service.reindex_embeddings(
            workspace_id="default",
            knowledge_base_id="kb_default",
            provider="openai_compatible",
            model="mimo-embedding-test",
            dimension=3,
            collection="kb_default_mimo_v1",
        )

    active = service.get_active_embedding("default", "kb_default")
    assert active["collection"] == before["collection"]


def test_reindex_embeddings_rejects_vector_adapter_reported_failure(
    tmp_path,
) -> None:
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore(ok=False)
    service, _ = _indexed_service(
        tmp_path,
        embedding_client=embedding_client,
        vector_store=vector_store,
    )
    before = service.get_active_embedding("default", "kb_default")

    with pytest.raises(ValueError, match="Vector store upsert failed"):
        service.reindex_embeddings(
            workspace_id="default",
            knowledge_base_id="kb_default",
            provider="openai_compatible",
            model="mimo-embedding-test",
            dimension=3,
            collection="kb_default_mimo_v1",
        )

    active = service.get_active_embedding("default", "kb_default")
    assert active["collection"] == before["collection"]
