from __future__ import annotations

import json
from collections.abc import Callable
from time import perf_counter
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.model_connector.connector import LLMConnector
from app.runtime.state import AgentState, PendingToolCall, RuntimeToolResult
from app.runtime.tools import ToolRegistry, redact_runtime_value
from app.schemas.model import (
    ModelConfig,
    ModelError,
    ModelMessage,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ModelToolCall,
    ModelUsage,
)

DEFAULT_MAX_TOOL_ITERATIONS = 8
MAX_TOOL_INVENTORY_DESCRIPTION_CHARS = 220


def _message_to_model_message(message: BaseMessage) -> ModelMessage:
    if message.type == "human":
        role = "user"
    elif message.type == "ai":
        role = "assistant"
    elif message.type == "tool":
        role = "tool"
    else:
        role = "system"
    tool_calls: list[ModelToolCall] = []
    for call in getattr(message, "tool_calls", []) or []:
        if not isinstance(call, dict):
            continue
        tool_calls.append(
            ModelToolCall(
                tool_call_id=str(call.get("id") or call.get("tool_call_id") or ""),
                name=str(call.get("name") or ""),
                args=call.get("args") if isinstance(call.get("args"), dict) else {},
            )
        )
    return ModelMessage(
        role=role,
        content=str(message.content),
        tool_call_id=str(getattr(message, "tool_call_id", "") or "") or None,
        tool_calls=tool_calls,
    )


def _runtime_context_message(state: AgentState) -> SystemMessage:
    return SystemMessage(
        content=json.dumps(
            {
                "run_id": state["run_id"],
                "trace_id": state.get("trace_id") or state["run_id"],
                "thread_id": state["thread_id"],
                "workspace_id": state["workspace_id"],
                "user_id": state["user_id"],
                "role": state["role"],
            },
            separators=(",", ":"),
        )
    )


def _model_visible_tools_message(tool_specs: list[dict[str, Any]]) -> SystemMessage:
    tools = []
    for spec in tool_specs:
        description = str(spec.get("description") or "")
        if len(description) > MAX_TOOL_INVENTORY_DESCRIPTION_CHARS:
            description = (
                description[:MAX_TOOL_INVENTORY_DESCRIPTION_CHARS].rstrip() + "..."
            )
        tools.append(
            {
                "name": str(spec.get("name") or ""),
                "source": str(spec.get("source") or "built_in"),
                "risk_level": str(spec.get("risk_level") or "low"),
                "requires_approval": bool(spec.get("requires_approval") or False),
                "description": description,
            }
        )
    return SystemMessage(
        content=json.dumps(
            {
                "model_visible_tools": tools,
                "tool_policy": (
                    "Only call tools in model_visible_tools. If the user asks what "
                    "tools or computer-control capabilities are available, answer from "
                    "model_visible_tools. Tools with requires_approval=true pause for "
                    "user approval before execution."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def build_runtime_graph(
    tool_registry: ToolRegistry,
    llm_connector: LLMConnector | None = None,
    model_config: ModelConfig | None = None,
    context_preflight: Any | None = None,
    model_stream_callback: Callable[[ModelStreamEvent], None] | None = None,
    observability_service: Any | None = None,
):
    try:
        from langgraph.graph import END, START, StateGraph
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("langgraph"):
            raise AgentSystemError(
                "runtime_graph_dependency_missing",
                "Runtime graph dependency is not installed.",
                status_code=503,
                retryable=False,
                details={"missing_module": exc.name},
            ) from exc
        raise
    active_llm_connector = llm_connector or LLMConnector(secret_resolver=None)
    active_model_config = model_config or ModelConfig(provider="fake", model="fake-runtime-smoke")

    def _build_model_request(state: AgentState) -> ModelRequest:
        tool_specs = tool_registry.model_safe_specs()
        request_messages = [
            _runtime_context_message(state),
            _model_visible_tools_message(tool_specs),
            *state["messages"],
        ]
        return ModelRequest(
            request_id=state["run_id"],
            messages=[_message_to_model_message(message) for message in request_messages],
            tools=tool_specs,
            max_output_tokens=int(
                state.get("max_output_tokens") or active_model_config.max_output_tokens
            ),
        )

    def _call_model(state: AgentState, request: ModelRequest) -> ModelResult:
        if model_stream_callback is None:
            return active_llm_connector.call(
                workspace_id=state["workspace_id"],
                config=active_model_config,
                request=request,
            )
        content_parts: list[str] = []
        tool_calls_by_index: dict[int, dict[str, Any]] = {}
        usage = ModelUsage(usage_estimated=True)
        for event in active_llm_connector.stream(
            workspace_id=state["workspace_id"],
            config=active_model_config,
            request=request,
        ):
            model_stream_callback(event)
            if event.type == "content_delta" and event.delta:
                content_parts.append(event.delta)
            if event.tool_call_delta is not None:
                delta = event.tool_call_delta
                tool_state = tool_calls_by_index.setdefault(
                    delta.index,
                    {
                        "tool_call_id": delta.tool_call_id or new_id("call"),
                        "name": delta.name or "",
                        "args_delta": "",
                        "args": {},
                    },
                )
                if delta.tool_call_id:
                    tool_state["tool_call_id"] = delta.tool_call_id
                if delta.name:
                    tool_state["name"] = delta.name
                if delta.args_delta:
                    tool_state["args_delta"] += delta.args_delta
                if delta.args is not None:
                    tool_state["args"] = delta.args
            if event.usage is not None:
                usage = event.usage
        tool_calls: list[ModelToolCall] = []
        for item in sorted(tool_calls_by_index.values(), key=lambda value: value["tool_call_id"]):
            args = item.get("args") or {}
            if not args and item.get("args_delta"):
                try:
                    parsed = json.loads(str(item["args_delta"]))
                    args = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(
                ModelToolCall(
                    tool_call_id=str(item["tool_call_id"]),
                    name=str(item.get("name") or ""),
                    args=args,
                )
            )
        return ModelResult(content="".join(content_parts), tool_calls=tool_calls, usage=usage)

    def _call_model_with_observability(
        state: AgentState,
        request: ModelRequest,
        *,
        attempt: str,
    ) -> ModelResult:
        started = perf_counter()
        _record_runtime_observability(
            observability_service,
            state,
            component="model_connector",
            event_type="model_call_started",
            message="Model call started.",
            payload_summary={
                "attempt": attempt,
                "model": active_model_config.model,
                "provider": active_model_config.provider,
                "request_id": request.request_id,
                "tool_count": len(request.tools),
            },
            status="started",
        )
        try:
            result = _call_model(state, request)
        except ModelError as exc:
            _record_runtime_observability(
                observability_service,
                state,
                component="model_connector",
                event_type="model_call_failed",
                message="Model call failed.",
                severity="ERROR",
                payload_summary={
                    "attempt": attempt,
                    "model": active_model_config.model,
                    "provider": active_model_config.provider,
                    "request_id": request.request_id,
                },
                duration_ms=int((perf_counter() - started) * 1000),
                status="error",
                error_type=exc.error_type,
            )
            raise
        except Exception as exc:
            _record_runtime_observability(
                observability_service,
                state,
                component="model_connector",
                event_type="model_call_failed",
                message="Model call failed.",
                severity="ERROR",
                payload_summary={
                    "attempt": attempt,
                    "model": active_model_config.model,
                    "provider": active_model_config.provider,
                    "request_id": request.request_id,
                },
                duration_ms=int((perf_counter() - started) * 1000),
                status="error",
                error_type=exc.__class__.__name__,
            )
            raise
        _record_runtime_observability(
            observability_service,
            state,
            component="model_connector",
            event_type="model_call_completed",
            message="Model call completed.",
            payload_summary={
                "attempt": attempt,
                "model": active_model_config.model,
                "provider": active_model_config.provider,
                "request_id": request.request_id,
                "tool_call_count": len(result.tool_calls),
                "usage": result.usage.model_dump(),
            },
            duration_ms=int((perf_counter() - started) * 1000),
            status="ok",
        )
        return result

    def model_call_node(state: AgentState) -> AgentState:
        active_state = state
        request = _build_model_request(active_state)
        result: ModelResult
        try:
            result = _call_model_with_observability(
                active_state,
                request,
                attempt="primary",
            )
        except ModelError as exc:
            can_retry_overflow = (
                exc.error_type == "context_overflow"
                and context_preflight is not None
                and not bool(active_state.get("context_overflow_retried"))
                and hasattr(context_preflight, "force_compress_after_overflow")
            )
            if can_retry_overflow:
                retry_state = context_preflight.force_compress_after_overflow(active_state)
                retry_request = _build_model_request(retry_state)
                try:
                    result = _call_model_with_observability(
                        retry_state,
                        retry_request,
                        attempt="context_overflow_retry",
                    )
                    active_state = retry_state
                except ModelError as retry_exc:
                    return {
                        **retry_state,
                        "status": "failed",
                        "pending_tool_calls": [],
                        "model_error": retry_exc.error_type,
                        "warnings": [
                            *list(retry_state.get("warnings") or []),
                            "context_overflow_retry_failed",
                        ],
                    }
            else:
                return {
                    **active_state,
                    "status": "failed",
                    "pending_tool_calls": [],
                    "model_error": exc.error_type,
                }

        runtime_context = _tool_runtime_context(active_state)
        pending_tool_calls: list[PendingToolCall] = []
        for tool_call in result.tool_calls:
            scoped_args = tool_registry.scope_args(
                tool_call.name,
                tool_call.args,
                runtime_context=runtime_context,
            )
            pending_tool_calls.append(
                {
                    "tool_call_id": tool_call.tool_call_id or new_id("call"),
                    "name": tool_call.name,
                    "args": redact_runtime_value(scoped_args),
                }
            )
        if pending_tool_calls and int(
            active_state.get("tool_iteration_count") or 0
        ) >= int(active_state.get("max_tool_iterations") or DEFAULT_MAX_TOOL_ITERATIONS):
            return {
                **active_state,
                "status": "failed",
                "pending_tool_calls": [],
                "model_error": "max_tool_iterations_exceeded",
                "warnings": [
                    *list(active_state.get("warnings") or []),
                    "Model returned tool calls after the maximum tool iteration limit.",
                ],
                "context_usage": result.usage.model_dump(),
            }
        messages = [
            *active_state["messages"],
            AIMessage(
                content=redact_runtime_value(result.content),
                tool_calls=[
                    {
                        "id": call["tool_call_id"],
                        "name": call["name"],
                        "args": call["args"],
                        "type": "tool_call",
                    }
                    for call in pending_tool_calls
                ],
            ),
        ]
        return {
            **active_state,
            "messages": messages,
            "pending_tool_calls": pending_tool_calls,
            "status": "model_called",
            "requires_approval": False,
            "context_usage": result.usage.model_dump(),
            "model_error": None,
        }

    def tool_execution_node(state: AgentState) -> AgentState:
        next_results: list[RuntimeToolResult] = [*state["tool_results"]]
        next_messages = [*state["messages"]]
        requires_approval = bool(state.get("requires_approval"))
        for call in state["pending_tool_calls"]:
            started = perf_counter()
            _record_runtime_observability(
                observability_service,
                state,
                component="tool_runtime",
                event_type="tool_call_started",
                message="Runtime tool call started.",
                payload_summary={
                    "tool_call_id": call["tool_call_id"],
                    "tool_name": call["name"],
                },
                status="started",
            )
            try:
                runtime_context = {
                    **_tool_runtime_context(state),
                    "tool_call_id": call["tool_call_id"],
                }
                content = redact_runtime_value(
                    tool_registry.invoke(
                        call["name"],
                        call["args"],
                        runtime_context=runtime_context,
                    )
                )
                tool_ok = True
                error_type = None
                if isinstance(content, dict) and content.get("ok") is False:
                    tool_ok = False
                    raw_error_type = content.get("error_type")
                    error_type = str(raw_error_type) if raw_error_type else None
                result: RuntimeToolResult = {
                    "tool_call_id": call["tool_call_id"],
                    "name": call["name"],
                    "ok": tool_ok,
                    "content": content,
                    "error_type": error_type,
                }
            except Exception as exc:  # noqa: BLE001 - tool boundary converts to result.
                result = {
                    "tool_call_id": call["tool_call_id"],
                    "name": call["name"],
                    "ok": False,
                    "content": {},
                    "error_type": exc.__class__.__name__,
                }
            event_type = "tool_call_completed" if result["ok"] else "tool_call_failed"
            if result["error_type"] == "approval_required":
                event_type = "tool_call_approval_required"
            _record_runtime_observability(
                observability_service,
                state,
                component="tool_runtime",
                event_type=event_type,
                message="Runtime tool call completed."
                if result["ok"]
                else "Runtime tool call failed.",
                severity="INFO" if result["ok"] else "WARNING",
                payload_summary={
                    "tool_call_id": result["tool_call_id"],
                    "tool_name": result["name"],
                },
                duration_ms=int((perf_counter() - started) * 1000),
                status="ok" if result["ok"] else "error",
                error_type=result["error_type"],
            )
            _record_boundary_observability(
                observability_service,
                state,
                result,
                duration_ms=int((perf_counter() - started) * 1000),
            )
            next_results.append(result)
            next_messages.append(
                ToolMessage(
                    content=str(redact_runtime_value(result["content"])),
                    tool_call_id=call["tool_call_id"],
                )
            )
            if result["error_type"] == "approval_required":
                requires_approval = True
                break
        return {
            **state,
            "messages": next_messages,
            "tool_results": next_results,
            "pending_tool_calls": [],
            "status": "waiting_approval" if requires_approval else "tools_completed",
            "requires_approval": requires_approval,
            "tool_iteration_count": int(state.get("tool_iteration_count") or 0) + 1,
        }

    def finalize_node(state: AgentState) -> AgentState:
        if state["status"] in {"failed", "waiting_approval"}:
            return state
        return {
            **state,
            "status": "completed",
        }

    def route_after_model(state: AgentState) -> Literal["tools", "finalize"]:
        if state["status"] == "failed":
            return "finalize"
        return "tools" if state["pending_tool_calls"] else "finalize"

    def route_after_tools(state: AgentState) -> Literal["model_call", "finalize"]:
        if state["status"] in {"failed", "waiting_approval"}:
            return "finalize"
        return "model_call"

    graph = StateGraph(AgentState)
    if context_preflight is not None:
        preflight_node = (
            context_preflight.invoke
            if hasattr(context_preflight, "invoke")
            else context_preflight
        )
        graph.add_node("context_preflight", preflight_node)
    graph.add_node("model_call", model_call_node)
    graph.add_node("tools", tool_execution_node)
    graph.add_node("finalize", finalize_node)
    if context_preflight is not None:
        graph.add_edge(START, "context_preflight")
        graph.add_edge("context_preflight", "model_call")
    else:
        graph.add_edge(START, "model_call")
    graph.add_conditional_edges(
        "model_call",
        route_after_model,
        {"tools": "tools", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "tools",
        route_after_tools,
        {"model_call": "model_call", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile()


def _tool_runtime_context(state: AgentState) -> dict[str, Any]:
    return {
        "run_id": state["run_id"],
        "trace_id": state.get("trace_id") or state["run_id"],
        "thread_id": state["thread_id"],
        "workspace_id": state["workspace_id"],
        "user_id": state["user_id"],
        "role": state["role"],
    }


def _record_boundary_observability(
    observability_service: Any | None,
    state: AgentState,
    result: RuntimeToolResult,
    *,
    duration_ms: int,
) -> None:
    content = result.get("content")
    if not isinstance(content, dict):
        return
    if result["name"] in {"database_health_check", "database_health_diagnose"}:
        snapshot = content.get("data") if isinstance(content.get("data"), dict) else {}
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
        _record_runtime_observability(
            observability_service,
            state,
            component="database_health",
            event_type=f'{result["name"]}_completed',
            message="Database boundary health check completed.",
            severity="INFO" if result["ok"] else "WARNING",
            payload_summary={
                "tool_call_id": result["tool_call_id"],
                "tool_name": result["name"],
                "healthy_targets": summary.get("healthy_targets") or [],
                "unhealthy_targets": summary.get("unhealthy_targets") or [],
                "unknown_targets": summary.get("unknown_targets") or [],
                "source": summary.get("source"),
            },
            duration_ms=duration_ms,
            status="ok" if result["ok"] else "error",
            error_type=result.get("error_type"),
        )
        return
    if "server_name" in content and "tool_name" in content:
        _record_runtime_observability(
            observability_service,
            state,
            component="mcp_transport",
            event_type="mcp_tool_invocation_completed",
            message="MCP tool invocation boundary completed.",
            severity="INFO" if result["ok"] else "WARNING",
            payload_summary={
                "tool_call_id": result["tool_call_id"],
                "server_name": content.get("server_name"),
                "tool_name": content.get("tool_name"),
                "retryable": content.get("retryable"),
            },
            duration_ms=duration_ms,
            status="ok" if result["ok"] else "error",
            error_type=result.get("error_type") or content.get("error_type"),
        )


def _record_runtime_observability(
    observability_service: Any | None,
    state: AgentState,
    *,
    component: str,
    event_type: str,
    message: str,
    severity: str = "INFO",
    payload_summary: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    status: str | None = None,
    error_type: str | None = None,
) -> None:
    if observability_service is None:
        return
    try:
        observability_service.record_event(
            component=component,
            event_type=event_type,
            message=message,
            workspace_id=state.get("workspace_id"),
            severity=severity,
            trace_id=state.get("trace_id") or state.get("run_id"),
            run_id=state.get("run_id"),
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            role=state.get("role"),
            payload_summary=payload_summary or {},
            duration_ms=duration_ms,
            status=status,
            error_type=error_type,
        )
    except Exception:
        return
