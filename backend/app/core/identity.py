from __future__ import annotations

from app.core.settings import Settings, get_settings
from app.schemas.identity import RuntimeIdentity


def get_default_identity(settings: Settings | None = None) -> RuntimeIdentity:
    current = settings or get_settings()
    return RuntimeIdentity(
        user_id=current.default_user_id,
        role=current.default_user_role,
        workspace_id=current.default_workspace_id,
        workspace_role=current.default_workspace_role,
    )

