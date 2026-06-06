from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.model_connector.connector import LLMConnector
from app.runtime.context.preflight import RuntimeContextPreflight
from app.runtime.state import AgentState
from app.runtime.tools import ToolRegistry, build_default_tool_registry, redact_runtime_value
from app.schemas.identity import RuntimeIdentity
from app.schemas.model import ModelConfig, ModelStreamEvent
from app.schemas.runtime import RuntimeSmokeRequest, RuntimeSmokeResponse
from app.storage.object_store import ObjectStore


def _build_runtime_graph_or_raise(**kwargs: Any):
    try:
        from app.runtime.graph import build_runtime_graph
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
    return build_runtime_graph(**kwargs)


def serialize_message(message: BaseMessage) -> dict[str, Any]:
    content: Any = "[user message omitted]" if message.type == "human" else message.content
    return {
        "type": message.type,
        "content": redact_runtime_value(content),
    }


class RuntimeRunner:
    def __init__(
        self,
        llm_connector: LLMConnector | None = None,
        model_config: ModelConfig | None = None,
        tool_registry: ToolRegistry | None = None,
        object_store: ObjectStore | None = None,
        context_preflight: RuntimeContextPreflight | None = None,
        observability_service: Any | None = None,
    ) -> None:
        self.llm_connector = llm_connector or LLMConnector(secret_resolver=None)
        self.model_config = model_config or ModelConfig(provider="fake", model="fake-runtime-smoke")
        self.object_store = object_store
        self.tool_registry = tool_registry or build_default_tool_registry(object_store)
        self.observability_service = observability_service
        self.context_preflight = context_preflight
        if self.context_preflight is None and object_store is not None:
            from app.memory.service import MemoryService

            self.context_preflight = RuntimeContextPreflight(
                object_store=object_store,
                memory_service=MemoryService(object_store),
            )
        self.graph = _build_runtime_graph_or_raise(
            tool_registry=self.tool_registry,
            llm_connector=self.llm_connector,
            model_config=self.model_config,
            context_preflight=self.context_preflight,
            observability_service=self.observability_service,
        )

    def invoke_smoke(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: RuntimeSmokeRequest,
    ) -> RuntimeSmokeResponse:
        run_id = new_id("run")
        thread_id = request.thread_id or new_id("thread")
        return self.invoke_for_run(
            workspace_id=workspace_id,
            identity=identity,
            run_id=run_id,
            thread_id=thread_id,
            user_message=request.user_message,
        )

    def invoke_for_run(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        run_id: str,
        thread_id: str,
        user_message: str,
        initial_messages: list[BaseMessage] | None = None,
        previous_compaction: dict[str, Any] | None = None,
        trace_id: str | None = None,
        model_stream_callback: Callable[[ModelStreamEvent], None] | None = None,
    ) -> RuntimeSmokeResponse:
        messages = (
            initial_messages
            if initial_messages is not None
            else [HumanMessage(content=user_message)]
        )
        initial_state: AgentState = {
            "run_id": run_id,
            "trace_id": trace_id or run_id,
            "thread_id": thread_id,
            "workspace_id": workspace_id,
            "user_id": identity.user_id,
            "role": identity.role,
            "messages": messages,
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 1,
                "output_tokens": 0,
                "total_tokens": 1,
                "usage_estimated": True,
            },
            "memory_snapshot": {},
            "compaction": previous_compaction,
            "warnings": [],
            "context_window_tokens": self.model_config.context_window_tokens,
            "max_output_tokens": self.model_config.max_output_tokens,
            "requires_approval": False,
            "pending_tool_calls": [],
            "tool_iteration_count": 0,
            "max_tool_iterations": 8,
            "model_error": None,
            "context_overflow_retried": False,
        }
        active_graph = self.graph
        if model_stream_callback is not None:
            active_graph = _build_runtime_graph_or_raise(
                tool_registry=self.tool_registry,
                llm_connector=self.llm_connector,
                model_config=self.model_config,
                context_preflight=self.context_preflight,
                model_stream_callback=model_stream_callback,
                observability_service=self.observability_service,
            )
        final_state = active_graph.invoke(initial_state)
        return RuntimeSmokeResponse(
            run_id=final_state["run_id"],
            thread_id=final_state["thread_id"],
            workspace_id=final_state["workspace_id"],
            status=final_state["status"],
            model_error=final_state["model_error"],
            requires_approval=final_state["requires_approval"],
            context_usage=dict(final_state["context_usage"]),
            memory_snapshot=redact_runtime_value(final_state.get("memory_snapshot") or {}),
            compaction=redact_runtime_value(final_state.get("compaction")),
            warnings=list(final_state.get("warnings") or []),
            messages=[serialize_message(message) for message in final_state["messages"]],
            tool_results=redact_runtime_value(final_state["tool_results"]),
            tool_specs=redact_runtime_value(self.tool_registry.model_safe_specs()),
        )
