from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from app.api.dependencies import get_secret_service
from app.core.settings import Settings
from app.main import app
from app.secret_store.crypto import generate_master_key
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_service import SecretService
from app.storage.local_object_store import LocalObjectStore
from app.storage.path_builder import database_config_key, model_config_key

FORBIDDEN_SECRET_FIELDS = {"plaintext", "ciphertext", "nonce", "tag"}


def _contains_forbidden_secret_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key in FORBIDDEN_SECRET_FIELDS or _contains_forbidden_secret_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_secret_field(item) for item in value)
    return False


def _secret_test_client(tmp_path) -> tuple[TestClient, SecretService]:
    service = SecretService(
        LocalObjectStore(tmp_path / "objects"),
        MasterKeyProvider(Settings(agent_master_key=generate_master_key())),
    )
    app.dependency_overrides[get_secret_service] = lambda: service
    return TestClient(app), service


def test_secret_api_responses_never_return_raw_secret_material(tmp_path) -> None:
    client, _ = _secret_test_client(tmp_path)
    try:
        create_response = client.post(
            "/workspaces/default/secrets",
            json={
                "type": "model_api_key",
                "display_name": "API key",
                "plaintext": "sk-api-secret-value-9012",
            },
        )
        assert create_response.status_code == 200
        created = create_response.json()
        secret_id = created["secret_id"]

        list_response = client.get("/workspaces/default/secrets")
        detail_response = client.get(f"/workspaces/default/secrets/{secret_id}")
        disable_response = client.post(f"/workspaces/default/secrets/{secret_id}/disable")

        for response in [create_response, list_response, detail_response, disable_response]:
            assert response.status_code == 200
            body = response.json()
            assert not _contains_forbidden_secret_field(body)
            assert "sk-api-secret-value-9012" not in json.dumps(body, ensure_ascii=False)
    finally:
        app.dependency_overrides.clear()


def test_secret_api_create_list_detail_rotate_delete_contract(tmp_path) -> None:
    client, _ = _secret_test_client(tmp_path)
    first_plaintext = "sk-api-contract-secret-1111"
    rotated_plaintext = "sk-api-contract-secret-2222"

    try:
        create_response = client.post(
            "/workspaces/default/secrets",
            json={
                "type": "model_api_key",
                "display_name": "Contract key",
                "plaintext": first_plaintext,
            },
        )
        assert create_response.status_code == 200
        created = create_response.json()
        secret_id = created["secret_id"]
        assert created["masked"] == "sk-****1111"

        list_response = client.get("/workspaces/default/secrets")
        detail_response = client.get(f"/workspaces/default/secrets/{secret_id}")
        rotate_response = client.post(
            f"/workspaces/default/secrets/{secret_id}/rotate",
            json={"plaintext": rotated_plaintext},
        )
        delete_response = client.delete(f"/workspaces/default/secrets/{secret_id}")
        list_after_delete_response = client.get("/workspaces/default/secrets")

        for response in [
            list_response,
            detail_response,
            rotate_response,
            delete_response,
            list_after_delete_response,
        ]:
            assert response.status_code == 200
            body_text = json.dumps(response.json(), ensure_ascii=False)
            assert first_plaintext not in body_text
            assert rotated_plaintext not in body_text
            assert not _contains_forbidden_secret_field(response.json())

        assert detail_response.json()["secret_id"] == secret_id
        assert rotate_response.json()["masked"] == "sk-****2222"
        assert delete_response.json()["status"] == "soft_deleted"
        listed_ids = {item["secret_id"] for item in list_after_delete_response.json()["secrets"]}
        assert secret_id not in listed_ids
    finally:
        app.dependency_overrides.clear()


def test_secret_api_validation_error_does_not_leak_plaintext_value(tmp_path) -> None:
    client, _ = _secret_test_client(tmp_path)
    leaked_candidate = "sk-validation-error-secret-value"

    try:
        response = client.post(
            "/workspaces/default/secrets",
            json={
                "type": "not_a_secret_type",
                "display_name": "Invalid key",
                "plaintext": leaked_candidate,
                "unexpected": leaked_candidate,
            },
        )

        assert response.status_code == 422
        body_text = json.dumps(response.json(), ensure_ascii=False)
        assert leaked_candidate not in body_text
    finally:
        app.dependency_overrides.clear()


def test_secret_references_scan_model_and_database_configs(tmp_path) -> None:
    client, service = _secret_test_client(tmp_path)

    try:
        created = client.post(
            "/workspaces/default/secrets",
            json={
                "type": "model_api_key",
                "display_name": "Referenced key",
                "plaintext": "sk-referenced-secret-1234",
            },
        ).json()
        secret_id = created["secret_id"]
        service.json_store.write_json(
            model_config_key("default", "main_chat"),
            {
                "schema_version": 1,
                "workspace_id": "default",
                "config_id": "main_chat",
                "api_key_ref": secret_id,
                "revision": 1,
            },
        )
        service.json_store.write_json(
            database_config_key("default"),
            {
                "schema_version": 1,
                "workspace_id": "default",
                "targets": [
                    {
                        "target": "milvus",
                        "credential_refs": {"primary": f"secret_ref://{secret_id}"},
                    }
                ],
                "revision": 1,
            },
        )

        response = client.get(f"/workspaces/default/secrets/{secret_id}/references")

        assert response.status_code == 200
        references = response.json()["references"]
        assert {
            (reference["object_type"], reference["object_id"], reference["field"])
            for reference in references
        } == {
            ("database_config", "database", "targets.0.credential_refs.primary"),
            ("model_config", "main_chat", "api_key_ref"),
        }
        body_text = json.dumps(response.json(), ensure_ascii=False)
        assert "sk-referenced-secret-1234" not in body_text
        assert not _contains_forbidden_secret_field(response.json())
    finally:
        app.dependency_overrides.clear()


def test_secret_delete_blocks_active_references_and_returns_reference_list(tmp_path) -> None:
    client, service = _secret_test_client(tmp_path)

    try:
        created = client.post(
            "/workspaces/default/secrets",
            json={
                "type": "model_api_key",
                "display_name": "Blocked key",
                "plaintext": "sk-blocked-secret-1234",
            },
        ).json()
        secret_id = created["secret_id"]
        service.json_store.write_json(
            model_config_key("default", "main_chat"),
            {
                "schema_version": 1,
                "workspace_id": "default",
                "config_id": "main_chat",
                "api_key_ref": secret_id,
                "revision": 1,
            },
        )

        delete_response = client.delete(f"/workspaces/default/secrets/{secret_id}")
        detail_response = client.get(f"/workspaces/default/secrets/{secret_id}")

        assert delete_response.status_code == 409
        body = delete_response.json()
        assert body["error_type"] == "secret_still_referenced"
        assert body["details"]["references"][0]["object_type"] == "model_config"
        assert detail_response.status_code == 200
        assert detail_response.json()["status"] == "active"
        assert "sk-blocked-secret-1234" not in json.dumps(body, ensure_ascii=False)
        assert not _contains_forbidden_secret_field(body)
    finally:
        app.dependency_overrides.clear()
