from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_database_config_service, require_workspace_role
from app.database.service import DatabaseConfigService
from app.schemas.database import (
    DatabaseConfigResponse,
    DatabaseHealthSnapshotResponse,
    UpdateDatabaseConfigRequest,
)
from app.schemas.identity import RuntimeIdentity

router = APIRouter(prefix="/workspaces/{workspace_id}/database", tags=["database"])


@router.get("/config", response_model=DatabaseConfigResponse)
async def get_database_config(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DatabaseConfigService, Depends(get_database_config_service)],
) -> dict:
    return service.get_config(workspace_id)


@router.put("/config", response_model=DatabaseConfigResponse)
async def update_database_config(
    workspace_id: str,
    request: UpdateDatabaseConfigRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[DatabaseConfigService, Depends(get_database_config_service)],
) -> dict:
    return service.update_config(workspace_id, request)


@router.get("/health", response_model=DatabaseHealthSnapshotResponse)
async def get_database_health(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DatabaseConfigService, Depends(get_database_config_service)],
) -> dict:
    return service.get_health_snapshot(workspace_id)


@router.post("/health/check", response_model=DatabaseHealthSnapshotResponse)
async def run_database_health_check(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[DatabaseConfigService, Depends(get_database_config_service)],
) -> dict:
    return await service.run_health_check(workspace_id)
