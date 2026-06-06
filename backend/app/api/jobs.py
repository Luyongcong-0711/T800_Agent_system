from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_job_service, get_job_worker, require_workspace_role
from app.core.errors import AgentSystemError
from app.jobs.service import TERMINAL_JOB_STATUSES, JobService
from app.jobs.worker import JobWorker, JobWorkerDaemon
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import (
    CancelJobResponse,
    CreateJobRequest,
    JobDetailResponse,
    JobStatus,
    JobSummary,
    ListJobEventsResponse,
    ListJobsResponse,
    RetryJobRequest,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/jobs", tags=["jobs"])


@router.post("", response_model=JobDetailResponse)
async def create_job(
    workspace_id: str,
    request: CreateJobRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, Any]:
    return service.create_job(workspace_id, identity, request)


@router.post("/claim-next")
async def claim_next_job(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("owner"))],
    service: Annotated[JobService, Depends(get_job_service)],
    job_type: Annotated[list[str], Query(default_factory=list)],
) -> dict[str, Any]:
    claimed = service.claim_next_job(workspace_id, job_types=job_type)
    return {"workspace_id": workspace_id, "claimed": claimed is not None, "job": claimed}


@router.post("/recover-stale")
async def recover_stale_jobs(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[JobService, Depends(get_job_service)],
    stale_after_seconds: Annotated[int, Query(ge=1, le=86400)] = 3600,
) -> dict[str, Any]:
    return service.recover_stale_running_jobs(
        workspace_id,
        stale_after_seconds=stale_after_seconds,
    )


@router.post("/rebuild-index")
async def rebuild_jobs_index(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("owner"))],
    service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, Any]:
    return service.rebuild_jobs_index(workspace_id)


@router.post("/process-next")
async def process_next_job(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("owner"))],
    worker: Annotated[JobWorker, Depends(get_job_worker)],
    job_type: Annotated[list[str], Query(default_factory=list)],
) -> dict[str, Any]:
    return worker.process_next(
        workspace_id,
        job_types=job_type or None,
    )


@router.post("/worker/start")
async def start_job_worker(
    workspace_id: str,
    request: Request,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("owner"))],
    worker: Annotated[JobWorker, Depends(get_job_worker)],
    job_type: Annotated[list[str], Query(default_factory=list)],
    poll_interval_ms: Annotated[int, Query(ge=50, le=60000)] = 1000,
    max_jobs_per_tick: Annotated[int, Query(ge=1, le=100)] = 5,
) -> dict[str, Any]:
    registry = _worker_daemon_registry(request)
    daemon = registry.get(workspace_id)
    if daemon is None or not daemon.running:
        daemon = JobWorkerDaemon(
            worker,
            workspace_id=workspace_id,
            job_types=job_type or None,
            poll_interval_seconds=poll_interval_ms / 1000,
            max_jobs_per_tick=max_jobs_per_tick,
        )
        registry[workspace_id] = daemon
        await daemon.start()
    return daemon.status()


@router.post("/worker/stop")
async def stop_job_worker(
    workspace_id: str,
    request: Request,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("owner"))],
) -> dict[str, Any]:
    daemon = _worker_daemon_registry(request).get(workspace_id)
    if daemon is None:
        return {"workspace_id": workspace_id, "running": False}
    return await daemon.stop()


@router.get("/worker/status")
async def get_job_worker_status(
    workspace_id: str,
    request: Request,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
) -> dict[str, Any]:
    daemon = _worker_daemon_registry(request).get(workspace_id)
    if daemon is None:
        return {"workspace_id": workspace_id, "running": False}
    return daemon.status()


@router.get("", response_model=ListJobsResponse)
async def list_jobs(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[JobService, Depends(get_job_service)],
    status: JobStatus | None = None,
    job_type: str | None = None,
    related_run_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ListJobsResponse:
    jobs = [
        JobSummary(**item)
        for item in service.list_jobs(
            workspace_id,
            status=status,
            job_type=job_type,
            related_run_id=related_run_id,
            limit=limit,
        )
    ]
    return ListJobsResponse(workspace_id=workspace_id, jobs=jobs)


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(
    workspace_id: str,
    job_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, Any]:
    return service.get_job(workspace_id, job_id)


@router.post("/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_job(
    workspace_id: str,
    job_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[JobService, Depends(get_job_service)],
) -> CancelJobResponse:
    job = service.cancel_job(workspace_id, job_id, identity)
    return CancelJobResponse(
        job_id=job["job_id"],
        workspace_id=job["workspace_id"],
        status=job["status"],
    )


@router.post("/{job_id}/retry", response_model=JobDetailResponse)
async def retry_job(
    workspace_id: str,
    job_id: str,
    request: RetryJobRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, Any]:
    return service.retry_job(workspace_id, job_id, identity, request)


@router.get("/{job_id}/events", response_model=ListJobEventsResponse)
async def list_job_events(
    workspace_id: str,
    job_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[JobService, Depends(get_job_service)],
    after_event_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
) -> ListJobEventsResponse:
    events, manifest = service.list_job_events(
        workspace_id,
        job_id,
        after_event_id=after_event_id,
        limit=limit,
    )
    next_after_event_id = events[-1]["event_id"] if events else after_event_id
    return ListJobEventsResponse(
        workspace_id=workspace_id,
        job_id=job_id,
        events=events,
        next_after_event_id=next_after_event_id,
        job_status=manifest["status"],
    )


@router.get("/{job_id}/events/stream")
async def stream_job_events(
    workspace_id: str,
    job_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[JobService, Depends(get_job_service)],
    after_event_id: str | None = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    wait_ms: Annotated[int, Query(ge=0, le=60000)] = 30000,
) -> StreamingResponse:
    if after_event_id and last_event_id and after_event_id != last_event_id:
        raise AgentSystemError(
            "invalid_event_cursor",
            "Conflicting SSE cursors.",
            status_code=400,
        )
    cursor = after_event_id or last_event_id

    async def event_stream() -> AsyncIterator[str]:
        next_cursor = cursor
        remaining = limit
        deadline = monotonic() + (wait_ms / 1000)
        while remaining > 0:
            events, manifest = service.list_job_events(
                workspace_id,
                job_id,
                after_event_id=next_cursor,
                limit=remaining,
            )
            for event in events:
                next_cursor = event["event_id"]
                remaining -= 1
                yield _format_sse_event(event)
            if manifest["status"] in TERMINAL_JOB_STATUSES:
                yield _format_sse_event(service.stream_closed_event(manifest))
                return
            if events:
                continue
            if monotonic() >= deadline:
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _format_sse_event(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"id: {event['event_id']}\nevent: {event['type']}\ndata: {data}\n\n"


def _worker_daemon_registry(request: Request) -> dict[str, JobWorkerDaemon]:
    registry = getattr(request.app.state, "job_worker_daemons", None)
    if not isinstance(registry, dict):
        registry = {}
        request.app.state.job_worker_daemons = registry
    return registry
