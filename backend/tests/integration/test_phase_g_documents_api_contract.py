from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from io import BytesIO
from typing import Any
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_job_worker, get_object_store
from app.jobs.handlers import build_document_ingestion_handler, build_embedding_reindex_handler
from app.jobs.service import JobService
from app.jobs.worker import JobWorker
from app.main import app
from app.storage.local_object_store import LocalObjectStore
from app.vector_store.milvus_http import MilvusVectorStoreError


@pytest.fixture()
def object_store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def embed_documents(self, **kwargs: Any) -> list[list[float]]:
        self.calls.append(kwargs)
        dimension = int(kwargs["dimension"])
        return [
            [float(index), *([0.1] * max(0, dimension - 1))]
            for index, _ in enumerate(kwargs["texts"])
        ]


class _FakeVectorStore:
    def __init__(
        self,
        *,
        fail_upsert: bool = False,
        fail_exception: Exception | None = None,
    ) -> None:
        self.collections: list[dict[str, Any]] = []
        self.upserts: list[dict[str, Any]] = []
        self.fail_upsert = fail_upsert
        self.fail_exception = fail_exception

    def ensure_collection(self, **kwargs: Any) -> None:
        self.collections.append(kwargs)

    def upsert(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_exception is not None:
            raise self.fail_exception
        if self.fail_upsert:
            raise RuntimeError("milvus unavailable")
        self.upserts.append(kwargs)
        return {"ok": True, "upserted_count": len(kwargs["records"])}


@pytest.fixture()
def embedding_backend() -> tuple[_FakeEmbeddingClient, _FakeVectorStore]:
    return _FakeEmbeddingClient(), _FakeVectorStore()


@pytest.fixture()
def client(
    object_store: LocalObjectStore,
    embedding_backend: tuple[_FakeEmbeddingClient, _FakeVectorStore],
) -> Iterator[TestClient]:
    embedding_client, vector_store = embedding_backend

    def build_test_worker() -> JobWorker:
        return JobWorker(
            JobService(object_store, runtime_instance_id="rt_local"),
            {
                "document_ingestion_job": build_document_ingestion_handler(object_store),
                "embedding_reindex_job": build_embedding_reindex_handler(
                    object_store,
                    embedding_client=embedding_client,
                    vector_store=vector_store,
                ),
            },
        )

    app.dependency_overrides[get_object_store] = lambda: object_store
    app.dependency_overrides[get_job_worker] = build_test_worker
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()


def _assert_no_secret_material(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str).lower()
    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "plaintext" not in serialized
    assert "ciphertext" not in serialized
    assert "secret" not in serialized


def _upload_document(client: TestClient, file_name: str = "contract.md") -> dict[str, Any]:
    response = client.post(
        "/workspaces/default/knowledge-bases/kb_default/documents",
        files={"file": (file_name, b"# Contract\nParty A signs with Party B.\n", "text/markdown")},
        data={"source_file_name": file_name},
    )
    assert response.status_code in {200, 201, 202}
    body = response.json()
    _assert_no_secret_material(body)
    return body


def _write_chunked_document_fixture(object_store: LocalObjectStore) -> None:
    base = "workspaces/default/knowledge_bases/kb_default/documents/doc_seed"
    manifest = {
        "workspace_id": "default",
        "knowledge_base_id": "kb_default",
        "doc_id": "doc_seed",
        "doc_version_id": "docv_seed",
        "source_file_name": "seed.md",
        "mime_type": "text/markdown",
        "file_sha256": "sha256-seed",
        "title": "Seed Contract",
        "parser_quality": "full",
        "ingestion_status": "chunked",
        "embedding_status": "pending",
        "chunk_total": 2,
        "chunk_embedded": 0,
        "chunk_failed": 0,
        "search_available": False,
        "last_job_id": "job_seed",
        "warnings": [],
    }
    chunks = [
        {
            "chunk_id": "chk_seed_001",
            "doc_id": "doc_seed",
            "doc_version_id": "docv_seed",
            "chunk_index": 0,
            "parent_chunk_id": "pchk_seed_001",
            "section_path": ["Seed Contract", "Parties"],
            "text": "Party A is Guangzhou Xinghe. Party B is Shenzhen Lanhai.",
            "source_block_ids": ["blk_001"],
            "metadata_filter": {
                "workspace_id": "default",
                "knowledge_base_id": "kb_default",
                "doc_id": "doc_seed",
                "doc_version_id": "docv_seed",
                "chunk_id": "chk_seed_001",
            },
        },
        {
            "chunk_id": "chk_seed_002",
            "doc_id": "doc_seed",
            "doc_version_id": "docv_seed",
            "chunk_index": 1,
            "parent_chunk_id": "pchk_seed_001",
            "section_path": ["Seed Contract", "Delivery"],
            "text": "Party B must deliver equipment before 2026-06-30.",
            "source_block_ids": ["blk_002"],
            "metadata_filter": {
                "workspace_id": "default",
                "knowledge_base_id": "kb_default",
                "doc_id": "doc_seed",
                "doc_version_id": "docv_seed",
                "chunk_id": "chk_seed_002",
            },
        },
    ]
    object_store.write_text(
        f"{base}/manifest.json",
        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
    )
    object_store.write_text(
        f"{base}/chunks.json",
        json.dumps({"chunks": chunks}, ensure_ascii=False, sort_keys=True),
    )
    for chunk in chunks:
        object_store.write_text(
            f"{base}/chunks/{chunk['chunk_id']}.json",
            json.dumps(chunk, ensure_ascii=False, sort_keys=True),
        )
    object_store.write_text(
        "workspaces/default/knowledge_bases/kb_default/documents_index.json",
        json.dumps({"documents": [manifest]}, ensure_ascii=False, sort_keys=True),
    )


def _docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    strings = []
    string_indexes: dict[str, int] = {}
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            if value not in string_indexes:
                string_indexes[value] = len(strings)
                strings.append(value)
            cell_ref = f"{chr(64 + column_index)}{row_index}"
            cells.append(f'<c r="{cell_ref}" t="s"><v>{string_indexes[value]}</v></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in strings)
        + "</sst>"
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def test_upload_document_creates_manifest_and_document_ingestion_job(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    body = _upload_document(client)

    assert body["workspace_id"] == "default"
    assert body["knowledge_base_id"] == "kb_default"
    assert body["doc_id"]
    assert body["doc_version_id"]
    assert body["ingestion_status"] == "uploaded"
    assert body["job_id"]
    assert body["job_type"] == "document_ingestion_job"
    assert body["search_available"] is False
    keys = object_store.list_keys("workspaces/default")
    assert any(key.endswith(f"documents/{body['doc_id']}/manifest.json") for key in keys)
    assert any(key.endswith(f"documents/{body['doc_id']}/original/contract.md") for key in keys)
    assert any(key.endswith(f"jobs/{body['job_id']}/manifest.json") for key in keys)
    job = client.get(f"/workspaces/default/jobs/{body['job_id']}").json()
    assert job["status"] == "queued"


def test_document_ingestion_job_worker_chunks_uploaded_document(
    client: TestClient,
) -> None:
    body = _upload_document(client, file_name="worker-contract.md")

    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "document_ingestion_job"},
    )
    detail_response = client.get(
        f"/workspaces/default/knowledge-bases/kb_default/documents/{body['doc_id']}"
    )
    chunks_response = client.get(
        f"/workspaces/default/knowledge-bases/kb_default/documents/{body['doc_id']}/chunks"
    )

    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["job_id"] == body["job_id"]
    assert worker_body["job"]["status"] == "succeeded"
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["ingestion_status"] == "chunked"
    assert detail["embedding_status"] == "pending"
    assert detail["search_available"] is False
    assert chunks_response.status_code == 200
    assert chunks_response.json()["chunks"]
    _assert_no_secret_material([worker_body, detail_response.json(), chunks_response.json()])


@pytest.mark.parametrize(
    ("file_name", "mime_type", "payload"),
    [
        ("notes.txt", "text/plain", b"plain text note"),
        ("page.html", "text/html", b"<html><body><h1>Title</h1><p>Hello</p></body></html>"),
        ("table.csv", "text/csv", b"name,role\nAlice,Owner\n"),
        ("script.py", "text/x-python", b"print('hello')\n"),
        ("report.pdf", "application/pdf", b"%PDF-1.4\n"),
        (
            "brief.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"PK\x03\x04docx-placeholder",
        ),
        (
            "sheet.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK\x03\x04xlsx-placeholder",
        ),
        ("scan.png", "image/png", b"\x89PNG\r\n\x1a\n"),
    ],
)
def test_p0_file_type_matrix_uploads_with_manifest_entry(
    client: TestClient,
    file_name: str,
    mime_type: str,
    payload: bytes,
) -> None:
    response = client.post(
        "/workspaces/default/knowledge-bases/kb_default/documents",
        files={"file": (file_name, payload, mime_type)},
        data={"source_file_name": file_name},
    )

    assert response.status_code in {200, 201, 202}
    body = response.json()
    assert body["ingestion_status"] == "uploaded"
    assert body["parse_status"] == "uploaded"
    assert body["chunk_status"] == "pending"
    assert body["size_bytes"] == len(payload)
    assert body["file_sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["job_type"] == "document_ingestion_job"
    _assert_no_secret_material(body)


def test_upload_image_original_preserves_bytes_and_failed_manifest_after_parser_job(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    payload = b"\x89PNG\r\n\x1a\nbinary-\x00-content"
    response = client.post(
        "/workspaces/default/knowledge-bases/kb_default/documents",
        files={"file": ("scan.png", payload, "image/png")},
        data={"source_file_name": "scan.png"},
    )
    body = response.json()

    assert response.status_code in {200, 201, 202}
    assert body["size_bytes"] == len(payload)
    assert body["file_sha256"] == hashlib.sha256(payload).hexdigest()
    assert object_store.read_bytes(body["object_keys"]["original"]) == payload

    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "document_ingestion_job"},
    )
    detail_response = client.get(
        f"/workspaces/default/knowledge-bases/kb_default/documents/{body['doc_id']}"
    )

    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["status"] == "failed"
    assert worker_body["job"]["current_stage"] == "parse_chunk_index"
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["ingestion_status"] == "failed"
    assert detail["parse_status"] == "failed"
    assert detail["chunk_status"] == "failed"
    assert detail["parser_quality"] == "failed"
    assert detail["warnings"][0]["error_type"] == "parser_backend_not_configured"
    assert any(
        key.endswith(".jsonl")
        and f"documents/{body['doc_id']}/chunks/errors/" in key
        for key in object_store.list_keys("workspaces/default")
    )
    _assert_no_secret_material([worker_body, detail])


@pytest.mark.parametrize(
    ("file_name", "mime_type", "payload", "expected_text"),
    [
        (
            "brief.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            lambda: _docx_bytes(["Party A signs with Party B.", "Delivery happens in June."]),
            "Party A signs with Party B",
        ),
        (
            "sheet.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            lambda: _xlsx_bytes([["name", "role"], ["Alice", "Owner"], ["Bob", "Reviewer"]]),
            "Reviewer",
        ),
        (
            "report.pdf",
            "application/pdf",
            lambda: b"%PDF-1.4\nBT /F1 12 Tf (Party B must deliver equipment.) Tj ET\n%%EOF",
            "Party B must deliver equipment",
        ),
    ],
)
def test_binary_text_formats_index_with_parser_backend(
    client: TestClient,
    file_name: str,
    mime_type: str,
    payload,
    expected_text: str,
) -> None:
    body = client.post(
        "/workspaces/default/knowledge-bases/kb_default/documents",
        files={"file": (file_name, payload(), mime_type)},
        data={"source_file_name": file_name},
    ).json()

    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "document_ingestion_job"},
    )
    chunks_response = client.get(
        f"/workspaces/default/knowledge-bases/kb_default/documents/{body['doc_id']}/chunks"
    )

    assert worker_response.status_code == 200
    assert worker_response.json()["job"]["status"] == "succeeded"
    assert chunks_response.status_code == 200
    chunk_text = "\n".join(chunk["text"] for chunk in chunks_response.json()["chunks"])
    assert expected_text in chunk_text
    _assert_no_secret_material([worker_response.json(), chunks_response.json()])


def test_csv_upload_indexes_with_tabular_parser_metadata(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    payload = b"name,role\nAlice,Owner\nBob,Reviewer\n"
    body = client.post(
        "/workspaces/default/knowledge-bases/kb_default/documents",
        files={"file": ("people.csv", payload, "text/csv")},
        data={"source_file_name": "people.csv"},
    ).json()

    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "document_ingestion_job"},
    )
    detail_response = client.get(
        f"/workspaces/default/knowledge-bases/kb_default/documents/{body['doc_id']}"
    )
    chunks_response = client.get(
        f"/workspaces/default/knowledge-bases/kb_default/documents/{body['doc_id']}/chunks"
    )

    assert worker_response.status_code == 200
    assert worker_response.json()["job"]["status"] == "succeeded"
    detail = detail_response.json()
    assert detail["ingestion_status"] == "chunked"
    assert detail["embedding_status"] == "pending"
    assert detail["search_available"] is False
    parsed = json.loads(object_store.read_text(detail["object_keys"]["document_representation"]))
    assert parsed["document_format"] == "csv"
    assert parsed["metadata"]["row_count"] == 2
    assert parsed["metadata"]["column_count"] == 2
    assert chunks_response.status_code == 200
    chunk_text = "\n".join(chunk["text"] for chunk in chunks_response.json()["chunks"])
    assert "Alice" in chunk_text
    assert "Reviewer" in chunk_text
    _assert_no_secret_material([worker_response.json(), detail, chunks_response.json()])


def test_embedding_reindex_job_switches_active_embedding_after_worker_success(
    client: TestClient,
    embedding_backend: tuple[_FakeEmbeddingClient, _FakeVectorStore],
) -> None:
    embedding_client, vector_store = embedding_backend
    body = _upload_document(client, file_name="embedding-contract.md")
    ingest_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "document_ingestion_job"},
    )
    before_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/active-embedding"
    )
    reindex_response = client.post(
        "/workspaces/default/knowledge-bases/kb_default/embedding/reindex",
        json={
            "provider": "openai_compatible",
            "model": "mimo-embedding-test",
            "dimension": 3,
            "idempotency_key": "reindex-kb-default",
        },
    )
    queued_active_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/active-embedding"
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "embedding_reindex_job"},
    )
    after_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/active-embedding"
    )
    chunks_response = client.get(
        f"/workspaces/default/knowledge-bases/kb_default/documents/{body['doc_id']}/chunks"
    )

    assert ingest_response.status_code == 200
    assert ingest_response.json()["job"]["status"] == "succeeded"
    assert before_response.status_code == 200
    before_active = before_response.json()
    assert before_active["collection"] == "object_store_lexical_fallback"
    assert reindex_response.status_code == 200
    reindex_body = reindex_response.json()
    assert reindex_body["job_type"] == "embedding_reindex_job"
    assert reindex_body["job_status"] == "queued"
    assert queued_active_response.status_code == 200
    assert queued_active_response.json()["collection"] == before_active["collection"]
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["job_id"] == reindex_body["job_id"]
    assert worker_body["job"]["status"] == "succeeded"
    artifacts = worker_body["job"]["leaf_state"]["artifacts"]
    assert artifacts[0]["artifact_type"] == "embedding_collection"
    assert after_response.status_code == 200
    after_active = after_response.json()
    assert after_active["provider"] == "openai_compatible"
    assert after_active["model"] == "mimo-embedding-test"
    assert after_active["dimension"] == 3
    assert after_active["collection"] != before_active["collection"]
    assert after_active["previous_collection"] == before_active["collection"]
    assert embedding_client.calls
    assert vector_store.collections == [
        {"collection": after_active["collection"], "dimension": 3}
    ]
    assert vector_store.upserts
    assert vector_store.upserts[0]["collection"] == after_active["collection"]
    assert chunks_response.status_code == 200
    assert {
        chunk["metadata_filter"]["embedding_collection"]
        for chunk in chunks_response.json()["chunks"]
    } == {after_active["collection"]}
    _assert_no_secret_material([reindex_body, worker_body, after_active, chunks_response.json()])


def test_embedding_reindex_job_uses_default_embedding_config_when_payload_omits_overrides(
    client: TestClient,
    embedding_backend: tuple[_FakeEmbeddingClient, _FakeVectorStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedding_client, vector_store = embedding_backend
    monkeypatch.setenv("DEFAULT_EMBEDDING_MODEL_NAME", "mimo-default-embedding")
    monkeypatch.setenv("DEFAULT_EMBEDDING_DIMENSION", "3")
    _upload_document(client, file_name="embedding-defaults.md")
    ingest_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "document_ingestion_job"},
    )

    reindex_response = client.post(
        "/workspaces/default/knowledge-bases/kb_default/embedding/reindex",
        json={"idempotency_key": "reindex-kb-default-env-defaults"},
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "embedding_reindex_job"},
    )
    after_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/active-embedding"
    )

    assert ingest_response.status_code == 200
    assert reindex_response.status_code == 200
    assert worker_response.status_code == 200
    assert worker_response.json()["job"]["status"] == "succeeded"
    after_active = after_response.json()
    assert after_active["provider"] == "openai_compatible"
    assert after_active["model"] == "mimo-default-embedding"
    assert after_active["dimension"] == 3
    assert embedding_client.calls[0]["model"] == "mimo-default-embedding"
    assert embedding_client.calls[0]["dimension"] == 3
    assert vector_store.collections[0]["dimension"] == 3
    _assert_no_secret_material([reindex_response.json(), worker_response.json(), after_active])


def test_embedding_reindex_job_keeps_active_embedding_when_vector_backend_fails(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    body = _upload_document(client, file_name="embedding-failure.md")
    ingest_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "document_ingestion_job"},
    )
    before_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/active-embedding"
    )

    failing_embedding = _FakeEmbeddingClient()
    failing_vector_store = _FakeVectorStore(fail_upsert=True)

    def build_failing_worker() -> JobWorker:
        return JobWorker(
            JobService(object_store, runtime_instance_id="rt_local"),
            {
                "embedding_reindex_job": build_embedding_reindex_handler(
                    object_store,
                    embedding_client=failing_embedding,
                    vector_store=failing_vector_store,
                ),
            },
        )

    app.dependency_overrides[get_job_worker] = build_failing_worker

    reindex_response = client.post(
        "/workspaces/default/knowledge-bases/kb_default/embedding/reindex",
        json={
            "provider": "openai_compatible",
            "model": "mimo-embedding-test",
            "dimension": 3,
            "idempotency_key": "reindex-kb-default-failure",
        },
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "embedding_reindex_job"},
    )
    after_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/active-embedding"
    )
    chunks_response = client.get(
        f"/workspaces/default/knowledge-bases/kb_default/documents/{body['doc_id']}/chunks"
    )

    assert ingest_response.status_code == 200
    assert before_response.status_code == 200
    before_active = before_response.json()
    assert reindex_response.status_code == 200
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["status"] == "failed"
    assert worker_body["job"]["current_stage"] == "embedding_insert"
    assert after_response.status_code == 200
    assert after_response.json()["collection"] == before_active["collection"]
    assert chunks_response.status_code == 200
    assert "mimo-embedding-test" not in json.dumps(chunks_response.json(), default=str)
    _assert_no_secret_material([reindex_response.json(), worker_body, after_response.json()])


def test_embedding_reindex_job_preserves_non_retryable_vector_error(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    _upload_document(client, file_name="embedding-dimension-failure.md")
    ingest_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "document_ingestion_job"},
    )
    before_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/active-embedding"
    )

    failing_embedding = _FakeEmbeddingClient()
    failing_vector_store = _FakeVectorStore(
        fail_exception=MilvusVectorStoreError(
            "dimension_mismatch",
            "Milvus vector dimension does not match collection schema.",
            retryable=False,
        )
    )

    def build_failing_worker() -> JobWorker:
        return JobWorker(
            JobService(object_store, runtime_instance_id="rt_local"),
            {
                "embedding_reindex_job": build_embedding_reindex_handler(
                    object_store,
                    embedding_client=failing_embedding,
                    vector_store=failing_vector_store,
                ),
            },
        )

    app.dependency_overrides[get_job_worker] = build_failing_worker

    reindex_response = client.post(
        "/workspaces/default/knowledge-bases/kb_default/embedding/reindex",
        json={
            "provider": "openai_compatible",
            "model": "mimo-embedding-test",
            "dimension": 3,
            "idempotency_key": "reindex-kb-default-dimension-failure",
        },
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "embedding_reindex_job"},
    )
    after_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/active-embedding"
    )
    events_response = client.get(
        f"/workspaces/default/jobs/{reindex_response.json()['job_id']}/events"
    )

    assert ingest_response.status_code == 200
    assert ingest_response.json()["job"]["status"] == "succeeded"
    assert before_response.status_code == 200
    assert reindex_response.status_code == 200
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["job"]["status"] == "failed"
    assert after_response.json()["collection"] == before_response.json()["collection"]
    last_event = events_response.json()["events"][-1]
    assert last_event["type"] == "job_failed"
    assert last_event["payload"]["error_type"] == "dimension_mismatch"
    assert last_event["payload"]["retryable"] is False
    _assert_no_secret_material([reindex_response.json(), worker_body, events_response.json()])


def test_upload_rejects_unsupported_file_type(client: TestClient) -> None:
    response = client.post(
        "/workspaces/default/knowledge-bases/kb_default/documents",
        files={"file": ("malware.exe", b"not a supported document", "application/octet-stream")},
    )

    assert response.status_code == 415
    body = response.json()
    assert body["error_type"] == "unsupported_document_type"
    _assert_no_secret_material(body)


def test_list_detail_and_chunks_read_from_local_object_store(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    _write_chunked_document_fixture(object_store)

    list_response = client.get("/workspaces/default/knowledge-bases/kb_default/documents")
    detail_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/documents/doc_seed"
    )
    chunks_response = client.get(
        "/workspaces/default/knowledge-bases/kb_default/documents/doc_seed/chunks"
    )

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert chunks_response.status_code == 200
    documents = list_response.json()["documents"]
    detail = detail_response.json()
    chunks = chunks_response.json()["chunks"]
    assert [document["doc_id"] for document in documents] == ["doc_seed"]
    assert detail["doc_id"] == "doc_seed"
    assert detail["ingestion_status"] == "chunked"
    assert detail["embedding_status"] == "pending"
    assert detail["search_available"] is False
    assert detail["chunk_total"] == 2
    assert [chunk["chunk_id"] for chunk in chunks] == ["chk_seed_001", "chk_seed_002"]
    assert all(chunk["text"] for chunk in chunks)
    _assert_no_secret_material([documents, detail, chunks])


def test_document_detail_returns_404_for_missing_document(client: TestClient) -> None:
    response = client.get("/workspaces/default/knowledge-bases/kb_default/documents/missing")

    assert response.status_code == 404
    body = response.json()
    assert body.get("error_type") == "document_not_found"
    _assert_no_secret_material(body)
