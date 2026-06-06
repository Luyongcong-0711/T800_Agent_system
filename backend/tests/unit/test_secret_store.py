from __future__ import annotations

import json

import pytest

from app.core.errors import AgentSystemError
from app.core.settings import Settings
from app.schemas.secret import CreateSecretRequest
from app.secret_store.crypto import generate_master_key
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_resolver import (
    SecretCallerDeniedError,
    SecretPurposeDeniedError,
    SecretResolver,
    SecretUnavailableError,
)
from app.secret_store.secret_service import SecretService
from app.storage.local_object_store import LocalObjectStore
from app.storage.path_builder import (
    database_config_key,
    mcp_server_manifest_key,
    model_config_key,
    secret_object_key,
    secrets_index_key,
)


def _secret_service(tmp_path) -> SecretService:
    settings = Settings(agent_master_key=generate_master_key())
    return SecretService(LocalObjectStore(tmp_path / "objects"), MasterKeyProvider(settings))


def test_create_secret_stores_encrypted_object_without_plaintext(tmp_path) -> None:
    service = _secret_service(tmp_path)
    plaintext = "sk-phase-b-secret-value-1234"

    response = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Main model key",
            plaintext=plaintext,
        ),
        created_by="default_user",
    )

    object_key = secret_object_key("default", response.secret_id)
    raw_record = service.json_store.object_store.read_text(object_key)
    record = json.loads(raw_record)

    assert plaintext not in raw_record
    assert "plaintext" not in record
    assert record["encrypted_value"]["ciphertext"]
    assert record["encrypted_value"]["nonce"]
    assert record["encrypted_value"]["tag"]
    assert response.masked == "sk-****1234"


def test_ensure_static_secret_creates_stable_encrypted_record_once(tmp_path) -> None:
    service = _secret_service(tmp_path)
    plaintext = "tp-static-secret-value-1234"

    first = service.ensure_static_secret(
        "default",
        secret_id="secret_mimo_openai_compatible_key",
        request=CreateSecretRequest(
            type="model_api_key",
            display_name="Default local model API key",
            plaintext=plaintext,
        ),
        created_by="default_user",
    )
    second = service.ensure_static_secret(
        "default",
        secret_id="secret_mimo_openai_compatible_key",
        request=CreateSecretRequest(
            type="model_api_key",
            display_name="Default local model API key",
            plaintext="tp-different-value-5678",
        ),
        created_by="default_user",
    )

    raw_record = service.json_store.object_store.read_text(
        secret_object_key("default", "secret_mimo_openai_compatible_key")
    )
    index = service.json_store.read_json(secrets_index_key("default"))

    assert first.secret_id == "secret_mimo_openai_compatible_key"
    assert second.secret_id == first.secret_id
    assert first.masked == second.masked
    assert len(index["secrets"]) == 1
    assert plaintext not in raw_record
    assert "tp-different-value-5678" not in raw_record


def test_secrets_index_contains_only_redacted_summary(tmp_path) -> None:
    service = _secret_service(tmp_path)
    plaintext = "sk-index-secret-value-5678"

    response = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Index key",
            plaintext=plaintext,
        ),
        created_by="default_user",
    )

    index = service.json_store.read_json(secrets_index_key("default"))
    summary = index["secrets"][0]

    assert summary.items() >= {
        "secret_id": response.secret_id,
        "secret_ref": response.secret_ref,
        "type": "model_api_key",
        "display_name": "Index key",
        "masked": "sk-****5678",
        "status": "active",
        "last_used_at": None,
        "updated_at": response.updated_at,
    }.items()
    assert summary["object_key"] == secret_object_key("default", response.secret_id)
    forbidden_text = json.dumps(index, ensure_ascii=False)
    assert plaintext not in forbidden_text
    assert "encrypted_value" not in forbidden_text
    assert "ciphertext" not in forbidden_text
    assert "nonce" not in forbidden_text
    assert "tag" not in forbidden_text


def test_secret_resolver_decrypts_active_secret_and_updates_usage(tmp_path) -> None:
    service = _secret_service(tmp_path)
    response = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Resolver key",
            plaintext="sk-resolver-secret-value",
        ),
        created_by="default_user",
    )
    resolver = SecretResolver(service, service.master_key_provider)

    resolved = resolver.resolve(
        "default",
        response.secret_id,
        "model_call",
        caller="llm_connector",
    )

    assert resolved.secret_id == response.secret_id
    assert resolved.plaintext == "sk-resolver-secret-value"
    record = service.get_secret_record("default", response.secret_id)
    assert record.last_used_at is not None
    assert record.revision == 2


def test_secret_resolver_rejects_disabled_and_wrong_purpose(tmp_path) -> None:
    service = _secret_service(tmp_path)
    disabled = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Disabled key",
            plaintext="sk-disabled-secret",
        ),
        created_by="default_user",
    )
    wrong_purpose = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Wrong purpose key",
            plaintext="sk-wrong-purpose-secret",
        ),
        created_by="default_user",
    )
    service.disable_secret("default", disabled.secret_id)
    resolver = SecretResolver(service, service.master_key_provider)

    with pytest.raises(SecretUnavailableError):
        resolver.resolve(
            "default",
            disabled.secret_id,
            "model_call",
            caller="llm_connector",
        )
    with pytest.raises(SecretPurposeDeniedError):
        resolver.resolve(
            "default",
            wrong_purpose.secret_id,
            "minio_connect",
            caller="minio_connector",
        )


def test_secret_resolver_rejects_unauthorized_caller(tmp_path) -> None:
    service = _secret_service(tmp_path)
    response = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Caller key",
            plaintext="sk-caller-secret",
        ),
        created_by="default_user",
    )
    resolver = SecretResolver(service, service.master_key_provider)

    with pytest.raises(SecretCallerDeniedError):
        resolver.resolve(
            "default",
            response.secret_id,
            "model_call",
            caller="tool_registry",
        )


def test_secret_resolver_allows_neo4j_readonly_query_adapter(tmp_path) -> None:
    service = _secret_service(tmp_path)
    response = service.create_secret(
        "default",
        CreateSecretRequest(
            type="neo4j_username_password",
            display_name="Neo4j readonly credential",
            plaintext='{"username":"neo4j","password":"secret"}',
        ),
        created_by="default_user",
    )
    resolver = SecretResolver(service, service.master_key_provider)

    resolved = resolver.resolve(
        "default",
        response.secret_id,
        "neo4j_connect",
        caller="neo4j_readonly_query_adapter",
    )

    assert resolved.secret_id == response.secret_id
    assert '"password":"secret"' in resolved.plaintext


def test_rotate_secret_changes_revision_and_resolved_value(tmp_path) -> None:
    from app.schemas.secret import RotateSecretRequest

    service = _secret_service(tmp_path)
    response = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Rotated key",
            plaintext="sk-original-secret",
        ),
        created_by="default_user",
    )
    original_record = service.get_secret_record("default", response.secret_id)

    rotated = service.rotate_secret(
        "default",
        response.secret_id,
        RotateSecretRequest(plaintext="sk-rotated-secret"),
    )
    resolved = SecretResolver(service, service.master_key_provider).resolve(
        "default",
        response.secret_id,
        "model_call",
        caller="llm_connector",
    )

    assert rotated.secret_id == response.secret_id
    assert rotated.masked == "sk-****cret"
    assert (
        service.get_secret_record("default", response.secret_id).revision
        > original_record.revision
    )
    assert resolved.plaintext == "sk-rotated-secret"


def test_soft_deleted_secret_is_hidden_from_list(tmp_path) -> None:
    service = _secret_service(tmp_path)
    deleted = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Deleted key",
            plaintext="sk-deleted-secret",
        ),
        created_by="default_user",
    )
    visible = service.create_secret(
        "default",
        CreateSecretRequest(
            type="embedding_api_key",
            display_name="Visible key",
            plaintext="emb-visible-secret",
        ),
        created_by="default_user",
    )

    service.delete_secret("default", deleted.secret_id)

    listed_ids = {secret.secret_id for secret in service.list_secrets("default").secrets}
    assert visible.secret_id in listed_ids
    assert deleted.secret_id not in listed_ids


def test_secret_references_scan_model_database_and_mcp_configs(tmp_path) -> None:
    service = _secret_service(tmp_path)
    response = service.create_secret(
        "default",
        CreateSecretRequest(
            type="model_api_key",
            display_name="Referenced key",
            plaintext="sk-referenced-secret",
        ),
        created_by="default_user",
    )
    service.json_store.write_json(
        model_config_key("default", "main_chat"),
        {
            "config_id": "main_chat",
            "api_key_ref": response.secret_ref,
        },
    )
    service.json_store.write_json(
        database_config_key("default"),
        {
            "targets": [
                {
                    "target": "minio",
                    "credential_refs": {"access_key": f"secret_ref://{response.secret_id}"},
                }
            ]
        },
    )
    service.json_store.write_json(
        mcp_server_manifest_key("default", "filesystem"),
        {
            "server_name": "filesystem",
            "config": {"headers_ref": response.secret_id},
        },
    )

    references = service.list_references("default", response.secret_id)

    assert {
        (item["object_type"], item["object_id"], item["field"])
        for item in references
    } == {
        ("database_config", "database", "targets.0.credential_refs.access_key"),
        ("mcp_server", "filesystem", "config.headers_ref"),
        ("model_config", "main_chat", "api_key_ref"),
    }
    with pytest.raises(AgentSystemError) as exc_info:
        service.delete_secret("default", response.secret_id)
    assert exc_info.value.error_type == "secret_still_referenced"
