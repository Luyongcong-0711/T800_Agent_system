from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_job_worker, get_object_store
from app.jobs.handlers import build_graph_build_handler
from app.jobs.service import JobService
from app.jobs.worker import JobWorker
from app.main import app
from app.storage.local_object_store import LocalObjectStore


class _FakeGraphWriter:
    def __init__(self, *, ok: bool = True) -> None:
        self.batches: list[dict[str, Any]] = []
        self.ok = ok

    def write_graph_batch_internal(self, batch: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.batches.append({"batch": batch, **kwargs})
        return {
            "ok": self.ok,
            "deduped": False,
            "error_type": None if self.ok else "neo4j_write_failed",
        }

    def write_graph_batch(self, **kwargs: Any) -> dict[str, Any]:
        self.batches.append(kwargs)
        return {"ok": True, "deduped": False}


def _graph_build_module() -> Any:
    candidates = (
        "app.graph_pipeline.build_job",
        "app.graph_pipeline.builder",
        "app.jobs.workers.graph_build",
        "app.api.graph",
    )
    for module_name in candidates:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    pytest.fail(
        "Phase H requires a graph build job or graph build API module that can build "
        "graph artifacts from existing chunks and fail safely when the Neo4j writer "
        "is not configured."
    )


def _write_chunked_pending_chunks(object_store: LocalObjectStore) -> None:
    base = "workspaces/default/knowledge_bases/kb_default/documents/doc_001"
    manifest = {
        "workspace_id": "default",
        "knowledge_base_id": "kb_default",
        "doc_id": "doc_001",
        "doc_version_id": "docv_001",
        "ingestion_status": "chunked",
        "embedding_status": "pending",
        "graph_status": "pending",
        "graphrag_available": False,
        "chunk_total": 2,
        "chunk_embedded": 0,
        "chunk_failed": 0,
        "search_available": False,
    }
    chunks = [
        {
            "chunk_id": "chk_001",
            "doc_id": "doc_001",
            "doc_version_id": "docv_001",
            "chunk_index": 0,
            "text": "Party A signs with Party B.",
            "source": {"source_file_name": "contract.md", "page_start": 1},
            "metadata_filter": {
                "workspace_id": "default",
                "knowledge_base_id": "kb_default",
                "doc_id": "doc_001",
                "doc_version_id": "docv_001",
                "chunk_id": "chk_001",
            },
        },
        {
            "chunk_id": "chk_002",
            "doc_id": "doc_001",
            "doc_version_id": "docv_001",
            "chunk_index": 1,
            "text": "Party B delivers equipment.",
            "source": {"source_file_name": "contract.md", "page_start": 2},
            "metadata_filter": {
                "workspace_id": "default",
                "knowledge_base_id": "kb_default",
                "doc_id": "doc_001",
                "doc_version_id": "docv_001",
                "chunk_id": "chk_002",
            },
        },
    ]
    object_store.write_text(f"{base}/manifest.json", json.dumps(manifest, sort_keys=True))
    object_store.write_text(
        f"{base}/chunks/chunks.json",
        json.dumps({"chunks": chunks}, sort_keys=True),
    )
    for chunk in chunks:
        object_store.write_text(
            f"{base}/chunks/{chunk['chunk_id']}.json",
            json.dumps(chunk, sort_keys=True),
        )


def _invoke_graph_build(
    module: Any,
    object_store: LocalObjectStore,
    graph_writer: _FakeGraphWriter,
) -> Any:
    for builder_name in ("build_graph_build_job_handler", "build_graph_build_service"):
        builder = getattr(module, builder_name, None)
        if builder is None:
            continue
        signature = inspect.signature(builder)
        kwargs = {
            name: value
            for name, value in {
                "object_store": object_store,
                "graph_writer": graph_writer,
                "neo4j": graph_writer,
            }.items()
            if name in signature.parameters
        }
        handler = builder(**kwargs)
        return _invoke_handler(handler)

    service_type = getattr(module, "GraphBuildJobService", None)
    if service_type is not None:
        signature = inspect.signature(service_type)
        kwargs = {
            name: value
            for name, value in {
                "object_store": object_store,
                "graph_writer": graph_writer,
                "neo4j": graph_writer,
            }.items()
            if name in signature.parameters
        }
        service = service_type(**kwargs)
        return _invoke_handler(service)

    pytest.fail(
        "Phase H graph build contract requires build_graph_build_job_handler(), "
        "build_graph_build_service(), or GraphBuildJobService."
    )


def _invoke_handler(handler: Any) -> Any:
    args = {
        "workspace_id": "default",
        "knowledge_base_id": "kb_default",
        "doc_id": "doc_001",
        "doc_version_id": "docv_001",
        "job_id": "job_graph_001",
        "operation_id": "op_graph_001",
    }
    for method_name in ("run", "handle", "build", "execute"):
        method = getattr(handler, method_name, None)
        if method is None:
            continue
        signature = inspect.signature(method)
        kwargs = {name: value for name, value in args.items() if name in signature.parameters}
        if kwargs:
            return method(**kwargs)
        return method(args)
    if callable(handler):
        signature = inspect.signature(handler)
        kwargs = {name: value for name, value in args.items() if name in signature.parameters}
        if kwargs:
            return handler(**kwargs)
        return handler(args)
    pytest.fail("Graph build handler must be callable or expose run/handle/build/execute.")


def test_graph_build_job_writes_graph_artifacts_from_existing_chunks(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    graph_writer = _FakeGraphWriter()
    _write_chunked_pending_chunks(object_store)
    module = _graph_build_module()

    result = _invoke_graph_build(module, object_store, graph_writer)

    keys = object_store.list_keys("workspaces/default/knowledge_bases/kb_default/documents/doc_001")
    graph_artifact_keys = [
        key
        for key in keys
        if any(
            suffix in key
            for suffix in (
                "graph/entities",
                "graph/relation_facts",
                "graph/evidence",
                "graph/mentions",
                "graph/decisions",
            )
        )
    ]
    assert graph_artifact_keys, f"result={result!r}, keys={keys!r}"
    assert graph_writer.batches
    serialized_batches = json.dumps(graph_writer.batches, sort_keys=True, default=str)
    assert "chk_001" in serialized_batches or "chk_002" in serialized_batches
    assert "RelationFact" in serialized_batches or "relation_facts" in serialized_batches
    assert "Evidence" in serialized_batches or "evidence" in serialized_batches
    assert "graph_build_job" in serialized_batches or "job_graph_001" in serialized_batches


@pytest.fixture()
def object_store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture()
def client(object_store: LocalObjectStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()


def test_graph_build_api_queues_job_and_worker_writes_artifacts(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    _write_chunked_pending_chunks(object_store)
    writer = _FakeGraphWriter(ok=True)

    def build_test_worker() -> JobWorker:
        return JobWorker(
            JobService(object_store, runtime_instance_id="rt_local"),
            {
                "graph_build_job": build_graph_build_handler(
                    object_store,
                    graph_writer=writer,
                )
            },
        )

    app.dependency_overrides[get_job_worker] = build_test_worker

    create_response = client.post("/workspaces/default/graph/build/kb_default/documents/doc_001")
    create_body = create_response.json()
    job_response = client.get(f"/workspaces/default/jobs/{create_body['job_id']}")
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "graph_build_job"},
    )

    assert create_response.status_code == 200
    assert create_body["result"]["status"] == "queued"
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "queued"
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["job_id"] == create_body["job_id"]
    assert worker_body["job"]["status"] == "succeeded"

    keys = object_store.list_keys("workspaces/default/knowledge_bases/kb_default/documents/doc_001")
    assert any("graph/entities" in key for key in keys)
    assert any("graph/relation_facts" in key for key in keys)
    assert writer.batches


def test_graph_build_worker_fails_job_when_neo4j_writer_reports_failure(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    _write_chunked_pending_chunks(object_store)
    failing_writer = _FakeGraphWriter(ok=False)

    def build_test_worker() -> JobWorker:
        return JobWorker(
            JobService(object_store, runtime_instance_id="rt_local"),
            {
                "graph_build_job": build_graph_build_handler(
                    object_store,
                    graph_writer=failing_writer,
                )
            },
        )

    app.dependency_overrides[get_job_worker] = build_test_worker

    create_response = client.post("/workspaces/default/graph/build/kb_default/documents/doc_001")
    create_body = create_response.json()
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "graph_build_job"},
    )
    manifest = json.loads(
        object_store.read_text(
            "workspaces/default/knowledge_bases/kb_default/documents/doc_001/manifest.json"
        )
    )

    assert create_response.status_code == 200
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["job_id"] == create_body["job_id"]
    assert worker_body["job"]["status"] == "failed"
    assert worker_body["job"]["current_stage"] == "graph_index"
    assert manifest["graph_status"] == "pending"
    assert manifest["graphrag_available"] is False
    assert failing_writer.batches


def test_graph_build_worker_fails_job_when_neo4j_writer_is_not_configured(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    _write_chunked_pending_chunks(object_store)

    def build_test_worker() -> JobWorker:
        return JobWorker(
            JobService(object_store, runtime_instance_id="rt_local"),
            {
                "graph_build_job": build_graph_build_handler(object_store),
            },
        )

    app.dependency_overrides[get_job_worker] = build_test_worker

    create_response = client.post("/workspaces/default/graph/build/kb_default/documents/doc_001")
    create_body = create_response.json()
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "graph_build_job"},
    )
    events_response = client.get(f"/workspaces/default/jobs/{create_body['job_id']}/events")
    manifest = json.loads(
        object_store.read_text(
            "workspaces/default/knowledge_bases/kb_default/documents/doc_001/manifest.json"
        )
    )

    assert create_response.status_code == 200
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["job_id"] == create_body["job_id"]
    assert worker_body["job"]["status"] == "failed"
    assert worker_body["job"]["current_stage"] == "graph_index"
    assert events_response.status_code == 200
    last_event = events_response.json()["events"][-1]
    assert last_event["type"] == "job_failed"
    assert last_event["payload"]["error_type"] == "neo4j_writer_not_configured"
    assert manifest["graph_status"] == "pending"
    assert manifest["graphrag_available"] is False
