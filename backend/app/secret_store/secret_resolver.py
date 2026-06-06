from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.secret_store.crypto import decrypt_aes_256_gcm
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_service import SecretService

SecretPurpose = Literal[
    "model_call",
    "embedding_call",
    "rerank_call",
    "minio_connect",
    "milvus_connect",
    "neo4j_connect",
    "mcp_connect",
    "proxy_connect",
]

PURPOSE_ALLOWED_TYPES: dict[SecretPurpose, set[str]] = {
    "model_call": {"model_api_key"},
    "embedding_call": {"embedding_api_key"},
    "rerank_call": {"rerank_api_key"},
    "minio_connect": {"minio_access_key", "minio_secret_key"},
    "milvus_connect": {"milvus_token", "milvus_username_password"},
    "neo4j_connect": {"neo4j_username_password"},
    "mcp_connect": {"mcp_headers", "mcp_oauth_credential"},
    "proxy_connect": {"http_proxy_credential"},
}

PURPOSE_ALLOWED_CALLERS: dict[SecretPurpose, set[str]] = {
    "model_call": {"llm_connector"},
    "embedding_call": {"embedding_connector"},
    "rerank_call": {"rerank_connector"},
    "minio_connect": {"minio_connector", "storage_bootstrap"},
    "milvus_connect": {"milvus_connector"},
    "neo4j_connect": {"neo4j_connector", "neo4j_readonly_query_adapter"},
    "mcp_connect": {"mcp_connector"},
    "proxy_connect": {"proxy_connector"},
}


class SecretUnavailableError(Exception):
    pass


class SecretPurposeDeniedError(Exception):
    pass


class SecretCallerDeniedError(Exception):
    pass


@dataclass(frozen=True)
class ResolvedSecret:
    secret_id: str
    plaintext: str


class SecretResolver:
    def __init__(
        self,
        secret_service: SecretService,
        master_key_provider: MasterKeyProvider,
    ) -> None:
        self.secret_service = secret_service
        self.master_key_provider = master_key_provider

    def resolve(
        self,
        workspace_id: str,
        secret_ref: str,
        purpose: SecretPurpose,
        caller: str,
    ) -> ResolvedSecret:
        try:
            allowed_callers = PURPOSE_ALLOWED_CALLERS[purpose]
            if caller not in allowed_callers:
                raise SecretCallerDeniedError(f"{caller} cannot resolve secrets for {purpose}")

            record = self.secret_service.get_secret_record(workspace_id, secret_ref)
            if record.status != "active":
                raise SecretUnavailableError(secret_ref)

            allowed = PURPOSE_ALLOWED_TYPES[purpose]
            if record.type not in allowed:
                raise SecretPurposeDeniedError(f"{record.type} cannot be used for {purpose}")

            encrypted = record.encrypted_value
            plaintext = decrypt_aes_256_gcm(
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
                tag=encrypted.tag,
                master_key=self.master_key_provider.current(encrypted.key_version),
                aad=f"{workspace_id}:{secret_ref}:{record.type}",
            )
            self.secret_service.touch_last_used(workspace_id, secret_ref)
        except Exception as exc:
            self.secret_service.append_audit_event(
                workspace_id=workspace_id,
                event_type="secret_resolve_denied",
                secret_id=secret_ref,
                status="failed",
                caller=caller,
                purpose=purpose,
                error_type=exc.__class__.__name__,
            )
            raise
        self.secret_service.append_audit_event(
            workspace_id=workspace_id,
            event_type="secret_resolved",
            secret_id=secret_ref,
            status="success",
            caller=caller,
            purpose=purpose,
        )
        return ResolvedSecret(secret_id=secret_ref, plaintext=plaintext)
