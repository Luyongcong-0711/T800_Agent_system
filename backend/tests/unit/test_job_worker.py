from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.settings import Settings
from app.jobs.handlers import build_memory_sync_handler
from app.jobs.service import JobService
from app.jobs.worker import JobContext, JobHandlerResult, JobWorker, JobWorkerDaemon
from app.memory.service import MemoryService
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest
from app.schemas.memory import MemorySource, UpsertMemoryRequest
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore
from app.storage.path_builder import memory_sync_state_key


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(user_id="default_user", role="owner")


def _request(
    *,
    job_type: str = "demo_job",
    idempotency_key: str = "demo-job",
    params: dict[str, Any] | None = None,
) -> CreateJobRequest:
    return CreateJobRequest(
        job_type=job_type,
        target_scope={"scope_type": "demo", "target_id": idempotency_key},
        input=params or {"value": "example"},
        idempotency_key=idempotency_key,
        title=f"Run {job_type}",
    )


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def embed_query(self, **kwargs: object) -> list[float]:
        self.calls.append(kwargs)
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []

    def ensure_collection(self, *, collection: str, dimension: int) -> None:
        _ = collection, dimension

    def upsert(self, *, collection: str, records: list[dict[str, object]]) -> dict[str, object]:
        self.upserts.append({"collection": collection, "records": records})
        return {"ok": True}

    def delete_by_ids(self, *, collection: str, ids: list[str]) -> dict[str, object]:
        return {"ok": True, "collection": collection, "ids": ids}


@pytest.fixture()
def job_service(tmp_path) -> JobService:
    return JobService(LocalObjectStore(tmp_path / "objects"), runtime_instance_id="rt_worker")


def test_job_worker_processes_registered_handler(job_service: JobService) -> None:
    created = job_service.create_job("default", _identity(), _request())

    def handler(context: JobContext) -> JobHandlerResult:
        assert context.job_id == created["job_id"]
        assert context.fencing_token.startswith("fence_")
        context.mark_running(stage="demo_progress", message="Working.", percent=40)
        return JobHandlerResult.succeeded(
            stage="demo_done",
            message="Done.",
            artifacts=[{"artifact_type": "demo"}],
        )

    result = JobWorker(job_service, {"demo_job": handler}).process_next("default")

    assert result["claimed"] is True
    assert result["job"]["status"] == "succeeded"
    assert result["job"]["current_stage"] == "demo_done"
    assert result["job"]["manifest"]["owner"] is None
    events, _ = job_service.list_job_events("default", created["job_id"])
    assert [event["type"] for event in events][-2:] == ["job_started", "job_succeeded"]


def test_job_worker_processes_memory_sync_job(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    job_service = JobService(object_store, runtime_instance_id="rt_worker")
    memory = MemoryService(object_store).upsert_memory(
        "default",
        _identity(),
        UpsertMemoryRequest(
            type="user_preference",
            field="answer_style",
            summary="User prefers concise Chinese answers.",
            content="The user wants concise Chinese answers.",
            source=MemorySource(thread_id="thread_001", message_id="msg_001"),
        ),
    )
    vector_store = _FakeVectorStore()
    job_service.create_job(
        "default",
        _identity(),
        _request(
            job_type="memory_sync_job",
            idempotency_key="memory-sync",
            params={
                "collection": "default_memory",
                "dimension": 3,
                "model": "embedding-test",
            },
        ),
    )

    result = JobWorker(
        job_service,
        {
            "memory_sync_job": build_memory_sync_handler(
                object_store,
                embedding_client_factory=lambda _: _FakeEmbeddingClient(),
                vector_store_factory=lambda _: vector_store,
            )
        },
    ).process_next("default")
    state = JsonObjectStore(object_store).read_json(memory_sync_state_key("default"))

    assert result["claimed"] is True
    assert result["job"]["status"] == "succeeded"
    assert state["pending_targets"] == []
    assert vector_store.upserts[0]["records"][0]["chunk_id"] == memory["memory_id"]


def test_memory_sync_job_uses_default_embedding_settings_when_input_omits_overrides(
    tmp_path,
) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    job_service = JobService(object_store, runtime_instance_id="rt_worker")
    memory = MemoryService(object_store).upsert_memory(
        "default",
        _identity(),
        UpsertMemoryRequest(
            type="user_preference",
            field="answer_style",
            summary="User prefers concise Chinese answers.",
            content="The user wants concise Chinese answers.",
            source=MemorySource(thread_id="thread_001", message_id="msg_001"),
        ),
    )
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore()
    job_service.create_job(
        "default",
        _identity(),
        _request(
            job_type="memory_sync_job",
            idempotency_key="memory-sync-default-settings",
            params={},
        ),
    )

    result = JobWorker(
        job_service,
        {
            "memory_sync_job": build_memory_sync_handler(
                object_store,
                settings=Settings(
                    default_embedding_model_name="default-memory-embedding",
                    default_embedding_dimension=3,
                ),
                embedding_client_factory=lambda _: embedding_client,
                vector_store_factory=lambda _: vector_store,
            )
        },
    ).process_next("default")

    assert result["claimed"] is True
    assert result["job"]["status"] == "succeeded"
    assert embedding_client.calls[0]["model"] == "default-memory-embedding"
    assert embedding_client.calls[0]["dimension"] == 3
    assert vector_store.upserts[0]["collection"] == "default_memory_3"
    assert vector_store.upserts[0]["records"][0]["chunk_id"] == memory["memory_id"]


def test_memory_service_auto_queues_memory_sync_job_when_injected(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    job_service = JobService(object_store, runtime_instance_id="rt_worker")
    service = MemoryService(object_store, job_service=job_service)

    service.upsert_memory(
        "default",
        _identity(),
        UpsertMemoryRequest(
            type="user_preference",
            field="answer_style",
            summary="User prefers concise Chinese answers.",
            content="The user wants concise Chinese answers.",
            source=MemorySource(thread_id="thread_001", message_id="msg_001"),
        ),
    )
    service.upsert_memory(
        "default",
        _identity(),
        UpsertMemoryRequest(
            type="user_preference",
            field="tone",
            summary="User prefers direct conclusions.",
            content="The user wants direct conclusions before details.",
            source=MemorySource(thread_id="thread_001", message_id="msg_002"),
        ),
    )

    jobs = job_service.list_jobs("default", job_type="memory_sync_job")
    state = JsonObjectStore(object_store).read_json(memory_sync_state_key("default"))

    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["target_scope"]["scope_type"] == "memory_sync"
    assert state["last_enqueue"]["status"] == "already_queued"
    assert state["last_enqueue"]["existing_job_id"] == jobs[0]["job_id"]


def test_job_worker_marks_missing_handler_failed(job_service: JobService) -> None:
    created = job_service.create_job(
        "default",
        _identity(),
        _request(job_type="missing_handler_job", idempotency_key="missing-handler"),
    )

    result = JobWorker(job_service, {}).process_next("default")

    assert result["claimed"] is True
    assert result["job"]["job_id"] == created["job_id"]
    assert result["job"]["status"] == "failed"
    assert result["job"]["current_stage"] == "dispatch"
    assert result["job"]["manifest"]["owner"] is None


def test_job_worker_marks_handler_exception_failed(job_service: JobService) -> None:
    created = job_service.create_job(
        "default",
        _identity(),
        _request(job_type="exploding_job", idempotency_key="exploding-handler"),
    )

    def handler(_: JobContext) -> JobHandlerResult:
        raise RuntimeError("boom")

    result = JobWorker(job_service, {"exploding_job": handler}).process_next("default")

    assert result["claimed"] is True
    assert result["job"]["job_id"] == created["job_id"]
    assert result["job"]["status"] == "failed"
    assert result["job"]["current_stage"] == "handler_exception"
    assert result["job"]["manifest"]["owner"] is None


def test_job_worker_reports_empty_queue(job_service: JobService) -> None:
    result = JobWorker(job_service, {"demo_job": lambda _: JobHandlerResult.succeeded(
        stage="done",
        message="Done.",
    )}).process_next("default")

    assert result == {"workspace_id": "default", "claimed": False, "job": None}


def test_job_worker_defaults_to_registered_job_types(job_service: JobService) -> None:
    skipped = job_service.create_job(
        "default",
        _identity(),
        _request(job_type="other_job", idempotency_key="other-job"),
    )
    processed = job_service.create_job(
        "default",
        _identity(),
        _request(job_type="demo_job", idempotency_key="registered-job"),
    )

    result = JobWorker(
        job_service,
        {
            "demo_job": lambda _: JobHandlerResult.succeeded(
                stage="done",
                message="Done.",
            )
        },
    ).process_next("default")

    assert result["claimed"] is True
    assert result["job"]["job_id"] == processed["job_id"]
    assert job_service.get_job("default", skipped["job_id"])["status"] == "queued"


def test_job_worker_process_batch_respects_max_jobs(job_service: JobService) -> None:
    first = job_service.create_job(
        "default",
        _identity(),
        _request(job_type="demo_job", idempotency_key="batch-first"),
    )
    second = job_service.create_job(
        "default",
        _identity(),
        _request(job_type="demo_job", idempotency_key="batch-second"),
    )
    worker = JobWorker(
        job_service,
        {
            "demo_job": lambda _: JobHandlerResult.succeeded(
                stage="done",
                message="Done.",
            )
        },
    )

    first_batch = worker.process_batch("default", max_jobs=1)
    second_batch = worker.process_batch("default", max_jobs=5)

    assert first_batch["processed_count"] == 1
    assert first_batch["drained"] is False
    assert first_batch["jobs"][0]["job_id"] == first["job_id"]
    assert second_batch["processed_count"] == 1
    assert second_batch["drained"] is True
    assert second_batch["jobs"][0]["job_id"] == second["job_id"]


def test_job_worker_daemon_run_once_records_status(job_service: JobService) -> None:
    created = job_service.create_job(
        "default",
        _identity(),
        _request(job_type="demo_job", idempotency_key="daemon-once"),
    )
    worker = JobWorker(
        job_service,
        {
            "demo_job": lambda _: JobHandlerResult.succeeded(
                stage="done",
                message="Done.",
            )
        },
    )
    daemon = JobWorkerDaemon(worker, workspace_id="default", max_jobs_per_tick=2)

    result = asyncio.run(daemon.run_once())
    status = daemon.status()

    assert result["processed_count"] == 1
    assert result["jobs"][0]["job_id"] == created["job_id"]
    assert status["running"] is False
    assert status["tick_count"] == 1
    assert status["processed_count"] == 1
    assert status["last_error"] is None


def test_job_worker_daemon_start_and_stop(job_service: JobService) -> None:
    created = job_service.create_job(
        "default",
        _identity(),
        _request(job_type="demo_job", idempotency_key="daemon-loop"),
    )
    worker = JobWorker(
        job_service,
        {
            "demo_job": lambda _: JobHandlerResult.succeeded(
                stage="done",
                message="Done.",
            )
        },
    )
    daemon = JobWorkerDaemon(
        worker,
        workspace_id="default",
        poll_interval_seconds=0.05,
        max_jobs_per_tick=1,
    )

    async def scenario() -> dict[str, object]:
        await daemon.start()
        for _ in range(20):
            if job_service.get_job("default", created["job_id"])["status"] == "succeeded":
                break
            await asyncio.sleep(0.02)
        return await daemon.stop()

    stopped = asyncio.run(scenario())

    assert stopped["running"] is False
    assert stopped["processed_count"] >= 1
    assert stopped["stopped_at"]
    assert job_service.get_job("default", created["job_id"])["status"] == "succeeded"
