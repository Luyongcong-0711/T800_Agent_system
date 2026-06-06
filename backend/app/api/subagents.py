from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_subagent_service, require_workspace_role
from app.core.errors import AgentSystemError
from app.schemas.identity import RuntimeIdentity
from app.schemas.subagent import (
    ListSubAgentTasksResponse,
    SubAgentCompleteRequest,
    SubAgentResultResponse,
    SubAgentReviewRequest,
    SubAgentReviewResponse,
    SubAgentStatus,
    SubAgentTaskDetail,
    SubAgentTaskRequest,
    SubAgentTaskSummary,
)
from app.subagents.service import SubAgentService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["subagents"])


def _scoped_request(request: SubAgentTaskRequest, parent_run_id: str) -> SubAgentTaskRequest:
    return request.model_copy(update={"parent_run_id": parent_run_id})


def _ensure_parent_run(task: dict[str, Any], parent_run_id: str) -> dict[str, Any]:
    if task.get("parent_run_id") != parent_run_id:
        raise AgentSystemError(
            "subagent_task_not_found",
            "SubAgent task was not found in this run.",
            status_code=404,
        )
    return task


@router.post("/runs/{parent_run_id}/subagent-tasks", response_model=SubAgentTaskDetail)
async def create_run_scoped_subagent_task(
    workspace_id: str,
    parent_run_id: str,
    request: SubAgentTaskRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    return service.create_task(workspace_id, identity, _scoped_request(request, parent_run_id))


@router.get("/runs/{parent_run_id}/subagent-tasks", response_model=ListSubAgentTasksResponse)
async def list_run_scoped_subagent_tasks(
    workspace_id: str,
    parent_run_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
    status: SubAgentStatus | None = None,
) -> ListSubAgentTasksResponse:
    tasks = service.list_tasks(workspace_id, parent_run_id=parent_run_id, status=status)
    return ListSubAgentTasksResponse(
        workspace_id=workspace_id,
        tasks=[SubAgentTaskSummary(**task) for task in tasks],
    )


@router.get("/runs/{parent_run_id}/subagent-tasks/{task_id}", response_model=SubAgentTaskDetail)
async def get_run_scoped_subagent_task(
    workspace_id: str,
    parent_run_id: str,
    task_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    return _ensure_parent_run(service.get_task(workspace_id, task_id), parent_run_id)


@router.post(
    "/runs/{parent_run_id}/subagent-tasks/{task_id}/complete",
    response_model=SubAgentResultResponse,
)
async def complete_run_scoped_subagent_task(
    workspace_id: str,
    parent_run_id: str,
    task_id: str,
    request: SubAgentCompleteRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    _ensure_parent_run(service.get_task(workspace_id, task_id), parent_run_id)
    return service.complete_task(workspace_id, task_id, request)


@router.post(
    "/runs/{parent_run_id}/subagent-results/{task_id}/review",
    response_model=SubAgentReviewResponse,
)
async def review_run_scoped_subagent_result(
    workspace_id: str,
    parent_run_id: str,
    task_id: str,
    request: SubAgentReviewRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    _ensure_parent_run(service.get_task(workspace_id, task_id), parent_run_id)
    return service.review_result(workspace_id, task_id, identity, request)


@router.get("/runs/{parent_run_id}/subagents/leaf-state")
async def get_run_scoped_subagent_leaf_state(
    workspace_id: str,
    parent_run_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    return service.run_leaf_state(workspace_id, parent_run_id)


@router.post("/subagents/tasks", response_model=SubAgentTaskDetail)
async def create_subagent_task(
    workspace_id: str,
    request: SubAgentTaskRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    return service.create_task(workspace_id, identity, request)


@router.get("/subagents/tasks", response_model=ListSubAgentTasksResponse)
async def list_subagent_tasks(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
    parent_run_id: str | None = None,
    status: SubAgentStatus | None = None,
) -> ListSubAgentTasksResponse:
    tasks = service.list_tasks(workspace_id, parent_run_id=parent_run_id, status=status)
    return ListSubAgentTasksResponse(
        workspace_id=workspace_id,
        tasks=[SubAgentTaskSummary(**task) for task in tasks],
    )


@router.get("/subagents/tasks/{task_id}", response_model=SubAgentTaskDetail)
async def get_subagent_task(
    workspace_id: str,
    task_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    return service.get_task(workspace_id, task_id)


@router.post("/subagents/tasks/{task_id}/complete", response_model=SubAgentResultResponse)
async def complete_subagent_task(
    workspace_id: str,
    task_id: str,
    request: SubAgentCompleteRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    return service.complete_task(workspace_id, task_id, request)


@router.post("/subagents/tasks/{task_id}/review", response_model=SubAgentReviewResponse)
async def review_subagent_result(
    workspace_id: str,
    task_id: str,
    request: SubAgentReviewRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    return service.review_result(workspace_id, task_id, identity, request)


@router.get("/subagents/runs/{parent_run_id}/leaf-state")
async def get_subagent_run_leaf_state(
    workspace_id: str,
    parent_run_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SubAgentService, Depends(get_subagent_service)],
) -> dict[str, Any]:
    return service.run_leaf_state(workspace_id, parent_run_id)
