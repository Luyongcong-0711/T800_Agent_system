from __future__ import annotations

import base64
import hashlib
import json
import re
import zipfile
from io import BytesIO
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import (
    get_job_service,
    get_observability_service,
    require_workspace_role,
)
from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.jobs.service import JobService
from app.observability.service import ObservabilityService
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest
from app.schemas.observability import (
    CreateDiagnosticBundleRequest,
    CreateLogArchiveRequest,
    DiagnosticBundleResponse,
    LogArchiveJobResponse,
    LogArtifactResponse,
    LogQueryResponse,
    LogSummaryResponse,
)
from app.storage.object_store import ObjectNotFoundError

router = APIRouter(prefix="/workspaces/{workspace_id}/logs", tags=["logs"])

MAX_INLINE_ARTIFACT_BYTES = 500_000
MAX_BINARY_ARTIFACT_BYTES = 20 * 1024 * 1024


@router.get("/system/summary", response_model=LogSummaryResponse)
async def get_system_log_summary(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    trace_id: str | None = None,
    run_id: str | None = None,
    query: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> LogSummaryResponse:
    return LogSummaryResponse(
        workspace_id=workspace_id,
        items=service.read_summary(
            workspace_id=workspace_id,
            trace_id=trace_id,
            run_id=run_id,
            query=query,
            limit=limit,
        ),
    )


@router.get("/system/full", response_model=LogQueryResponse)
async def get_system_full_logs(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    level: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    query: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> LogQueryResponse:
    return _log_response(
        workspace_id,
        service.query_logs(
            workspace_id=workspace_id,
            stream="full",
            level=level,
            trace_id=trace_id,
            run_id=run_id,
            query=query,
            limit=limit,
        ),
    )


@router.get("/system/errors", response_model=LogQueryResponse)
async def get_system_error_logs(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    trace_id: str | None = None,
    run_id: str | None = None,
    query: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> LogQueryResponse:
    return _log_response(
        workspace_id,
        service.query_logs(
            workspace_id=workspace_id,
            stream="errors",
            trace_id=trace_id,
            run_id=run_id,
            query=query,
            limit=limit,
        ),
    )


@router.get("/components/{component}", response_model=LogQueryResponse)
async def get_component_logs(
    workspace_id: str,
    component: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    trace_id: str | None = None,
    run_id: str | None = None,
    query: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> LogQueryResponse:
    return _log_response(
        workspace_id,
        service.query_logs(
            workspace_id=workspace_id,
            stream="component",
            component=component,
            trace_id=trace_id,
            run_id=run_id,
            query=query,
            limit=limit,
        ),
    )


@router.get("/tail", response_model=LogQueryResponse)
async def tail_system_logs(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> LogQueryResponse:
    return _log_response(
        workspace_id,
        service.query_logs(workspace_id=workspace_id, stream="full", limit=limit),
    )


@router.get("/artifacts", response_model=LogArtifactResponse)
async def get_log_artifact(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    object_key: Annotated[str, Query(min_length=1, max_length=1200)],
    inline_max_bytes: Annotated[
        int,
        Query(ge=1_000, le=2_000_000),
    ] = MAX_INLINE_ARTIFACT_BYTES,
) -> LogArtifactResponse:
    artifact_key = _normalize_log_artifact_key(object_key)
    _assert_allowed_log_artifact_key(artifact_key)
    try:
        raw = service.object_store.read_bytes(artifact_key)
    except ObjectNotFoundError as exc:
        raise AgentSystemError(
            "log_artifact_not_found",
            "Log artifact was not found.",
            status_code=404,
            retryable=False,
        ) from exc

    if _is_diagnostic_artifact_key(artifact_key):
        _assert_diagnostic_artifact_workspace(
            artifact_key,
            raw,
            workspace_id=workspace_id,
        )

    content_type = _artifact_content_type(artifact_key)
    sha256 = "sha256:" + hashlib.sha256(raw).hexdigest()
    base_response: dict[str, Any] = {
        "workspace_id": workspace_id,
        "object_key": artifact_key,
        "file_name": artifact_key.rsplit("/", 1)[-1],
        "content_type": content_type,
        "artifact_type": _artifact_type(artifact_key),
        "size_bytes": len(raw),
        "sha256": sha256,
        "redacted": True,
    }

    if artifact_key.endswith(".zip"):
        if len(raw) > MAX_BINARY_ARTIFACT_BYTES:
            raise AgentSystemError(
                "log_artifact_too_large",
                "Log artifact is too large to return through the API.",
                status_code=413,
                retryable=False,
            )
        return LogArtifactResponse(
            **base_response,
            base64=base64.b64encode(raw).decode("ascii"),
        )

    preview = raw[:inline_max_bytes]
    text = preview.decode("utf-8", errors="replace")
    truncated = len(raw) > inline_max_bytes
    parsed_json: Any | None = None
    if artifact_key.endswith(".json") and not truncated:
        try:
            parsed_json = json.loads(text)
        except json.JSONDecodeError:
            parsed_json = None

    return LogArtifactResponse(
        **base_response,
        text=text,
        parsed_json=parsed_json,
        truncated=truncated,
    )


@router.post("/diagnostic-bundles", response_model=DiagnosticBundleResponse)
async def create_diagnostic_bundle(
    workspace_id: str,
    request: CreateDiagnosticBundleRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    job_service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, Any]:
    bundle_id = (
        f"diag_{_safe_id_fragment(request.request_id)}"
        if request.request_id
        else new_id("diag")
    )
    selected_component = request.component or (
        request.components[0] if request.components else None
    )
    job = job_service.create_job(
        workspace_id,
        identity,
        CreateJobRequest(
            job_type="diagnostic_bundle_job",
            title="Generate diagnostic bundle",
            target_scope={
                "scope_type": "diagnostic_bundle",
                "bundle_id": bundle_id,
                "trace_id": request.trace_id or "",
                "run_id": request.run_id or "",
                "component": selected_component or "",
            },
            input={
                "trace_id": request.trace_id,
                "run_id": request.run_id,
                "component": selected_component,
                "components": request.components,
                "limit": request.limit,
                "include_summary": request.include_summary,
                "include_errors": request.include_errors,
                "include_component_logs": request.include_component_logs,
            },
            trace_id=request.trace_id,
        ),
    )
    return {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "workspace_id": workspace_id,
        "created_by": identity.user_id,
        "created_at": job["created_at"],
        "runtime_instance_id": service.runtime_instance_id,
        "filters": {
            "trace_id": request.trace_id,
            "run_id": request.run_id,
            "component": selected_component,
            "components": request.components,
            "limit": request.limit,
        },
        "manifest_object_key": None,
        "object_key": None,
        "related_job_id": job["job_id"],
        "job_id": job["job_id"],
        "job_status": job["status"],
        "item_counts": {},
        "redacted": True,
    }


@router.post("/archive-jobs", response_model=LogArchiveJobResponse)
async def create_log_archive_job(
    workspace_id: str,
    request: CreateLogArchiveRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ObservabilityService, Depends(get_observability_service)],
    job_service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, Any]:
    archive_date = request.date or utc_now_iso().split("T", 1)[0]
    runtime_id = request.runtime_instance_id or service.runtime_instance_id
    request_id = request.request_id or new_id("logarch_req")
    job = job_service.create_job(
        workspace_id,
        identity,
        CreateJobRequest(
            job_type="log_archive_job",
            title="Archive system logs",
            target_scope={
                "scope_type": "system_logs",
                "runtime_instance_id": runtime_id,
                "date": archive_date,
            },
            input={
                "runtime_instance_id": runtime_id,
                "date": archive_date,
                "request_id": request_id,
            },
            idempotency_key=_log_archive_idempotency_key(
                workspace_id,
                runtime_id,
                archive_date,
                request_id,
            ),
        ),
    )
    return {
        "schema_version": 1,
        "workspace_id": workspace_id,
        "date": archive_date,
        "runtime_instance_id": runtime_id,
        "manifest_object_key": None,
        "related_job_id": job["job_id"],
        "job_id": job["job_id"],
        "job_status": job["status"],
        "redacted": True,
    }


def _log_response(workspace_id: str, items: list[dict[str, Any]]) -> LogQueryResponse:
    return LogQueryResponse(
        workspace_id=workspace_id,
        items=items,
        next_cursor=None,
        truncated=False,
        redacted=True,
    )


def _safe_id_fragment(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return normalized.strip("._-") or "request"


def _normalize_log_artifact_key(object_key: str) -> str:
    normalized = object_key.strip().replace("\\", "/").lstrip("/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise AgentSystemError(
            "invalid_log_artifact_key",
            "Invalid log artifact key.",
            status_code=400,
            retryable=False,
        )
    return normalized


def _assert_allowed_log_artifact_key(object_key: str) -> None:
    allowed = False
    if _is_diagnostic_artifact_key(object_key):
        allowed = object_key.endswith((
            "/manifest.json",
            "/bundle.json",
            "/bundle.zip",
        ))
    elif _is_log_archive_artifact_key(object_key):
        allowed = object_key.endswith("/manifest.json") or (
            "/log_archives/files/" in object_key
            and object_key.endswith((".jsonl", ".log", ".txt"))
        )
    if not object_key.startswith("system/logs/") or not allowed:
        raise AgentSystemError(
            "unsupported_log_artifact_key",
            "Only diagnostic bundle and log archive artifacts can be read here.",
            status_code=400,
            retryable=False,
        )


def _is_diagnostic_artifact_key(object_key: str) -> bool:
    return "/diagnostic_bundles/" in object_key


def _is_log_archive_artifact_key(object_key: str) -> bool:
    return "/log_archives/" in object_key


def _assert_diagnostic_artifact_workspace(
    object_key: str,
    raw: bytes,
    *,
    workspace_id: str,
) -> None:
    workspace_in_artifact: str | None = None
    try:
        if object_key.endswith(".zip"):
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            workspace_in_artifact = _workspace_from_diagnostic_json(manifest)
        else:
            workspace_in_artifact = _workspace_from_diagnostic_json(
                json.loads(raw.decode("utf-8"))
            )
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise AgentSystemError(
            "invalid_log_artifact_payload",
            "Log artifact payload is invalid.",
            status_code=422,
            retryable=False,
        ) from exc
    if workspace_in_artifact and workspace_in_artifact != workspace_id:
        raise AgentSystemError(
            "log_artifact_workspace_mismatch",
            "Log artifact does not belong to this workspace.",
            status_code=403,
            retryable=False,
        )


def _workspace_from_diagnostic_json(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    workspace_id = value.get("workspace_id")
    if isinstance(workspace_id, str):
        return workspace_id
    manifest = value.get("manifest")
    if isinstance(manifest, dict) and isinstance(manifest.get("workspace_id"), str):
        return str(manifest["workspace_id"])
    return None


def _artifact_content_type(object_key: str) -> str:
    if object_key.endswith(".json"):
        return "application/json"
    if object_key.endswith(".jsonl"):
        return "application/x-ndjson"
    if object_key.endswith(".zip"):
        return "application/zip"
    return "text/plain; charset=utf-8"


def _artifact_type(object_key: str) -> str:
    if object_key.endswith(".zip"):
        return "binary"
    if object_key.endswith(".json"):
        return "json"
    return "text"


def _log_archive_idempotency_key(
    workspace_id: str,
    runtime_instance_id: str,
    archive_date: str,
    request_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{workspace_id}\0{runtime_instance_id}\0{archive_date}\0{request_id}".encode()
    ).hexdigest()
    return f"log-archive:{digest}"
