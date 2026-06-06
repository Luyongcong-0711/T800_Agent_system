from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.health import ServiceHealth

DatabaseTarget = Literal["minio", "milvus", "neo4j", "redis"]
DatabaseMode = Literal["local", "remote"]


class DatabaseTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: DatabaseTarget
    mode: DatabaseMode = "local"
    enabled: bool = True
    endpoint: str
    tls: bool = False
    bucket: str | None = None
    credential_refs: dict[str, str] = Field(default_factory=dict)
    options: dict[str, str] = Field(default_factory=dict)

    @field_validator("credential_refs")
    @classmethod
    def credential_refs_must_point_to_secret_store(
        cls,
        value: dict[str, str],
    ) -> dict[str, str]:
        invalid_refs = [
            key
            for key, item in value.items()
            if item and not str(item).startswith("secret_ref://")
        ]
        if invalid_refs:
            raise ValueError("credential_refs values must use secret_ref:// references")
        return value

    @model_validator(mode="after")
    def enforce_target_invariants(self) -> DatabaseTargetConfig:
        if self.target == "redis":
            self.options = {**self.options, "role": "cache_only"}
        if self.target != "minio":
            self.bucket = None
        return self


class DatabaseConfigResponse(BaseModel):
    workspace_id: str
    targets: list[DatabaseTargetConfig]
    updated_at: str
    revision: int = 1


class UpdateDatabaseConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[DatabaseTargetConfig]


class DatabaseHealthSnapshotResponse(BaseModel):
    ok: bool
    workspace_id: str
    services: list[ServiceHealth]
    checked_at: str | None = None
    source: Literal["snapshot", "unknown", "live_check", "job_check"] = "unknown"
