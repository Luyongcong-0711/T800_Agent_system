from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from app.model_connector.budget import TokenBudgetManager
from app.model_connector.connector import LLMConnector
from app.model_connector.providers import (
    AnthropicAdapter,
    FakeModelAdapter,
    OpenAICompatibleAdapter,
    messages_to_anthropic,
    messages_to_openai,
    tools_to_anthropic,
    tools_to_openai,
)
from app.schemas.model import (
    ModelConfig,
    ModelError,
    ModelMessage,
    ModelRequest,
    ModelResult,
    ModelToolCall,
    ModelUsage,
)


def _request() -> ModelRequest:
    return ModelRequest(
        request_id="run_smoke",
        messages=[
            ModelMessage(
                role="system",
                content=json.dumps(
                    {
                        "run_id": "run_smoke",
                        "thread_id": "thread_smoke",
                        "workspace_id": "default",
                        "user_id": "default_user",
                        "role": "owner",
                    },
                    separators=(",", ":"),
                ),
            ),
            ModelMessage(role="user", content="Run a smoke test."),
        ],
        tools=[{"name": "echo_runtime_context", "description": "Safe smoke tool."}],
        max_output_tokens=8192,
    )


def _assert_no_secret_material(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str).lower()
    for forbidden in [
        "sk-test-secret",
        "plaintext",
        "ciphertext",
        "nonce",
        "tag",
        "agent_master_key",
        "authorization",
    ]:
        assert forbidden not in serialized


def test_token_budget_defaults_and_context_overflow_classification() -> None:
    config = ModelConfig()
    manager = TokenBudgetManager()

    assert config.context_window_tokens == 200000
    assert config.max_output_tokens == 8192

    usage = manager.enforce(
        config=config,
        messages=[ModelMessage(role="user", content="hello")],
        max_output_tokens=config.max_output_tokens,
    )

    assert usage.usage_estimated is True
    with pytest.raises(ModelError) as exc_info:
        manager.enforce(
            config=ModelConfig(context_window_tokens=10, max_output_tokens=8192),
            messages=[ModelMessage(role="user", content="x" * 100)],
            max_output_tokens=8192,
        )
    assert exc_info.value.error_type == "context_overflow"


def test_fake_provider_returns_deterministic_tool_call_and_estimated_usage() -> None:
    result = FakeModelAdapter().call(ModelConfig(provider="fake"), _request(), api_key=None)

    assert result.content == "Runtime smoke fake model requested a safe built-in tool."
    assert result.tool_calls == [
        ModelToolCall(
            tool_call_id="call_run_smoke_echo_runtime_context",
            name="echo_runtime_context",
            args={
                "run_id": "run_smoke",
                "thread_id": "thread_smoke",
                "workspace_id": "default",
                "user_id": "default_user",
                "role": "owner",
            },
        )
    ]
    assert result.usage.usage_estimated is True
    _assert_no_secret_material(result.model_dump())


def test_llm_connector_resolves_secret_with_llm_connector_caller_for_openai_compatible() -> None:
    @dataclass
    class RecordingResolver:
        calls: list[dict[str, str]]

        def resolve(self, workspace_id: str, secret_ref: str, purpose: str, caller: str) -> Any:
            self.calls.append(
                {
                    "workspace_id": workspace_id,
                    "secret_ref": secret_ref,
                    "purpose": purpose,
                    "caller": caller,
                }
            )
            return type("ResolvedSecret", (), {"plaintext": "sk-test-secret"})()

    class CapturingAdapter:
        def call(
            self,
            config: ModelConfig,
            request: ModelRequest,
            api_key: str | None,
        ) -> ModelResult:
            assert config.provider == "openai_compatible"
            assert request.request_id == "run_smoke"
            assert api_key == "sk-test-secret"
            return ModelResult(
                content="ok",
                tool_calls=[],
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    resolver = RecordingResolver(calls=[])
    connector = LLMConnector(
        secret_resolver=resolver,
        adapters={"openai_compatible": CapturingAdapter()},
    )

    result = connector.call(
        "default",
        ModelConfig(
            provider="openai_compatible",
            model="test-model",
            base_url="https://example.invalid/v1",
            api_key_ref="secret_openai",
        ),
        _request(),
    )

    assert resolver.calls == [
        {
            "workspace_id": "default",
            "secret_ref": "secret_openai",
            "purpose": "model_call",
            "caller": "llm_connector",
        }
    ]
    _assert_no_secret_material(result.model_dump())


def test_llm_connector_stream_resolves_secret_and_yields_provider_events() -> None:
    @dataclass
    class RecordingResolver:
        calls: list[dict[str, str]]

        def resolve(self, workspace_id: str, secret_ref: str, purpose: str, caller: str) -> Any:
            self.calls.append(
                {
                    "workspace_id": workspace_id,
                    "secret_ref": secret_ref,
                    "purpose": purpose,
                    "caller": caller,
                }
            )
            return type("ResolvedSecret", (), {"plaintext": "sk-test-secret"})()

    class StreamingAdapter:
        def call(
            self,
            config: ModelConfig,
            request: ModelRequest,
            api_key: str | None,
        ) -> ModelResult:
            _ = config, request, api_key
            raise AssertionError("stream test must not use non-stream call")

        def stream(
            self,
            config: ModelConfig,
            request: ModelRequest,
            api_key: str | None,
        ):
            assert config.provider == "openai_compatible"
            assert request.request_id == "run_smoke"
            assert api_key == "sk-test-secret"
            yield from FakeModelAdapter().stream(ModelConfig(provider="fake"), request, None)

    resolver = RecordingResolver(calls=[])
    connector = LLMConnector(
        secret_resolver=resolver,
        adapters={"openai_compatible": StreamingAdapter()},
    )

    events = list(
        connector.stream(
            "default",
            ModelConfig(
                provider="openai_compatible",
                model="test-model",
                base_url="https://example.invalid/v1",
                api_key_ref="secret_openai",
            ),
            _request(),
        )
    )

    assert resolver.calls == [
        {
            "workspace_id": "default",
            "secret_ref": "secret_openai",
            "purpose": "model_call",
            "caller": "llm_connector",
        }
    ]
    assert events[-1].type == "stream_closed"
    _assert_no_secret_material([event.model_dump() for event in events])


class MockResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "provider error",
                request=self.request,
                response=httpx.Response(
                    self.status_code,
                    request=self.request,
                    text=self.text,
                ),
            )


class MockStreamResponse:
    def __init__(
        self,
        status_code: int,
        lines: list[str],
        request_url: str = "https://example.invalid/v1/chat/completions",
    ) -> None:
        self.status_code = status_code
        self.lines = lines
        self.request = httpx.Request("POST", request_url)
        self.text = "\n".join(lines)

    def __enter__(self) -> MockStreamResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def iter_lines(self) -> list[str]:
        return self.lines

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "provider stream error",
                request=self.request,
                response=httpx.Response(
                    self.status_code,
                    request=self.request,
                    text=self.text,
                ),
            )


def test_openai_compatible_adapter_parses_success_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> MockResponse:
        captured.update({"url": url, **kwargs})
        return MockResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": "hello",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "echo_runtime_context",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

    monkeypatch.setattr("app.model_connector.providers.httpx.post", fake_post)
    result = OpenAICompatibleAdapter().call(
        ModelConfig(
            provider="openai_compatible",
            model="test-model",
            base_url="https://example.invalid/v1",
        ),
        _request(),
        api_key="sk-test-secret",
    )

    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert captured["json"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo_runtime_context",
                "description": "Safe smoke tool.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert result.content == "hello"
    assert result.tool_calls == [
        ModelToolCall(tool_call_id="call_1", name="echo_runtime_context", args={})
    ]
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 2
    assert result.usage.total_tokens == 5
    _assert_no_secret_material(result.model_dump())


def test_openai_compatible_stream_parses_content_tool_delta_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    lines = [
        'data: {"choices":[{"delta":{"content":"hel"}}]}',
        (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            '"function":{"name":"echo_runtime_context","arguments":"{\\"run_id\\""}}]}}]}'
        ),
        (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":":\\"run_1\\"}"}}]}}]}'
        ),
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        (
            'data: {"choices":[],"usage":{"prompt_tokens":3,'
            '"completion_tokens":2,"total_tokens":5}}'
        ),
        "data: [DONE]",
    ]

    def fake_stream(method: str, url: str, **kwargs: Any) -> MockStreamResponse:
        captured.update({"method": method, "url": url, **kwargs})
        return MockStreamResponse(200, lines)

    monkeypatch.setattr("app.model_connector.providers.httpx.stream", fake_stream)

    events = list(
        OpenAICompatibleAdapter().stream(
            ModelConfig(
                provider="openai_compatible",
                model="test-model",
                base_url="https://example.invalid/v1",
            ),
            _request(),
            api_key="sk-test-secret",
        )
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "https://example.invalid/v1/chat/completions"
    assert captured["json"]["stream"] is True
    assert captured["json"]["stream_options"] == {"include_usage": True}
    assert [event.type for event in events] == [
        "message_start",
        "content_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_completed",
        "message_completed",
        "usage_delta",
        "stream_closed",
    ]
    assert events[1].delta == "hel"
    assert events[5].tool_call_delta is not None
    assert events[5].tool_call_delta.args == {"run_id": "run_1"}
    assert events[6].finish_reason == "tool_calls"
    assert events[7].usage is not None
    assert events[7].usage.total_tokens == 5
    _assert_no_secret_material([event.model_dump() for event in events])


def test_openai_compatible_stream_requires_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_stream(method: str, url: str, **kwargs: Any) -> MockStreamResponse:
        _ = method, url, kwargs
        return MockStreamResponse(
            200,
            ['data: {"choices":[{"delta":{"content":"partial"}}]}'],
        )

    monkeypatch.setattr("app.model_connector.providers.httpx.stream", fake_stream)

    with pytest.raises(ModelError) as exc_info:
        list(
            OpenAICompatibleAdapter().stream(
                ModelConfig(
                    provider="openai_compatible",
                    model="test-model",
                    base_url="https://example.invalid/v1",
                ),
                _request(),
                api_key="sk-test-secret",
            )
        )

    assert exc_info.value.error_type == "stream_ended_before_terminal"
    assert exc_info.value.retryable is True


def test_openai_message_payload_preserves_assistant_tool_calls_before_tool_result() -> None:
    payload = messages_to_openai(
        [
            ModelMessage(role="user", content="Use a tool."),
            ModelMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ModelToolCall(
                        tool_call_id="call_1",
                        name="echo_runtime_context",
                        args={"run_id": "run_1"},
                    )
                ],
            ),
            ModelMessage(
                role="tool",
                content='{"run_id":"run_1"}',
                tool_call_id="call_1",
            ),
        ]
    )

    assert payload[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "echo_runtime_context",
                    "arguments": '{"run_id":"run_1"}',
                },
            }
        ],
    }
    assert payload[2] == {
        "role": "tool",
        "content": '{"run_id":"run_1"}',
        "tool_call_id": "call_1",
    }


def test_openai_tools_payload_uses_function_schema_shape() -> None:
    payload = tools_to_openai(
        [
            {
                "name": "echo_runtime_context",
                "description": "Return runtime context.",
                "args_schema": {
                    "type": "object",
                    "properties": {"run_id": {"type": "string"}},
                    "required": ["run_id"],
                },
            }
        ]
    )

    assert payload == [
        {
            "type": "function",
            "function": {
                "name": "echo_runtime_context",
                "description": "Return runtime context.",
                "parameters": {
                    "type": "object",
                    "properties": {"run_id": {"type": "string"}},
                    "required": ["run_id"],
                },
            },
        }
    ]


def test_anthropic_payload_preserves_tool_use_and_tool_result_messages() -> None:
    messages = messages_to_anthropic(
        [
            ModelMessage(role="user", content="Use a tool."),
            ModelMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ModelToolCall(
                        tool_call_id="toolu_1",
                        name="echo_runtime_context",
                        args={"run_id": "run_1"},
                    )
                ],
            ),
            ModelMessage(
                role="tool",
                content='{"run_id":"run_1"}',
                tool_call_id="toolu_1",
            ),
        ]
    )

    assert messages[1] == {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "echo_runtime_context",
                "input": {"run_id": "run_1"},
            }
        ],
    }
    assert messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": '{"run_id":"run_1"}',
            }
        ],
    }


def test_anthropic_tools_payload_uses_input_schema_shape() -> None:
    payload = tools_to_anthropic(
        [
            {
                "name": "echo_runtime_context",
                "description": "Return runtime context.",
                "args_schema": {
                    "type": "object",
                    "properties": {"run_id": {"type": "string"}},
                    "required": ["run_id"],
                },
            }
        ]
    )

    assert payload == [
        {
            "name": "echo_runtime_context",
            "description": "Return runtime context.",
            "input_schema": {
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "required": ["run_id"],
            },
        }
    ]


@pytest.mark.parametrize(
    ("status_code", "message", "expected_error_type"),
    [
        (401, "bad key", "auth_failed"),
        (404, "model not found", "model_not_found"),
        (429, "rate limited", "rate_limit"),
        (400, "maximum context length exceeded", "context_overflow"),
    ],
)
def test_openai_compatible_adapter_maps_error_categories(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    message: str,
    expected_error_type: str,
) -> None:
    def fake_post(url: str, **kwargs: Any) -> MockResponse:
        _ = url, kwargs
        return MockResponse(status_code, {"error": {"message": message}})

    monkeypatch.setattr("app.model_connector.providers.httpx.post", fake_post)

    with pytest.raises(ModelError) as exc_info:
        OpenAICompatibleAdapter().call(
            ModelConfig(
                provider="openai_compatible",
                model="test-model",
                base_url="https://example.invalid/v1",
            ),
            _request(),
            api_key="sk-test-secret",
        )

    assert exc_info.value.error_type == expected_error_type
    assert exc_info.value.__cause__ is None
    _assert_no_secret_material(
        {
            "error_type": exc_info.value.error_type,
            "retryable": exc_info.value.retryable,
            "status_code": exc_info.value.status_code,
        }
    )


def test_provider_error_exception_chain_does_not_keep_secret_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request(
        "POST",
        "https://example.invalid/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test-secret"},
    )
    response = httpx.Response(
        401,
        request=request,
        text='{"error":{"message":"bad key"}}',
    )

    def fake_post(url: str, **kwargs: Any) -> MockResponse:
        _ = url, kwargs
        raise httpx.HTTPStatusError("bad key", request=request, response=response)

    monkeypatch.setattr("app.model_connector.providers.httpx.post", fake_post)

    with pytest.raises(ModelError) as exc_info:
        OpenAICompatibleAdapter().call(
            ModelConfig(
                provider="openai_compatible",
                model="test-model",
                base_url="https://example.invalid/v1",
            ),
            _request(),
            api_key="sk-test-secret",
        )

    assert exc_info.value.error_type == "auth_failed"
    assert exc_info.value.__cause__ is None
    _assert_no_secret_material(str(exc_info.value))


def test_anthropic_adapter_parses_success_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kwargs: Any) -> MockResponse:
        assert url == "https://example.invalid/messages"
        assert kwargs["headers"]["x-api-key"] == "sk-test-secret"
        return MockResponse(
            200,
            {
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 4, "output_tokens": 3},
            },
        )

    monkeypatch.setattr("app.model_connector.providers.httpx.post", fake_post)
    result = AnthropicAdapter().call(
        ModelConfig(
            provider="anthropic",
            model="claude-test",
            base_url="https://example.invalid",
        ),
        _request(),
        api_key="sk-test-secret",
    )

    assert result.content == "hello"
    assert result.tool_calls == []
    assert result.usage.input_tokens == 4
    assert result.usage.output_tokens == 3
    assert result.usage.total_tokens == 7
    _assert_no_secret_material(result.model_dump())


def test_anthropic_adapter_parses_non_stream_tool_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> MockResponse:
        captured.update({"url": url, **kwargs})
        return MockResponse(
            200,
            {
                "content": [
                    {"type": "text", "text": "checking"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "echo_runtime_context",
                        "input": {"run_id": "run_1"},
                    },
                ],
                "usage": {"input_tokens": 4, "output_tokens": 3},
            },
        )

    monkeypatch.setattr("app.model_connector.providers.httpx.post", fake_post)
    result = AnthropicAdapter().call(
        ModelConfig(
            provider="anthropic",
            model="claude-test",
            base_url="https://example.invalid",
        ),
        _request(),
        api_key="sk-test-secret",
    )

    assert captured["json"]["tools"] == [
        {
            "name": "echo_runtime_context",
            "description": "Safe smoke tool.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert result.content == "checking"
    assert result.tool_calls == [
        ModelToolCall(
            tool_call_id="toolu_1",
            name="echo_runtime_context",
            args={"run_id": "run_1"},
        )
    ]
    _assert_no_secret_material(result.model_dump())


def test_anthropic_stream_parses_content_tool_use_and_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":4}}}',
        (
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"text_delta","text":"hel"}}'
        ),
        (
            'data: {"type":"content_block_start","index":1,'
            '"content_block":{"type":"tool_use","id":"toolu_1",'
            '"name":"echo_runtime_context","input":{}}}'
        ),
        (
            'data: {"type":"content_block_delta","index":1,'
            '"delta":{"type":"input_json_delta","partial_json":"{\\"run_id\\""}}'
        ),
        (
            'data: {"type":"content_block_delta","index":1,'
            '"delta":{"type":"input_json_delta","partial_json":":\\"run_1\\"}"}}'
        ),
        'data: {"type":"content_block_stop","index":1}',
        (
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
            '"usage":{"output_tokens":3}}'
        ),
        'data: {"type":"message_stop"}',
    ]

    def fake_stream(method: str, url: str, **kwargs: Any) -> MockStreamResponse:
        captured.update({"method": method, "url": url, **kwargs})
        return MockStreamResponse(200, lines, request_url="https://example.invalid/messages")

    monkeypatch.setattr("app.model_connector.providers.httpx.stream", fake_stream)

    events = list(
        AnthropicAdapter().stream(
            ModelConfig(
                provider="anthropic",
                model="claude-test",
                base_url="https://example.invalid",
            ),
            _request(),
            api_key="sk-test-secret",
        )
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "https://example.invalid/messages"
    assert captured["json"]["stream"] is True
    assert [event.type for event in events] == [
        "message_start",
        "content_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_completed",
        "usage_delta",
        "message_completed",
        "stream_closed",
    ]
    assert events[1].delta == "hel"
    assert events[5].tool_call_delta is not None
    assert events[5].tool_call_delta.tool_call_id == "toolu_1"
    assert events[5].tool_call_delta.args == {"run_id": "run_1"}
    assert events[6].usage is not None
    assert events[6].usage.output_tokens == 3
    assert events[7].finish_reason == "tool_use"
    _assert_no_secret_material([event.model_dump() for event in events])
