from __future__ import annotations

from collections.abc import Iterator

from app.model_connector.budget import TokenBudgetManager
from app.model_connector.providers import (
    AnthropicAdapter,
    FakeModelAdapter,
    ModelProviderAdapter,
    OpenAICompatibleAdapter,
    normalize_provider_exception,
)
from app.schemas.model import ModelConfig, ModelError, ModelRequest, ModelResult, ModelStreamEvent
from app.secret_store.secret_resolver import SecretResolver


class LLMConnector:
    def __init__(
        self,
        secret_resolver: SecretResolver | None = None,
        token_budget_manager: TokenBudgetManager | None = None,
        adapters: dict[str, ModelProviderAdapter] | None = None,
    ) -> None:
        self.secret_resolver = secret_resolver
        self.token_budget_manager = token_budget_manager or TokenBudgetManager()
        self.adapters = adapters or {
            "openai_compatible": OpenAICompatibleAdapter(),
            "anthropic": AnthropicAdapter(),
            "fake": FakeModelAdapter(),
        }

    def call(
        self,
        workspace_id: str,
        config: ModelConfig,
        request: ModelRequest,
    ) -> ModelResult:
        self.token_budget_manager.enforce(
            config=config,
            messages=request.messages,
            max_output_tokens=request.max_output_tokens,
        )
        api_key = self._resolve_api_key(workspace_id, config)
        try:
            return self.adapters[config.provider].call(config, request, api_key)
        except KeyError:
            raise ModelError(
                "invalid_request",
                f"Unsupported provider: {config.provider}",
            ) from None
        except ModelError:
            raise
        except Exception as exc:
            raise normalize_provider_exception(exc) from None

    def stream(
        self,
        workspace_id: str,
        config: ModelConfig,
        request: ModelRequest,
    ) -> Iterator[ModelStreamEvent]:
        self.token_budget_manager.enforce(
            config=config,
            messages=request.messages,
            max_output_tokens=request.max_output_tokens,
        )
        api_key = self._resolve_api_key(workspace_id, config)
        try:
            yield from self.adapters[config.provider].stream(config, request, api_key)
        except KeyError:
            raise ModelError(
                "invalid_request",
                f"Unsupported provider: {config.provider}",
            ) from None
        except ModelError:
            raise
        except Exception as exc:
            raise normalize_provider_exception(exc) from None

    def _resolve_api_key(self, workspace_id: str, config: ModelConfig) -> str | None:
        if config.provider == "fake":
            return None
        if not config.api_key_ref:
            raise ModelError("auth_failed", "Model api_key_ref is required.")
        if self.secret_resolver is None:
            raise ModelError("auth_failed", "Secret resolver is not configured.")
        resolved = self.secret_resolver.resolve(
            workspace_id=workspace_id,
            secret_ref=config.api_key_ref,
            purpose="model_call",
            caller="llm_connector",
        )
        return resolved.plaintext
