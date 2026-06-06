from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_object_store
from app.main import app
from app.storage.local_object_store import LocalObjectStore

SECRET_TEXT = (
    "api_key=sk-test-secret password=hunter2 token=secret-token "
    "Authorization: Bearer sk-test-secret Cookie: session=sk-test-secret"
)
FORBIDDEN_TERMS = (
    "api_key",
    "password",
    "token",
    "authorization",
    "cookie",
    "sk-test-secret",
    "hunter2",
    "secret-token",
)


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


def _assert_no_secret_material(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str).lower()
    for term in FORBIDDEN_TERMS:
        assert term not in serialized


def _store_text(object_store: LocalObjectStore) -> str:
    text_parts: list[str] = []
    for key in object_store.list_keys(""):
        if key.endswith((".json", ".jsonl", ".log", ".txt")):
            text_parts.append(object_store.read_text(key))
    return "\n".join(text_parts).lower()


def _items(body: Any, key: str = "items") -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    assert isinstance(body, dict)
    for candidate in (key, "logs", "events"):
        value = body.get(candidate)
        if isinstance(value, list):
            return value
    pytest.fail(f"Expected list payload under {key}/logs/events: {body}")


def _seed_system_logs(client: TestClient) -> str:
    trace_id = "trace_phase_m_api"
    response = client.get(
        "/workspaces/default/threads",
        headers={
            "x-trace-id": trace_id,
            "authorization": "Bearer sk-test-secret",
            "cookie": "session=sk-test-secret",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("x-trace-id") == trace_id
    _assert_no_secret_material(response.json())
    return trace_id


def test_fastapi_request_returns_trace_id_and_records_redacted_api_log(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    trace_id = _seed_system_logs(client)

    full_response = client.get(
        "/workspaces/default/logs/system/full",
        params={"trace_id": trace_id, "limit": 20},
    )
    assert full_response.status_code == 200
    logs = _items(full_response.json())
    assert logs
    assert any(item.get("trace_id") == trace_id for item in logs)
    assert all(item.get("redacted") is True for item in logs)
    assert all(item.get("workspace_id") == "default" for item in logs)
    assert all(item.get("component") for item in logs)
    assert all(item.get("severity") for item in logs)
    assert all(item.get("timestamp") for item in logs)
    _assert_no_secret_material(full_response.json())
    _assert_no_secret_material(_store_text(object_store))


def test_system_log_api_returns_summary_errors_and_component_slices(
    client: TestClient,
) -> None:
    trace_id = _seed_system_logs(client)

    summary = client.get(
        "/workspaces/default/logs/system/summary",
        params={"trace_id": trace_id, "limit": 20},
    )
    errors = client.get(
        "/workspaces/default/logs/system/errors",
        params={"trace_id": trace_id, "limit": 20},
    )
    component = client.get(
        "/workspaces/default/logs/components/api",
        params={"trace_id": trace_id, "limit": 20},
    )

    assert summary.status_code == 200
    assert errors.status_code == 200
    assert component.status_code == 200
    assert _items(summary.json())
    assert isinstance(_items(errors.json()), list)
    assert _items(component.json())
    assert all(item.get("component") == "api" for item in _items(component.json()))
    _assert_no_secret_material(summary.json())
    _assert_no_secret_material(errors.json())
    _assert_no_secret_material(component.json())


def test_diagnostic_bundle_api_creates_redacted_manifest_with_log_slices(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    trace_id = _seed_system_logs(client)

    response = client.post(
        "/workspaces/default/logs/diagnostic-bundles",
        json={
            "trace_id": trace_id,
            "run_id": "run_phase_m_api",
            "components": ["api"],
            "include_summary": True,
            "include_errors": True,
            "include_component_logs": True,
            "notes": SECRET_TEXT,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["redacted"] is True
    assert body["bundle_id"]
    assert body["job_id"]
    assert body["job_status"] == "queued"
    assert body.get("manifest_object_key") is None
    assert body.get("object_key") is None
    assert "postgres" not in json.dumps(body, ensure_ascii=False).lower()
    assert "mysql" not in json.dumps(body, ensure_ascii=False).lower()
    assert "redis queue" not in json.dumps(body, ensure_ascii=False).lower()
    assert "websocket" not in json.dumps(body, ensure_ascii=False).lower()
    _assert_no_secret_material(body)

    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "diagnostic_bundle_job"},
    )
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["job_id"] == body["job_id"]
    assert worker_body["job"]["status"] == "succeeded"
    artifacts = worker_body["job"]["leaf_state"]["artifacts"]
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["artifact_type"] == "diagnostic_bundle"
    assert artifact["manifest_object_key"].startswith("system/logs/")
    assert "/diagnostic_bundles/" in artifact["manifest_object_key"]

    manifest = json.loads(object_store.read_text(artifact["manifest_object_key"]))
    assert manifest["redacted"] is True
    assert manifest["workspace_id"] == "default"
    assert manifest["filters"]["trace_id"] == trace_id
    assert manifest.get("object_key", "").startswith("system/logs/")
    assert "summary" in manifest.get("included_slices", [])
    assert "errors" in manifest.get("included_slices", [])
    assert "components/api" in manifest.get("included_slices", [])
    _assert_no_secret_material(manifest)
    _assert_no_secret_material(_store_text(object_store))


def test_diagnostic_bundle_state_uses_object_store_paths_not_external_state(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    trace_id = _seed_system_logs(client)

    response = client.post(
        "/workspaces/default/logs/diagnostic-bundles",
        json={"trace_id": trace_id, "components": ["api"]},
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "diagnostic_bundle_job"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job_status"] == "queued"
    assert worker_response.status_code == 200
    artifact = worker_response.json()["job"]["leaf_state"]["artifacts"][0]

    keys = object_store.list_keys("")
    joined = "\n".join(keys).lower()
    assert artifact["manifest_object_key"] in keys
    assert any(key.startswith("system/logs/") for key in keys)
    assert any("/diagnostic_bundles/" in key for key in keys)
    assert "postgres" not in joined
    assert "mysql" not in joined
    assert "websocket" not in joined
    assert "redis/queue" not in joined
    assert "redis_queue" not in joined


@pytest.mark.parametrize("job_type", ["log_archive_job", "log_shipper_job"])
def test_log_archive_job_writes_redacted_archive_manifest(
    client: TestClient,
    object_store: LocalObjectStore,
    job_type: str,
) -> None:
    trace_id = _seed_system_logs(client)
    archive_date = next(
        key.split("/")[2]
        for key in object_store.list_keys("system/logs")
        if key.startswith("system/logs/")
    )

    response = client.post(
        "/workspaces/default/jobs",
        json={
            "job_type": job_type,
            "title": "Archive system logs",
            "target_scope": {
                "scope_type": "system_logs",
                "runtime_instance_id": "rt_local",
                "date": archive_date,
            },
            "input": {
                "runtime_instance_id": "rt_local",
                "date": archive_date,
                "trace_id": trace_id,
            },
            "idempotency_key": f"{job_type}-{archive_date}",
            "trace_id": trace_id,
        },
    )

    assert response.status_code == 200
    queued = response.json()
    assert queued["status"] == "queued"

    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": job_type},
    )

    assert worker_response.status_code == 200
    body = worker_response.json()
    assert body["claimed"] is True
    assert body["job"]["job_id"] == queued["job_id"]
    assert body["job"]["status"] == "succeeded"
    artifact = body["job"]["leaf_state"]["artifacts"][0]
    assert artifact["artifact_type"] == "log_archive_manifest"
    assert artifact["runtime_instance_id"] == "rt_local"
    assert artifact["date"] == archive_date
    assert artifact["manifest_object_key"].startswith("system/logs/")
    assert "/log_archives/manifest.json" in artifact["manifest_object_key"]
    assert artifact["archived_count"] >= 1

    manifest = json.loads(object_store.read_text(artifact["manifest_object_key"]))
    assert manifest["redacted"] is True
    assert manifest["job_id"] == queued["job_id"]
    assert manifest["last_archived_count"] == artifact["archived_count"]
    assert manifest["file_count"] == artifact["file_count"]
    for item in manifest["last_archived_files"]:
        assert object_store.exists(item["object_key"])
        _assert_no_secret_material(object_store.read_text(item["object_key"]))
    _assert_no_secret_material(manifest)
    _assert_no_secret_material(_store_text(object_store))


def test_log_archive_api_queues_idempotent_worker_job(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    trace_id = _seed_system_logs(client)
    archive_date = next(
        key.split("/")[2]
        for key in object_store.list_keys("system/logs")
        if key.startswith("system/logs/")
    )

    first = client.post(
        "/workspaces/default/logs/archive-jobs",
        json={
            "date": archive_date,
            "runtime_instance_id": "rt_local",
            "request_id": "archive-request-001",
        },
    )
    second = client.post(
        "/workspaces/default/logs/archive-jobs",
        json={
            "date": archive_date,
            "runtime_instance_id": "rt_local",
            "request_id": "archive-request-001",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["job_status"] == "queued"
    assert second_body["job_id"] == first_body["job_id"]
    assert first_body["manifest_object_key"] is None
    assert first_body["runtime_instance_id"] == "rt_local"
    assert first_body["date"] == archive_date
    _assert_no_secret_material(first_body)

    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "log_archive_job"},
        headers={"x-trace-id": trace_id},
    )
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["job_id"] == first_body["job_id"]
    assert worker_body["job"]["status"] == "succeeded"
    artifact = worker_body["job"]["leaf_state"]["artifacts"][0]
    assert artifact["artifact_type"] == "log_archive_manifest"
    assert object_store.exists(artifact["manifest_object_key"])
    _assert_no_secret_material(json.loads(object_store.read_text(artifact["manifest_object_key"])))


def test_log_artifact_api_reads_diagnostic_json_and_package_artifacts(
    client: TestClient,
) -> None:
    trace_id = _seed_system_logs(client)
    response = client.post(
        "/workspaces/default/logs/diagnostic-bundles",
        json={"trace_id": trace_id, "components": ["api"]},
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "diagnostic_bundle_job"},
    )
    assert response.status_code == 200
    assert worker_response.status_code == 200
    artifact = worker_response.json()["job"]["leaf_state"]["artifacts"][0]

    manifest_response = client.get(
        "/workspaces/default/logs/artifacts",
        params={"object_key": artifact["manifest_object_key"]},
    )
    payload_response = client.get(
        "/workspaces/default/logs/artifacts",
        params={"object_key": artifact["object_key"]},
    )
    package_response = client.get(
        "/workspaces/default/logs/artifacts",
        params={"object_key": artifact["package_object_key"]},
    )

    assert manifest_response.status_code == 200
    assert payload_response.status_code == 200
    assert package_response.status_code == 200
    manifest = manifest_response.json()
    payload = payload_response.json()
    package = package_response.json()
    assert manifest["artifact_type"] == "json"
    assert manifest["parsed_json"]["workspace_id"] == "default"
    assert payload["parsed_json"]["manifest"]["bundle_id"] == artifact["bundle_id"]
    assert package["artifact_type"] == "binary"
    assert package["content_type"] == "application/zip"
    assert base64.b64decode(package["base64"])
    _assert_no_secret_material([manifest, payload, package])


def test_log_artifact_api_rejects_non_observability_object_keys(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    object_store.write_text("workspaces/default/secrets/not-a-log.json", "{}")

    response = client.get(
        "/workspaces/default/logs/artifacts",
        params={"object_key": "workspaces/default/secrets/not-a-log.json"},
    )

    assert response.status_code == 400
    assert response.json()["error_type"] == "unsupported_log_artifact_key"
