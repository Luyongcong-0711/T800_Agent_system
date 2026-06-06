from __future__ import annotations

import json
from typing import Any

from app.jobs.service import JobService
from app.rag_pipeline.ingestion import DocumentIngestionService
from app.rag_pipeline.models import DocumentChunk, ParsedDocument
from app.rag_pipeline.paths import active_embedding_key
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest, JobDetailResponse, RetryJobRequest
from app.storage.local_object_store import LocalObjectStore


class _Parser:
    version = "test_parser_v1"

    def parse(self, content: bytes | str, **_: Any) -> ParsedDocument:
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        return ParsedDocument(
            text=text,
            document_format="text",
            source_sha256="sha256-test",
            byte_size=len(text.encode("utf-8")),
            blocks=[{"block_id": "blk_001", "text": text}],
            metadata={},
        )


class _Chunker:
    version = "test_chunker_v1"

    def chunk(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        doc_version_id: str,
        parsed_document: ParsedDocument,
        metadata: dict[str, Any],
    ) -> list[DocumentChunk]:
        base_metadata = {
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "doc_id": doc_id,
            "doc_version_id": doc_version_id,
            **metadata,
        }
        return [
            DocumentChunk(
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
                doc_version_id=doc_version_id,
                chunk_id="chk_ok",
                parent_chunk_id="pchk_001",
                chunk_index=0,
                chunk_type="paragraph",
                text="stable successful chunk",
                section_path=["Root"],
                page_start=None,
                page_end=None,
                char_start=0,
                char_end=23,
                source_block_ids=["blk_001"],
                token_count=3,
                text_hash="hash-ok",
                metadata_filter={**base_metadata, "chunk_id": "chk_ok"},
                source={},
            ),
            DocumentChunk(
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
                doc_version_id=doc_version_id,
                chunk_id="chk_retry",
                parent_chunk_id="pchk_001",
                chunk_index=1,
                chunk_type="paragraph",
                text="transient failed chunk",
                section_path=["Root"],
                page_start=None,
                page_end=None,
                char_start=24,
                char_end=46,
                source_block_ids=["blk_001"],
                token_count=3,
                text_hash="hash-retry",
                metadata_filter={**base_metadata, "chunk_id": "chk_retry"},
                source={},
            ),
        ]


class _EmbeddingClient:
    def embed_documents(self, **kwargs: Any) -> list[list[float]]:
        return [[float(index), 0.1, 0.2] for index, _ in enumerate(kwargs["texts"])]


class _FlakyVectorStore:
    def __init__(self) -> None:
        self.fail_retry_chunk_once = True
        self.upserted_chunk_ids: list[str] = []

    def ensure_collection(self, **_: Any) -> None:
        return None

    def upsert(self, **kwargs: Any) -> dict[str, Any]:
        chunk_id = kwargs["records"][0]["chunk_id"]
        self.upserted_chunk_ids.append(chunk_id)
        if chunk_id == "chk_retry" and self.fail_retry_chunk_once:
            self.fail_retry_chunk_once = False
            raise RuntimeError("temporary index outage")
        return {"ok": True, "upserted_count": len(kwargs["records"])}


def _activate_external_embedding(object_store: LocalObjectStore) -> None:
    object_store.write_text(
        active_embedding_key("default", "kb_default"),
        json.dumps(
            {
                "schema_version": 1,
                "workspace_id": "default",
                "knowledge_base_id": "kb_default",
                "version_id": "embv_external_test",
                "provider": "openai_compatible",
                "model": "mimo-embedding-test",
                "dimension": 3,
                "collection": "kb_default_mimo_embedding_test",
                "status": "active",
                "revision": 2,
            },
            sort_keys=True,
        ),
    )


def test_document_ingestion_records_partial_success_and_retry_skips_successful_chunks(
    tmp_path,
) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    vector_store = _FlakyVectorStore()
    service = DocumentIngestionService(
        object_store,
        parser=_Parser(),
        chunker=_Chunker(),
        embedding_client=_EmbeddingClient(),
        vector_store=vector_store,
    )
    service.create_uploaded_document(
        workspace_id="default",
        knowledge_base_id="kb_default",
        doc_id="doc_partial",
        doc_version_id="docv_partial",
        content="source document",
        source_file_name="partial.txt",
        mime_type="text/plain",
        last_job_id="job_first",
    )
    _activate_external_embedding(object_store)

    first = service.ingest_document_content(
        workspace_id="default",
        knowledge_base_id="kb_default",
        doc_id="doc_partial",
        content="source document",
        filename="partial.txt",
        content_type="text/plain",
        job_id="job_first",
    )

    assert first["ingestion_status"] == "partial_success"
    assert first["chunk_total"] == 2
    assert first["chunk_embedded"] == 1
    assert first["chunk_failed"] == 1
    assert first["retryable"] is True
    assert first["failure_strategy"] == "retry_failed_chunks"
    assert first["last_error"]["chunk_id"] == "chk_retry"
    assert first["warnings"][-1]["chunk_id"] == "chk_retry"
    assert first["warnings"][-1]["retryable"] is True
    chunks = service.get_chunks("default", "kb_default", "doc_partial")
    by_chunk_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    assert by_chunk_id["chk_ok"]["chunk_status"] == "indexed"
    assert by_chunk_id["chk_retry"]["chunk_status"] == "failed"
    assert by_chunk_id["chk_retry"]["search_index_status"] == "failed"
    assert by_chunk_id["chk_retry"]["retryable"] is True
    assert by_chunk_id["chk_retry"]["last_error"]["error_type"] == "RuntimeError"
    assert by_chunk_id["chk_retry"]["metadata_filter"]["retryable"] is True
    assert by_chunk_id["chk_retry"]["metadata_filter"]["last_error"]["error_type"] == "RuntimeError"
    assert any(
        key.endswith(".jsonl") and "/documents/doc_partial/chunks/errors/" in key
        for key in object_store.list_keys("workspaces/default")
    )

    second = service.ingest_document_content(
        workspace_id="default",
        knowledge_base_id="kb_default",
        doc_id="doc_partial",
        content="source document",
        filename="partial.txt",
        content_type="text/plain",
        job_id="job_retry",
    )

    assert second["ingestion_status"] == "indexed"
    assert second["retryable"] is False
    assert second["failure_strategy"] is None
    assert second["last_error"] is None
    assert second["chunk_embedded"] == 2
    assert second["chunk_failed"] == 0
    assert vector_store.upserted_chunk_ids == ["chk_ok", "chk_retry", "chk_retry"]
    chunks_index = json.loads(object_store.read_text(second["object_keys"]["chunks"]))
    skipped = {
        chunk["chunk_id"]: chunk.get("retry_skipped", False)
        for chunk in chunks_index["chunks"]
    }
    assert skipped == {"chk_ok": True, "chk_retry": False}


def test_partial_success_job_status_is_response_model_safe_and_retryable(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = JobService(object_store, runtime_instance_id="rt_test")
    identity = RuntimeIdentity()
    created = service.create_job(
        "default",
        identity,
        CreateJobRequest(
            job_type="document_ingestion_job",
            target_scope={
                "scope_type": "document_version",
                "knowledge_base_id": "kb_default",
                "doc_id": "doc_partial",
            },
        ),
    )
    claimed = service.claim_next_job_for_worker(
        "default",
        job_types=["document_ingestion_job"],
    )

    assert claimed is not None
    partial = service.mark_job_partial_success(
        "default",
        created["job_id"],
        stage="partial_success",
        message="Some chunks failed.",
        error_type="document_chunk_partial_failure",
        retryable=True,
        fencing_token=str(claimed["owner"]["fencing_token"]),
    )

    assert JobDetailResponse(**partial).status == "partial_success"
    retried = service.retry_job("default", created["job_id"], identity, RetryJobRequest())
    assert retried["status"] == "queued"
    assert retried["manifest"]["retry_of_job_id"] == created["job_id"]
