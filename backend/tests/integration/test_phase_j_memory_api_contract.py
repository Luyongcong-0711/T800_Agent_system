from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_object_store
from app.main import app
from app.schemas.identity import RuntimeIdentity
from app.schemas.memory import MemorySource, UpsertMemoryRequest
from app.storage.local_object_store import LocalObjectStore


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


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _require_module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Phase J must expose {name}: {exc}")


def _call_first(target: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    errors: list[str] = []
    for name in names:
        func = getattr(target, name, None)
        if not callable(func):
            continue
        try:
            return func(*args, **kwargs)
        except TypeError as exc:
            errors.append(f"{name}: {exc}")
            if kwargs and not args:
                try:
                    return func(kwargs)
                except TypeError as dict_exc:
                    errors.append(f"{name}(dict): {dict_exc}")
    detail = f" Signature errors: {'; '.join(errors)}" if errors else ""
    pytest.fail(f"{target!r} must expose one of: {', '.join(names)}.{detail}")


def _make_memory_service(object_store: LocalObjectStore) -> Any:
    module = _require_module("app.memory.service")
    service_cls = getattr(module, "MemoryService", None)
    if service_cls is None:
        pytest.fail("app.memory.service must expose MemoryService.")
    try:
        return service_cls(object_store=object_store)
    except TypeError:
        return service_cls(object_store)


def _upsert(service: Any, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "default",
        "user_id": "default_user",
        "memory_type": "user_preference",
        "type": "user_preference",
        "field": "answer_style",
        "summary": "User prefers concise Chinese answers.",
        "content": "The user wants concise Chinese answers with concrete conclusions.",
        "source_thread_id": "thread_seed",
        "source_message_id": "msg_seed",
        "source": {
            "thread_id": "thread_seed",
            "message_id": "msg_seed",
            "evidence": "User explicitly requested concise Chinese answers.",
        },
        "evidence": "User explicitly requested concise Chinese answers.",
        "confidence": 0.95,
        "enabled_for_model_context": True,
    }
    payload.update(overrides)
    if hasattr(service, "upsert_memory"):
        identity = RuntimeIdentity(
            user_id=payload["user_id"],
            role="owner",
            workspace_id=payload["workspace_id"],
            workspace_role="owner",
        )
        request = UpsertMemoryRequest(
            memory_id=payload.get("memory_id"),
            scope=payload.get("scope"),
            type=payload.get("memory_type") or payload["type"],
            field=payload.get("field"),
            value=payload.get("value"),
            summary=payload["summary"],
            content=payload["content"],
            source=MemorySource(
                thread_id=payload["source"].get("thread_id"),
                message_id=payload["source"].get("message_id"),
                evidence=payload["source"].get("evidence"),
            ),
            confidence=payload["confidence"],
            enabled_for_model_context=payload["enabled_for_model_context"],
        )
        return _dump(service.upsert_memory(payload["workspace_id"], identity, request))
    return _dump(
        _call_first(
            service,
            ("upsert", "upsert_memory", "memory_upsert", "upsert_canonical_json"),
            **payload,
        )
    )


def _propose(service: Any, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "default",
        "user_id": "default_user",
        "memory_type": "project_fact",
        "field": "storage_stack",
        "summary": "Storage stack uses MinIO.",
        "content": "The current project stores object payloads in MinIO.",
        "source": {
            "thread_id": "thread_seed",
            "message_id": "msg_seed",
            "evidence": "Project docs describe MinIO object storage.",
        },
        "confidence": 0.9,
        "enabled_for_model_context": True,
    }
    payload.update(overrides)
    identity = RuntimeIdentity(
        user_id=payload["user_id"],
        role="owner",
        workspace_id=payload["workspace_id"],
        workspace_role="owner",
    )
    request = UpsertMemoryRequest(
        type=payload.get("memory_type") or payload["type"],
        field=payload.get("field"),
        value=payload.get("value"),
        summary=payload["summary"],
        content=payload["content"],
        source=MemorySource(
            thread_id=payload["source"].get("thread_id"),
            message_id=payload["source"].get("message_id"),
            evidence=payload["source"].get("evidence"),
        ),
        confidence=payload["confidence"],
        enabled_for_model_context=payload["enabled_for_model_context"],
    )
    return _dump(service.propose_memory(payload["workspace_id"], identity, request))


def _memory_id(value: Any) -> str:
    dumped = _dump(value)
    if isinstance(dumped, dict):
        for key in ("memory_id", "id"):
            if dumped.get(key):
                return str(dumped[key])
        data = dumped.get("data")
        if isinstance(data, dict) and data.get("memory_id"):
            return str(data["memory_id"])
    pytest.fail(f"Memory result must include memory_id: {dumped}")


def _items(body: Any, key: str = "memories") -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    assert isinstance(body, dict)
    for candidate in (key, "items", "hits"):
        value = body.get(candidate)
        if isinstance(value, list):
            return value
    pytest.fail(f"Expected list payload under {key}/items/hits: {body}")


def _post_thread(client: TestClient) -> dict[str, Any]:
    response = client.post("/workspaces/default/threads", json={"title": "Phase J snapshot"})
    assert response.status_code == 200
    return response.json()


def _post_run(client: TestClient, thread_id: str) -> dict[str, Any]:
    response = client.post(
        f"/workspaces/default/threads/{thread_id}/runs",
        json={
            "idempotency_key": "phase-j-memory-snapshot",
            "user_message": "Use my saved preferences if they are enabled.",
        },
    )
    assert response.status_code == 200
    return response.json()


def _memory_snapshot_from_run(run: dict[str, Any]) -> dict[str, Any]:
    leaf_state = run.get("leaf_state") or {}
    snapshot = leaf_state.get("memory_snapshot")
    if isinstance(snapshot, dict):
        return snapshot
    pytest.fail(f"Run leaf_state must expose injected memory_snapshot for audit: {run}")


def test_memory_api_lists_summaries_and_reads_details(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    service = _make_memory_service(object_store)
    created = _upsert(
        service,
        summary="User prefers concise Chinese answers.",
        content="Full memory content should only appear on detail reads.",
    )

    list_response = client.get(
        "/workspaces/default/memories",
        params={"query": "Chinese answers", "limit": 10},
    )
    assert list_response.status_code == 200
    memories = _items(list_response.json())
    listed = [memory for memory in memories if memory.get("memory_id") == _memory_id(created)]
    assert listed
    assert listed[0]["summary"] == "User prefers concise Chinese answers."
    assert "content" not in listed[0]
    assert "source" not in listed[0]

    detail_response = client.get(f"/workspaces/default/memories/{_memory_id(created)}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["memory_id"] == _memory_id(created)
    assert detail["content"] == "Full memory content should only appear on detail reads."
    assert detail["source"]["evidence"]


def test_memory_api_exposes_user_visible_no_approval_profile_and_preference(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    service = _make_memory_service(object_store)
    profile = _upsert(
        service,
        memory_type="user_profile",
        type="user_profile",
        field="name",
        value="Zhang San",
        summary="User name is Zhang San.",
        content="The user said their name is Zhang San.",
    )
    preference = _upsert(service)

    response = client.get("/workspaces/default/memories")
    assert response.status_code == 200
    memories_by_id = {memory["memory_id"]: memory for memory in _items(response.json())}

    for memory_id in (_memory_id(profile), _memory_id(preference)):
        memory = memories_by_id[memory_id]
        assert memory["frontend_visible"] is True
        assert memory["requires_approval"] is False
        assert memory["enabled_for_model_context"] is True


def test_memory_api_exposes_sync_state_and_auto_queued_sync_job(
    client: TestClient,
) -> None:
    create_response = client.post(
        "/workspaces/default/memories",
        json={
            "type": "user_preference",
            "field": "answer_style",
            "summary": "User prefers concise Chinese answers.",
            "content": "The user wants concise Chinese answers with concrete conclusions.",
            "source": {
                "thread_id": "thread_seed",
                "message_id": "msg_seed",
                "evidence": "User explicitly requested concise Chinese answers.",
            },
            "confidence": 0.95,
            "enabled_for_model_context": True,
        },
    )

    state_response = client.get("/workspaces/default/memories/sync-state")
    jobs_response = client.get(
        "/workspaces/default/jobs",
        params={"job_type": "memory_sync_job", "limit": 10},
    )

    assert create_response.status_code == 200
    assert state_response.status_code == 200
    state = state_response.json()
    assert len(state["pending_targets"]) == 1
    assert state["pending_targets"][0]["target"] == "milvus"
    assert state["last_enqueue"]["status"] == "queued"
    assert jobs_response.status_code == 200
    jobs = jobs_response.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "memory_sync_job"
    assert jobs[0]["target_scope"]["scope_type"] == "memory_sync"


def test_memory_api_approves_and_rejects_pending_project_candidates(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    service = _make_memory_service(object_store)
    approved_candidate = _propose(
        service,
        summary="Backend object store is MinIO.",
        content="The backend object store is MinIO.",
    )
    rejected_candidate = _propose(
        service,
        memory_type="project_rule",
        field="edit_scope",
        summary="Rejected project rule should not be injected.",
        content="This candidate should be rejected before model injection.",
    )
    other_user_candidate = _propose(
        service,
        user_id="other_user",
        summary="Other user candidate must remain private.",
        content="This candidate belongs to a different user.",
    )

    approve_response = client.post(
        f"/workspaces/default/memories/{_memory_id(approved_candidate)}/approve"
    )
    reject_response = client.post(
        f"/workspaces/default/memories/{_memory_id(rejected_candidate)}/reject"
    )
    scoped_response = client.post(
        f"/workspaces/default/memories/{_memory_id(other_user_candidate)}/approve"
    )

    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["status"] == "active"
    assert approved["enabled_for_model_context"] is True
    assert approved["requires_approval"] is False
    assert reject_response.status_code == 200
    rejected = reject_response.json()
    assert rejected["status"] == "rejected"
    assert rejected["enabled_for_model_context"] is False
    assert rejected["requires_approval"] is False
    assert scoped_response.status_code == 404

    list_response = client.get("/workspaces/default/memories")
    assert list_response.status_code == 200
    memories = _items(list_response.json())
    memories_by_id = {memory["memory_id"]: memory for memory in memories}
    assert _memory_id(approved_candidate) in memories_by_id
    assert _memory_id(rejected_candidate) in memories_by_id
    assert _memory_id(other_user_candidate) not in memories_by_id
    assert "content" not in memories_by_id[_memory_id(approved_candidate)]
    assert "source" not in memories_by_id[_memory_id(approved_candidate)]


def test_disable_and_delete_memory_api_prevents_runtime_snapshot_injection(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    service = _make_memory_service(object_store)
    active = _upsert(service, field="active_pref", summary="Active preference.")
    disabled = _upsert(service, field="disabled_pref", summary="Disabled preference.")
    deleted = _upsert(service, field="deleted_pref", summary="Deleted preference.")

    disable_response = client.patch(
        f"/workspaces/default/memories/{_memory_id(disabled)}",
        json={"enabled_for_model_context": False},
    )
    delete_response = client.delete(
        f"/workspaces/default/memories/{_memory_id(deleted)}",
        params={"mode": "delete", "reason": "contract test"},
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["enabled_for_model_context"] is False
    assert delete_response.status_code in {200, 204}

    thread = _post_thread(client)
    run = _post_run(client, thread["thread_id"])
    snapshot = _memory_snapshot_from_run(run)
    included = set(snapshot["included_memory_ids"])

    assert _memory_id(active) in included
    assert _memory_id(disabled) not in included
    assert _memory_id(deleted) not in included


def test_memory_api_patch_supports_editable_metadata_fields(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    service = _make_memory_service(object_store)
    created = _upsert(
        service,
        field="answer_style",
        scope="global",
        value="concise",
        summary="User prefers concise Chinese answers.",
        content="The user wants concise Chinese answers.",
    )

    patch_response = client.patch(
        f"/workspaces/default/memories/{_memory_id(created)}",
        json={
            "confidence": 0.8,
            "content": "The user wants compact Chinese responses.",
            "field": "answer_format",
            "scope": "workspace",
            "summary": "User wants compact Chinese responses.",
            "value": None,
        },
    )
    list_response = client.get("/workspaces/default/memories")

    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched["scope"] == "workspace"
    assert patched["workspace_id"] == "default"
    assert patched["field"] == "answer_format"
    assert patched["value"] is None
    assert patched["confidence"] == 0.8
    assert patched["content"] == "The user wants compact Chinese responses."
    memories = [item for item in _items(list_response.json()) if item["memory_id"] == _memory_id(created)]
    assert len(memories) == 1
    assert memories[0]["scope"] == "workspace"


def test_disabled_memory_api_blocks_silent_rewrite(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    service = _make_memory_service(object_store)
    created = _upsert(
        service,
        field="answer_style",
        summary="User prefers concise Chinese answers.",
        content="The user wants concise Chinese answers with concrete conclusions.",
    )

    disable_response = client.patch(
        f"/workspaces/default/memories/{_memory_id(created)}",
        json={"enabled_for_model_context": False},
    )
    rewrite_response = client.post(
        "/workspaces/default/memories",
        json={
            "type": "user_preference",
            "field": "answer_style",
            "summary": "User prefers concise Chinese answers.",
            "content": "The user wants concise Chinese answers with concrete conclusions.",
            "source": {
                "thread_id": "thread_seed",
                "message_id": "msg_seed",
                "evidence": "Model tried to recreate a disabled preference.",
            },
            "confidence": 0.95,
            "enabled_for_model_context": True,
        },
    )

    assert disable_response.status_code == 200
    assert rewrite_response.status_code == 409
    body = rewrite_response.json()
    assert body["error_type"] == "memory_previously_disabled"
    assert body["details"]["field"] == "answer_style"
