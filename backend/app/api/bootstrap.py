from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.identity import get_default_identity
from app.core.settings import Settings, get_settings
from app.schemas.identity import (
    BootstrapResponse,
    FeatureFlags,
    UserIdentity,
    WorkspaceIdentity,
)

router = APIRouter(tags=["bootstrap"])


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    settings: Annotated[Settings, Depends(get_settings)],
) -> BootstrapResponse:
    identity = get_default_identity(settings)
    return BootstrapResponse(
        user=UserIdentity(user_id=identity.user_id, role=identity.role),
        workspace=WorkspaceIdentity(
            workspace_id=identity.workspace_id,
            workspace_role=identity.workspace_role,
        ),
        feature_flags=FeatureFlags(),
    )
