from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from app.api.dependencies import (
    get_conversation_service,
    get_runtime_runner,
    require_workspace_role,
)
from app.conversation.service import ConversationService
from app.core.errors import AgentSystemError
from app.schemas.conversation import (
    CancelRunResponse,
    ListRunEventsResponse,
    RunApprovalDecisionRequest,
    RunApprovalDecisionResponse,
    RunDetailResponse,
    RunOperationRollbackRequest,
    RunOperationRollbackResponse,
)
from app.schemas.identity import RuntimeIdentity
from app.schemas.runtime import RuntimeSmokeRequest, RuntimeSmokeResponse

router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])


@router.post("/smoke", response_model=RuntimeSmokeResponse)
async def run_runtime_smoke(
    workspace_id: str,
    request: RuntimeSmokeRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    runner: Annotated[Any, Depends(get_runtime_runner)],
) -> RuntimeSmokeResponse:
    return runner.invoke_smoke(workspace_id, identity, request)


@router.post("/recover-stale")
async def recover_stale_runs(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
    stale_after_seconds: Annotated[int, Query(ge=1, le=86400)] = 3600,
) -> dict:
    return service.recover_stale_running_runs(
        workspace_id,
        stale_after_seconds=stale_after_seconds,
    )


@router.get("/{run_id}", response_model=RunDetailResponse)
async def get_run(
    workspace_id: str,
    run_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> dict:
    return service.get_run(workspace_id, run_id)


@router.post("/{run_id}/cancel", response_model=CancelRunResponse)
async def cancel_run(
    workspace_id: str,
    run_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> dict:
    run = service.cancel_run(workspace_id, run_id)
    return {
        "run_id": run["run_id"],
        "workspace_id": run["workspace_id"],
        "thread_id": run["thread_id"],
        "status": run["status"],
    }


@router.post(
    "/{run_id}/operations/{operation_id}/rollback",
    response_model=RunOperationRollbackResponse,
)
async def rollback_run_operation(
    workspace_id: str,
    run_id: str,
    operation_id: str,
    request: RunOperationRollbackRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> dict:
    return service.rollback_run_operation(
        workspace_id=workspace_id,
        run_id=run_id,
        operation_id=operation_id,
        request=request,
        identity=identity,
    )


@router.post(
    "/{run_id}/approvals/{approval_id}/approve",
    response_model=RunApprovalDecisionResponse,
)
async def approve_run_approval(
    workspace_id: str,
    run_id: str,
    approval_id: str,
    request: RunApprovalDecisionRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> dict:
    return service.resolve_run_approval(
        workspace_id=workspace_id,
        run_id=run_id,
        approval_id=approval_id,
        decision="approved",
        identity=identity,
        reason=request.reason,
    )


@router.post(
    "/{run_id}/approvals/{approval_id}/reject",
    response_model=RunApprovalDecisionResponse,
)
async def reject_run_approval(
    workspace_id: str,
    run_id: str,
    approval_id: str,
    request: RunApprovalDecisionRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> dict:
    return service.resolve_run_approval(
        workspace_id=workspace_id,
        run_id=run_id,
        approval_id=approval_id,
        decision="rejected",
        identity=identity,
        reason=request.reason,
    )


@router.get("/{run_id}/events", response_model=ListRunEventsResponse)
async def list_run_events(
    workspace_id: str,
    run_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
    after_event_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
) -> ListRunEventsResponse:
    events, manifest = service.list_run_events(
        workspace_id,
        run_id,
        after_event_id=after_event_id,
        limit=limit,
    )
    next_after_event_id = events[-1]["event_id"] if events else after_event_id
    return ListRunEventsResponse(
        workspace_id=workspace_id,
        run_id=run_id,
        events=events,
        next_after_event_id=next_after_event_id,
        run_status=manifest["status"],
    )


@router.get("/{run_id}/events/stream")
async def stream_run_events(
    workspace_id: str,
    run_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
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
            events, manifest = service.list_run_events(
                workspace_id,
                run_id,
                after_event_id=next_cursor,
                limit=remaining,
            )
            for event in events:
                next_cursor = event["event_id"]
                remaining -= 1
                yield _format_sse_event(event)
            if manifest["status"] in {"completed", "failed", "cancelled", "waiting_approval"}:
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
