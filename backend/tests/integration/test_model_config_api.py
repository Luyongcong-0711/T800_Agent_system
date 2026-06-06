from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_conversation_service, get_object_store
from app.main import app
from app.secret_store.crypto import generate_master_key
from app.storage.local_object_store import LocalObjectStore

SENSITIVE_TERMS = [
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "password",
    "sk-test-secret",
    "tp-test-secret",
]


@pytest.fixture()
def object_store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture()
def client(
    object_store: LocalObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("AGENT_MASTER_KEY", generate_master_key())
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


def test_model_config_defaults_include_all_p0_slots(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("DEFAULT_MODEL_NAME", "mimo-v2.5-pro")
    monkeypatch.setenv("DEFAULT_MODEL_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
    monkeypatch.setenv("DEFAULT_MODEL_API_KEY_REF", "secret_main_model")

    response = client.get("/workspaces/default/model-configs")

    assert response.status_code == 200
    body = response.json()
    configs = {item["config_id"]: item for item in body["configs"]}
    assert set(configs) == {
        "main_chat",
        "graphrag_llm",
        "embedding",
        "rerank",
        "compression",
        "fallback",
    }
    assert configs["main_chat"].items() >= {
        "provider": "openai_compatible",
        "model": "mimo-v2.5-pro",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "api_key_ref": "secret_main_model",
        "context_window_tokens": 200000,
        "max_output_tokens": 8192,
        "enabled": True,
        "source": "default_env",
    }.items()
    assert configs["graphrag_llm"]["model"] == "mimo-v2.5-pro"
    assert configs["embedding"].items() >= {
        "provider": "openai_compatible",
        "model": "text-embedding-v4",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_ref": None,
        "supports_tool_calling": False,
        "status": "missing_secret",
    }.items()
    assert configs["embedding"]["supports_tool_calling"] is False
    _assert_no_secret_material(body)


def test_model_config_can_save_without_plaintext_secret(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    secret_response = client.post(
        "/workspaces/default/secrets",
        json={
            "type": "model_api_key",
            "display_name": "Main model key",
            "plaintext": "tp-test-secret",
        },
    )
    assert secret_response.status_code == 200
    secret_id = secret_response.json()["secret_id"]
    payload = {
        "provider": "openai_compatible",
        "model": "mimo-v2.5-pro",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "api_key_ref": f"secret_ref://{secret_id}",
        "context_window_tokens": 200000,
        "max_output_tokens": 8192,
        "timeout_ms": 60000,
        "supports_tool_calling": True,
        "enabled": True,
    }

    response = client.put("/workspaces/default/model-configs/main_chat", json=payload)
    read_back = client.get("/workspaces/default/model-configs/main_chat")

    assert response.status_code == 200
    assert read_back.status_code == 200
    assert response.json()["revision"] == 2
    assert response.json()["source"] == "stored"
    assert read_back.json()["source"] == "stored"
    assert read_back.json()["provider"] == "openai_compatible"
    assert read_back.json()["api_key_ref"] == secret_id
    assert any(
        key.endswith("model_configs/main_chat.json")
        for key in object_store.list_keys("")
    )
    _assert_no_secret_material([response.json(), read_back.json()])


def test_model_config_rejects_unsupported_provider(client: TestClient) -> None:
    response = client.put(
        "/workspaces/default/model-configs/main_chat",
        json={
            "provider": "fake",
            "model": "fake-runtime-smoke",
            "base_url": "https://example.invalid",
            "api_key_ref": "secret_fake",
        },
    )

    assert response.status_code == 422
    _assert_no_secret_material(response.json())


def test_model_config_rejects_output_tokens_larger_than_context(
    client: TestClient,
) -> None:
    response = client.put(
        "/workspaces/default/model-configs/main_chat",
        json={
            "provider": "openai_compatible",
            "model": "mimo-v2.5-pro",
            "base_url": "https://example.invalid/v1",
            "api_key_ref": "secret_main",
            "context_window_tokens": 1024,
            "max_output_tokens": 2048,
            "timeout_ms": 60000,
            "supports_tool_calling": True,
            "enabled": True,
        },
    )

    assert response.status_code == 422
    assert response.json()["error_type"] == "validation_failed"
    _assert_no_secret_material(response.json())


def test_model_config_rejects_missing_or_wrong_type_secret_ref(
    client: TestClient,
) -> None:
    missing_response = client.put(
        "/workspaces/default/model-configs/main_chat",
        json={
            "provider": "openai_compatible",
            "model": "mimo-v2.5-pro",
            "base_url": "https://example.invalid/v1",
            "api_key_ref": "secret_missing",
            "context_window_tokens": 200000,
            "max_output_tokens": 8192,
            "timeout_ms": 60000,
            "supports_tool_calling": True,
            "enabled": True,
        },
    )

    assert missing_response.status_code == 400
    assert missing_response.json()["error_type"] == "model_config_secret_ref_invalid"
    assert missing_response.json()["details"]["reason"] == "secret_not_found"

    embedding_secret = client.post(
        "/workspaces/default/secrets",
        json={
            "type": "embedding_api_key",
            "display_name": "Embedding key",
            "plaintext": "tp-test-secret",
        },
    )
    assert embedding_secret.status_code == 200
    wrong_type_response = client.put(
        "/workspaces/default/model-configs/main_chat",
        json={
            "provider": "openai_compatible",
            "model": "mimo-v2.5-pro",
            "base_url": "https://example.invalid/v1",
            "api_key_ref": embedding_secret.json()["secret_id"],
            "context_window_tokens": 200000,
            "max_output_tokens": 8192,
            "timeout_ms": 60000,
            "supports_tool_calling": True,
            "enabled": True,
        },
    )

    assert wrong_type_response.status_code == 400
    assert wrong_type_response.json()["error_type"] == "model_config_secret_ref_invalid"
    assert wrong_type_response.json()["details"]["reason"] == "secret_type_not_allowed"
    _assert_no_secret_material([missing_response.json(), wrong_type_response.json()])


def test_saved_main_chat_config_is_used_by_conversation_runtime(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    secret_response = client.post(
        "/workspaces/default/secrets",
        json={
            "type": "model_api_key",
            "display_name": "Anthropic key",
            "plaintext": "tp-test-secret",
        },
    )
    assert secret_response.status_code == 200
    secret_id = secret_response.json()["secret_id"]
    response = client.put(
        "/workspaces/default/model-configs/main_chat",
        json={
            "provider": "anthropic",
            "model": "claude-test",
            "base_url": "https://anthropic.example.invalid",
            "api_key_ref": secret_id,
            "context_window_tokens": 200000,
            "max_output_tokens": 4096,
            "timeout_ms": 45000,
            "supports_tool_calling": True,
            "enabled": True,
        },
    )
    assert response.status_code == 200

    service = get_conversation_service(object_store, workspace_id="default")
    runner = service.runtime_runner

    assert runner.model_config.provider == "anthropic"
    assert runner.model_config.model == "claude-test"
    assert runner.model_config.base_url == "https://anthropic.example.invalid"
    assert runner.model_config.api_key_ref == secret_id
    assert runner.model_config.context_window_tokens == 200000
    assert runner.model_config.max_output_tokens == 4096
    assert runner.llm_connector.secret_resolver is not None


def test_model_config_test_call_resolves_secret_and_redacts_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_response = client.post(
        "/workspaces/default/secrets",
        json={
            "type": "model_api_key",
            "display_name": "Main model key",
            "plaintext": "tp-test-secret",
        },
    )
    assert secret_response.status_code == 200
    secret_id = secret_response.json()["secret_id"]

    config_response = client.put(
        "/workspaces/default/model-configs/main_chat",
        json={
            "provider": "openai_compatible",
            "model": "mimo-v2.5-pro",
            "base_url": "https://example.invalid/v1",
            "api_key_ref": secret_id,
            "context_window_tokens": 200000,
            "max_output_tokens": 8192,
            "timeout_ms": 60000,
            "supports_tool_calling": True,
            "enabled": True,
        },
    )
    assert config_response.status_code == 200

    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any):
        captured.update({"url": url, **kwargs})

        class Response:
            status_code = 200
            text = "{}"

            @staticmethod
            def json() -> dict[str, Any]:
                return {
                    "choices": [{"message": {"content": "pong"}}],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                }

            @staticmethod
            def raise_for_status() -> None:
                return None

        return Response()

    monkeypatch.setattr("app.model_connector.providers.httpx.post", fake_post)

    response = client.post(
        "/workspaces/default/model-configs/main_chat/test",
        json={"prompt": "ping", "max_output_tokens": 16},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["content_preview"] == "pong"
    assert body["usage"]["total_tokens"] == 3
    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer tp-test-secret"
    _assert_no_secret_material(body)


def test_embedding_config_test_uses_embeddings_endpoint_and_secret_type(
    client: TestClient,
    object_store: LocalObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_response = client.post(
        "/workspaces/default/secrets",
        json={
            "type": "embedding_api_key",
            "display_name": "Embedding model key",
            "plaintext": "tp-test-secret",
        },
    )
    assert secret_response.status_code == 200
    secret_id = secret_response.json()["secret_id"]

    config_response = client.put(
        "/workspaces/default/model-configs/embedding",
        json={
            "provider": "openai_compatible",
            "model": "text-embedding-v4",
            "base_url": "https://example.invalid/v1",
            "api_key_ref": secret_id,
            "context_window_tokens": 200000,
            "max_output_tokens": 8192,
            "timeout_ms": 60000,
            "supports_tool_calling": True,
            "enabled": True,
        },
    )
    assert config_response.status_code == 200
    assert config_response.json()["supports_tool_calling"] is False

    captured: dict[str, Any] = {}

    class FakeEmbeddingHttpClient:
        def post(self, url: str, **kwargs: Any):
            captured.update({"url": url, **kwargs})

            class Response:
                status_code = 200
                text = "{}"

                @staticmethod
                def json() -> dict[str, Any]:
                    return {
                        "data": [
                            {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                        ],
                    }

                @staticmethod
                def raise_for_status() -> None:
                    return None

            return Response()

    monkeypatch.setattr(
        "app.embedding.client.httpx.Client",
        lambda: FakeEmbeddingHttpClient(),
    )

    response = client.post(
        "/workspaces/default/model-configs/embedding/test",
        json={"prompt": "embedding smoke", "max_output_tokens": 16},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["content_preview"] == "embedding_dimension=3"
    assert body["usage"]["embedding_dimensions"] == 3
    assert captured["url"] == "https://example.invalid/v1/embeddings"
    assert captured["headers"]["Authorization"] == "Bearer tp-test-secret"
    assert captured["json"] == {
        "dimensions": 1024,
        "input": ["embedding smoke"],
        "model": "text-embedding-v4",
    }
    assert any(
        key.endswith("model_configs/embedding.json")
        for key in object_store.list_keys("")
    )
    _assert_no_secret_material(body)


def test_model_config_test_call_uses_public_default_not_fake_provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "fake")
    monkeypatch.setenv("DEFAULT_MODEL_NAME", "fake-runtime-smoke")
    monkeypatch.delenv("DEFAULT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL_API_KEY_REF", raising=False)

    response = client.post(
        "/workspaces/default/model-configs/main_chat/test",
        json={"prompt": "ping", "max_output_tokens": 16},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["provider"] == "openai_compatible"
    assert body["model"] == "mimo-v2.5-pro"
    assert body["error_type"] == "auth_failed"
    _assert_no_secret_material(body)


def test_incomplete_default_model_config_is_not_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("DEFAULT_MODEL_NAME", "mimo-v2.5-pro")
    monkeypatch.delenv("DEFAULT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("DEFAULT_MODEL_API_KEY_REF", raising=False)

    config_response = client.get("/workspaces/default/model-configs/main_chat")
    test_response = client.post(
        "/workspaces/default/model-configs/main_chat/test",
        json={"prompt": "ping", "max_output_tokens": 16},
    )

    assert config_response.status_code == 200
    assert config_response.json()["status"] == "missing_secret"
    assert test_response.status_code == 200
    assert test_response.json()["ok"] is False
    assert test_response.json()["error_type"] == "auth_failed"
    _assert_no_secret_material([config_response.json(), test_response.json()])


def test_model_config_test_call_accepts_unsaved_override_without_persisting(
    client: TestClient,
    object_store: LocalObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_response = client.post(
        "/workspaces/default/secrets",
        json={
            "type": "model_api_key",
            "display_name": "Temporary model key",
            "plaintext": "tp-test-secret",
        },
    )
    assert secret_response.status_code == 200
    secret_id = secret_response.json()["secret_id"]

    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any):
        captured.update({"url": url, **kwargs})

        class Response:
            status_code = 200
            text = "{}"

            @staticmethod
            def json() -> dict[str, Any]:
                return {
                    "choices": [{"message": {"content": "pong"}}],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    },
                }

            @staticmethod
            def raise_for_status() -> None:
                return None

        return Response()

    monkeypatch.setattr("app.model_connector.providers.httpx.post", fake_post)

    response = client.post(
        "/workspaces/default/model-configs/main_chat/test",
        json={
            "prompt": "ping",
            "max_output_tokens": 16,
            "config": {
                "provider": "openai_compatible",
                "model": "mimo-v2.5-pro",
                "base_url": "https://example.invalid/v1",
                "api_key_ref": secret_id,
                "context_window_tokens": 200000,
                "max_output_tokens": 8192,
                "timeout_ms": 60000,
                "supports_tool_calling": True,
                "enabled": True,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert captured["json"]["model"] == "mimo-v2.5-pro"
    assert not any(
        key.endswith("model_configs/main_chat.json")
        for key in object_store.list_keys("")
    )
    _assert_no_secret_material(response.json())
