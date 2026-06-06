from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass
from typing import Any

import pytest

from app.storage.local_object_store import LocalObjectStore

pytest.importorskip(
    "app.jobs.service",
    reason="Phase E job service implementation has not landed yet.",
)


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@dataclass(frozen=True)
class _Identity:
    user_id: str = "default_user"
    role: str = "owner"
    workspace_id: str = "default"
    workspace_role: str = "owner"


def _job_store_class() -> type:
    for module_name, class_names in (
        ("app.jobs.store", ("JobStore",)),
        ("app.jobs.service", ("JobStore", "JobService")),
    ):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        for class_name in class_names:
            store_class = getattr(module, class_name, None)
            if store_class is not None:
                return store_class
    pytest.skip("No JobStore/JobService implementation is available yet.")


def _schema_class(name: str) -> type | None:
    for module_name in ("app.schemas.jobs", "app.schemas.job", "app.jobs.schemas"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        value = getattr(module, name, None)
        if value is not None:
            return value
    return None


def _job_store(tmp_path):
    store_class = _job_store_class()
    object_store = LocalObjectStore(tmp_path / "objects")
    try:
        return store_class(object_store), object_store
    except TypeError:
        return store_class(object_store=object_store), object_store


def _target(doc_id: str = "doc_001", doc_version_id: str = "docv_001") -> dict[str, str]:
    return {
        "scope_type": "document_version",
        "knowledge_base_id": "kb_default",
        "doc_id": doc_id,
        "doc_version_id": doc_version_id,
    }


def _request(
    *,
    job_type: str = "document_ingestion_job",
    target_scope: dict[str, Any] | None = None,
    idempotency_key: str = "idem-doc-001",
    params: dict[str, Any] | None = None,
    priority: str = "normal",
) -> Any:
    payload = {
        "job_type": job_type,
        "priority": priority,
        "target_scope": target_scope or _target(),
        "input": params or {"pipeline_version": "v1"},
        "idempotency_key": idempotency_key,
        "related_run_id": None,
        "related_thread_id": None,
    }
    schema = _schema_class("CreateJobRequest")
    if schema is None:
        return payload
    return schema(**payload)


def _call_create_job(store: Any, request: Any, workspace_id: str = "default") -> Any:
    signature = inspect.signature(store.create_job)
    kwargs: dict[str, Any] = {}
    if "workspace_id" in signature.parameters:
        kwargs["workspace_id"] = workspace_id
    if "identity" in signature.parameters:
        kwargs["identity"] = _Identity(workspace_id=workspace_id)
    if "created_by" in signature.parameters:
        kwargs["created_by"] = "default_user"
    if "request" in signature.parameters:
        kwargs["request"] = request
    elif "req" in signature.parameters:
        kwargs["req"] = request
    if kwargs:
        return store.create_job(**kwargs)
    return store.create_job("default", _Identity(), request)


def _call_get_manifest(store: Any, job_id: str, workspace_id: str = "default") -> Any:
    if hasattr(store, "get_job"):
        return _as_dict(store.get_job(workspace_id, job_id))["manifest"]
    signature = inspect.signature(store.get_manifest)
    kwargs: dict[str, Any] = {}
    if "workspace_id" in signature.parameters:
        kwargs["workspace_id"] = workspace_id
    if "job_id" in signature.parameters:
        kwargs["job_id"] = job_id
    if kwargs:
        return store.get_manifest(**kwargs)
    return store.get_manifest(workspace_id, job_id)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    raise TypeError(f"Cannot convert {type(value)!r} to dict.")


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


def _json_key(object_store: LocalObjectStore, suffix: str) -> str:
    matches = [key for key in object_store.list_keys("") if key.endswith(suffix)]
    assert len(matches) == 1, matches
    return matches[0]


def _read_json(object_store: LocalObjectStore, suffix: str) -> dict[str, Any]:
    return json.loads(object_store.read_text(_json_key(object_store, suffix)))


def _events(object_store: LocalObjectStore, job_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for key in sorted(object_store.list_keys(f"workspaces/default/jobs/{job_id}/events")):
        if key.endswith(".jsonl"):
            events.extend(json.loads(line) for line in object_store.read_text(key).splitlines())
    return events


def _assert_conflict(exc: pytest.ExceptionInfo[BaseException], error_type: str) -> None:
    value = exc.value
    assert getattr(value, "status_code", None) in {409, None}
    assert getattr(value, "error_type", error_type) == error_type or error_type in str(value)


def test_create_job_writes_manifest_index_leaf_and_initial_events(tmp_path) -> None:
    store, object_store = _job_store(tmp_path)

    created = _call_create_job(store, _request())
    job_id = _field(created, "job_id")

    manifest = _read_json(object_store, f"jobs/{job_id}/manifest.json")
    leaf = _read_json(object_store, f"jobs/{job_id}/leaf_state.json")
    event_index = _read_json(object_store, f"jobs/{job_id}/event_index.json")
    jobs_index = _read_json(object_store, "indexes/jobs_index.json")
    events = _events(object_store, job_id)

    assert manifest["job_id"] == job_id
    assert manifest["workspace_id"] == "default"
    assert manifest["job_type"] == "document_ingestion_job"
    assert manifest["target_scope"] == _target()
    assert manifest["status"] not in TERMINAL_STATUSES
    assert leaf["job_id"] == job_id
    assert leaf["status"] == manifest["status"]
    assert event_index["last_event_id"] == events[-1]["event_id"]
    assert [event["event_seq"] for event in events] == list(range(1, len(events) + 1))
    assert {"job_created", "job_queued"} <= {event["type"] for event in events}
    assert any(item["job_id"] == job_id for item in jobs_index["jobs"])


def test_duplicate_idempotency_returns_same_non_terminal_job(tmp_path) -> None:
    store, _ = _job_store(tmp_path)
    request = _request(idempotency_key="same-request")

    first = _call_create_job(store, request)
    second = _call_create_job(store, request)

    assert _field(second, "job_id") == _field(first, "job_id")
    manifest = _as_dict(_call_get_manifest(store, _field(first, "job_id")))
    assert manifest["status"] not in TERMINAL_STATUSES


def test_same_idempotency_key_with_different_payload_returns_conflict(tmp_path) -> None:
    store, _ = _job_store(tmp_path)
    _call_create_job(store, _request(idempotency_key="same-key", params={"pipeline_version": "v1"}))

    with pytest.raises(Exception) as exc:
        _call_create_job(
            store,
            _request(idempotency_key="same-key", params={"pipeline_version": "v2"}),
        )

    _assert_conflict(exc, "idempotency_conflict")


def test_active_critical_job_conflicts_or_dedupes_same_target_scope(tmp_path) -> None:
    store, _ = _job_store(tmp_path)
    first = _call_create_job(store, _request(idempotency_key="scope-a"))

    try:
        second = _call_create_job(store, _request(idempotency_key="scope-b"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409 or "conflict" in str(exc).lower()
    else:
        assert _field(second, "job_id") == _field(first, "job_id")


def test_different_document_target_scopes_create_separate_jobs(tmp_path) -> None:
    store, _ = _job_store(tmp_path)

    first = _call_create_job(
        store,
        _request(idempotency_key="doc-a", target_scope=_target("doc_a", "docv_a")),
    )
    second = _call_create_job(
        store,
        _request(idempotency_key="doc-b", target_scope=_target("doc_b", "docv_b")),
    )

    assert _field(first, "job_id") != _field(second, "job_id")
