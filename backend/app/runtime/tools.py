from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.storage.object_store import JsonObjectStore
from app.storage.path_builder import run_prefix


class DuplicateToolNameError(ValueError):
    pass


class ToolNotFoundError(KeyError):
    pass


class UnsafeToolSurfaceError(ValueError):
    pass


FORBIDDEN_TOOL_TERMS = (
    "api_key",
    "apikey",
    "password",
    "token",
    "plaintext",
    "secret_value",
    "ciphertext",
    "nonce",
    "tag",
    "authorization",
    "cookie",
    "master_key",
)

FORBIDDEN_CAPABILITY_TERMS = (
    "secretresolver",
    "secret resolver",
    "master key",
    "decrypt",
    "read-secret",
    "read_secret",
    "read secret",
)

SAFE_TOKEN_TERMS = {
    "token_budget",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "max_tokens",
    "context_window_tokens",
    "rollback_token",
}

RUNTIME_REDACT_KEYS = (
    *FORBIDDEN_TOOL_TERMS,
    "secret",
    "env",
    "environment",
    "environ",
)
RUNTIME_REDACT_KEY_SEGMENTS = {
    "password",
    "token",
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "authorization",
    "cookie",
    "secret",
    "env",
    "environment",
    "environ",
}
RUNTIME_REDACT_KEY_PATTERNS = {
    "api_key",
    "apikey",
    "secret_value",
    "master_key",
}

RUNTIME_STRING_PATTERNS = (
    (
        re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[^\s,;]+"),
        "Authorization: Bearer ***",
    ),
    (
        re.compile(r"(?i)\bCookie\s*:\s*[^,\n\r;]+(?:;[^,\n\r]+)*"),
        "Cookie: ***",
    ),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET|MASTER_KEY))\s*=\s*[^\s,;]+"
        ),
        "[redacted]",
    ),
    (
        re.compile(
            r"(?i)\b(?:api[_-]?key|password|plaintext|ciphertext|nonce|tag|"
            r"agent[_-]?master[_-]?key|master[_ -]?key|provider[_ -]?raw[_ -]?payload)"
            r"\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
        ),
        "[redacted]",
    ),
    (
        re.compile(
            r"(?i)\b(?:agent[_ -]?master[_ -]?key|master key|provider raw payload)"
            r"\b[^\n\r,;]*"
        ),
        "[redacted]",
    ),
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
        "sk-***",
    ),
    (
        re.compile(r"(?i)\bsecret_ref\s*[:=]\s*[A-Za-z0-9_.:-]+"),
        "secret_ref=***",
    ),
)


def _normalize_text(value: Any) -> str:
    return str(value).lower().replace("-", "_").replace(" ", "_")


def _contains_forbidden_tool_term(value: Any) -> bool:
    normalized = _normalize_text(value)
    if normalized in SAFE_TOKEN_TERMS:
        return False
    for term in FORBIDDEN_TOOL_TERMS:
        if term == "token" and normalized in SAFE_TOKEN_TERMS:
            continue
        if term in normalized:
            return True
    lowered = str(value).lower()
    return any(term in lowered for term in FORBIDDEN_CAPABILITY_TERMS)


def _collect_schema_text(value: Any) -> list[str]:
    if isinstance(value, dict):
        text: list[str] = []
        for key, item in value.items():
            text.append(str(key))
            text.extend(_collect_schema_text(item))
        return text
    if isinstance(value, list):
        text = []
        for item in value:
            text.extend(_collect_schema_text(item))
        return text
    if isinstance(value, str):
        return [value]
    return []


def _is_runtime_redact_key(key: Any) -> bool:
    normalized = _normalize_text(key)
    if normalized in SAFE_TOKEN_TERMS:
        return False
    if normalized in RUNTIME_REDACT_KEYS:
        return True
    segments = set(normalized.split("_"))
    return bool(
        segments.intersection(RUNTIME_REDACT_KEY_SEGMENTS)
        or any(pattern in normalized for pattern in RUNTIME_REDACT_KEY_PATTERNS)
    )


def redact_runtime_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_runtime_redact_key(key):
                redacted[key] = "***"
            else:
                redacted[key] = redact_runtime_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_runtime_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_runtime_value(item) for item in value)
    if isinstance(value, str):
        redacted = value
        for pattern, replacement in RUNTIME_STRING_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    return value


class EchoRuntimeContextArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1)


def echo_runtime_context(
    run_id: str,
    thread_id: str,
    workspace_id: str,
    user_id: str,
    role: str,
) -> dict[str, str]:
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "role": role,
    }


def build_echo_runtime_context_tool() -> StructuredTool:
    return StructuredTool.from_function(
        func=echo_runtime_context,
        name="echo_runtime_context",
        description="Return non-sensitive runtime identity and run context for smoke testing.",
        args_schema=EchoRuntimeContextArgs,
    )


class ToolRegistry:
    def __init__(
        self,
        tools: list[BaseTool] | None = None,
        object_store: Any | None = None,
    ) -> None:
        self._tools: dict[str, BaseTool] = {}
        self.object_store = object_store
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise DuplicateToolNameError(f"Duplicate tool name: {tool.name}")
        self._assert_model_safe_tool(tool)
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(name) from exc

    def model_safe_specs(self) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for tool in self._tools.values():
            args_schema = tool.args_schema
            args: dict[str, Any] = {}
            if isinstance(args_schema, type) and issubclass(args_schema, BaseModel):
                args = args_schema.model_json_schema()
            elif isinstance(args_schema, dict):
                args = args_schema
            specs.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "args_schema": args,
                    "source": _tool_metadata(tool).get("source") or "built_in",
                    "risk_level": _tool_metadata(tool).get("risk_level") or "low",
                    "requires_approval": bool(
                        _tool_metadata(tool).get("requires_approval") or False
                    ),
                }
            )
        return specs

    def invoke(
        self,
        name: str,
        args: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
        *,
        skip_approval: bool = False,
    ) -> Any:
        tool = self.get(name)
        scoped_args = self._scoped_args(tool, args, runtime_context)
        if not skip_approval:
            approval_result = self._approval_required_result(tool, scoped_args, runtime_context)
            if approval_result is not None:
                return approval_result
        return tool.invoke(scoped_args)

    def scope_args(
        self,
        name: str,
        args: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._scoped_args(self.get(name), args, runtime_context)

    def _scoped_args(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        runtime_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not runtime_context:
            return args
        scoped = dict(args)
        properties = _tool_arg_properties(tool)
        for key in ("workspace_id", "user_id", "role"):
            if key in scoped or key in properties:
                scoped[key] = runtime_context[key]
        return scoped

    def _assert_model_safe_tool(self, tool: BaseTool) -> None:
        args_schema = tool.args_schema
        schema_text: list[str] = []
        if isinstance(args_schema, type) and issubclass(args_schema, BaseModel):
            schema_text = _collect_schema_text(args_schema.model_json_schema())
        elif isinstance(args_schema, dict):
            schema_text = _collect_schema_text(args_schema)
        surfaces = [tool.name, tool.description, *schema_text]
        unsafe_surfaces = [
            str(surface)
            for surface in surfaces
            if _contains_forbidden_tool_term(surface)
        ]
        if unsafe_surfaces:
            joined = ", ".join(sorted(set(unsafe_surfaces))[:5])
            raise UnsafeToolSurfaceError(
                f"Unsafe model-callable tool surface for {tool.name}: {joined}"
            )

    def _approval_required_result(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        runtime_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        metadata = _tool_metadata(tool)
        risk_level = str(metadata.get("risk_level") or "low")
        requires_approval = bool(metadata.get("requires_approval") or False)
        if not requires_approval and risk_level not in {"high", "critical"}:
            return None
        approval_id = new_id("approval")
        operation_plan_key = None
        if self.object_store is not None and runtime_context:
            operation_plan_key = _write_tool_approval_plan(
                self.object_store,
                approval_id=approval_id,
                tool=tool,
                args=args,
                metadata=metadata,
                runtime_context=runtime_context,
            )
        return {
            "ok": False,
            "error_type": "approval_required",
            "message_for_model": "User approval is required before this tool can run.",
            "data": {
                "approval_id": approval_id,
                "approval_kind": "tool_invocation",
                "tool_name": tool.name,
                "risk_level": risk_level,
                "status": "waiting_approval",
                "operation_plan_object_key": operation_plan_key,
            },
        }


def _build_default_tools(
    object_store: Any | None = None,
    *,
    embedding_client: Any | None = None,
    graph_query: Any | None = None,
    include_mcp: bool = False,
    kb_store: Any | None = None,
    knowledge_base_store: Any | None = None,
    milvus: Any | None = None,
    vector_store: Any | None = None,
    workspace_id: str = "default",
) -> list[BaseTool]:
    tools: list[BaseTool] = [build_echo_runtime_context_tool()]
    if object_store is not None:
        from app.tools.builtin.database_tools import build_default_database_tools
        from app.tools.builtin.graph_tools import build_default_graph_tools
        from app.tools.builtin.local_file_tools import build_local_file_tools
        from app.tools.builtin.mcp_tools import build_mcp_snapshot_tools
        from app.tools.builtin.memory_tools import build_default_memory_tools
        from app.tools.builtin.rag_tools import (
            build_document_chunk_get_tool,
            build_rag_search_tool,
        )
        from app.tools.builtin.session_tools import build_default_session_tools
        from app.tools.builtin.skill_tools import build_default_skill_tools
        from app.tools.builtin.subagent_tools import build_default_subagent_tools

        tools.extend(
            [
                build_rag_search_tool(
                    object_store=object_store,
                    embedding_client=embedding_client,
                    kb_store=kb_store,
                    knowledge_base_store=knowledge_base_store,
                    milvus=milvus,
                    vector_store=vector_store,
                ),
                build_document_chunk_get_tool(object_store=object_store),
                *build_default_database_tools(object_store),
                *build_default_graph_tools(
                    object_store,
                    embedding_client=embedding_client,
                    graph_query=graph_query,
                    kb_store=kb_store,
                    knowledge_base_store=knowledge_base_store,
                    milvus=milvus,
                    vector_store=vector_store,
                ),
                *build_default_memory_tools(object_store),
                *build_default_session_tools(object_store),
                *build_local_file_tools(),
                *build_default_skill_tools(object_store),
                *build_default_subagent_tools(object_store),
            ]
        )
        if include_mcp:
            tools.extend(build_mcp_snapshot_tools(object_store, workspace_id=workspace_id))
    return tools


def build_default_reserved_tool_names(object_store: Any | None = None) -> set[str]:
    return {tool.name for tool in _build_default_tools(object_store, include_mcp=False)}


def build_default_tool_registry(
    object_store: Any | None = None,
    *,
    embedding_client: Any | None = None,
    graph_query: Any | None = None,
    kb_store: Any | None = None,
    knowledge_base_store: Any | None = None,
    milvus: Any | None = None,
    vector_store: Any | None = None,
    workspace_id: str = "default",
) -> ToolRegistry:
    tools = _build_default_tools(
        object_store,
        embedding_client=embedding_client,
        graph_query=graph_query,
        include_mcp=True,
        kb_store=kb_store,
        knowledge_base_store=knowledge_base_store,
        milvus=milvus,
        vector_store=vector_store,
        workspace_id=workspace_id,
    )
    return ToolRegistry(tools, object_store=object_store)


def _tool_arg_properties(tool: BaseTool) -> set[str]:
    args_schema = tool.args_schema
    if isinstance(args_schema, type) and issubclass(args_schema, BaseModel):
        properties = args_schema.model_json_schema().get("properties") or {}
    elif isinstance(args_schema, dict):
        properties = args_schema.get("properties") or {}
    else:
        properties = {}
    return set(properties) if isinstance(properties, dict) else set()


def _tool_metadata(tool: BaseTool) -> dict[str, Any]:
    metadata = getattr(tool, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _write_tool_approval_plan(
    object_store: Any,
    *,
    approval_id: str,
    tool: BaseTool,
    args: dict[str, Any],
    metadata: dict[str, Any],
    runtime_context: dict[str, Any],
) -> str:
    workspace_id = str(runtime_context["workspace_id"])
    run_id = str(runtime_context["run_id"])
    now = utc_now_iso()
    operation_plan_key = (
        f"{run_prefix(workspace_id, run_id)}/tool_approvals/{approval_id}/operation_plan.json"
    )
    plan = {
        "schema_version": 1,
        "approval_id": approval_id,
        "approval_kind": "tool_invocation",
        "status": "waiting_approval",
        "phase": "approval_required",
        "approval_ready": True,
        "tool_name": tool.name,
        "source": metadata.get("source") or "built_in",
        "risk_level": metadata.get("risk_level") or "low",
        "requires_approval": bool(metadata.get("requires_approval") or False),
        **(
            {"tool_call_id": runtime_context["tool_call_id"]}
            if runtime_context.get("tool_call_id")
            else {}
        ),
        "runtime_context": redact_runtime_value(runtime_context),
        "args": redact_runtime_value(args),
        "artifacts": {},
        "created_at": now,
        "updated_at": now,
        "revision": 1,
    }
    JsonObjectStore(object_store).write_json(operation_plan_key, plan)
    return operation_plan_key
