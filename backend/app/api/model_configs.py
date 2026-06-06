from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_llm_connector, get_model_config_service, require_workspace_role
from app.model_connector.config_service import ModelConfigService
from app.model_connector.connector import LLMConnector
from app.schemas.identity import RuntimeIdentity
from app.schemas.model_config import (
    ListModelConfigsResponse,
    ModelConfigResponse,
    TestModelConfigRequest,
    TestModelConfigResponse,
    UpdateModelConfigRequest,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/model-configs", tags=["model-configs"])


@router.get("", response_model=ListModelConfigsResponse)
async def list_model_configs(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ModelConfigService, Depends(get_model_config_service)],
) -> dict:
    return service.list_configs(workspace_id)


@router.get("/{config_id}", response_model=ModelConfigResponse)
async def get_model_config(
    workspace_id: str,
    config_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ModelConfigService, Depends(get_model_config_service)],
) -> dict:
    return service.get_config(workspace_id, config_id)


@router.put("/{config_id}", response_model=ModelConfigResponse)
async def update_model_config(
    workspace_id: str,
    config_id: str,
    request: UpdateModelConfigRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ModelConfigService, Depends(get_model_config_service)],
) -> dict:
    return service.update_config(workspace_id, config_id, request)


@router.post("/{config_id}/test", response_model=TestModelConfigResponse)
async def test_model_config(
    workspace_id: str,
    config_id: str,
    request: TestModelConfigRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[ModelConfigService, Depends(get_model_config_service)],
    connector: Annotated[LLMConnector, Depends(get_llm_connector)],
) -> dict:
    return service.test_config(workspace_id, config_id, request, connector)
