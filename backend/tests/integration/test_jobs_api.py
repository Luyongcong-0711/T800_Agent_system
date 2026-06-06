from __future__ import annotations

# ruff: noqa: E402,I001

import json
import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("app.api.jobs", reason="Phase E jobs API has not landed yet.")

from app.api.dependencies import get_identity, get_object_store
from app.jobs.service import JobService
from app.main import app
from app.schemas.identity import RuntimeIdentity
from app.storage.local_object_store import LocalObjectStore


SENSITIVE_FIELD_NAMES = {
    "api_key",
    "password",
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "agent_master_key",
    "provider_raw_payload",
}
SENSITIVE_VALUES = [
    "hunter2",
    "clear",
    "encrypted",
    "nonce-value",
    "tag-value",
    "master key",
    "raw-provider-secret",
    "sk-test-secret",
]

NON_TERMINAL_STATUSES = {
    "created",
    "queued",
    "running",
    "waiting_retry",
    "unknown_outcome",
    "recovering",
}
TERMINAL_STATUSES = {"succeeded", "partial_success", "failed", "cancelled"}


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
    if isinstance(value, dict):
        for key, item in value.items():
            assert str(key).lower() not in SENSITIVE_FIELD_NAMES
            _assert_no_secret_material(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_no_secret_material(item)
        return
    if isinstance(value, str):
        lowered = value.lower()
        for term in SENSITIVE_VALUES:
            assert term not in lowered


def _items(body: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    assert isinstance(body, dict)
    value = body.get(key)
    assert isinstance(value, list)
    return value


def _target(doc_id: str = "doc_001", doc_version_id: str = "docv_001") -> dict[str, str]:
    return {
        "scope_type": "document_version",
        "knowledge_base_id": "kb_default",
        "doc_id": doc_id,
        "doc_version_id": doc_version_id,
    }


def _job_payload(
    *,
    job_type: str = "document_ingestion_job",
    doc_id: str = "doc_001",
    doc_version_id: str = "docv_001",
    idempotency_key: str = "idem-doc-001",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "job_type": job_type,
        "priority": "normal",
        "target_scope": _target(doc_id, doc_version_id),
        "input": params or {"pipeline_version": "v1"},
        "idempotency_key": idempotency_key,
    }


def _post_job(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/workspaces/default/jobs", json=payload)
    assert response.status_code in {200, 201}
    body = response.json()
    assert body["job_id"]
    _assert_no_secret_material(body)
    return body


def _get_job(client: TestClient, job_id: str) -> dict[str, Any]:
    response = client.get(f"/workspaces/default/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    _assert_no_secret_material(body)
    return body


def _get_events(
    client: TestClient,
    job_id: str,
    after_event_id: str | None = None,
) -> list[dict[str, Any]]:
    params = {"after_event_id": after_event_id} if after_event_id else None
    response = client.get(f"/workspaces/default/jobs/{job_id}/events", params=params)
    assert response.status_code == 200
    events = _items(response.json(), "events")
    _assert_no_secret_material(events)
    return events


def _parse_sse(text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text.strip()):
        if not block:
            continue
        event: dict[str, Any] = {}
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("id:"):
                event["id"] = line.removeprefix("id:").strip()
            elif line.startswith("event:"):
                event["event"] = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if data_lines:
            event["data"] = json.loads("\n".join(data_lines))
        parsed.append(event)
    return parsed


def _json_key(object_store: LocalObjectStore, suffix: str) -> str:
    matches = [key for key in object_store.list_keys("") if key.endswith(suffix)]
    assert len(matches) == 1, matches
    return matches[0]


def _read_json(object_store: LocalObjectStore, suffix: str) -> dict[str, Any]:
    return json.loads(object_store.read_text(_json_key(object_store, suffix)))


def _write_json(object_store: LocalObjectStore, suffix: str, value: dict[str, Any]) -> None:
    object_store.write_text(
        _json_key(object_store, suffix),
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def _force_job_status(object_store: LocalObjectStore, job_id: str, status: str) -> None:
    manifest = _read_json(object_store, f"jobs/{job_id}/manifest.json")
    manifest["status"] = status
    manifest["revision"] = int(manifest.get("revision") or 0) + 1
    if status in TERMINAL_STATUSES:
        manifest["finished_at"] = manifest.get("updated_at")
    _write_json(object_store, f"jobs/{job_id}/manifest.json", manifest)

    leaf = _read_json(object_store, f"jobs/{job_id}/leaf_state.json")
    leaf["status"] = status
    _write_json(object_store, f"jobs/{job_id}/leaf_state.json", leaf)

    index = _read_json(object_store, "indexes/jobs_index.json")
    for job in index["jobs"]:
        if job["job_id"] == job_id:
            job["status"] = status
    _write_json(object_store, "indexes/jobs_index.json", index)


def test_create_job_writes_manifest_index_leaf_and_events(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload())
    job_id = created["job_id"]

    manifest = _read_json(object_store, f"jobs/{job_id}/manifest.json")
    leaf = _read_json(object_store, f"jobs/{job_id}/leaf_state.json")
    event_index = _read_json(object_store, f"jobs/{job_id}/event_index.json")
    jobs_index = _read_json(object_store, "indexes/jobs_index.json")
    events = _get_events(client, job_id)

    assert manifest["job_id"] == job_id
    assert manifest["target_scope"] == _target()
    assert leaf["job_id"] == job_id
    assert event_index["last_event_id"] == events[-1]["event_id"]
    assert {event["type"] for event in events} >= {"job_created", "job_queued"}
    assert any(job["job_id"] == job_id for job in jobs_index["jobs"])
    _assert_no_secret_material([manifest, leaf, event_index, jobs_index, events])


def test_duplicate_idempotency_returns_same_non_terminal_job(client: TestClient) -> None:
    payload = _job_payload(idempotency_key="idem-same")

    first = _post_job(client, payload)
    second = _post_job(client, payload)

    assert second["job_id"] == first["job_id"]
    assert second["status"] in NON_TERMINAL_STATUSES


def test_same_idempotency_key_with_different_payload_returns_409(client: TestClient) -> None:
    first = _post_job(
        client,
        _job_payload(idempotency_key="idem-conflict", params={"pipeline_version": "v1"}),
    )

    conflict = client.post(
        "/workspaces/default/jobs",
        json=_job_payload(idempotency_key="idem-conflict", params={"pipeline_version": "v2"}),
    )

    assert first["job_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error_type"] == "idempotency_conflict"
    _assert_no_secret_material(conflict.json())


def test_terminal_job_still_blocks_idempotency_key_payload_conflict(
    client: TestClient,
) -> None:
    first = _post_job(
        client,
        _job_payload(idempotency_key="terminal-idem", params={"pipeline_version": "v1"}),
    )
    cancel = client.post(f"/workspaces/default/jobs/{first['job_id']}/cancel")
    conflict = client.post(
        "/workspaces/default/jobs",
        json=_job_payload(idempotency_key="terminal-idem", params={"pipeline_version": "v2"}),
    )

    assert cancel.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error_type"] == "idempotency_conflict"
    _assert_no_secret_material(conflict.json())


def test_same_target_scope_active_critical_job_conflicts_or_dedupes(client: TestClient) -> None:
    first = _post_job(client, _job_payload(idempotency_key="scope-first"))

    response = client.post(
        "/workspaces/default/jobs",
        json=_job_payload(idempotency_key="scope-second"),
    )

    if response.status_code == 409:
        assert response.json()["error_type"] in {
            "job_conflict",
            "target_scope_conflict",
            "job_target_scope_conflict",
        }
    else:
        assert response.status_code in {200, 201}
        assert response.json()["job_id"] == first["job_id"]
    _assert_no_secret_material(response.json())


def test_different_document_target_scopes_can_create_separate_jobs(client: TestClient) -> None:
    first = _post_job(
        client,
        _job_payload(doc_id="doc_a", doc_version_id="docv_a", idempotency_key="doc-a"),
    )
    second = _post_job(
        client,
        _job_payload(doc_id="doc_b", doc_version_id="docv_b", idempotency_key="doc-b"),
    )

    assert first["job_id"] != second["job_id"]


def test_list_jobs_filters_by_status_and_job_type(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    first = _post_job(
        client,
        _job_payload(
            job_type="document_ingestion_job",
            doc_id="doc_filter_a",
            idempotency_key="filter-a",
        ),
    )
    second = _post_job(
        client,
        _job_payload(job_type="graph_build_job", doc_id="doc_filter_b", idempotency_key="filter-b"),
    )
    _force_job_status(object_store, second["job_id"], "failed")

    status_response = client.get("/workspaces/default/jobs", params={"status": "failed"})
    type_response = client.get(
        "/workspaces/default/jobs",
        params={"job_type": "document_ingestion_job"},
    )

    assert status_response.status_code == 200
    assert type_response.status_code == 200
    failed_jobs = _items(status_response.json(), "jobs")
    document_jobs = _items(type_response.json(), "jobs")
    assert {job["job_id"] for job in failed_jobs} == {second["job_id"]}
    assert first["job_id"] in {job["job_id"] for job in document_jobs}
    assert all(job["job_type"] == "document_ingestion_job" for job in document_jobs)
    _assert_no_secret_material([status_response.json(), type_response.json()])


def test_cancel_queued_job_writes_job_cancelled_terminal_state(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="cancel-queued"))
    job_id = created["job_id"]

    response = client.post(f"/workspaces/default/jobs/{job_id}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert _get_job(client, job_id)["status"] == "cancelled"
    assert _read_json(object_store, f"jobs/{job_id}/manifest.json")["status"] == "cancelled"
    assert _read_json(object_store, f"jobs/{job_id}/leaf_state.json")["status"] == "cancelled"
    assert _get_events(client, job_id)[-1]["type"] == "job_cancelled"
    _assert_no_secret_material(body)


def test_claim_next_job_assigns_owner_and_prevents_duplicate_claim(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="claim-next"))

    claim_response = client.post("/workspaces/default/jobs/claim-next")
    second_claim_response = client.post("/workspaces/default/jobs/claim-next")

    assert claim_response.status_code == 200
    assert second_claim_response.status_code == 200
    claimed = claim_response.json()
    assert claimed["claimed"] is True
    assert claimed["job"]["job_id"] == created["job_id"]
    assert claimed["job"]["status"] == "running"
    assert claimed["job"]["current_stage"] == "claimed"
    assert second_claim_response.json()["claimed"] is False

    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    assert manifest["owner"]["runtime_instance_id"] == "rt_local"
    assert manifest["owner"]["fencing_token"].startswith("fence_")
    assert "fencing_token" not in claimed["job"]["manifest"]["owner"]
    assert _get_events(client, created["job_id"])[-1]["type"] == "job_started"
    _assert_no_secret_material([claimed, second_claim_response.json(), manifest])


def test_editor_cannot_claim_or_process_worker_jobs(
    client: TestClient,
) -> None:
    _post_job(client, _job_payload(idempotency_key="editor-worker-forbidden"))

    app.dependency_overrides[get_identity] = lambda: RuntimeIdentity(
        user_id="editor_user",
        role="editor",
        workspace_id="default",
        workspace_role="editor",
    )
    try:
        claim_response = client.post("/workspaces/default/jobs/claim-next")
        process_response = client.post("/workspaces/default/jobs/process-next")
        start_response = client.post("/workspaces/default/jobs/worker/start")
        stop_response = client.post("/workspaces/default/jobs/worker/stop")
    finally:
        app.dependency_overrides.pop(get_identity, None)

    assert claim_response.status_code == 403
    assert process_response.status_code == 403
    assert start_response.status_code == 403
    assert stop_response.status_code == 403


def test_owner_can_start_and_stop_job_worker_daemon(client: TestClient) -> None:
    start_response = client.post(
        "/workspaces/default/jobs/worker/start",
        params={"poll_interval_ms": 50, "max_jobs_per_tick": 1},
    )
    status_response = client.get("/workspaces/default/jobs/worker/status")
    stop_response = client.post("/workspaces/default/jobs/worker/stop")
    stopped_status_response = client.get("/workspaces/default/jobs/worker/status")

    assert start_response.status_code == 200
    assert start_response.json()["running"] is True
    assert start_response.json()["workspace_id"] == "default"
    assert status_response.status_code == 200
    assert status_response.json()["running"] is True
    assert stop_response.status_code == 200
    assert stop_response.json()["running"] is False
    assert stopped_status_response.status_code == 200
    assert stopped_status_response.json()["running"] is False


def test_claimed_job_completion_requires_matching_fencing_token(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="claim-complete"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    service = JobService(object_store, runtime_instance_id="rt_local")

    stale_result = service.mark_job_succeeded(
        "default",
        created["job_id"],
        stage="done",
        message="stale worker",
        fencing_token="fence_wrong",
    )
    final_result = service.mark_job_succeeded(
        "default",
        created["job_id"],
        stage="done",
        message="worker completed",
        fencing_token=manifest["owner"]["fencing_token"],
    )

    assert stale_result["status"] == "running"
    assert final_result["status"] == "succeeded"
    assert final_result["manifest"]["owner"] is None
    assert _get_job(client, created["job_id"])["status"] == "succeeded"
    _assert_no_secret_material([stale_result, final_result])


def test_claimed_job_completion_without_fencing_token_is_rejected(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="claim-complete-no-token"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    service = JobService(object_store, runtime_instance_id="rt_local")

    missing_token_result = service.mark_job_succeeded(
        "default",
        created["job_id"],
        stage="done",
        message="missing token",
    )
    final_result = service.mark_job_succeeded(
        "default",
        created["job_id"],
        stage="done",
        message="worker completed",
        fencing_token=manifest["owner"]["fencing_token"],
    )

    assert missing_token_result["status"] == "running"
    assert final_result["status"] == "succeeded"
    _assert_no_secret_material([missing_token_result, final_result])


def test_claimed_job_failure_requires_matching_fencing_token_and_runtime(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="claim-fail-token"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    token = manifest["owner"]["fencing_token"]

    wrong_runtime = JobService(object_store, runtime_instance_id="rt_other")
    stale_result = wrong_runtime.mark_job_failed(
        "default",
        created["job_id"],
        stage="failed",
        message="wrong runtime",
        error_type="wrong_runtime",
        retryable=True,
        fencing_token=token,
    )
    owner_runtime = JobService(object_store, runtime_instance_id="rt_local")
    final_result = owner_runtime.mark_job_failed(
        "default",
        created["job_id"],
        stage="failed",
        message="worker failed",
        error_type="worker_failed",
        retryable=True,
        fencing_token=token,
    )

    assert stale_result["status"] == "running"
    assert final_result["status"] == "failed"
    assert final_result["manifest"]["owner"] is None
    _assert_no_secret_material([stale_result, final_result])


def test_claimed_job_running_update_requires_active_fencing_token(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="claim-running-token"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    assert claim_response.json()["claimed"] is True
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    token = manifest["owner"]["fencing_token"]
    service = JobService(object_store, runtime_instance_id="rt_local")
    before_update = _get_job(client, created["job_id"])

    missing_token_result = service.mark_job_running(
        "default",
        created["job_id"],
        stage="stale_progress",
        message="missing token",
        percent=50,
    )
    valid_result = service.mark_job_running(
        "default",
        created["job_id"],
        stage="active_progress",
        message="valid token",
        percent=60,
        fencing_token=token,
    )

    assert missing_token_result["current_stage"] == before_update["current_stage"]
    assert missing_token_result["progress_percent"] == before_update["progress_percent"]
    assert valid_result["current_stage"] == "active_progress"
    assert valid_result["progress_percent"] == 60
    progress_event = _get_events(client, created["job_id"])[-1]
    assert progress_event["payload"]["message"] == "valid token"
    assert progress_event["payload"]["percent"] == 60
    _assert_no_secret_material([missing_token_result, valid_result])


def test_claimed_job_running_update_renews_active_lease(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="claim-running-renew"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    token = manifest["owner"]["fencing_token"]
    old_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    manifest["owner"]["expires_at"] = old_expires_at
    _write_json(object_store, f"jobs/{created['job_id']}/manifest.json", manifest)
    service = JobService(object_store, runtime_instance_id="rt_local")

    result = service.mark_job_running(
        "default",
        created["job_id"],
        stage="heartbeat",
        message="renew lease",
        percent=10,
        fencing_token=token,
    )

    renewed = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    assert result["status"] == "running"
    assert renewed["owner"]["fencing_token"] == token
    assert renewed["owner"]["expires_at"] > old_expires_at


def test_claimed_job_completion_after_lease_expiry_is_rejected(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="claim-expired-token"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    token = manifest["owner"]["fencing_token"]
    manifest["owner"]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    _write_json(object_store, f"jobs/{created['job_id']}/manifest.json", manifest)
    service = JobService(object_store, runtime_instance_id="rt_local")

    result = service.mark_job_succeeded(
        "default",
        created["job_id"],
        stage="done",
        message="expired worker",
        fencing_token=token,
    )

    assert result["status"] == "running"
    assert _get_job(client, created["job_id"])["status"] == "running"
    event_types = [event["type"] for event in _get_events(client, created["job_id"])]
    assert "job_succeeded" not in event_types
    _assert_no_secret_material(result)


def test_recover_stale_running_job_marks_unknown_outcome(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="job-stale-recovery"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    manifest["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    manifest["owner"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _write_json(object_store, f"jobs/{created['job_id']}/manifest.json", manifest)

    response = client.post(
        "/workspaces/default/jobs/recover-stale",
        params={"stale_after_seconds": 60},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recovered_count"] == 1
    assert body["recovered_jobs"][0]["status"] == "unknown_outcome"
    detail = _get_job(client, created["job_id"])
    assert detail["status"] == "unknown_outcome"
    assert detail["leaf_state"]["recovery_probe"]["error_type"] == "stale_running_recovered"
    event_types = [event["type"] for event in _get_events(client, created["job_id"])]
    assert "job_recovery_started" in event_types
    assert event_types[-1] == "job_unknown_outcome"
    _assert_no_secret_material(body)


def test_recover_stale_running_job_skips_active_owner_lease(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="job-active-lease"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    manifest["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    manifest["owner"]["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(minutes=5)
    ).isoformat()
    _write_json(object_store, f"jobs/{created['job_id']}/manifest.json", manifest)

    response = client.post(
        "/workspaces/default/jobs/recover-stale",
        params={"stale_after_seconds": 60},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recovered_count"] == 0
    assert _get_job(client, created["job_id"])["status"] == "running"
    _assert_no_secret_material(body)


def test_rebuild_jobs_index_restores_index_from_manifests(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="rebuild-index"))
    _write_json(
        object_store,
        "indexes/jobs_index.json",
        {
            "schema_version": 1,
            "workspace_id": "default",
            "jobs": [],
            "updated_at": None,
            "revision": 7,
        },
    )

    response = client.post("/workspaces/default/jobs/rebuild-index")
    list_response = client.get("/workspaces/default/jobs")

    assert response.status_code == 200
    body = response.json()
    assert body["rebuilt_count"] == 1
    assert body["skipped_count"] == 0
    jobs = _items(list_response.json(), "jobs")
    assert [job["job_id"] for job in jobs] == [created["job_id"]]
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    leaf_state = _read_json(object_store, f"jobs/{created['job_id']}/leaf_state.json")
    assert manifest["last_event_seq"] >= 2
    assert leaf_state["last_event_seq"] == manifest["last_event_seq"]
    _assert_no_secret_material([body, list_response.json(), manifest, leaf_state])


def test_retry_unknown_outcome_recovers_terminal_event_projection(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="unknown-terminal-projection"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    manifest = _read_json(object_store, f"jobs/{created['job_id']}/manifest.json")
    token = manifest["owner"]["fencing_token"]
    service = JobService(object_store, runtime_instance_id="rt_local")
    finished = service.mark_job_succeeded(
        "default",
        created["job_id"],
        stage="done",
        message="worker completed before recovery probe",
        fencing_token=token,
    )
    assert finished["status"] == "succeeded"
    _force_job_status(object_store, created["job_id"], "unknown_outcome")

    response = client.post(f"/workspaces/default/jobs/{created['job_id']}/retry", json={})

    assert response.status_code == 200
    recovered = response.json()
    assert recovered["job_id"] == created["job_id"]
    assert recovered["status"] == "succeeded"
    assert recovered["leaf_state"]["recovery_probe"]["terminal_status"] == "succeeded"
    events = _get_events(client, created["job_id"])
    assert events[-1]["type"] == "job_recovery_completed"
    assert events[-1]["payload"]["recovery_probe"]["terminal_status"] == "succeeded"
    _assert_no_secret_material(recovered)


@pytest.mark.parametrize("status", ["failed", "unknown_outcome"])
def test_retry_failed_or_unknown_outcome_creates_or_requeues(
    client: TestClient,
    object_store: LocalObjectStore,
    status: str,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key=f"retry-{status}"))
    _force_job_status(object_store, created["job_id"], status)

    response = client.post(f"/workspaces/default/jobs/{created['job_id']}/retry", json={})

    assert response.status_code in {200, 201}
    retried = response.json()
    assert retried["job_id"]
    assert retried["status"] in NON_TERMINAL_STATUSES
    if retried["job_id"] == created["job_id"]:
        events = _get_events(client, created["job_id"])
        assert any(event["type"] in {"job_queued", "job_recovering"} for event in events)
    _assert_no_secret_material(retried)


def test_events_after_event_id_replays_without_duplicates(client: TestClient) -> None:
    created = _post_job(client, _job_payload(idempotency_key="events-replay"))
    events = _get_events(client, created["job_id"])

    assert len(events) >= 2
    later = _get_events(client, created["job_id"], after_event_id=events[0]["event_id"])

    assert [event["event_id"] for event in later] == [event["event_id"] for event in events[1:]]
    assert len({event["event_id"] for event in later}) == len(later)


def test_sse_replay_and_terminal_stream_closed(client: TestClient) -> None:
    created = _post_job(client, _job_payload(idempotency_key="sse-terminal"))
    cancel_response = client.post(f"/workspaces/default/jobs/{created['job_id']}/cancel")
    assert cancel_response.status_code == 200
    events = _get_events(client, created["job_id"])

    with client.stream(
        "GET",
        f"/workspaces/default/jobs/{created['job_id']}/events/stream",
        headers={"Last-Event-ID": events[0]["event_id"]},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.read().decode("utf-8")

    parsed = _parse_sse(body)
    replayed_event_ids = [
        event.get("id")
        for event in parsed
        if event.get("event") != "stream_closed" and event.get("id")
    ]
    assert replayed_event_ids == [event["event_id"] for event in events[1:]]
    assert parsed[-1]["event"] == "stream_closed"
    assert parsed[-1]["data"]["job_id"] == created["job_id"]
    assert parsed[-1]["data"]["status"] == "cancelled"
    _assert_no_secret_material(parsed)


def test_sse_wait_ms_zero_returns_without_terminal_close_for_running_job(
    client: TestClient,
) -> None:
    created = _post_job(client, _job_payload(idempotency_key="sse-running-wait-zero"))
    claim_response = client.post("/workspaces/default/jobs/claim-next")
    assert claim_response.status_code == 200
    events = _get_events(client, created["job_id"])

    with client.stream(
        "GET",
        f"/workspaces/default/jobs/{created['job_id']}/events/stream",
        headers={"Last-Event-ID": events[-1]["event_id"]},
        params={"wait_ms": 0},
    ) as response:
        assert response.status_code == 200
        body = response.read().decode("utf-8")

    assert body == ""


def test_sse_conflicting_cursor_sources_return_400(client: TestClient) -> None:
    created = _post_job(client, _job_payload(idempotency_key="sse-cursor-conflict"))
    events = _get_events(client, created["job_id"])

    response = client.get(
        f"/workspaces/default/jobs/{created['job_id']}/events/stream",
        headers={"Last-Event-ID": events[0]["event_id"]},
        params={"after_event_id": events[-1]["event_id"]},
    )

    assert response.status_code == 400
    assert response.json()["error_type"] == "invalid_event_cursor"
    _assert_no_secret_material(response.json())


def test_invalid_and_cross_job_event_cursors_return_400(client: TestClient) -> None:
    first = _post_job(client, _job_payload(doc_id="doc_cursor_a", idempotency_key="cursor-a"))
    second = _post_job(client, _job_payload(doc_id="doc_cursor_b", idempotency_key="cursor-b"))
    first_events = _get_events(client, first["job_id"])

    invalid_response = client.get(
        f"/workspaces/default/jobs/{first['job_id']}/events",
        params={"after_event_id": "evt_bad_cursor"},
    )
    cross_job_response = client.get(
        f"/workspaces/default/jobs/{second['job_id']}/events",
        params={"after_event_id": first_events[0]["event_id"]},
    )

    assert invalid_response.status_code == 400
    assert cross_job_response.status_code == 400
    assert invalid_response.json()["error_type"] == "invalid_event_cursor"
    assert cross_job_response.json()["error_type"] == "invalid_event_cursor"
    _assert_no_secret_material([invalid_response.json(), cross_job_response.json()])


def test_job_responses_do_not_leak_secret_material(client: TestClient) -> None:
    created = _post_job(
        client,
        _job_payload(
            idempotency_key="redaction",
            params={
                "api_key": "sk-test-secret",
                "password": "hunter2",
                "plaintext": "clear",
                "ciphertext": "encrypted",
                "nonce": "nonce-value",
                "tag": "tag-value",
                "agent_master_key": "master key value",
                "provider_raw_payload": "raw-provider-secret",
            },
        ),
    )

    responses = [
        created,
        _get_job(client, created["job_id"]),
        client.get("/workspaces/default/jobs").json(),
        client.get(f"/workspaces/default/jobs/{created['job_id']}/events").json(),
    ]

    _assert_no_secret_material(responses)


def test_job_target_scope_does_not_leak_sensitive_material(client: TestClient) -> None:
    response = client.post(
        "/workspaces/default/jobs",
        json={
            "job_type": "document_ingestion_job",
            "priority": "normal",
            "target_scope": {
                "scope_type": "document_version",
                "knowledge_base_id": "kb_default",
                "doc_id": "doc_sensitive",
                "doc_version_id": "docv_sensitive",
                "api_key": "sk-test-secret",
                "provider_raw_payload": "raw-provider-secret",
            },
            "input": {"pipeline_version": "v1"},
            "idempotency_key": "target-scope-redaction",
        },
    )

    assert response.status_code in {200, 201}
    body = response.json()
    detail = _get_job(client, body["job_id"])
    list_body = client.get("/workspaces/default/jobs").json()
    _assert_no_secret_material([body, detail, list_body])
