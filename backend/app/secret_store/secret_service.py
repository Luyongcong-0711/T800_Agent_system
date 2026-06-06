from __future__ import annotations

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.schemas.secret import (
    CreateSecretRequest,
    CreateSecretResponse,
    EncryptedValue,
    ListSecretsResponse,
    RotateSecretRequest,
    SecretIndex,
    SecretIndexItem,
    SecretRecord,
    SecretSummary,
    UpdateSecretRequest,
)
from app.secret_store.crypto import encrypt_aes_256_gcm
from app.secret_store.master_key import MasterKeyProvider
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import (
    database_config_key,
    secret_audit_prefix,
    secret_object_key,
    secrets_index_key,
    workspace_prefix,
)


class SecretNotFoundError(Exception):
    pass


class SecretConflictError(Exception):
    pass


def mask_plaintext(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}****{value[-4:]}"


class SecretService:
    def __init__(self, object_store: ObjectStore, master_key_provider: MasterKeyProvider) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.master_key_provider = master_key_provider

    def create_secret(
        self,
        workspace_id: str,
        request: CreateSecretRequest,
        created_by: str,
    ) -> CreateSecretResponse:
        return self._create_secret(
            workspace_id=workspace_id,
            secret_id=new_id("secret"),
            request=request,
            created_by=created_by,
        )

    def ensure_static_secret(
        self,
        workspace_id: str,
        *,
        secret_id: str,
        request: CreateSecretRequest,
        created_by: str,
    ) -> SecretSummary:
        try:
            record = self.get_secret_record(workspace_id, secret_id)
        except SecretNotFoundError:
            return self._create_secret(
                workspace_id=workspace_id,
                secret_id=secret_id,
                request=request,
                created_by=created_by,
            )
        return SecretSummary(**self._summary(record).model_dump(exclude={"object_key"}))

    def _create_secret(
        self,
        *,
        workspace_id: str,
        secret_id: str,
        request: CreateSecretRequest,
        created_by: str,
    ) -> CreateSecretResponse:
        aad = self._aad(workspace_id, secret_id, request.type)
        encrypted = encrypt_aes_256_gcm(
            request.plaintext,
            self.master_key_provider.current(),
            aad=aad,
        )
        now = utc_now_iso()
        record = SecretRecord(
            secret_id=secret_id,
            workspace_id=workspace_id,
            scope=request.scope,
            type=request.type,
            display_name=request.display_name,
            encrypted_value=EncryptedValue(**encrypted.__dict__),
            masked=mask_plaintext(request.plaintext),
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.json_store.write_json(
            secret_object_key(workspace_id, secret_id),
            record.model_dump(),
        )
        summary = self._summary(record)
        self._upsert_index(workspace_id, summary)
        return CreateSecretResponse(**summary.model_dump(exclude={"object_key"}))

    def list_secrets(self, workspace_id: str) -> ListSecretsResponse:
        return ListSecretsResponse(
            workspace_id=workspace_id,
            secrets=[
                SecretSummary(**item.model_dump(exclude={"object_key"}))
                for item in self._read_index(workspace_id).secrets
                if item.status != "soft_deleted"
            ],
        )

    def get_secret_summary(self, workspace_id: str, secret_id: str) -> SecretSummary:
        index = self._read_index(workspace_id)
        for item in index.secrets:
            if item.secret_id == secret_id:
                return SecretSummary(**item.model_dump(exclude={"object_key"}))
        raise SecretNotFoundError(secret_id)

    def get_secret_record(self, workspace_id: str, secret_id: str) -> SecretRecord:
        key = secret_object_key(workspace_id, secret_id)
        if not self.json_store.object_store.exists(key):
            raise SecretNotFoundError(secret_id)
        return SecretRecord(**self.json_store.read_json(key))

    def disable_secret(self, workspace_id: str, secret_id: str) -> SecretSummary:
        return self.update_secret(
            workspace_id,
            secret_id,
            UpdateSecretRequest(status="disabled"),
        )

    def update_secret(
        self,
        workspace_id: str,
        secret_id: str,
        request: UpdateSecretRequest,
    ) -> SecretSummary:
        record = self.get_secret_record(workspace_id, secret_id)
        previous_revision = record.revision
        now = utc_now_iso()
        if request.display_name is not None:
            record.display_name = request.display_name
        if request.status is not None:
            record.status = request.status
            if request.status == "disabled":
                record.disabled_at = now
            if request.status == "active":
                record.disabled_at = None
        record.updated_at = now
        record.revision += 1
        return self._persist_record(workspace_id, record, previous_revision)

    def rotate_secret(
        self,
        workspace_id: str,
        secret_id: str,
        request: RotateSecretRequest,
    ) -> SecretSummary:
        record = self.get_secret_record(workspace_id, secret_id)
        previous_revision = record.revision
        encrypted = encrypt_aes_256_gcm(
            request.plaintext,
            self.master_key_provider.current(record.encrypted_value.key_version),
            aad=self._aad(workspace_id, secret_id, record.type),
            key_version=record.encrypted_value.key_version,
        )
        now = utc_now_iso()
        record.encrypted_value = EncryptedValue(**encrypted.__dict__)
        record.masked = mask_plaintext(request.plaintext)
        record.status = "active"
        record.disabled_at = None
        record.updated_at = now
        record.revision += 1
        return self._persist_record(workspace_id, record, previous_revision)

    def delete_secret(self, workspace_id: str, secret_id: str) -> SecretSummary:
        references = self.list_references(workspace_id, secret_id)
        if references:
            raise AgentSystemError(
                "secret_still_referenced",
                "Secret is still referenced by active configuration.",
                status_code=409,
                retryable=False,
                details={"references": references},
            )
        record = self.get_secret_record(workspace_id, secret_id)
        previous_revision = record.revision
        now = utc_now_iso()
        record.status = "soft_deleted"
        record.deleted_at = now
        record.updated_at = now
        record.revision += 1
        return self._persist_record(workspace_id, record, previous_revision)

    def list_references(self, workspace_id: str, secret_id: str) -> list[dict[str, str]]:
        normalized_secret_id = _normalize_secret_ref(secret_id)
        references: list[dict[str, str]] = []
        for key in self._reference_candidate_keys(workspace_id):
            if not self.object_store.exists(key):
                continue
            value = self.json_store.read_json(key)
            references.extend(
                _references_in_object(
                    value,
                    secret_id=normalized_secret_id,
                    object_key=key,
                    object_type=_reference_object_type(key),
                    object_id=_reference_object_id(key),
                )
            )
        return sorted(references, key=lambda item: (item["object_type"], item["field"]))

    def _reference_candidate_keys(self, workspace_id: str) -> list[str]:
        workspace = workspace_prefix(workspace_id)
        keys = [database_config_key(workspace_id)]
        keys.extend(
            key
            for key in self.object_store.list_keys(f"{workspace}/model_configs/")
            if key.endswith(".json")
        )
        keys.extend(
            key
            for key in self.object_store.list_keys(f"{workspace}/mcp/servers/")
            if key.endswith("/manifest.json")
        )
        return sorted(set(keys))

    def append_audit_event(
        self,
        workspace_id: str,
        event_type: str,
        secret_id: str,
        status: str,
        caller: str | None = None,
        purpose: str | None = None,
        error_type: str | None = None,
    ) -> None:
        JsonlSegmentStore(self.object_store, secret_audit_prefix(workspace_id)).append(
            {
                "schema_version": 1,
                "event_type": event_type,
                "secret_id": secret_id,
                "status": status,
                "caller": caller,
                "purpose": purpose,
                "error_type": error_type,
                "created_at": utc_now_iso(),
            }
        )

    def touch_last_used(self, workspace_id: str, secret_id: str) -> None:
        record = self.get_secret_record(workspace_id, secret_id)
        previous_revision = record.revision
        record.last_used_at = utc_now_iso()
        record.updated_at = record.last_used_at
        record.revision += 1
        self._persist_record(workspace_id, record, previous_revision)

    def _read_index(self, workspace_id: str) -> SecretIndex:
        key = secrets_index_key(workspace_id)
        if not self.json_store.object_store.exists(key):
            return SecretIndex(workspace_id=workspace_id, updated_at=utc_now_iso())
        return SecretIndex(**self.json_store.read_json(key))

    def _upsert_index(self, workspace_id: str, summary: SecretIndexItem) -> None:
        index = self._read_index(workspace_id)
        index_exists = self.json_store.object_store.exists(secrets_index_key(workspace_id))
        previous_revision = index.revision if index_exists else None
        next_items = [item for item in index.secrets if item.secret_id != summary.secret_id]
        next_items.append(summary)
        index.secrets = sorted(next_items, key=lambda item: item.updated_at, reverse=True)
        index.revision = index.revision + 1 if index_exists else 1
        index.updated_at = utc_now_iso()
        self.json_store.write_json(
            secrets_index_key(workspace_id),
            index.model_dump(),
            expected_revision=previous_revision,
        )

    def _persist_record(
        self,
        workspace_id: str,
        record: SecretRecord,
        previous_revision: int,
    ) -> SecretSummary:
        self.json_store.write_json(
            secret_object_key(workspace_id, record.secret_id),
            record.model_dump(),
            expected_revision=previous_revision,
        )
        summary = self._summary(record)
        self._upsert_index(workspace_id, summary)
        return SecretSummary(**summary.model_dump(exclude={"object_key"}))

    @staticmethod
    def _summary(record: SecretRecord) -> SecretIndexItem:
        return SecretIndexItem(
            secret_id=record.secret_id,
            secret_ref=record.secret_id,
            type=record.type,
            display_name=record.display_name,
            masked=record.masked,
            status=record.status,
            last_used_at=record.last_used_at,
            updated_at=record.updated_at,
            object_key=secret_object_key(record.workspace_id, record.secret_id),
        )

    @staticmethod
    def _aad(workspace_id: str, secret_id: str, secret_type: str) -> str:
        return f"{workspace_id}:{secret_id}:{secret_type}"


def _normalize_secret_ref(value: str) -> str:
    return str(value).removeprefix("secret_ref://")


def _path_can_hold_secret_ref(path: tuple[str, ...]) -> bool:
    joined = ".".join(path).lower()
    return any(marker in joined for marker in ("ref", "refs", "credential", "secret"))


def _references_in_object(
    value: object,
    *,
    secret_id: str,
    object_key: str,
    object_type: str,
    object_id: str,
    path: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    if isinstance(value, dict):
        references: list[dict[str, str]] = []
        for key, item in value.items():
            references.extend(
                _references_in_object(
                    item,
                    secret_id=secret_id,
                    object_key=object_key,
                    object_type=object_type,
                    object_id=object_id,
                    path=(*path, str(key)),
                )
            )
        return references
    if isinstance(value, list):
        references = []
        for index, item in enumerate(value):
            references.extend(
                _references_in_object(
                    item,
                    secret_id=secret_id,
                    object_key=object_key,
                    object_type=object_type,
                    object_id=object_id,
                    path=(*path, str(index)),
                )
            )
        return references
    if (
        isinstance(value, str)
        and _path_can_hold_secret_ref(path)
        and _normalize_secret_ref(value) == secret_id
    ):
        return [
            {
                "object_type": object_type,
                "object_id": object_id,
                "field": ".".join(path),
                "object_key": object_key,
            }
        ]
    return []


def _reference_object_type(key: str) -> str:
    if "/model_configs/" in key:
        return "model_config"
    if key.endswith("/database/config.json"):
        return "database_config"
    if "/mcp/servers/" in key:
        return "mcp_server"
    return "config"


def _reference_object_id(key: str) -> str:
    if "/model_configs/" in key:
        return key.rsplit("/", 1)[-1].removesuffix(".json")
    if key.endswith("/database/config.json"):
        return "database"
    if "/mcp/servers/" in key:
        parts = key.split("/")
        try:
            return parts[parts.index("servers") + 1]
        except (ValueError, IndexError):
            return "mcp_server"
    return key.rsplit("/", 1)[-1]
