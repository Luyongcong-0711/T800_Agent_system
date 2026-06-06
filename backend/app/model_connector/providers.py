from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any, Protocol

import httpx

from app.schemas.model import (
    ModelConfig,
    ModelError,
    ModelMessage,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ModelToolCall,
    ModelUsage,
    ToolCallDelta,
)


class ModelProviderAdapter(Protocol):
    def call(self, config: ModelConfig, request: ModelRequest, api_key: str | None) -> ModelResult:
        raise NotImplementedError

    def stream(
        self,
        config: ModelConfig,
        request: ModelRequest,
        api_key: str | None,
    ) -> Iterator[ModelStreamEvent]:
        raise NotImplementedError


def _looks_like_context_overflow(response_text: str) -> bool:
    lowered = response_text[:4096].lower()
    context_markers = (
        "context_overflow",
        "context_length_exceeded",
        "maximum context",
        "context window",
        "too many tokens",
        "prompt is too long",
    )
    return any(marker in lowered for marker in context_markers)


def classify_http_error(status_code: int, response_text: str = "") -> str:
    if status_code in {400, 413, 422} and _looks_like_context_overflow(response_text):
        return "context_overflow"
    if status_code in {401, 403}:
        return "auth_failed"
    if status_code == 404:
        return "model_not_found"
    if status_code == 429:
        return "rate_limit"
    if 500 <= status_code <= 599:
        return "provider_5xx"
    if 400 <= status_code <= 499:
        return "invalid_request"
    return "unknown"


def normalize_provider_exception(exc: Exception) -> ModelError:
    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return ModelError("timeout", "Provider request timed out.", retryable=True)
    if isinstance(exc, httpx.HTTPStatusError):
        error_type = classify_http_error(exc.response.status_code, exc.response.text)
        return ModelError(
            error_type,
            "Provider request failed.",
            retryable=error_type in {"rate_limit", "provider_5xx"},
            status_code=exc.response.status_code,
        )
    if isinstance(exc, httpx.RequestError):
        return ModelError("unknown", "Provider request failed.", retryable=True)
    return ModelError("unknown", "Provider call failed.", retryable=False)


def _iter_sse_data(lines: Iterable[str | bytes]) -> Iterator[str]:
    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            yield line[5:].strip()


def _json_loads_or_empty(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def messages_to_openai(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        item: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_call_id:
            item["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": tool_call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(
                            tool_call.args,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    },
                }
                for tool_call in message.tool_calls
            ]
        payload.append(item)
    return payload


def tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for tool in tools:
        parameters = tool.get("parameters") or tool.get("input_schema") or tool.get("args_schema")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        payload.append(
            {
                "type": "function",
                "function": {
                    "name": str(tool.get("name") or ""),
                    "description": str(tool.get("description") or ""),
                    "parameters": parameters,
                },
            }
        )
    return payload


def tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for tool in tools:
        input_schema = tool.get("parameters") or tool.get("input_schema") or tool.get("args_schema")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        payload.append(
            {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
                "input_schema": input_schema,
            }
        )
    return payload


def tool_call_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def parse_openai_usage(data: dict[str, Any]) -> ModelUsage:
    usage = data.get("usage") or {}
    return ModelUsage(
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        usage_estimated=False if usage else True,
    )


class OpenAICompatibleAdapter:
    def call(self, config: ModelConfig, request: ModelRequest, api_key: str | None) -> ModelResult:
        if not config.base_url:
            raise ModelError("invalid_request", "OpenAI-compatible base_url is required.")
        if not api_key:
            raise ModelError("auth_failed", "OpenAI-compatible api key is required.")
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages_to_openai(request.messages),
            "max_tokens": request.max_output_tokens,
        }
        if request.tools:
            payload["tools"] = tools_to_openai(request.tools)
            payload["tool_choice"] = "auto"
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=config.timeout_ms / 1000,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise normalize_provider_exception(exc) from None

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls: list[ModelToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            tool_calls.append(
                ModelToolCall(
                    tool_call_id=str(call.get("id") or ""),
                    name=str(function.get("name") or ""),
                    args=tool_call_args(function.get("arguments")),
                )
            )
        return ModelResult(
            content=str(message.get("content") or ""),
            tool_calls=tool_calls,
            usage=parse_openai_usage(data),
            raw_provider="openai_compatible",
        )

    def stream(
        self,
        config: ModelConfig,
        request: ModelRequest,
        api_key: str | None,
    ) -> Iterator[ModelStreamEvent]:
        if not config.base_url:
            raise ModelError("invalid_request", "OpenAI-compatible base_url is required.")
        if not api_key:
            raise ModelError("auth_failed", "OpenAI-compatible api key is required.")
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages_to_openai(request.messages),
            "max_tokens": request.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            payload["tools"] = tools_to_openai(request.tools)
            payload["tool_choice"] = "auto"

        yield ModelStreamEvent(
            type="message_start",
            request_id=request.request_id,
            raw_provider="openai_compatible",
        )
        buffers: dict[int, dict[str, Any]] = {}
        completed_tool_indexes: set[int] = set()
        terminal_seen = False
        try:
            with httpx.stream(
                "POST",
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=config.timeout_ms / 1000,
            ) as response:
                response.raise_for_status()
                for raw_data in _iter_sse_data(response.iter_lines()):
                    if raw_data == "[DONE]":
                        terminal_seen = True
                        break
                    data = _json_loads_or_empty(raw_data)
                    if not data:
                        continue
                    if data.get("usage"):
                        yield ModelStreamEvent(
                            type="usage_delta",
                            request_id=request.request_id,
                            usage=parse_openai_usage(data),
                            raw_provider="openai_compatible",
                        )
                    choice = (data.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    content_delta = delta.get("content")
                    if content_delta:
                        yield ModelStreamEvent(
                            type="content_delta",
                            request_id=request.request_id,
                            delta=str(content_delta),
                            raw_provider="openai_compatible",
                        )
                    for tool_delta in delta.get("tool_calls") or []:
                        index = int(tool_delta.get("index") or 0)
                        function = tool_delta.get("function") or {}
                        buffer = buffers.setdefault(
                            index,
                            {"tool_call_id": None, "name": None, "args": "", "started": False},
                        )
                        if tool_delta.get("id"):
                            buffer["tool_call_id"] = str(tool_delta["id"])
                        if function.get("name"):
                            buffer["name"] = str(function["name"])
                        if not buffer["started"] and (buffer["tool_call_id"] or buffer["name"]):
                            buffer["started"] = True
                            yield ModelStreamEvent(
                                type="tool_call_start",
                                request_id=request.request_id,
                                tool_call_delta=ToolCallDelta(
                                    index=index,
                                    tool_call_id=buffer["tool_call_id"],
                                    name=buffer["name"],
                                ),
                                raw_provider="openai_compatible",
                            )
                        args_delta = str(function.get("arguments") or "")
                        if args_delta:
                            buffer["args"] += args_delta
                            yield ModelStreamEvent(
                                type="tool_call_delta",
                                request_id=request.request_id,
                                tool_call_delta=ToolCallDelta(
                                    index=index,
                                    tool_call_id=buffer["tool_call_id"],
                                    name=buffer["name"],
                                    args_delta=args_delta,
                                ),
                                raw_provider="openai_compatible",
                            )
                    finish_reason = choice.get("finish_reason")
                    if finish_reason:
                        terminal_seen = True
                        yield from _complete_buffered_tool_calls(
                            request.request_id,
                            "openai_compatible",
                            buffers,
                            completed_tool_indexes,
                        )
                        yield ModelStreamEvent(
                            type="message_completed",
                            request_id=request.request_id,
                            finish_reason=str(finish_reason),
                            raw_provider="openai_compatible",
                        )
        except Exception as exc:
            raise normalize_provider_exception(exc) from None
        if not terminal_seen:
            raise ModelError(
                "stream_ended_before_terminal",
                "Provider stream ended before a terminal event.",
                retryable=True,
            )
        yield from _complete_buffered_tool_calls(
            request.request_id,
            "openai_compatible",
            buffers,
            completed_tool_indexes,
        )
        yield ModelStreamEvent(
            type="stream_closed",
            request_id=request.request_id,
            raw_provider="openai_compatible",
        )


def messages_to_anthropic(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        role = "assistant" if message.role == "assistant" else "user"
        if message.role == "tool":
            payload.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id or "",
                            "content": message.content,
                        }
                    ],
                }
            )
            continue
        if message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for tool_call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.tool_call_id,
                        "name": tool_call.name,
                        "input": tool_call.args,
                    }
                )
            payload.append({"role": role, "content": content})
            continue
        payload.append({"role": role, "content": message.content})
    return payload


def parse_anthropic_usage(data: dict[str, Any]) -> ModelUsage:
    usage = data.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        usage_estimated=False if usage else True,
    )


class AnthropicAdapter:
    def call(self, config: ModelConfig, request: ModelRequest, api_key: str | None) -> ModelResult:
        if not config.base_url:
            raise ModelError("invalid_request", "Anthropic base_url is required.")
        if not api_key:
            raise ModelError("auth_failed", "Anthropic api key is required.")
        url = f"{config.base_url.rstrip('/')}/messages"
        payload = {
            "model": config.model,
            "messages": messages_to_anthropic(request.messages),
            "max_tokens": request.max_output_tokens,
        }
        if request.tools:
            payload["tools"] = tools_to_anthropic(request.tools)
        try:
            response = httpx.post(
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
                timeout=config.timeout_ms / 1000,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise normalize_provider_exception(exc) from None

        text_parts = [
            str(part.get("text") or "")
            for part in data.get("content", [])
            if part.get("type") == "text"
        ]
        tool_calls = [
            ModelToolCall(
                tool_call_id=str(part.get("id") or ""),
                name=str(part.get("name") or ""),
                args=part.get("input") if isinstance(part.get("input"), dict) else {},
            )
            for part in data.get("content", [])
            if part.get("type") == "tool_use"
        ]
        return ModelResult(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage=parse_anthropic_usage(data),
            raw_provider="anthropic",
        )

    def stream(
        self,
        config: ModelConfig,
        request: ModelRequest,
        api_key: str | None,
    ) -> Iterator[ModelStreamEvent]:
        if not config.base_url:
            raise ModelError("invalid_request", "Anthropic base_url is required.")
        if not api_key:
            raise ModelError("auth_failed", "Anthropic api key is required.")
        url = f"{config.base_url.rstrip('/')}/messages"
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages_to_anthropic(request.messages),
            "max_tokens": request.max_output_tokens,
            "stream": True,
        }
        if request.tools:
            payload["tools"] = tools_to_anthropic(request.tools)

        yield ModelStreamEvent(
            type="message_start",
            request_id=request.request_id,
            raw_provider="anthropic",
        )
        buffers: dict[int, dict[str, Any]] = {}
        completed_tool_indexes: set[int] = set()
        terminal_seen = False
        try:
            with httpx.stream(
                "POST",
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
                timeout=config.timeout_ms / 1000,
            ) as response:
                response.raise_for_status()
                for raw_data in _iter_sse_data(response.iter_lines()):
                    data = _json_loads_or_empty(raw_data)
                    if not data:
                        continue
                    event_type = str(data.get("type") or "")
                    if event_type == "content_block_start":
                        index = int(data.get("index") or 0)
                        block = data.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            buffers[index] = {
                                "tool_call_id": str(block.get("id") or ""),
                                "name": str(block.get("name") or ""),
                                "args": "",
                                "started": True,
                            }
                            yield ModelStreamEvent(
                                type="tool_call_start",
                                request_id=request.request_id,
                                tool_call_delta=ToolCallDelta(
                                    index=index,
                                    tool_call_id=buffers[index]["tool_call_id"],
                                    name=buffers[index]["name"],
                                ),
                                raw_provider="anthropic",
                            )
                    elif event_type == "content_block_delta":
                        index = int(data.get("index") or 0)
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield ModelStreamEvent(
                                type="content_delta",
                                request_id=request.request_id,
                                delta=str(delta.get("text") or ""),
                                raw_provider="anthropic",
                            )
                        elif delta.get("type") == "input_json_delta":
                            buffer = buffers.setdefault(
                                index,
                                {
                                    "tool_call_id": None,
                                    "name": None,
                                    "args": "",
                                    "started": False,
                                },
                            )
                            args_delta = str(delta.get("partial_json") or "")
                            buffer["args"] += args_delta
                            yield ModelStreamEvent(
                                type="tool_call_delta",
                                request_id=request.request_id,
                                tool_call_delta=ToolCallDelta(
                                    index=index,
                                    tool_call_id=buffer["tool_call_id"],
                                    name=buffer["name"],
                                    args_delta=args_delta,
                                ),
                                raw_provider="anthropic",
                            )
                    elif event_type == "content_block_stop":
                        yield from _complete_buffered_tool_calls(
                            request.request_id,
                            "anthropic",
                            buffers,
                            completed_tool_indexes,
                            indexes=[int(data.get("index") or 0)],
                        )
                    elif event_type == "message_delta":
                        usage = data.get("usage")
                        if usage:
                            yield ModelStreamEvent(
                                type="usage_delta",
                                request_id=request.request_id,
                                usage=parse_anthropic_usage({"usage": usage}),
                                raw_provider="anthropic",
                            )
                        stop_reason = (data.get("delta") or {}).get("stop_reason")
                        if stop_reason:
                            yield ModelStreamEvent(
                                type="message_completed",
                                request_id=request.request_id,
                                finish_reason=str(stop_reason),
                                raw_provider="anthropic",
                            )
                    elif event_type == "message_stop":
                        terminal_seen = True
                        break
        except Exception as exc:
            raise normalize_provider_exception(exc) from None
        if not terminal_seen:
            raise ModelError(
                "stream_ended_before_terminal",
                "Provider stream ended before a terminal event.",
                retryable=True,
            )
        yield from _complete_buffered_tool_calls(
            request.request_id,
            "anthropic",
            buffers,
            completed_tool_indexes,
        )
        yield ModelStreamEvent(
            type="stream_closed",
            request_id=request.request_id,
            raw_provider="anthropic",
        )


class FakeModelAdapter:
    def call(self, config: ModelConfig, request: ModelRequest, api_key: str | None) -> ModelResult:
        _ = config, api_key
        if any(message.role == "tool" for message in request.messages):
            return ModelResult(
                content="Runtime smoke completed with 1 tool result(s).",
                tool_calls=[],
                usage=ModelUsage(
                    input_tokens=len(request.messages),
                    output_tokens=1,
                    total_tokens=len(request.messages) + 1,
                    usage_estimated=True,
                ),
                raw_provider="fake",
            )
        tool_name = "echo_runtime_context"
        tool_call_id = f"call_{request.request_id}_echo_runtime_context"
        args = {
            "run_id": request.request_id,
            "thread_id": "unknown_thread",
            "workspace_id": "unknown_workspace",
            "user_id": "unknown_user",
            "role": "owner",
        }
        for message in request.messages:
            if message.role == "system":
                try:
                    context = json.loads(message.content)
                except json.JSONDecodeError:
                    context = {}
                if isinstance(context, dict):
                    args.update(
                        {
                            "run_id": str(context.get("run_id") or args["run_id"]),
                            "thread_id": str(context.get("thread_id") or args["thread_id"]),
                            "workspace_id": str(
                                context.get("workspace_id") or args["workspace_id"]
                            ),
                            "user_id": str(context.get("user_id") or args["user_id"]),
                            "role": str(context.get("role") or args["role"]),
                        }
                    )
        return ModelResult(
            content="Runtime smoke fake model requested a safe built-in tool.",
            tool_calls=[ModelToolCall(tool_call_id=tool_call_id, name=tool_name, args=args)],
            usage=ModelUsage(
                input_tokens=len(request.messages),
                output_tokens=1,
                total_tokens=len(request.messages) + 1,
                usage_estimated=True,
            ),
            raw_provider="fake",
        )

    def stream(
        self,
        config: ModelConfig,
        request: ModelRequest,
        api_key: str | None,
    ) -> Iterator[ModelStreamEvent]:
        result = self.call(config, request, api_key)
        yield ModelStreamEvent(
            type="message_start",
            request_id=request.request_id,
            raw_provider="fake",
        )
        if result.content:
            yield ModelStreamEvent(
                type="content_delta",
                request_id=request.request_id,
                delta=result.content,
                raw_provider="fake",
            )
        yield ModelStreamEvent(
            type="usage_delta",
            request_id=request.request_id,
            usage=result.usage,
            raw_provider="fake",
        )
        yield ModelStreamEvent(
            type="message_completed",
            request_id=request.request_id,
            finish_reason="stop",
            raw_provider="fake",
        )
        yield ModelStreamEvent(
            type="stream_closed",
            request_id=request.request_id,
            raw_provider="fake",
        )


def _complete_buffered_tool_calls(
    request_id: str,
    raw_provider: str,
    buffers: dict[int, dict[str, Any]],
    completed_tool_indexes: set[int],
    indexes: list[int] | None = None,
) -> Iterator[ModelStreamEvent]:
    target_indexes = indexes if indexes is not None else sorted(buffers)
    for index in target_indexes:
        if index in completed_tool_indexes or index not in buffers:
            continue
        buffer = buffers[index]
        raw_args = str(buffer.get("args") or "")
        yield ModelStreamEvent(
            type="tool_call_completed",
            request_id=request_id,
            tool_call_delta=ToolCallDelta(
                index=index,
                tool_call_id=buffer.get("tool_call_id"),
                name=buffer.get("name"),
                args=tool_call_args(raw_args),
            ),
            raw_provider=raw_provider,
        )
        completed_tool_indexes.add(index)
