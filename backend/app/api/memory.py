from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_memory_service, require_workspace_role
from app.memory.service import MemoryService
from app.schemas.identity import RuntimeIdentity
from app.schemas.memory import (
    ListMemoriesResponse,
    MemoryDetailResponse,
    MemorySearchResponse,
    MemorySnapshotResponse,
    MemorySyncStateResponse,
    MemorySummary,
    PatchMemoryRequest,
    UpsertMemoryRequest,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/memories", tags=["memory"])
snapshot_router = APIRouter(
    prefix="/workspaces/{workspace_id}/memory-snapshots",
    tags=["memory"],
)


@router.get("", response_model=ListMemoriesResponse)
async def list_memories(
    workspace_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    include_deleted: bool = False,
) -> ListMemoriesResponse:
    memories = [
        MemorySummary(**item)
        for item in service.list_memories(
            workspace_id,
            identity.user_id,
            include_deleted=include_deleted,
        )
    ]
    return ListMemoriesResponse(workspace_id=workspace_id, memories=memories)


@router.post("", response_model=MemoryDetailResponse)
async def upsert_memory(
    workspace_id: str,
    request: UpsertMemoryRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> dict[str, Any]:
    return service.upsert_memory(workspace_id, identity, request)


@router.get("/search", response_model=MemorySearchResponse)
async def search_memories(
    workspace_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    query: Annotated[str, Query(min_length=1)],
    memory_type: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> MemorySearchResponse:
    return MemorySearchResponse(
        workspace_id=workspace_id,
        hits=service.search(
            workspace_id,
            identity.user_id,
            query=query,
            memory_types=memory_type or [],
            limit=limit,
        ),
    )


@router.get("/sync-state", response_model=MemorySyncStateResponse)
async def get_memory_sync_state(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> dict[str, Any]:
    return service.get_sync_state(workspace_id)


@router.get("/{memory_id}", response_model=MemoryDetailResponse)
async def get_memory(
    workspace_id: str,
    memory_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> dict[str, Any]:
    return service.get_memory(workspace_id, identity.user_id, memory_id)


@router.patch("/{memory_id}", response_model=MemoryDetailResponse)
async def patch_memory(
    workspace_id: str,
    memory_id: str,
    request: PatchMemoryRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> dict[str, Any]:
    return service.patch_memory(workspace_id, identity.user_id, memory_id, request)


@router.post("/{memory_id}/approve", response_model=MemoryDetailResponse)
async def approve_memory(
    workspace_id: str,
    memory_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> dict[str, Any]:
    return service.approve_memory(workspace_id, identity.user_id, memory_id)


@router.post("/{memory_id}/reject", response_model=MemoryDetailResponse)
async def reject_memory(
    workspace_id: str,
    memory_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> dict[str, Any]:
    return service.reject_memory(workspace_id, identity.user_id, memory_id)


@router.delete("/{memory_id}", response_model=MemoryDetailResponse)
async def delete_memory(
    workspace_id: str,
    memory_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> dict[str, Any]:
    return service.delete_memory(workspace_id, identity.user_id, memory_id)


@snapshot_router.post("", response_model=MemorySnapshotResponse)
async def create_memory_snapshot(
    workspace_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
    thread_id: str = Query(min_length=1),
    query: str | None = None,
) -> dict[str, Any]:
    return service.build_memory_snapshot(
        workspace_id,
        identity.user_id,
        thread_id,
        query=query,
    )


@snapshot_router.get("/{snapshot_id}", response_model=MemorySnapshotResponse)
async def get_memory_snapshot(
    workspace_id: str,
    snapshot_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[MemoryService, Depends(get_memory_service)],
) -> dict[str, Any]:
    return service.get_memory_snapshot(workspace_id, snapshot_id)
