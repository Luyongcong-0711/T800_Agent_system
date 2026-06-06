from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SecretStatus = Literal["active", "disabled", "rotated", "soft_deleted"]
SecretScope = Literal["workspace"]
SecretType = Literal[
    "model_api_key",
    "embedding_api_key",
    "rerank_api_key",
    "minio_access_key",
    "minio_secret_key",
    "milvus_token",
    "milvus_username_password",
    "neo4j_username_password",
    "mcp_headers",
    "mcp_oauth_credential",
    "http_proxy_credential",
    "web_fetch_credential",
]


class EncryptedValue(BaseModel):
    alg: Literal["AES-256-GCM"] = "AES-256-GCM"
    ciphertext: str
    nonce: str
    tag: str
    key_version: str = "v1"


class SecretRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    secret_id: str
    workspace_id: str
    scope: SecretScope = "workspace"
    type: SecretType
    display_name: str
    encrypted_value: EncryptedValue
    masked: str
    status: SecretStatus = "active"
    created_by: str = "default_user"
    created_at: str
    updated_at: str
    last_used_at: str | None = None
    rotated_from_secret_id: str | None = None
    disabled_at: str | None = None
    deleted_at: str | None = None
    revision: int = 1


class SecretSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret_id: str
    secret_ref: str
    type: SecretType
    display_name: str
    masked: str
    status: SecretStatus
    last_used_at: str | None = None
    updated_at: str


class SecretIndexItem(SecretSummary):
    object_key: str


class SecretIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    workspace_id: str
    secrets: list[SecretIndexItem] = Field(default_factory=list)
    revision: int = 1
    updated_at: str


class CreateSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SecretType
    display_name: str = Field(min_length=1, max_length=120)
    plaintext: str = Field(min_length=1, max_length=65536)
    scope: SecretScope = "workspace"


class CreateSecretResponse(SecretSummary):
    pass


class ListSecretsResponse(BaseModel):
    workspace_id: str
    secrets: list[SecretSummary]


class UpdateSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "disabled"] | None = None


class RotateSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plaintext: str = Field(min_length=1, max_length=65536)


class SecretReferencesResponse(BaseModel):
    secret_id: str
    references: list[dict[str, str]] = Field(default_factory=list)
