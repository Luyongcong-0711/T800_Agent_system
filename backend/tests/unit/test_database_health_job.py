from __future__ import annotations

from app.core.settings import Settings
from app.database.service import DatabaseConfigService
from app.jobs.handlers import build_database_health_check_handler
from app.jobs.service import JobService
from app.jobs.worker import JobWorker
from app.schemas.health import ServiceHealth
from app.schemas.job import CreateJobRequest
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore
from app.storage.path_builder import database_health_snapshot_key


class _Identity:
    user_id = "default_user"
    role = "owner"
    workspace_id = "default"
    workspace_role = "owner"


def test_database_health_check_job_writes_latest_snapshot_and_artifact(
    tmp_path,
    monkeypatch,
) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")

    def fake_check_database_services_sync(_settings):
        return [
            ServiceHealth(
                target="minio",
                status="healthy",
                latency_ms=1,
                message="ok",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="milvus",
                status="healthy",
                latency_ms=2,
                message="ok",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="neo4j",
                status="unhealthy",
                latency_ms=3,
                message="ConnectionError",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="redis",
                status="healthy",
                latency_ms=1,
                message="tcp_connect_ok",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
        ]

    monkeypatch.setattr(
        "app.database.service.check_database_services_sync",
        fake_check_database_services_sync,
    )
    job_service = JobService(
        object_store,
        runtime_instance_id="rt_test",
        job_lease_ttl_seconds=300,
    )
    created = job_service.create_job(
        "default",
        _Identity(),
        CreateJobRequest(
            job_type="database_health_check_job",
            target_scope={"scope_type": "database_health"},
            input={},
            idempotency_key="database-health-check",
            title="Check database health",
        ),
    )
    worker = JobWorker(
        job_service,
        {
            "database_health_check_job": build_database_health_check_handler(
                object_store,
                settings=Settings(),
            )
        },
    )

    result = worker.process_next("default")

    assert result["claimed"] is True
    assert result["job"]["job_id"] == created["job_id"]
    assert result["job"]["status"] == "partial_success"
    assert result["job"]["current_stage"] == "database_degraded"
    assert result["job"]["error_type"] == "database_health_degraded"
    artifact = result["job"]["leaf_state"]["artifacts"][0]
    assert artifact == {
        "artifact_type": "database_health_snapshot",
        "object_key": database_health_snapshot_key("default"),
        "ok": False,
        "source": "job_check",
        "unhealthy_targets": ["neo4j"],
    }
    snapshot = JsonObjectStore(object_store).read_json(database_health_snapshot_key("default"))
    assert snapshot["source"] == "job_check"
    assert snapshot["ok"] is False
    assert snapshot["services"][2]["target"] == "neo4j"
    assert snapshot["services"][2]["status"] == "unhealthy"


def test_disabled_external_database_targets_do_not_make_health_not_ok(
    tmp_path,
    monkeypatch,
) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")

    def fake_check_database_services_sync(settings):
        assert settings.enabled_targets == {
            "minio": False,
            "milvus": False,
            "neo4j": False,
            "redis": True,
        }
        return [
            ServiceHealth(
                target="minio",
                status="disabled",
                message="service_disabled",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="milvus",
                status="disabled",
                message="service_disabled",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="neo4j",
                status="disabled",
                message="service_disabled",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="redis",
                status="healthy",
                latency_ms=1,
                message="tcp_connect_ok",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
        ]

    monkeypatch.setattr(
        "app.database.service.check_database_services_sync",
        fake_check_database_services_sync,
    )

    snapshot = DatabaseConfigService(
        object_store,
        Settings(external_database_targets_enabled=False),
    ).run_health_check_sync("default")

    assert snapshot["ok"] is True
    assert [service["status"] for service in snapshot["services"]] == [
        "disabled",
        "disabled",
        "disabled",
        "healthy",
    ]
