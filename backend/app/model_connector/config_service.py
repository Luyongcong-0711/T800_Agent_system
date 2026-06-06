from __future__ import annotations

from time import perf_counter
from typing import Any

from app.core.errors import AgentSystemError
from app.core.settings import Settings
from app.core.time import utc_now_iso
from app.embedding.client import OpenAICompatibleEmbeddingClient
from app.model_connector.connector import LLMConnector
from app.model_connector.providers import normalize_provider_exception
from app.schemas.model import ModelConfig, ModelError, ModelMessage, ModelRequest
from app.schemas.model_config import (
    ModelConfigId,
    TestModelConfigRequest,
    UpdateModelConfigRequest,
)
from app.secret_store.secret_resolver import (
    PURPOSE_ALLOWED_TYPES,
    SecretPurpose,
    SecretCallerDeniedError,
    SecretPurposeDeniedError,
    SecretUnavailableError,
)
from app.secret_store.secret_service import SecretNotFoundError, SecretService
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import model_config_key

CONFIG_ORDER: tuple[ModelConfigId, ...] = (
    "main_chat",
    "graphrag_llm",
    "embedding",
    "rerank",
    "compression",
    "fallback",
)

CONFIG_METADATA: dict[str, dict[str, str]] = {
    "main_chat": {"display_name": "Main chat model", "purpose": "chat"},
    "graphrag_llm": {"display_name": "GraphRAG LLM", "purpose": "chat"},
    "embedding": {"display_name": "Embedding model", "purpose": "embedding"},
    "rerank": {"display_name": "Rerank model", "purpose": "rerank"},
    "compression": {"display_name": "Compression model", "purpose": "compression"},
    "fallback": {"display_name": "Fallback model", "purpose": "fallback"},
}

PUBLIC_PROVIDERS = {"openai_compatible", "anthropic"}


MODEL_SECRET_PURPOSE_BY_CONFIG_ID: dict[str, SecretPurpose] = {
    "embedding": "embedding_call",
    "rerank": "rerank_call",
}


class ModelConfigService:
    def __init__(
        self,
        object_store: ObjectStore,
        settings: Settings,
        secret_service: SecretService | None = None,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.settings = settings
        self.secret_service = secret_service

    def list_configs(self, workspace_id: str) -> dict[str, Any]:
        return {
            "workspace_id": workspace_id,
            "configs": [self.get_config(workspace_id, config_id) for config_id in CONFIG_ORDER],
        }

    def get_config(self, workspace_id: str, config_id: str) -> dict[str, Any]:
        self._ensure_config_id(config_id)
        key = model_config_key(workspace_id, config_id)
        exists = self.object_store.exists(key)
        stored = self.json_store.read_json_or_default(
            key,
            self._default_config(workspace_id, config_id),
        )
        source = "stored" if exists else "default_env"
        return self._normalize_config(workspace_id, config_id, stored, source=source)

    def update_config(
        self,
        workspace_id: str,
        config_id: str,
        request: UpdateModelConfigRequest,
    ) -> dict[str, Any]:
        self._ensure_config_id(config_id)
        self._validate_slot_request(config_id, request)
        self._validate_api_key_ref(workspace_id, config_id, request)
        current = self.get_config(workspace_id, config_id)
        metadata = CONFIG_METADATA[config_id]
        payload = request.model_dump()
        if payload.get("api_key_ref"):
            payload["api_key_ref"] = _normalize_secret_ref(str(payload["api_key_ref"]))
        if config_id == "embedding":
            payload["supports_tool_calling"] = False
        next_config = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "config_id": config_id,
            "display_name": metadata["display_name"],
            "purpose": metadata["purpose"],
            **payload,
            "updated_at": utc_now_iso(),
            "revision": int(current.get("revision", 1)) + 1,
        }
        self.json_store.write_json(model_config_key(workspace_id, config_id), next_config)
        return self._normalize_config(workspace_id, config_id, next_config, source="stored")

    def get_runtime_model_config(
        self,
        workspace_id: str,
        config_id: str = "main_chat",
        fallback: ModelConfig | None = None,
    ) -> ModelConfig:
        self._ensure_config_id(config_id)
        key = model_config_key(workspace_id, config_id)
        if not self.object_store.exists(key):
            config = self.get_config(workspace_id, config_id)
            if config.get("status") != "configured" and fallback is not None:
                return fallback
            return self._runtime_config_from_public_config(config)
        config = self.get_config(workspace_id, config_id)
        return self._runtime_config_from_public_config(config)

    def test_config(
        self,
        workspace_id: str,
        config_id: str,
        request: TestModelConfigRequest,
        connector: LLMConnector,
    ) -> dict[str, Any]:
        self._ensure_config_id(config_id)
        started = perf_counter()
        if request.config is None:
            try:
                config = self.get_runtime_model_config(workspace_id, config_id)
            except ModelError as exc:
                public_config = self.get_config(workspace_id, config_id)
                return self._test_failure_response(
                    workspace_id=workspace_id,
                    config_id=config_id,
                    provider=str(public_config.get("provider") or "openai_compatible"),
                    model=str(public_config.get("model") or ""),
                    started=started,
                    error=exc,
                )
        else:
            self._validate_slot_request(config_id, request.config)
            config = self._runtime_config_from_request(config_id, request.config)
        if config_id == "embedding":
            return self._test_embedding_config(workspace_id, config_id, request, config, connector)
        try:
            result = connector.call(
                workspace_id=workspace_id,
                config=config,
                request=ModelRequest(
                    request_id=f"model_config_test_{config_id}",
                    messages=[ModelMessage(role="user", content=request.prompt)],
                    tools=[],
                    max_output_tokens=request.max_output_tokens,
                ),
            )
        except ModelError as exc:
            return {
                "workspace_id": workspace_id,
                "config_id": config_id,
                "ok": False,
                "provider": config.provider,
                "model": config.model,
                "latency_ms": int((perf_counter() - started) * 1000),
                "content_preview": None,
                "usage": None,
                "error_type": exc.error_type,
                "retryable": exc.retryable,
                "redacted": True,
            }
        return {
            "workspace_id": workspace_id,
            "config_id": config_id,
            "ok": True,
            "provider": config.provider,
            "model": config.model,
            "latency_ms": int((perf_counter() - started) * 1000),
            "content_preview": str(result.content or "")[:500],
            "usage": result.usage.model_dump(),
            "error_type": None,
            "retryable": False,
            "redacted": True,
        }

    @staticmethod
    def _test_failure_response(
        *,
        workspace_id: str,
        config_id: str,
        provider: str,
        model: str,
        started: float,
        error: ModelError,
    ) -> dict[str, Any]:
        return {
            "workspace_id": workspace_id,
            "config_id": config_id,
            "ok": False,
            "provider": provider if provider in PUBLIC_PROVIDERS else "openai_compatible",
            "model": model,
            "latency_ms": int((perf_counter() - started) * 1000),
            "content_preview": None,
            "usage": None,
            "error_type": error.error_type,
            "retryable": error.retryable,
            "redacted": True,
        }

    @staticmethod
    def _runtime_config_from_public_config(config: dict[str, Any]) -> ModelConfig:
        if config.get("status") != "configured":
            raise ModelError(
                "auth_failed",
                "Model config must include provider, model, base_url, and api_key_ref.",
            )
        return ModelConfig(
            config_id=config["config_id"],
            provider=config["provider"],
            model=config["model"],
            base_url=config["base_url"],
            api_key_ref=config["api_key_ref"],
            context_window_tokens=config["context_window_tokens"],
            max_output_tokens=config["max_output_tokens"],
            timeout_ms=config["timeout_ms"],
            supports_tool_calling=config["supports_tool_calling"],
        )

    @staticmethod
    def _runtime_config_from_request(
        config_id: str,
        request: UpdateModelConfigRequest,
    ) -> ModelConfig:
        payload = request.model_dump(exclude={"enabled"})
        if payload.get("api_key_ref"):
            payload["api_key_ref"] = _normalize_secret_ref(str(payload["api_key_ref"]))
        if config_id == "embedding":
            payload["supports_tool_calling"] = False
        return ModelConfig(config_id=config_id, **payload)

    def _default_config(self, workspace_id: str, config_id: str) -> dict[str, Any]:
        metadata = CONFIG_METADATA[config_id]
        provider = self._default_provider(config_id)
        return {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "config_id": config_id,
            "display_name": metadata["display_name"],
            "purpose": metadata["purpose"],
            "provider": provider,
            "model": self._default_model(config_id),
            "base_url": self._default_base_url(config_id),
            "api_key_ref": self._default_api_key_ref(config_id),
            "context_window_tokens": self.settings.default_model_context_window_tokens,
            "max_output_tokens": self.settings.default_model_max_output_tokens,
            "timeout_ms": self.settings.default_model_timeout_ms,
            "supports_tool_calling": config_id != "embedding",
            "enabled": True,
            "updated_at": utc_now_iso(),
            "revision": 1,
        }

    def _normalize_config(
        self,
        workspace_id: str,
        config_id: str,
        value: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        metadata = CONFIG_METADATA[config_id]
        raw_provider = str(value.get("provider") or "openai_compatible")
        provider = raw_provider if raw_provider in PUBLIC_PROVIDERS else "openai_compatible"
        model = value.get("model") or ""
        base_url = value.get("base_url")
        api_key_ref = value.get("api_key_ref")
        provider_supported = raw_provider in PUBLIC_PROVIDERS and (
            config_id != "embedding" or raw_provider == "openai_compatible"
        )
        enabled = bool(value.get("enabled", True))
        if not enabled:
            status = "disabled"
        elif not provider_supported or not model or not base_url or not api_key_ref:
            status = "missing_secret"
        else:
            status = "configured"
        return {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "config_id": config_id,
            "display_name": metadata["display_name"],
            "purpose": metadata["purpose"],
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key_ref": api_key_ref,
            "context_window_tokens": int(value.get("context_window_tokens") or 200000),
            "max_output_tokens": int(value.get("max_output_tokens") or 8192),
            "timeout_ms": int(value.get("timeout_ms") or 60000),
            "supports_tool_calling": bool(value.get("supports_tool_calling", True)),
            "enabled": enabled,
            "status": status,
            "source": source,
            "updated_at": value.get("updated_at") or utc_now_iso(),
            "revision": int(value.get("revision", 1)),
        }

    def _test_embedding_config(
        self,
        workspace_id: str,
        config_id: str,
        request: TestModelConfigRequest,
        config: ModelConfig,
        connector: LLMConnector,
    ) -> dict[str, Any]:
        started = perf_counter()
        try:
            if config.provider != "openai_compatible":
                raise ModelError(
                    "unsupported_feature",
                    "Embedding config must use openai_compatible provider.",
                )
            if not config.model or not config.base_url:
                raise ModelError(
                    "invalid_request",
                    "Embedding model and base_url are required.",
                )
            if not config.api_key_ref:
                raise ModelError("auth_failed", "Embedding api_key_ref is required.")
            if connector.secret_resolver is None:
                raise ModelError("auth_failed", "Secret resolver is not configured.")
            resolved = connector.secret_resolver.resolve(
                workspace_id=workspace_id,
                secret_ref=config.api_key_ref,
                purpose="embedding_call",
                caller="embedding_connector",
            )
            vector = OpenAICompatibleEmbeddingClient(
                base_url=config.base_url,
                api_key=resolved.plaintext,
                timeout_ms=config.timeout_ms,
            ).embed_query(
                text=request.prompt,
                model=config.model,
                dimension=self.settings.default_embedding_dimension,
            )
        except ModelError as exc:
            error = exc
        except (
            SecretCallerDeniedError,
            SecretPurposeDeniedError,
            SecretUnavailableError,
        ):
            error = ModelError(
                "auth_failed",
                "Embedding secret could not be resolved for embedding_call.",
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary is normalized.
            error = normalize_provider_exception(exc)
        else:
            estimated_input_tokens = max(1, len(request.prompt.split()))
            return {
                "workspace_id": workspace_id,
                "config_id": config_id,
                "ok": True,
                "provider": config.provider,
                "model": config.model,
                "latency_ms": int((perf_counter() - started) * 1000),
                "content_preview": f"embedding_dimension={len(vector)}",
                "usage": {
                    "input_tokens": estimated_input_tokens,
                    "output_tokens": 0,
                    "total_tokens": estimated_input_tokens,
                    "usage_estimated": True,
                    "embedding_dimensions": len(vector),
                },
                "error_type": None,
                "retryable": False,
                "redacted": True,
            }
        return {
            "workspace_id": workspace_id,
            "config_id": config_id,
            "ok": False,
            "provider": config.provider,
            "model": config.model,
            "latency_ms": int((perf_counter() - started) * 1000),
            "content_preview": None,
            "usage": None,
            "error_type": error.error_type,
            "retryable": error.retryable,
            "redacted": True,
        }

    def _default_provider(self, config_id: str) -> str:
        if config_id in {"embedding", "rerank"}:
            return "openai_compatible"
        if self.settings.default_model_provider in PUBLIC_PROVIDERS:
            return self.settings.default_model_provider
        return "openai_compatible"

    def _default_model(self, config_id: str) -> str:
        if config_id == "embedding":
            return self.settings.default_embedding_model_name
        if config_id == "rerank":
            return ""
        if self.settings.default_model_provider in PUBLIC_PROVIDERS:
            return self.settings.default_model_name
        return "mimo-v2.5-pro" if config_id == "main_chat" else ""

    def _default_base_url(self, config_id: str) -> str | None:
        if config_id == "embedding":
            return self.settings.default_embedding_base_url
        return self.settings.default_model_base_url

    def _default_api_key_ref(self, config_id: str) -> str | None:
        if config_id == "embedding":
            return self.settings.default_embedding_api_key_ref
        return self.settings.default_model_api_key_ref

    @staticmethod
    def _validate_slot_request(config_id: str, request: UpdateModelConfigRequest) -> None:
        if config_id == "embedding" and request.provider != "openai_compatible":
            raise AgentSystemError(
                "unsupported_model_provider",
                "Embedding config must use an OpenAI-compatible provider.",
                status_code=400,
                retryable=False,
            )

    def _validate_api_key_ref(
        self,
        workspace_id: str,
        config_id: str,
        request: UpdateModelConfigRequest,
    ) -> None:
        if self.secret_service is None or not request.enabled or not request.api_key_ref:
            return
        secret_id = _normalize_secret_ref(request.api_key_ref)
        try:
            summary = self.secret_service.get_secret_summary(workspace_id, secret_id)
        except SecretNotFoundError as exc:
            raise AgentSystemError(
                "model_config_secret_ref_invalid",
                "Model config api_key_ref points to a missing secret.",
                status_code=400,
                retryable=False,
                details={"field": "api_key_ref", "reason": "secret_not_found"},
            ) from exc
        if summary.status != "active":
            raise AgentSystemError(
                "model_config_secret_ref_invalid",
                "Model config api_key_ref points to an inactive secret.",
                status_code=400,
                retryable=False,
                details={"field": "api_key_ref", "reason": "secret_not_active"},
            )
        purpose = MODEL_SECRET_PURPOSE_BY_CONFIG_ID.get(config_id, "model_call")
        allowed_types = PURPOSE_ALLOWED_TYPES[purpose]
        if summary.type not in allowed_types:
            raise AgentSystemError(
                "model_config_secret_ref_invalid",
                "Model config api_key_ref points to a secret with the wrong type.",
                status_code=400,
                retryable=False,
                details={
                    "field": "api_key_ref",
                    "reason": "secret_type_not_allowed",
                    "allowed_types": sorted(allowed_types),
                },
            )

    @staticmethod
    def _ensure_config_id(config_id: str) -> None:
        if config_id not in CONFIG_METADATA:
            raise AgentSystemError(
                "model_config_not_found",
                "Model config was not found.",
                status_code=404,
                retryable=False,
            )


def _normalize_secret_ref(value: str) -> str:
    return str(value).removeprefix("secret_ref://")
