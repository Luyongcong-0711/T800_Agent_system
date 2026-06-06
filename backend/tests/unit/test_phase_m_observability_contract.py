from __future__ import annotations

import importlib
import json
import zipfile
from io import BytesIO
from typing import Any

import pytest

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


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_dump(item) for item in value)
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _require_module(*names: str) -> Any:
    errors: list[str] = []
    for name in names:
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            errors.append(f"{name}: {exc}")
    pytest.fail(f"Phase M must expose one of {names}. Missing: {'; '.join(errors)}")


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
            if kwargs:
                try:
                    return func(kwargs)
                except TypeError as dict_exc:
                    errors.append(f"{name}(dict): {dict_exc}")
    detail = f" Signature errors: {'; '.join(errors)}" if errors else ""
    pytest.fail(f"{target!r} must expose one of: {', '.join(names)}.{detail}")


def _make_service(tmp_path: Any) -> tuple[Any, LocalObjectStore]:
    object_store = LocalObjectStore(tmp_path / "objects")
    module = _require_module(
        "app.observability.service",
        "app.observability.logging",
    )
    service_cls = (
        getattr(module, "ObservabilityService", None)
        or getattr(module, "SystemLogService", None)
        or getattr(module, "RuntimeLogService", None)
    )
    if service_cls is None:
        pytest.fail(
            "Phase M observability module must expose ObservabilityService, "
            "SystemLogService, or RuntimeLogService."
        )
    kwargs = {
        "object_store": object_store,
        "runtime_instance_id": "rt_contract",
        "service": "agent-runtime",
        "environment": "test",
    }
    try:
        return service_cls(**kwargs), object_store
    except TypeError:
        try:
            return service_cls(object_store=object_store), object_store
        except TypeError:
            return service_cls(object_store), object_store


def _emit_log(service: Any, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "default",
        "trace_id": "trace_phase_m_unit",
        "component": "model_connector",
        "severity": "ERROR",
        "level": "ERROR",
        "event_type": "model_provider_failed",
        "message": f"Provider failed with {SECRET_TEXT}",
        "runtime_instance_id": "rt_contract",
        "user_id": "default_user",
        "run_id": "run_phase_m",
        "thread_id": "thread_phase_m",
        "error_type": "provider_5xx",
        "payload_summary": {
            "request_headers": {
                "authorization": "Bearer sk-test-secret",
                "cookie": "session=sk-test-secret",
            },
            "api_key": "sk-test-secret",
            "password": "hunter2",
            "token": "secret-token",
            "safe_count": 1,
        },
    }
    payload.update(overrides)
    result = _call_first(
        service,
        (
            "write_event",
            "write_log_event",
            "record_event",
            "append_event",
            "log_event",
            "emit",
            "log",
        ),
        **payload,
    )
    dumped = _dump(result)
    if isinstance(dumped, dict):
        return dumped
    return payload


def _read_object_jsonl(object_store: LocalObjectStore, key: str) -> list[dict[str, Any]]:
    lines = object_store.read_text(key).splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _all_store_text(object_store: LocalObjectStore) -> str:
    text_parts: list[str] = []
    for key in object_store.list_keys(""):
        if key.endswith((".json", ".jsonl", ".log", ".txt")):
            text_parts.append(object_store.read_text(key))
    return "\n".join(text_parts).lower()


def _assert_no_secret_material(value: Any) -> None:
    serialized = json.dumps(_dump(value), ensure_ascii=False, default=str).lower()
    for term in FORBIDDEN_TERMS:
        assert term not in serialized


def _find_keys(object_store: LocalObjectStore, *contains: str) -> list[str]:
    keys = object_store.list_keys("")
    return [key for key in keys if all(part in key for part in contains)]


def _first_json_event(object_store: LocalObjectStore, key: str) -> dict[str, Any]:
    if key.endswith(".jsonl"):
        records = _read_object_jsonl(object_store, key)
        assert records, f"{key} must contain at least one JSONL record."
        return records[0]
    return json.loads(object_store.read_text(key))


def test_observability_service_writes_summary_full_errors_and_component_logs(tmp_path) -> None:
    service, object_store = _make_service(tmp_path)

    event = _emit_log(service)

    summary_keys = _find_keys(object_store, "system/logs", "system_summary")
    full_keys = _find_keys(object_store, "system/logs", "system_full")
    error_keys = _find_keys(object_store, "system/logs", "errors")
    component_keys = _find_keys(object_store, "system/logs", "components", "model_connector")

    assert summary_keys, "system_summary logs must be archived under system/logs."
    assert full_keys, "system_full JSONL logs must be archived under system/logs."
    assert error_keys, "WARNING/ERROR logs must be copied to errors JSONL."
    assert component_keys, "component logs must be archived by component name."

    full_event = _first_json_event(object_store, full_keys[0])
    for required in ("trace_id", "workspace_id", "component", "severity", "timestamp"):
        assert full_event.get(required), f"system_full event must include {required}."
    assert full_event["trace_id"] == "trace_phase_m_unit"
    assert full_event["workspace_id"] == "default"
    assert full_event["component"] == "model_connector"
    assert str(full_event["severity"]).upper() in {"ERROR", "WARNING", "WARN", "FATAL"}
    assert full_event.get("redacted") is True
    _assert_no_secret_material(event)
    _assert_no_secret_material(full_event)


def test_observability_redacts_secret_like_fields_across_all_sinks(tmp_path) -> None:
    service, object_store = _make_service(tmp_path)

    _emit_log(service)

    all_text = _all_store_text(object_store)
    for term in FORBIDDEN_TERMS:
        assert term not in all_text
    assert "***" in all_text or "redacted" in all_text


def test_component_logs_preserve_queryable_trace_and_component_fields(tmp_path) -> None:
    service, object_store = _make_service(tmp_path)

    _emit_log(
        service,
        component="context_engine",
        event_type="context_preflight_finished",
        severity="INFO",
        level="INFO",
        error_type=None,
    )

    component_keys = _find_keys(object_store, "components", "context_engine")
    assert component_keys
    component_event = _first_json_event(object_store, component_keys[0])
    assert component_event["component"] == "context_engine"
    assert component_event["trace_id"] == "trace_phase_m_unit"
    assert component_event["workspace_id"] == "default"
    assert component_event["event_type"] == "context_preflight_finished"
    assert component_event.get("redacted") is True


def test_diagnostic_bundle_writes_redacted_sidecar_package(tmp_path) -> None:
    service, object_store = _make_service(tmp_path)

    _emit_log(service)
    manifest = service.create_diagnostic_bundle(
        workspace_id="default",
        created_by="default_user",
        bundle_id="diag_unit",
        trace_id="trace_phase_m_unit",
        components=["model_connector"],
        related_job_id="job_diag_unit",
    )

    package_key = manifest["package_object_key"]
    assert package_key.startswith("system/logs/")
    assert package_key.endswith("/bundle.zip")
    assert object_store.exists(package_key)
    assert manifest["package_sha256"].startswith("sha256:")
    assert manifest["package_bytes"] == len(object_store.read_bytes(package_key))

    with zipfile.ZipFile(BytesIO(object_store.read_bytes(package_key))) as archive:
        names = set(archive.namelist())
        assert {"manifest.json", "bundle.json", "sidecar.json"} <= names
        packaged_manifest = json.loads(archive.read("manifest.json"))
        packaged_payload = json.loads(archive.read("bundle.json"))
        packaged_sidecar = json.loads(archive.read("sidecar.json"))

    assert packaged_manifest["redacted"] is True
    assert packaged_payload["redacted"] is True
    assert packaged_sidecar["package_type"] == "runtime_diagnostic_sidecar"
    assert packaged_sidecar["redacted"] is True
    _assert_no_secret_material(packaged_manifest)
    _assert_no_secret_material(packaged_payload)
    _assert_no_secret_material(packaged_sidecar)
    _assert_no_secret_material(object_store.read_text(manifest["manifest_object_key"]))
    _assert_no_secret_material(object_store.read_text(manifest["object_key"]))


def test_archive_system_logs_writes_redacted_manifest_and_archive_files(tmp_path) -> None:
    service, object_store = _make_service(tmp_path)

    event = _emit_log(service)
    date = str(event["timestamp"]).split("T", 1)[0]
    object_store.write_text(
        f"system/logs/{date}/rt_contract/raw/plain.log",
        f"raw log line {SECRET_TEXT}\n",
    )

    manifest = service.archive_system_logs(
        date=date,
        runtime_instance_id="rt_contract",
        job_id="job_log_archive_unit",
        archive_id="arch_unit",
    )

    assert manifest["redacted"] is True
    assert manifest["runtime_instance_id"] == "rt_contract"
    assert manifest["date"] == date
    assert manifest["last_archived_count"] >= 1
    assert manifest["file_count"] >= manifest["last_archived_count"]
    assert object_store.exists(
        f"system/logs/{date}/rt_contract/log_archives/manifest.json"
    )
    assert all(
        "/log_archives/files/arch_unit/" in item["object_key"]
        for item in manifest["last_archived_files"]
    )
    for item in manifest["last_archived_files"]:
        assert object_store.exists(item["object_key"])
        _assert_no_secret_material(object_store.read_text(item["object_key"]))
    _assert_no_secret_material(manifest)


def test_archive_system_logs_is_idempotent_by_redacted_sha256(tmp_path) -> None:
    service, object_store = _make_service(tmp_path)

    event = _emit_log(service)
    date = str(event["timestamp"]).split("T", 1)[0]

    first = service.archive_system_logs(
        date=date,
        runtime_instance_id="rt_contract",
        job_id="job_log_archive_first",
        archive_id="arch_first",
    )
    first_archive_file_keys = _find_keys(object_store, "log_archives", "files")
    second = service.archive_system_logs(
        date=date,
        runtime_instance_id="rt_contract",
        job_id="job_log_archive_second",
        archive_id="arch_second",
    )
    second_archive_file_keys = _find_keys(object_store, "log_archives", "files")

    assert first["last_archived_count"] >= 1
    assert second["last_archived_count"] == 0
    assert len(second["last_skipped_files"]) >= first["file_count"]
    assert all(item["reason"] == "duplicate_sha256" for item in second["last_skipped_files"])
    assert second["file_count"] == first["file_count"]
    assert second_archive_file_keys == first_archive_file_keys
    _assert_no_secret_material(second)
