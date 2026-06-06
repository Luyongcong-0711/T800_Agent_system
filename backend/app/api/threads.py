from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from app.api.dependencies import get_conversation_service, require_workspace_role
from app.conversation.service import ConversationService
from app.schemas.conversation import (
    ConversationMessage,
    CreateRunRequest,
    CreateThreadRequest,
    ListMessagesResponse,
    ListThreadsResponse,
    PatchThreadRequest,
    RunDetailResponse,
    ThreadDetailResponse,
    ThreadSummary,
)
from app.schemas.identity import RuntimeIdentity

router = APIRouter(prefix="/workspaces/{workspace_id}/threads", tags=["threads"])


@router.post("", response_model=ThreadDetailResponse)
async def create_thread(
    workspace_id: str,
    request: CreateThreadRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> dict:
    return service.create_thread(workspace_id, identity, request)


@router.get("", response_model=ListThreadsResponse)
async def list_threads(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ListThreadsResponse:
    threads = [ThreadSummary(**item) for item in service.list_threads(workspace_id)]
    return ListThreadsResponse(workspace_id=workspace_id, threads=threads)


@router.get("/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(
    workspace_id: str,
    thread_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> dict:
    return service.get_thread(workspace_id, thread_id)


@router.patch("/{thread_id}", response_model=ThreadDetailResponse)
async def patch_thread(
    workspace_id: str,
    thread_id: str,
    request: PatchThreadRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> dict:
    return service.patch_thread(workspace_id, thread_id, request)


@router.get("/{thread_id}/messages", response_model=ListMessagesResponse)
async def list_messages(
    workspace_id: str,
    thread_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
    after_message_id: str | None = None,
    before_message_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ListMessagesResponse:
    messages = [
        ConversationMessage(**item)
        for item in service.list_messages(
            workspace_id,
            thread_id,
            after_message_id=after_message_id,
            before_message_id=before_message_id,
            limit=limit,
        )
    ]
    return ListMessagesResponse(
        workspace_id=workspace_id,
        thread_id=thread_id,
        messages=messages,
    )


@router.post("/{thread_id}/runs", response_model=RunDetailResponse)
async def create_run(
    workspace_id: str,
    thread_id: str,
    request: CreateRunRequest,
    http_request: Request,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ConversationService, Depends(get_conversation_service)],
    background_tasks: BackgroundTasks,
) -> dict:
    trace_id = request.trace_id or getattr(http_request.state, "trace_id", None)
    run = service.create_run(
        workspace_id,
        thread_id,
        identity,
        request,
        execute_inline=not request.stream,
        trace_id=trace_id,
    )
    if request.stream and run["status"] == "running":
        background_tasks.add_task(
            service.execute_run,
            workspace_id=workspace_id,
            thread_id=thread_id,
            identity=identity,
            run_id=run["run_id"],
            user_message=request.user_message,
        )
    return run
