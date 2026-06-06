from __future__ import annotations

import hashlib
import json
import re
import zipfile
from io import BytesIO
from typing import Any

from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.observability.redact import redact_log_value
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import (
    diagnostic_bundle_manifest_key,
    diagnostic_bundle_package_key,
    diagnostic_bundle_payload_key,
    log_archive_manifest_key,
    log_archive_object_key,
    system_component_logs_prefix,
    system_error_logs_prefix,
    system_full_logs_prefix,
    system_logs_prefix,
    system_summary_log_key,
)

ERROR_LEVELS = {"WARNING", "WARN", "ERROR", "FATAL"}
DEFAULT_RUNTIME_INSTANCE_ID = "rt_local"


class ObservabilityService:
    def __init__(
        self,
        object_store: ObjectStore,
        runtime_instance_id: str = DEFAULT_RUNTIME_INSTANCE_ID,
        service: str = "agent-runtime",
        environment: str = "development",
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.runtime_instance_id = _safe_component(runtime_instance_id)
        self.service_name = service
        self.environment = environment

    def record_event(
        self,
        *,
        component: str,
        event_type: str,
        message: str,
        workspace_id: str | None = None,
        severity: str = "INFO",
        level: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        role: str | None = None,
        runtime_instance_id: str | None = None,
        payload_summary: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        status: str | None = None,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now_iso()
        normalized_severity = (level or severity).upper()
        normalized_component = _safe_component(component)
        runtime_id = _safe_component(runtime_instance_id or self.runtime_instance_id)
        event = {
            "schema_version": 1,
            "log_id": new_id("log"),
            "timestamp": timestamp,
            "level": normalized_severity,
            "severity": normalized_severity,
            "component": normalized_component,
            "event_type": event_type,
            "message": redact_log_value(message),
            "runtime_instance_id": runtime_id,
            "service": self.service_name,
            "environment": self.environment,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "role": role,
            "thread_id": thread_id,
            "run_id": run_id,
            "trace_id": trace_id or new_id("trace"),
            "span_id": new_id("span"),
            "duration_ms": duration_ms,
            "status": status,
            "error_type": error_type,
            "payload_summary": redact_log_value(payload_summary or {}),
            "redacted": True,
        }
        event = redact_log_value(event)
        date = _date_from_timestamp(str(event["timestamp"]))
        self._append_full(date, event)
        self._append_summary(date, event)
        self._append_component(date, normalized_component, event)
        if normalized_severity in ERROR_LEVELS:
            self._append_error(date, event)
        return event

    write_event = record_event
    write_log_event = record_event
    append_event = record_event
    log_event = record_event
    emit = record_event
    log = record_event

    def query_logs(
        self,
        *,
        workspace_id: str | None = None,
        stream: str = "full",
        component: str | None = None,
        level: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        records = self._read_records(stream=stream, component=component)
        filtered = [
            record
            for record in records
            if _matches_log(
                record,
                workspace_id=workspace_id,
                level=level,
                trace_id=trace_id,
                run_id=run_id,
                query=query,
            )
        ]
        filtered = sorted(filtered, key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        return [redact_log_value(item) for item in filtered[: max(1, min(limit, 1000))]]

    def read_summary(
        self,
        *,
        workspace_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[str]:
        records = self.query_logs(
            workspace_id=workspace_id,
            stream="full",
            trace_id=trace_id,
            run_id=run_id,
            query=query,
            limit=limit,
        )
        return [_summary_line(record) for record in records]

    def create_diagnostic_bundle(
        self,
        *,
        workspace_id: str,
        created_by: str,
        bundle_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        component: str | None = None,
        components: list[str] | None = None,
        limit: int = 100,
        related_job_id: str | None = None,
        include_summary: bool = True,
        include_errors: bool = True,
        include_component_logs: bool = True,
    ) -> dict[str, Any]:
        current_bundle_id = bundle_id or new_id("diag")
        created_at = utc_now_iso()
        date = _date_from_timestamp(created_at)
        full = self.query_logs(
            workspace_id=workspace_id,
            stream="full",
            component=component,
            trace_id=trace_id,
            run_id=run_id,
            limit=limit,
        )
        errors = self.query_logs(
            workspace_id=workspace_id,
            stream="errors",
            trace_id=trace_id,
            run_id=run_id,
            limit=limit,
        )
        selected_components = [component] if component else []
        selected_components.extend(components or [])
        selected_components = [item for item in dict.fromkeys(selected_components) if item]
        component_logs: list[dict[str, Any]] = []
        for selected_component in selected_components:
            component_logs.extend(
                self.query_logs(
                    workspace_id=workspace_id,
                    stream="component",
                    component=selected_component,
                    trace_id=trace_id,
                    run_id=run_id,
                    limit=limit,
                )
            )
        included_slices = []
        if include_summary:
            included_slices.append("summary")
        if include_errors:
            included_slices.append("errors")
        if include_component_logs:
            included_slices.extend(f"components/{item}" for item in selected_components)
        manifest_key = diagnostic_bundle_manifest_key(
            date,
            self.runtime_instance_id,
            current_bundle_id,
        )
        payload_key = diagnostic_bundle_payload_key(
            date,
            self.runtime_instance_id,
            current_bundle_id,
        )
        package_key = diagnostic_bundle_package_key(
            date,
            self.runtime_instance_id,
            current_bundle_id,
        )
        manifest = {
            "schema_version": 1,
            "bundle_id": current_bundle_id,
            "workspace_id": workspace_id,
            "created_by": created_by,
            "created_at": created_at,
            "runtime_instance_id": self.runtime_instance_id,
            "filters": {
                "trace_id": trace_id,
                "run_id": run_id,
                "component": component,
                "components": selected_components,
                "limit": limit,
            },
            "included_slices": included_slices,
            "manifest_object_key": manifest_key,
            "object_key": payload_key,
            "package_object_key": package_key,
            "package_content_type": "application/zip",
            "related_job_id": related_job_id,
            "item_counts": {
                "system_full": len(full),
                "errors": len(errors),
                "component_logs": len(component_logs),
            },
            "redacted": True,
        }
        manifest = redact_log_value(manifest)
        payload = {
            "manifest": manifest,
            "system_summary": self.read_summary(
                workspace_id=workspace_id,
                trace_id=trace_id,
                run_id=run_id,
                limit=limit,
            )
            if include_summary
            else [],
            "system_full_filtered": full,
            "errors": errors if include_errors else [],
            "component_logs": component_logs if include_component_logs else [],
            "redacted": True,
        }
        payload = redact_log_value(payload)
        package_bytes = _build_diagnostic_bundle_package(
            manifest=manifest,
            payload=payload,
        )
        package_sha256 = "sha256:" + hashlib.sha256(package_bytes).hexdigest()
        manifest.update(
            {
                "package_sha256": package_sha256,
                "package_bytes": len(package_bytes),
            }
        )
        payload["manifest"] = manifest
        self.object_store.write_bytes(
            package_key,
            package_bytes,
            content_type="application/zip",
        )
        self.json_store.write_json(manifest_key, manifest)
        self.json_store.write_json(payload_key, payload)
        return manifest

    def archive_system_logs(
        self,
        *,
        date: str | None = None,
        runtime_instance_id: str | None = None,
        job_id: str | None = None,
        archive_id: str | None = None,
    ) -> dict[str, Any]:
        current_date = date or _date_from_timestamp(utc_now_iso())
        runtime_id = _safe_component(runtime_instance_id or self.runtime_instance_id)
        current_archive_id = archive_id or new_id("logarch")
        prefix = system_logs_prefix(current_date, runtime_id)
        manifest_key = log_archive_manifest_key(current_date, runtime_id)
        previous = self.json_store.read_json_or_default(
            manifest_key,
            {
                "schema_version": 1,
                "runtime_instance_id": runtime_id,
                "date": current_date,
                "files": [],
                "revision": 0,
            },
        )
        archived_by_sha = {
            str(item.get("sha256")): item
            for item in previous.get("files", [])
            if isinstance(item, dict) and item.get("sha256")
        }
        archived_files: list[dict[str, Any]] = []
        skipped_files: list[dict[str, Any]] = []
        for source_key in sorted(self.object_store.list_keys(prefix)):
            if not _is_archivable_log_key(source_key):
                continue
            raw_text = self.object_store.read_text(source_key)
            redacted_text = _redact_log_text(raw_text)
            sha256 = "sha256:" + hashlib.sha256(redacted_text.encode("utf-8")).hexdigest()
            if sha256 in archived_by_sha:
                skipped_files.append(
                    {
                        "source_object_key": source_key,
                        "sha256": sha256,
                        "reason": "duplicate_sha256",
                    }
                )
                continue
            file_name = _archive_file_name(source_key)
            object_key = log_archive_object_key(
                current_date,
                runtime_id,
                current_archive_id,
                file_name,
            )
            self.object_store.write_text(object_key, redacted_text)
            record = {
                "source_object_key": source_key,
                "object_key": object_key,
                "sha256": sha256,
                "bytes": len(redacted_text.encode("utf-8")),
                "archived_at": utc_now_iso(),
                "job_id": job_id,
                "redacted": True,
            }
            archived_by_sha[sha256] = record
            archived_files.append(record)

        manifest = {
            "schema_version": 1,
            "runtime_instance_id": runtime_id,
            "date": current_date,
            "archive_id": current_archive_id,
            "job_id": job_id,
            "files": sorted(
                archived_by_sha.values(),
                key=lambda item: str(item.get("source_object_key") or ""),
            ),
            "last_archived_files": archived_files,
            "last_skipped_files": skipped_files,
            "file_count": len(archived_by_sha),
            "last_archived_count": len(archived_files),
            "updated_at": utc_now_iso(),
            "revision": int(previous.get("revision") or 0) + 1,
            "redacted": True,
        }
        self.json_store.write_json(manifest_key, redact_log_value(manifest))
        return redact_log_value(manifest)

    def _append_full(self, date: str, event: dict[str, Any]) -> None:
        JsonlSegmentStore(
            self.object_store,
            system_full_logs_prefix(date, self.runtime_instance_id),
        ).append(event)

    def _append_error(self, date: str, event: dict[str, Any]) -> None:
        JsonlSegmentStore(
            self.object_store,
            system_error_logs_prefix(date, self.runtime_instance_id),
        ).append(event)

    def _append_component(self, date: str, component: str, event: dict[str, Any]) -> None:
        JsonlSegmentStore(
            self.object_store,
            system_component_logs_prefix(date, self.runtime_instance_id, component),
        ).append(event)

    def _append_summary(self, date: str, event: dict[str, Any]) -> None:
        key = system_summary_log_key(date, self.runtime_instance_id)
        existing = self.object_store.read_text(key) if self.object_store.exists(key) else ""
        self.object_store.write_text(key, f"{existing}{_summary_line(event)}\n")

    def _read_records(self, *, stream: str, component: str | None = None) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for date in self._available_dates():
            if stream == "full":
                prefix = system_full_logs_prefix(date, self.runtime_instance_id)
            elif stream == "errors":
                prefix = system_error_logs_prefix(date, self.runtime_instance_id)
            elif stream == "component":
                if not component:
                    return []
                prefix = system_component_logs_prefix(
                    date,
                    self.runtime_instance_id,
                    _safe_component(component),
                )
            else:
                return []
            records.extend(JsonlSegmentStore(self.object_store, prefix).read_all())
        return records

    def _available_dates(self) -> list[str]:
        dates: set[str] = set()
        for key in self.object_store.list_keys("system/logs"):
            parts = key.split("/")
            if len(parts) >= 4 and parts[0] == "system" and parts[1] == "logs":
                if parts[3] == self.runtime_instance_id:
                    dates.add(parts[2])
        if not dates:
            return [_date_from_timestamp(utc_now_iso())]
        return sorted(dates)


def _summary_line(record: dict[str, Any]) -> str:
    return (
        f"{record.get('timestamp')} {record.get('severity')} "
        f"{record.get('component')} {record.get('event_type')} "
        f"trace={record.get('trace_id')} {record.get('message')}"
    )


def _matches_log(
    record: dict[str, Any],
    *,
    workspace_id: str | None,
    level: str | None,
    trace_id: str | None,
    run_id: str | None,
    query: str | None,
) -> bool:
    if workspace_id and record.get("workspace_id") not in {None, workspace_id}:
        return False
    if level and str(record.get("severity", "")).upper() != level.upper():
        return False
    if trace_id and record.get("trace_id") != trace_id:
        return False
    if run_id and record.get("run_id") != run_id:
        return False
    if query and query.lower() not in json.dumps(record, ensure_ascii=False).lower():
        return False
    return True


def _date_from_timestamp(timestamp: str) -> str:
    return timestamp.split("T", 1)[0]


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return normalized.strip("._-") or "system"


def _is_archivable_log_key(key: str) -> bool:
    if "/diagnostic_bundles/" in key or "/log_archives/" in key:
        return False
    return key.endswith((".jsonl", ".log", ".txt"))


def _archive_file_name(source_key: str) -> str:
    digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:16]
    raw_name = source_key.rsplit("/", 1)[-1] or "log"
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_name).strip("._-")
    if safe_name.endswith(".jsonl"):
        stem = safe_name[:-6]
        extension = ".jsonl"
    elif "." in safe_name:
        stem, extension = safe_name.rsplit(".", 1)
        extension = f".{extension[:12]}"
    else:
        stem = safe_name
        extension = ".log"
    stem = (stem or "log")[:64]
    return f"{digest}_{stem}{extension}"


def _redact_log_text(value: str) -> str:
    redacted_lines = []
    for line in value.splitlines():
        if not line.strip():
            redacted_lines.append(line)
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            redacted_lines.append(str(redact_log_value(line)))
        else:
            redacted_lines.append(
                json.dumps(redact_log_value(parsed), ensure_ascii=False, sort_keys=True)
            )
    suffix = "\n" if value.endswith("\n") else ""
    return "\n".join(redacted_lines) + suffix


def _build_diagnostic_bundle_package(
    *,
    manifest: dict[str, Any],
    payload: dict[str, Any],
) -> bytes:
    sidecar = {
        "schema_version": 1,
        "package_type": "runtime_diagnostic_sidecar",
        "bundle_id": manifest.get("bundle_id"),
        "manifest_object_key": manifest.get("manifest_object_key"),
        "payload_object_key": manifest.get("object_key"),
        "redacted": True,
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _canonical_json(manifest))
        archive.writestr("bundle.json", _canonical_json(payload))
        archive.writestr("sidecar.json", _canonical_json(sidecar))
    return buffer.getvalue()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
