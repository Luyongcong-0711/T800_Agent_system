from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_object_store
from app.core.settings import Settings
from app.database import service as database_service_module
from app.database.service import DatabaseConfigService
from app.main import app
from app.schemas.database import DatabaseTargetConfig, UpdateDatabaseConfigRequest
from app.schemas.health import ServiceHealth
from app.schemas.secret import CreateSecretRequest
from app.secret_store.crypto import generate_master_key
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_resolver import SecretResolver
from app.secret_store.secret_service import SecretService
from app.storage.local_object_store import LocalObjectStore

SENSITIVE_TERMS = [
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "password",
    "sk-test-secret",
    "raw-token-value",
]


@pytest.fixture()
def object_store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture()
def client(object_store: LocalObjectStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()


def _assert_no_secret_material(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str).lower()
    for term in SENSITIVE_TERMS:
        assert term not in serialized


def _create_secret(
    object_store: LocalObjectStore,
    *,
    secret_id: str,
    secret_type: str,
    plaintext: str = "secret-value-for-test",
) -> None:
    service = SecretService(
        object_store,
        MasterKeyProvider(Settings(agent_master_key=generate_master_key())),
    )
    service.ensure_static_secret(
        "default",
        secret_id=secret_id,
        request=CreateSecretRequest(
            type=secret_type,  # type: ignore[arg-type]
            display_name=secret_id,
            plaintext=plaintext,
        ),
        created_by="default_user",
    )


def test_database_config_defaults_are_local_and_cache_only(
    client: TestClient,
) -> None:
    response = client.get("/workspaces/default/database/config")

    assert response.status_code == 200
    body = response.json()
    targets = {target["target"]: target for target in body["targets"]}
    assert set(targets) == {"minio", "milvus", "neo4j", "redis"}
    assert all(target["mode"] == "local" for target in targets.values())
    assert targets["redis"]["options"]["role"] == "cache_only"
    _assert_no_secret_material(body)


def test_database_config_can_save_remote_targets_without_plaintext_secrets(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    for secret_id, secret_type in [
        ("minio-primary", "minio_access_key"),
        ("milvus-primary", "milvus_token"),
        ("neo4j-primary", "neo4j_username_password"),
    ]:
        _create_secret(object_store, secret_id=secret_id, secret_type=secret_type)

    payload = {
        "targets": [
            {
                "target": "minio",
                "mode": "remote",
                "enabled": True,
                "endpoint": "https://minio.example.com",
                "tls": True,
                "bucket": "agent-system-prod",
                "credential_refs": {"primary": "secret_ref://minio-primary"},
                "options": {},
            },
            {
                "target": "milvus",
                "mode": "remote",
                "enabled": True,
                "endpoint": "https://milvus.example.com",
                "tls": True,
                "credential_refs": {"primary": "secret_ref://milvus-primary"},
                "options": {},
            },
            {
                "target": "neo4j",
                "mode": "remote",
                "enabled": True,
                "endpoint": "neo4j+s://neo4j.example.com:7687",
                "tls": True,
                "credential_refs": {"primary": "secret_ref://neo4j-primary"},
                "options": {"http_url": "https://neo4j.example.com"},
            },
            {
                "target": "redis",
                "mode": "remote",
                "enabled": True,
                "endpoint": "rediss://redis.example.com:6379/0",
                "tls": True,
                "credential_refs": {},
                "options": {"role": "cache_only"},
            },
        ]
    }

    response = client.put("/workspaces/default/database/config", json=payload)
    read_back = client.get("/workspaces/default/database/config")

    assert response.status_code == 200
    assert read_back.status_code == 200
    assert response.json()["revision"] == 2
    assert read_back.json()["targets"][0]["mode"] == "remote"
    assert any(key.endswith("database/config.json") for key in object_store.list_keys(""))
    _assert_no_secret_material([response.json(), read_back.json()])


def test_database_config_rejects_plaintext_credential_refs(
    client: TestClient,
) -> None:
    payload = {
        "targets": [
            {
                "target": "minio",
                "mode": "remote",
                "enabled": True,
                "endpoint": "https://minio.example.com",
                "tls": True,
                "bucket": "agent-system-prod",
                "credential_refs": {"primary": "raw-token-value"},
                "options": {},
            }
        ]
    }

    response = client.put("/workspaces/default/database/config", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["error_type"] == "validation_failed"
    _assert_no_secret_material(body)


def test_database_config_rejects_missing_secret_refs(client: TestClient) -> None:
    payload = {
        "targets": [
            {
                "target": "milvus",
                "mode": "remote",
                "enabled": True,
                "endpoint": "https://milvus.example.com",
                "tls": True,
                "credential_refs": {"primary": "secret_ref://missing-milvus"},
                "options": {},
            }
        ]
    }

    response = client.put("/workspaces/default/database/config", json=payload)

    assert response.status_code == 400
    body = response.json()
    assert body["error_type"] == "database_secret_ref_invalid"
    assert body["details"]["invalid_refs"][0]["reason"] == "secret_not_found"
    _assert_no_secret_material(body)


def test_database_config_forces_redis_cache_only_role(
    client: TestClient,
) -> None:
    payload = {
        "targets": [
            {
                "target": "redis",
                "mode": "remote",
                "enabled": True,
                "endpoint": "rediss://redis.example.com:6379/0",
                "tls": True,
                "credential_refs": {},
                "options": {"role": "queue"},
            }
        ]
    }

    response = client.put("/workspaces/default/database/config", json=payload)
    read_back = client.get("/workspaces/default/database/config")

    assert response.status_code == 200
    assert read_back.status_code == 200
    redis_target = next(
        target for target in read_back.json()["targets"] if target["target"] == "redis"
    )
    assert redis_target["options"]["role"] == "cache_only"


def test_database_health_get_returns_unknown_without_snapshot(client: TestClient) -> None:
    response = client.get("/workspaces/default/database/health")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "unknown"
    assert {service["target"] for service in body["services"]} == {
        "minio",
        "milvus",
        "neo4j",
        "redis",
    }
    assert all(service["status"] == "unknown" for service in body["services"])
    _assert_no_secret_material(body)


def test_database_health_check_writes_latest_snapshot(
    client: TestClient,
    object_store: LocalObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_check_database_services(_settings):
        return [
            ServiceHealth(
                target="minio",
                status="healthy",
                latency_ms=1,
                message="ok",
                checked_at="2026-05-30T00:00:00+00:00",
            ),
            ServiceHealth(
                target="milvus",
                status="healthy",
                latency_ms=1,
                message="ok",
                checked_at="2026-05-30T00:00:00+00:00",
            ),
            ServiceHealth(
                target="neo4j",
                status="healthy",
                latency_ms=1,
                message="ok",
                checked_at="2026-05-30T00:00:00+00:00",
            ),
            ServiceHealth(
                target="redis",
                status="healthy",
                latency_ms=1,
                message="ok",
                checked_at="2026-05-30T00:00:00+00:00",
            ),
        ]

    monkeypatch.setattr(
        "app.database.service.check_database_services",
        fake_check_database_services,
    )

    response = client.post("/workspaces/default/database/health/check")
    latest = client.get("/workspaces/default/database/health")

    assert response.status_code == 200
    assert latest.status_code == 200
    assert response.json()["ok"] is True
    assert latest.json()["source"] == "live_check"
    assert any(key.endswith("database/health/latest.json") for key in object_store.list_keys(""))
    _assert_no_secret_material([response.json(), latest.json()])


def test_database_health_check_resolves_configured_runtime_credentials(
    object_store: LocalObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(agent_master_key=generate_master_key())
    master_key_provider = MasterKeyProvider(settings)
    secret_service = SecretService(object_store, master_key_provider)
    secret_resolver = SecretResolver(secret_service, master_key_provider)
    for secret_id, secret_type, plaintext in [
        ("milvus-token", "milvus_token", "milvus-runtime-token"),
        ("neo4j-login", "neo4j_username_password", "neo4j:runtime-password"),
    ]:
        secret_service.ensure_static_secret(
            "default",
            secret_id=secret_id,
            request=CreateSecretRequest(
                type=secret_type,  # type: ignore[arg-type]
                display_name=secret_id,
                plaintext=plaintext,
            ),
            created_by="default_user",
        )
    service = DatabaseConfigService(
        object_store,
        settings,
        secret_service=secret_service,
        secret_resolver=secret_resolver,
    )
    service.update_config(
        "default",
        UpdateDatabaseConfigRequest(
            targets=[
                DatabaseTargetConfig(
                    target="milvus",
                    mode="remote",
                    enabled=True,
                    endpoint="https://milvus.example.com",
                    tls=True,
                    credential_refs={"primary": "secret_ref://milvus-token"},
                ),
                DatabaseTargetConfig(
                    target="neo4j",
                    mode="remote",
                    enabled=True,
                    endpoint="neo4j+s://neo4j.example.com:7687",
                    tls=True,
                    credential_refs={"primary": "secret_ref://neo4j-login"},
                    options={"http_url": "https://neo4j.example.com"},
                ),
            ],
        ),
    )
    captured: dict[str, str | None] = {}

    def fake_check_database_services_sync(runtime_settings):
        captured["milvus_token"] = runtime_settings.milvus_token
        captured["neo4j_username_password"] = runtime_settings.neo4j_username_password
        return [
            ServiceHealth(
                target=target,
                status="healthy",
                latency_ms=1,
                message="ok",
                checked_at="2026-05-30T00:00:00+00:00",
            )
            for target in ("minio", "milvus", "neo4j", "redis")
        ]

    monkeypatch.setattr(
        database_service_module,
        "check_database_services_sync",
        fake_check_database_services_sync,
    )

    snapshot = service.run_health_check_sync("default")

    assert snapshot["ok"] is True
    assert captured == {
        "milvus_token": "milvus-runtime-token",
        "neo4j_username_password": "neo4j:runtime-password",
    }
    _assert_no_secret_material(snapshot)
