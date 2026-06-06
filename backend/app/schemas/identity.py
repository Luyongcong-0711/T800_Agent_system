from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Role = Literal["owner", "admin", "editor", "viewer"]


class RuntimeIdentity(BaseModel):
    user_id: str = "default_user"
    role: Role = "owner"
    workspace_id: str = "default"
    workspace_role: Role = "owner"


class UserIdentity(BaseModel):
    user_id: str
    role: Role


class WorkspaceIdentity(BaseModel):
    workspace_id: str
    workspace_role: Role


class FeatureFlags(BaseModel):
    login_enabled: bool = False
    workspace_switch_enabled: bool = False


class BootstrapResponse(BaseModel):
    user: UserIdentity
    workspace: WorkspaceIdentity
    feature_flags: FeatureFlags

