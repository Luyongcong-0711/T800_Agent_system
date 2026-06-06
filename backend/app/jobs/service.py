from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.runtime.tools import redact_runtime_value
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest, RetryJobRequest
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore, RevisionConflictError
from app.storage.path_builder import (
    job_errors_key,
    job_event_index_key,
    job_events_prefix,
    job_leaf_state_key,
    job_manifest_key,
    workspace_jobs_index_key,
    workspace_prefix,
)

TERMINAL_JOB_STATUSES = {"succeeded", "partial_success", "failed", "cancelled"}
NON_RETRYABLE_RETRY_STATUSES = {"created", "queued", "running", "waiting_retry", "recovering"}
EVENT_ID_RE = re.compile(r"^evt_(?P<job_id>[A-Za-z0-9_.-]+)_(?P<seq>[0-9]{12})$")
CLAIMABLE_JOB_STATUSES = {"created", "queued", "waiting_retry", "recovering"}
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}
SENSITIVE_RESPONSE_KEY_TERMS = (
    "api_key",
    "apikey",
    "password",
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "agent_master_key",
    "master_key",
    "provider_raw_payload",
    "token",
    "secret",
)
SENSITIVE_RESPONSE_KEY_SEGMENTS = {
    "password",
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "token",
    "secret",
}
SENSITIVE_RESPONSE_KEY_PATTERNS = {
    "api_key",
    "apikey",
    "agent_master_key",
    "master_key",
    "provider_raw_payload",
}


def _stable_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _revision_next(value: dict[str, Any]) -> int:
    return int(value.get("revision", 0)) + 1


def _event_id(job_id: str, event_seq: int) -> str:
    return f"evt_{job_id}_{event_seq:012d}"


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_sensitive_response_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_").replace(" ", "_")
    if normalized in SENSITIVE_RESPONSE_KEY_TERMS:
        return True
    segments = set(normalized.split("_"))
    return bool(
        segments.intersection(SENSITIVE_RESPONSE_KEY_SEGMENTS)
        or any(pattern in normalized for pattern in SENSITIVE_RESPONSE_KEY_PATTERNS)
    )


def _sanitize_public_value(value: Any) -> Any:
    redacted = redact_runtime_value(value)
    if isinstance(redacted, dict):
        sanitized: dict[str, Any] = {}
        redacted_count = 0
        for key, item in redacted.items():
            if _is_sensitive_response_key(key):
                redacted_count += 1
                sanitized[f"redacted_field_{redacted_count}"] = "***"
            else:
                sanitized[str(key)] = _sanitize_public_value(item)
        return sanitized
    if isinstance(redacted, list):
        return [_sanitize_public_value(item) for item in redacted]
    if isinstance(redacted, tuple):
        return [_sanitize_public_value(item) for item in redacted]
    return redacted


class JobService:
    def __init__(
        self,
        object_store: ObjectStore,
        runtime_instance_id: str = "rt_local",
        job_lease_ttl_seconds: int = 300,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.runtime_instance_id = runtime_instance_id
        self.job_lease_ttl_seconds = max(1, job_lease_ttl_seconds)

    def create_job(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: CreateJobRequest,
    ) -> dict[str, Any]:
        idempotency_key = request.idempotency_key or self._computed_idempotency_key(
            workspace_id,
            request,
        )
        request_hash = self._request_hash(workspace_id, request)
        target_scope_key = self._target_scope_key(
            workspace_id,
            request.job_type,
            request.target_scope,
        )

        for _ in range(2):
            index = self._jobs_index(workspace_id)
            existing_idempotent = self._find_by_idempotency(index, idempotency_key)
            if existing_idempotent:
                if existing_idempotent.get("request_hash") != request_hash:
                    raise AgentSystemError(
                        "idempotency_conflict",
                        "Idempotency key was reused with different payload.",
                        status_code=409,
                        retryable=False,
                    )
                return self.get_job(workspace_id, existing_idempotent["job_id"])

            conflicting = self._find_active_by_target_scope(index, target_scope_key)
            if conflicting:
                raise AgentSystemError(
                    "job_target_scope_conflict",
                    "A non-terminal job already owns this target scope.",
                    status_code=409,
                    retryable=True,
                    details={
                        "existing_job_id": conflicting["job_id"],
                        "target_scope_key": target_scope_key,
                    },
                )

            try:
                return self._create_new_job(
                    workspace_id=workspace_id,
                    identity=identity,
                    request=request,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    target_scope_key=target_scope_key,
                    expected_index_revision=int(index.get("revision", 0)),
                )
            except RevisionConflictError:
                continue

        raise AgentSystemError(
            "jobs_index_concurrency_conflict",
            "Job index changed concurrently. Retry the request.",
            status_code=409,
            retryable=True,
        )

    def list_jobs(
        self,
        workspace_id: str,
        status: str | None = None,
        job_type: str | None = None,
        related_run_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        jobs = list(self._jobs_index(workspace_id)["jobs"])
        if status:
            jobs = [item for item in jobs if item.get("status") == status]
        if job_type:
            jobs = [item for item in jobs if item.get("job_type") == job_type]
        if related_run_id:
            jobs = [item for item in jobs if item.get("related_run_id") == related_run_id]
        sorted_jobs = sorted(
            jobs,
            key=lambda item: (item.get("updated_at") or "", item.get("created_at") or ""),
            reverse=True,
        )
        return [
            _sanitize_public_value(item)
            for item in sorted_jobs[: max(1, min(limit, 1000))]
        ]

    def get_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        manifest = self._job_manifest(workspace_id, job_id)
        leaf_state = self.json_store.read_json_or_default(
            job_leaf_state_key(workspace_id, job_id),
            self._initial_leaf_state(workspace_id, job_id, manifest["status"]),
        )
        public_manifest = _sanitize_public_value(manifest)
        public_leaf_state = _sanitize_public_value(leaf_state)
        return {
            **self._public_summary(public_manifest),
            "manifest": public_manifest,
            "leaf_state": public_leaf_state,
        }

    def cancel_job(
        self,
        workspace_id: str,
        job_id: str,
        identity: RuntimeIdentity,
    ) -> dict[str, Any]:
        manifest = self._job_manifest(workspace_id, job_id)
        if manifest["status"] in TERMINAL_JOB_STATUSES:
            return self.get_job(workspace_id, job_id)

        now = utc_now_iso()
        self._append_event(
            workspace_id,
            job_id,
            "job_cancelled",
            {"status": "cancelled", "cancelled_by": identity.user_id},
            trace_id=manifest.get("trace_id"),
        )
        manifest = self._job_manifest(workspace_id, job_id)
        manifest.update(
            {
                "status": "cancelled",
                "cancelled_by": identity.user_id,
                "owner": None,
                "finished_at": now,
                "updated_at": now,
                "revision": _revision_next(manifest),
            }
        )
        self._write_manifest(workspace_id, job_id, manifest)
        self._write_leaf_state(workspace_id, job_id, manifest, status="cancelled")
        self._upsert_job_index(workspace_id, manifest)
        return self.get_job(workspace_id, job_id)

    def retry_job(
        self,
        workspace_id: str,
        job_id: str,
        identity: RuntimeIdentity,
        request: RetryJobRequest,
    ) -> dict[str, Any]:
        manifest = self._job_manifest(workspace_id, job_id)
        status = manifest["status"]
        if status == "unknown_outcome":
            return self.recover_unknown_outcome_job(
                workspace_id,
                job_id,
                requested_by=identity.user_id,
                trace_id=request.trace_id,
            )
        if status in NON_RETRYABLE_RETRY_STATUSES:
            raise AgentSystemError(
                "job_not_retryable",
                "Only terminal failed or cancelled jobs can be retried.",
                status_code=409,
                retryable=False,
                details={"job_id": job_id, "status": status},
            )
        if status == "succeeded":
            raise AgentSystemError(
                "job_not_retryable",
                "Succeeded jobs cannot be retried.",
                status_code=409,
                retryable=False,
                details={"job_id": job_id, "status": status},
            )

        retry_request = CreateJobRequest(
            job_type=manifest["job_type"],
            priority=manifest.get("priority", "normal"),
            target_scope=manifest.get("target_scope", {}),
            input=manifest.get("input", {}),
            idempotency_key=request.idempotency_key or f"retry:{job_id}:{new_id('idem')}",
            title=manifest.get("title"),
            related_run_id=manifest.get("related_run_id"),
            related_thread_id=manifest.get("related_thread_id"),
            trace_id=request.trace_id or manifest.get("trace_id"),
        )
        result = self.create_job(workspace_id, identity, retry_request)
        retry_manifest = self._job_manifest(workspace_id, result["job_id"])
        retry_manifest["retry_of_job_id"] = job_id
        retry_manifest["revision"] = _revision_next(retry_manifest)
        self._write_manifest(workspace_id, retry_manifest["job_id"], retry_manifest)
        self._upsert_job_index(workspace_id, retry_manifest)
        return self.get_job(workspace_id, retry_manifest["job_id"])

    def claim_next_job(
        self,
        workspace_id: str,
        *,
        job_types: list[str] | None = None,
    ) -> dict[str, Any] | None:
        claimed = self.claim_next_job_for_worker(workspace_id, job_types=job_types)
        if claimed is None:
            return None
        return self.get_job(workspace_id, claimed["job_id"])

    def claim_next_job_for_worker(
        self,
        workspace_id: str,
        *,
        job_types: list[str] | None = None,
    ) -> dict[str, Any] | None:
        allowed_job_types = set(job_types or [])
        candidates = [
            job
            for job in self._jobs_index(workspace_id)["jobs"]
            if job.get("status") in CLAIMABLE_JOB_STATUSES
            and (not allowed_job_types or job.get("job_type") in allowed_job_types)
        ]
        candidates = sorted(
            candidates,
            key=lambda item: (
                PRIORITY_ORDER.get(str(item.get("priority") or "normal"), 1),
                item.get("created_at") or "",
            ),
        )
        for candidate in candidates:
            job_id = candidate["job_id"]
            manifest = self._job_manifest(workspace_id, job_id)
            if manifest.get("status") not in CLAIMABLE_JOB_STATUSES:
                continue
            if self._job_owner_is_active(manifest):
                continue
            active_conflict = self._find_active_by_target_scope(
                self._jobs_index(workspace_id),
                str(manifest.get("target_scope_key") or ""),
                exclude_job_id=job_id,
            )
            if active_conflict:
                continue
            lease = self._new_lease()
            now = lease["acquired_at"]
            next_manifest = {
                **manifest,
                "status": "running",
                "owner": lease,
                "started_at": manifest.get("started_at") or now,
                "updated_at": now,
                "revision": _revision_next(manifest),
                "progress": {
                    **(manifest.get("progress") or {}),
                    "current_stage": "claimed",
                    "message": "Claimed by worker.",
                },
            }
            try:
                self._write_manifest(
                    workspace_id,
                    job_id,
                    next_manifest,
                    expected_revision=int(manifest.get("revision", 0)),
                )
            except RevisionConflictError:
                continue
            self._append_event(
                workspace_id,
                job_id,
                "job_started",
                {
                    "status": "running",
                    "stage": "claimed",
                    "message": "Claimed by worker.",
                    "percent": float((manifest.get("progress") or {}).get("percent") or 0),
                    "done_units": int((manifest.get("progress") or {}).get("done_units") or 0),
                    "total_units": int((manifest.get("progress") or {}).get("total_units") or 0),
                    "runtime_instance_id": self.runtime_instance_id,
                    "fencing_token": lease["fencing_token"],
                },
                trace_id=manifest.get("trace_id"),
            )
            claimed = self._job_manifest(workspace_id, job_id)
            self._write_leaf_state(workspace_id, job_id, claimed, status="running")
            self._upsert_job_index(workspace_id, claimed)
            return claimed
        return None

    def recover_stale_running_jobs(
        self,
        workspace_id: str,
        stale_after_seconds: int = 3600,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        stale_after = timedelta(seconds=max(1, stale_after_seconds))
        recovered: list[dict[str, Any]] = []
        for key in self.object_store.list_keys(f"{workspace_prefix(workspace_id)}/jobs"):
            if not key.endswith("/manifest.json"):
                continue
            manifest = self.json_store.read_json(key)
            if manifest.get("status") != "running":
                continue
            if self._job_owner_is_active(manifest, now=now):
                continue
            updated_at = _parse_iso_datetime(manifest.get("updated_at"))
            if updated_at and now - updated_at < stale_after:
                continue
            recovered.append(
                self._mark_running_job_unknown_outcome(
                    workspace_id=workspace_id,
                    manifest=manifest,
                    recovered_at=now.isoformat(),
                    stale_after_seconds=stale_after_seconds,
                )
            )
        return {
            "workspace_id": workspace_id,
            "recovered_count": len(recovered),
            "recovered_jobs": recovered,
        }

    def recover_unknown_outcome_job(
        self,
        workspace_id: str,
        job_id: str,
        *,
        requested_by: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._job_manifest(workspace_id, job_id)
        if manifest["status"] != "unknown_outcome":
            return self.get_job(workspace_id, job_id)

        event_index = self._rebuild_event_index(workspace_id, job_id)
        events = self._event_segment_store(workspace_id, job_id).read_all()
        terminal_projection = self._terminal_projection_from_events(events)
        now = utc_now_iso()
        probe = {
            "event_index_object_key": job_event_index_key(workspace_id, job_id),
            "event_count": event_index["event_count"],
            "last_event_id": event_index["last_event_id"],
            "terminal_event_id": terminal_projection.get("event_id"),
            "terminal_status": terminal_projection.get("status"),
            "requested_by": requested_by,
        }

        if terminal_projection:
            manifest.update(
                {
                    "status": terminal_projection["status"],
                    "owner": None,
                    "finished_at": terminal_projection["created_at"],
                    "updated_at": now,
                    "last_event_id": event_index["last_event_id"],
                    "last_event_seq": event_index["last_event_seq"],
                    "revision": _revision_next(manifest),
                    "progress": {
                        **(manifest.get("progress") or {}),
                        "current_stage": terminal_projection.get("stage"),
                        "percent": terminal_projection.get("percent", 100),
                        "message": "Recovered terminal outcome from job events.",
                        "recovery_probe": probe,
                    },
                }
            )
            self._write_manifest(workspace_id, job_id, manifest)
            self._append_event(
                workspace_id,
                job_id,
                "job_recovery_completed",
                {
                    "status": manifest["status"],
                    "stage": manifest["progress"].get("current_stage"),
                    "message": "Recovered terminal outcome from event log.",
                    "recovery_probe": probe,
                },
                trace_id=trace_id or manifest.get("trace_id"),
            )
            manifest = self._job_manifest(workspace_id, job_id)
            self._write_leaf_state(workspace_id, job_id, manifest, status=manifest["status"])
            self._upsert_job_index(workspace_id, manifest)
            return self.get_job(workspace_id, job_id)

        self._append_event(
            workspace_id,
            job_id,
            "job_recovering",
            {
                "status": "recovering",
                "stage": "recovery_probe",
                "message": "No terminal event was found; job is safe to retry.",
                "recovery_probe": probe,
                "requested_by": requested_by,
            },
            trace_id=trace_id or manifest.get("trace_id"),
        )
        manifest = self._job_manifest(workspace_id, job_id)
        manifest.update(
            {
                "status": "recovering",
                "owner": None,
                "updated_at": now,
                "revision": _revision_next(manifest),
                "progress": {
                    **(manifest.get("progress") or {}),
                    "current_stage": "recovery_probe",
                    "message": "No terminal event was found; job is safe to retry.",
                    "recovery_probe": probe,
                },
            }
        )
        self._write_manifest(workspace_id, job_id, manifest)
        self._write_leaf_state(workspace_id, job_id, manifest, status="recovering")
        self._upsert_job_index(workspace_id, manifest)
        return self.get_job(workspace_id, job_id)

    def rebuild_jobs_index(self, workspace_id: str) -> dict[str, Any]:
        rebuilt: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for key in sorted(self.object_store.list_keys(f"{workspace_prefix(workspace_id)}/jobs")):
            if not key.endswith("/manifest.json"):
                continue
            try:
                manifest = self.json_store.read_json(key)
                job_id = str(manifest["job_id"])
                event_index = self._rebuild_event_index(workspace_id, job_id)
                manifest["last_event_id"] = event_index["last_event_id"]
                manifest["last_event_seq"] = event_index["last_event_seq"]
                manifest["updated_at"] = manifest.get("updated_at") or utc_now_iso()
                self._write_manifest(workspace_id, job_id, manifest)
                self._write_leaf_state(workspace_id, job_id, manifest, status=manifest["status"])
                rebuilt.append(self._public_summary(manifest))
            except Exception as exc:  # noqa: BLE001 - rebuild must report corrupt jobs and continue.
                skipped.append({"manifest_object_key": key, "error_type": exc.__class__.__name__})
        rebuilt = sorted(
            rebuilt,
            key=lambda item: (item.get("updated_at") or "", item.get("created_at") or ""),
            reverse=True,
        )
        previous = self._jobs_index(workspace_id)
        index = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "jobs": rebuilt,
            "rebuilt_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "revision": _revision_next(previous),
        }
        self._write_jobs_index(workspace_id, index)
        return {
            "workspace_id": workspace_id,
            "rebuilt_count": len(rebuilt),
            "skipped_count": len(skipped),
            "skipped": _sanitize_public_value(skipped),
            "index_object_key": workspace_jobs_index_key(workspace_id),
        }

    def mark_job_running(
        self,
        workspace_id: str,
        job_id: str,
        *,
        stage: str,
        message: str,
        percent: float = 0,
        fencing_token: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._job_manifest(workspace_id, job_id)
        if manifest["status"] in TERMINAL_JOB_STATUSES:
            return self.get_job(workspace_id, job_id)
        if not self._job_mutation_is_allowed(manifest, fencing_token):
            return self.get_job(workspace_id, job_id)
        now = utc_now_iso()
        progress_percent = max(0, min(float(percent), 100))
        self._append_event(
            workspace_id,
            job_id,
            "job_started",
            {
                "status": "running",
                "stage": stage,
                "message": message,
                "percent": progress_percent,
                "done_units": 0,
                "total_units": 0,
            },
            trace_id=manifest.get("trace_id"),
        )
        manifest = self._job_manifest(workspace_id, job_id)
        if not self._job_mutation_is_allowed(manifest, fencing_token):
            return self.get_job(workspace_id, job_id)
        manifest.update(
            {
                "status": "running",
                "owner": self._renew_lease(manifest) if fencing_token else manifest.get("owner"),
                "started_at": manifest.get("started_at") or now,
                "updated_at": now,
                "revision": _revision_next(manifest),
                "progress": {
                    "current_stage": stage,
                    "percent": progress_percent,
                    "done_units": 0,
                    "total_units": 0,
                    "message": message,
                },
            }
        )
        self._write_manifest(workspace_id, job_id, manifest)
        self._write_leaf_state(workspace_id, job_id, manifest, status="running")
        self._upsert_job_index(workspace_id, manifest)
        return self.get_job(workspace_id, job_id)

    def mark_job_succeeded(
        self,
        workspace_id: str,
        job_id: str,
        *,
        stage: str,
        message: str,
        artifacts: list[dict[str, Any]] | None = None,
        fencing_token: str | None = None,
    ) -> dict[str, Any]:
        return self._finish_job(
            workspace_id,
            job_id,
            status="succeeded",
            event_type="job_succeeded",
            stage=stage,
            message=message,
            artifacts=artifacts or [],
            fencing_token=fencing_token,
        )

    def mark_job_failed(
        self,
        workspace_id: str,
        job_id: str,
        *,
        stage: str,
        message: str,
        error_type: str,
        retryable: bool = False,
        artifacts: list[dict[str, Any]] | None = None,
        fencing_token: str | None = None,
    ) -> dict[str, Any]:
        return self._finish_job(
            workspace_id,
            job_id,
            status="failed",
            event_type="job_failed",
            stage=stage,
            message=message,
            payload={"error_type": error_type, "retryable": retryable},
            artifacts=artifacts or [],
            fencing_token=fencing_token,
        )

    def mark_job_partial_success(
        self,
        workspace_id: str,
        job_id: str,
        *,
        stage: str,
        message: str,
        error_type: str,
        retryable: bool = True,
        artifacts: list[dict[str, Any]] | None = None,
        fencing_token: str | None = None,
    ) -> dict[str, Any]:
        return self._finish_job(
            workspace_id,
            job_id,
            status="partial_success",
            event_type="job_partial_success",
            stage=stage,
            message=message,
            payload={"error_type": error_type, "retryable": retryable},
            artifacts=artifacts or [],
            fencing_token=fencing_token,
        )

    def list_job_events(
        self,
        workspace_id: str,
        job_id: str,
        after_event_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        manifest = self._job_manifest(workspace_id, job_id)
        after_seq = self._event_seq_from_id(job_id, after_event_id)
        events = [
            event
            for event in self._event_segment_store(workspace_id, job_id).read_all()
            if int(event["event_seq"]) > after_seq
        ]
        events = sorted(events, key=lambda item: int(item["event_seq"]))
        return events[: max(1, min(limit, 1000))], manifest

    def stream_closed_event(self, manifest: dict[str, Any]) -> dict[str, Any]:
        event_seq = int(manifest.get("last_event_seq") or 0) + 1
        return {
            "schema_version": 1,
            "event_id": _event_id(manifest["job_id"], event_seq),
            "event_seq": event_seq,
            "workspace_id": manifest["workspace_id"],
            "job_id": manifest["job_id"],
            "status": manifest["status"],
            "type": "stream_closed",
            "created_at": utc_now_iso(),
            "trace_id": manifest.get("trace_id"),
            "payload": {"job_id": manifest["job_id"], "status": manifest["status"]},
        }

    def _create_new_job(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: CreateJobRequest,
        idempotency_key: str,
        request_hash: str,
        target_scope_key: str,
        expected_index_revision: int,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        job_id = new_id("job")
        trace_id = request.trace_id or new_id("trace")
        manifest = {
            "schema_version": 1,
            "job_id": job_id,
            "workspace_id": workspace_id,
            "job_type": request.job_type,
            "status": "created",
            "priority": request.priority,
            "title": _sanitize_public_value(
                request.title or self._default_title(request.job_type, request.target_scope)
            ),
            "target_scope": _sanitize_public_value(request.target_scope),
            "target_scope_key": target_scope_key,
            "input": _sanitize_public_value(request.input),
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "created_by": identity.user_id,
            "role": identity.role,
            "retry_of_job_id": None,
            "related_run_id": request.related_run_id,
            "related_thread_id": request.related_thread_id,
            "trace_id": trace_id,
            "owner": None,
            "progress": {
                "current_stage": None,
                "percent": 0,
                "done_units": 0,
                "total_units": 0,
                "message": "Queued.",
            },
            "retry_policy": {
                "max_attempts": 3,
                "attempt": 0,
                "backoff": "exponential_jitter",
                "next_retry_at": None,
            },
            "object_keys": {
                "manifest": job_manifest_key(workspace_id, job_id),
                "events_prefix": job_events_prefix(workspace_id, job_id),
                "event_index": job_event_index_key(workspace_id, job_id),
                "leaf_state": job_leaf_state_key(workspace_id, job_id),
                "errors": job_errors_key(workspace_id, job_id),
            },
            "last_event_id": None,
            "last_event_seq": 0,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "revision": 1,
        }
        self._write_manifest(workspace_id, job_id, manifest)
        self._write_leaf_state(workspace_id, job_id, manifest, status="created")
        self._append_event(
            workspace_id,
            job_id,
            "job_created",
            {"status": "created", "created_by": identity.user_id},
            trace_id=trace_id,
        )
        self._append_event(
            workspace_id,
            job_id,
            "job_queued",
            {"status": "queued", "reason": "offline_deterministic_create"},
            trace_id=trace_id,
        )
        manifest = self._job_manifest(workspace_id, job_id)
        manifest.update(
            {
                "status": "queued",
                "updated_at": utc_now_iso(),
                "revision": _revision_next(manifest),
            }
        )
        self._write_manifest(workspace_id, job_id, manifest)
        self._write_leaf_state(workspace_id, job_id, manifest, status="queued")
        self._upsert_job_index(workspace_id, manifest, expected_revision=expected_index_revision)
        return self.get_job(workspace_id, job_id)

    def _append_event(
        self,
        workspace_id: str,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        current = self.json_store.read_json_or_default(
            job_event_index_key(workspace_id, job_id),
            {
                "schema_version": 1,
                "job_id": job_id,
                "workspace_id": workspace_id,
                "segments": [],
                "event_count": 0,
                "last_event_seq": 0,
                "last_event_id": None,
                "revision": 0,
            },
        )
        event_seq = int(current.get("last_event_seq") or 0) + 1
        event = {
            "schema_version": 1,
            "event_seq": event_seq,
            "event_id": _event_id(job_id, event_seq),
            "workspace_id": workspace_id,
            "job_id": job_id,
            "type": event_type,
            "created_at": utc_now_iso(),
            "trace_id": trace_id,
            "payload": _sanitize_public_value(payload),
        }
        self._event_segment_store(workspace_id, job_id).append(event)
        index = self._rebuild_event_index(workspace_id, job_id)
        manifest = self._job_manifest_or_none(workspace_id, job_id)
        if manifest:
            manifest["last_event_id"] = index["last_event_id"]
            manifest["last_event_seq"] = index["last_event_seq"]
            manifest["updated_at"] = utc_now_iso()
            manifest["revision"] = _revision_next(manifest)
            self._write_manifest(workspace_id, job_id, manifest)
        return event

    def _rebuild_event_index(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        store = self._event_segment_store(workspace_id, job_id)
        segments = []
        last_event_id: str | None = None
        last_event_seq = 0
        event_count = 0
        seen_event_ids: set[str] = set()
        duplicate_event_count = 0

        for segment in store.list_segments():
            records = store._read_segment_records(segment.object_key)
            first_seq: int | None = None
            segment_last_seq: int | None = None
            for record in records:
                event_id = str(record["event_id"])
                event_seq = int(record["event_seq"])
                if event_id in seen_event_ids:
                    duplicate_event_count += 1
                    continue
                seen_event_ids.add(event_id)
                if event_seq != last_event_seq + 1:
                    raise AgentSystemError(
                        "job_event_index_corrupt",
                        "Job event sequence is not contiguous.",
                        status_code=500,
                        retryable=True,
                        details={
                            "job_id": job_id,
                            "expected_event_seq": last_event_seq + 1,
                            "actual_event_seq": event_seq,
                        },
                    )
                first_seq = event_seq if first_seq is None else first_seq
                segment_last_seq = event_seq
                last_event_seq = event_seq
                last_event_id = event_id
                event_count += 1
            segments.append(
                {
                    "object_key": segment.object_key,
                    "from_event_seq": first_seq,
                    "to_event_seq": segment_last_seq,
                    "event_count": len(records),
                    "sealed": False,
                }
            )

        key = job_event_index_key(workspace_id, job_id)
        previous = self.json_store.read_json_or_default(key, {"revision": 0})
        index = {
            "schema_version": 1,
            "job_id": job_id,
            "workspace_id": workspace_id,
            "segments": segments,
            "event_count": event_count,
            "duplicate_event_count": duplicate_event_count,
            "last_event_seq": last_event_seq,
            "last_event_id": last_event_id,
            "revision": _revision_next(previous),
            "updated_at": utc_now_iso(),
        }
        self.json_store.write_json(key, index)
        return index

    @staticmethod
    def _terminal_projection_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
        terminal_by_type = {
            "job_cancelled": "cancelled",
            "job_failed": "failed",
            "job_partial_success": "partial_success",
            "job_succeeded": "succeeded",
        }
        for event in sorted(events, key=lambda item: int(item.get("event_seq") or 0), reverse=True):
            status = terminal_by_type.get(str(event.get("type") or ""))
            if not status:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            return {
                "event_id": event["event_id"],
                "created_at": event["created_at"],
                "status": status,
                "stage": payload.get("stage"),
                "percent": payload.get("percent"),
            }
        return {}

    def _write_leaf_state(
        self,
        workspace_id: str,
        job_id: str,
        manifest: dict[str, Any],
        status: str,
    ) -> None:
        previous = self.json_store.read_json_or_default(
            job_leaf_state_key(workspace_id, job_id),
            {},
        )
        progress = manifest.get("progress") or {}
        leaf_state = {
            "schema_version": 1,
            "job_id": job_id,
            "workspace_id": workspace_id,
            "status": status,
            "current_stage": progress.get("current_stage"),
            "last_event_seq": int(manifest.get("last_event_seq") or 0),
            "last_event_id": manifest.get("last_event_id"),
            "progress": {
                "percent": progress.get("percent", 0),
                "done_units": progress.get("done_units", 0),
                "total_units": progress.get("total_units", 0),
                "message": _sanitize_public_value(progress.get("message")),
            },
            "stage_state": _sanitize_public_value(previous.get("stage_state", {})),
            "unknown_outcome": _sanitize_public_value(previous.get("unknown_outcome")),
            "recovery_probe": _sanitize_public_value(
                progress.get("recovery_probe") or previous.get("recovery_probe")
            ),
            "artifacts": _sanitize_public_value(previous.get("artifacts", [])),
            "updated_at": utc_now_iso(),
            "revision": _revision_next(previous),
        }
        self.json_store.write_json(job_leaf_state_key(workspace_id, job_id), leaf_state)

    def _finish_job(
        self,
        workspace_id: str,
        job_id: str,
        *,
        status: str,
        event_type: str,
        stage: str,
        message: str,
        payload: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        fencing_token: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._job_manifest(workspace_id, job_id)
        if manifest["status"] in TERMINAL_JOB_STATUSES:
            return self.get_job(workspace_id, job_id)
        if not self._job_mutation_is_allowed(manifest, fencing_token):
            return self.get_job(workspace_id, job_id)
        now = utc_now_iso()
        expected_revision = int(manifest.get("revision", 0))
        finished_percent = (
            100
            if status in {"succeeded", "partial_success"}
            else manifest["progress"]["percent"]
        )
        manifest.update(
            {
                "status": status,
                "owner": None,
                "finished_at": now,
                "updated_at": now,
                "revision": _revision_next(manifest),
                "progress": {
                    "current_stage": stage,
                    "percent": finished_percent,
                    "done_units": manifest["progress"].get("done_units", 0),
                    "total_units": manifest["progress"].get("total_units", 0),
                    "message": message,
                },
            }
        )
        if payload and "error_type" in payload:
            manifest["error_type"] = str(payload["error_type"])
            manifest["retryable"] = bool(payload.get("retryable", False))
        try:
            self._write_manifest(
                workspace_id,
                job_id,
                manifest,
                expected_revision=expected_revision,
            )
        except RevisionConflictError:
            return self.get_job(workspace_id, job_id)
        self._append_event(
            workspace_id,
            job_id,
            event_type,
            {
                "status": status,
                "stage": stage,
                "message": message,
                "percent": finished_percent,
                "done_units": manifest["progress"].get("done_units", 0),
                "total_units": manifest["progress"].get("total_units", 0),
                **(payload or {}),
            },
            trace_id=manifest.get("trace_id"),
        )
        manifest = self._job_manifest(workspace_id, job_id)
        if artifacts:
            previous_leaf = self.json_store.read_json_or_default(
                job_leaf_state_key(workspace_id, job_id),
                {},
            )
            previous_leaf["artifacts"] = _sanitize_public_value(artifacts)
            self.json_store.write_json(job_leaf_state_key(workspace_id, job_id), previous_leaf)
        self._write_leaf_state(workspace_id, job_id, manifest, status=status)
        self._upsert_job_index(workspace_id, manifest)
        return self.get_job(workspace_id, job_id)

    def _mark_running_job_unknown_outcome(
        self,
        workspace_id: str,
        manifest: dict[str, Any],
        recovered_at: str,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        job_id = manifest["job_id"]
        if self._job_manifest(workspace_id, job_id)["status"] in TERMINAL_JOB_STATUSES:
            return self.get_job(workspace_id, job_id)
        payload = {
            "status": "unknown_outcome",
            "error_type": "stale_running_recovered",
            "previous_updated_at": manifest.get("updated_at"),
            "recovered_at": recovered_at,
            "stale_after_seconds": stale_after_seconds,
        }
        self._append_event(
            workspace_id,
            job_id,
            "job_recovery_started",
            payload,
            trace_id=manifest.get("trace_id"),
        )
        self._append_event(
            workspace_id,
            job_id,
            "job_unknown_outcome",
            payload,
            trace_id=manifest.get("trace_id"),
        )
        latest = self._job_manifest(workspace_id, job_id)
        latest.update(
            {
                "status": "unknown_outcome",
                "owner": None,
                "updated_at": recovered_at,
                "revision": _revision_next(latest),
                "progress": {
                    **(latest.get("progress") or {}),
                    "current_stage": "recovery",
                    "message": "Recovered stale running job with unknown outcome.",
                    "recovery_probe": payload,
                },
            }
        )
        self._write_manifest(workspace_id, job_id, latest)
        self._write_leaf_state(workspace_id, job_id, latest, status="unknown_outcome")
        self._upsert_job_index(workspace_id, latest)
        return self.get_job(workspace_id, job_id)

    def _new_lease(self) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        return {
            "runtime_instance_id": self.runtime_instance_id,
            "fencing_token": new_id("fence"),
            "acquired_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self.job_lease_ttl_seconds)).isoformat(),
        }

    def _renew_lease(self, manifest: dict[str, Any]) -> dict[str, Any] | None:
        owner = manifest.get("owner")
        if not isinstance(owner, dict) or not owner:
            return None
        now = datetime.now(timezone.utc)
        return {
            **owner,
            "expires_at": (now + timedelta(seconds=self.job_lease_ttl_seconds)).isoformat(),
        }

    def _job_owner_is_active(
        self,
        manifest: dict[str, Any],
        now: datetime | None = None,
    ) -> bool:
        owner = manifest.get("owner") or {}
        owner_expires_at = _parse_iso_datetime(owner.get("expires_at"))
        current_time = now or datetime.now(timezone.utc)
        return bool(owner and owner_expires_at and owner_expires_at > current_time)

    def _job_mutation_is_allowed(
        self,
        manifest: dict[str, Any],
        fencing_token: str | None,
    ) -> bool:
        if not (manifest.get("owner") or {}):
            return True
        if not fencing_token:
            return False
        return self._job_lease_is_active(manifest, fencing_token)

    def _job_lease_is_active(self, manifest: dict[str, Any], fencing_token: str) -> bool:
        if manifest.get("status") in TERMINAL_JOB_STATUSES:
            return False
        owner = manifest.get("owner") or {}
        owner_expires_at = _parse_iso_datetime(owner.get("expires_at"))
        current_time = datetime.now(timezone.utc)
        return (
            owner.get("runtime_instance_id") == self.runtime_instance_id
            and owner.get("fencing_token") == fencing_token
            and owner_expires_at is not None
            and owner_expires_at > current_time
        )

    def _upsert_job_index(
        self,
        workspace_id: str,
        manifest: dict[str, Any],
        expected_revision: int | None = None,
    ) -> None:
        index = self._jobs_index(workspace_id)
        index["jobs"] = [
            item for item in index["jobs"] if item["job_id"] != manifest["job_id"]
        ]
        index["jobs"].append(self._public_summary(manifest))
        index["jobs"] = sorted(
            index["jobs"],
            key=lambda item: (item.get("updated_at") or "", item.get("created_at") or ""),
            reverse=True,
        )
        index["updated_at"] = utc_now_iso()
        index["revision"] = _revision_next(index)
        self._write_jobs_index(workspace_id, index, expected_revision=expected_revision)

    def _jobs_index(self, workspace_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            workspace_jobs_index_key(workspace_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "jobs": [],
                "updated_at": None,
                "revision": 0,
            },
        )

    def _write_jobs_index(
        self,
        workspace_id: str,
        index: dict[str, Any],
        expected_revision: int | None = None,
    ) -> None:
        key = workspace_jobs_index_key(workspace_id)
        if expected_revision is not None and self.object_store.exists(key):
            self.json_store.write_json(key, index, expected_revision=expected_revision)
            return
        self.json_store.write_json(key, index)

    def _job_manifest(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        key = job_manifest_key(workspace_id, job_id)
        if not self.object_store.exists(key):
            raise AgentSystemError("job_not_found", "Job was not found.", status_code=404)
        return self.json_store.read_json(key)

    def _job_manifest_or_none(self, workspace_id: str, job_id: str) -> dict[str, Any] | None:
        key = job_manifest_key(workspace_id, job_id)
        if not self.object_store.exists(key):
            return None
        return self.json_store.read_json(key)

    def _write_manifest(
        self,
        workspace_id: str,
        job_id: str,
        manifest: dict[str, Any],
        expected_revision: int | None = None,
    ) -> None:
        self.json_store.write_json(
            job_manifest_key(workspace_id, job_id),
            manifest,
            expected_revision=expected_revision,
        )

    def _event_segment_store(self, workspace_id: str, job_id: str) -> JsonlSegmentStore:
        return JsonlSegmentStore(self.object_store, job_events_prefix(workspace_id, job_id))

    def _event_seq_from_id(self, job_id: str, event_id: str | None) -> int:
        if not event_id:
            return 0
        match = EVENT_ID_RE.fullmatch(event_id)
        if not match or match.group("job_id") != job_id:
            raise AgentSystemError(
                "invalid_event_cursor",
                "Event cursor does not belong to this job.",
                status_code=400,
            )
        return int(match.group("seq"))

    def _computed_idempotency_key(self, workspace_id: str, request: CreateJobRequest) -> str:
        return "sha256:" + _stable_hash(
            {
                "workspace_id": workspace_id,
                "job_type": request.job_type,
                "target_scope": request.target_scope,
                "input": request.input,
            }
        )

    def _request_hash(self, workspace_id: str, request: CreateJobRequest) -> str:
        return "sha256:" + _stable_hash(
            {
                "workspace_id": workspace_id,
                "job_type": request.job_type,
                "priority": request.priority,
                "target_scope": request.target_scope,
                "input": request.input,
                "related_run_id": request.related_run_id,
                "related_thread_id": request.related_thread_id,
            }
        )

    def _target_scope_key(
        self,
        workspace_id: str,
        job_type: str,
        target_scope: dict[str, str],
    ) -> str:
        return "sha256:" + _stable_hash(
            {
                "workspace_id": workspace_id,
                "job_type": job_type,
                "target_scope": target_scope,
            }
        )

    @staticmethod
    def _find_by_idempotency(
        index: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        for job in index["jobs"]:
            if job.get("idempotency_key") == idempotency_key:
                return job
        return None

    @staticmethod
    def _find_active_by_target_scope(
        index: dict[str, Any],
        target_scope_key: str,
        exclude_job_id: str | None = None,
    ) -> dict[str, Any] | None:
        for job in index["jobs"]:
            if (
                job.get("target_scope_key") == target_scope_key
                and job.get("job_id") != exclude_job_id
                and job.get("status") not in TERMINAL_JOB_STATUSES
            ):
                return job
        return None

    @staticmethod
    def _default_title(job_type: str, target_scope: dict[str, str]) -> str:
        scope_type = target_scope.get("scope_type")
        if scope_type:
            return f"{job_type} ({scope_type})"
        return job_type

    @staticmethod
    def _initial_leaf_state(workspace_id: str, job_id: str, status: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "job_id": job_id,
            "workspace_id": workspace_id,
            "status": status,
            "current_stage": None,
            "last_event_seq": 0,
            "last_event_id": None,
            "progress": {"percent": 0, "done_units": 0, "total_units": 0, "message": None},
            "stage_state": {},
            "unknown_outcome": None,
            "artifacts": [],
            "updated_at": utc_now_iso(),
            "revision": 0,
        }

    @staticmethod
    def _public_summary(manifest: dict[str, Any]) -> dict[str, Any]:
        progress = manifest.get("progress") or {}
        object_keys = manifest["object_keys"]
        summary = {
            "job_id": manifest["job_id"],
            "workspace_id": manifest["workspace_id"],
            "job_type": manifest["job_type"],
            "status": manifest["status"],
            "priority": manifest.get("priority", "normal"),
            "title": manifest.get("title") or manifest["job_type"],
            "target_scope": _sanitize_public_value(manifest.get("target_scope", {})),
            "target_scope_key": manifest["target_scope_key"],
            "idempotency_key": manifest["idempotency_key"],
            "request_hash": manifest.get("request_hash"),
            "progress_percent": float(progress.get("percent") or 0),
            "current_stage": progress.get("current_stage"),
            "manifest_object_key": object_keys["manifest"],
            "event_index_object_key": object_keys["event_index"],
            "leaf_state_object_key": object_keys["leaf_state"],
            "last_event_id": manifest.get("last_event_id"),
            "last_event_seq": int(manifest.get("last_event_seq") or 0),
            "created_by": manifest["created_by"],
            "related_run_id": manifest.get("related_run_id"),
            "related_thread_id": manifest.get("related_thread_id"),
            "created_at": manifest["created_at"],
            "updated_at": manifest["updated_at"],
        }
        if manifest.get("error_type"):
            summary["error_type"] = manifest["error_type"]
            summary["retryable"] = bool(manifest.get("retryable", False))
        return summary
