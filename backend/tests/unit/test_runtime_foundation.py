from __future__ import annotations

import json

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.conversation.service import ConversationService
from app.model_connector.connector import LLMConnector
from app.observability.service import ObservabilityService
from app.runtime.context.preflight import RuntimeContextPreflight
from app.runtime.graph import build_runtime_graph
from app.runtime.runner import RuntimeRunner
from app.runtime.tools import (
    ToolRegistry,
    build_default_tool_registry,
    build_echo_runtime_context_tool,
    redact_runtime_value,
)
from app.schemas.conversation import CreateRunRequest, CreateThreadRequest
from app.schemas.identity import RuntimeIdentity
from app.schemas.model import (
    ModelConfig,
    ModelError,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ModelToolCall,
    ModelUsage,
    ToolCallDelta,
)
from app.schemas.skill import (
    SkillActivateRequest,
    SkillCreateFromProposalRequest,
    SkillEntrypointSpec,
    SkillPermissions,
    SkillProposalRequest,
    SkillSource,
    SkillValidateRequest,
)
from app.skills.service import SkillService
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore
from app.storage.path_builder import (
    run_manifest_key,
    run_skill_context_key,
    thread_compaction_latest_key,
    thread_compaction_lock_key,
    thread_manifest_key,
)


def _assert_no_secret_material(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str).lower()
    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "plaintext" not in serialized
    assert "ciphertext" not in serialized
    assert "nonce" not in serialized
    assert "tag" not in serialized
    assert "agent_master_key" not in serialized


def test_tool_registry_rejects_duplicate_tool_names() -> None:
    registry = ToolRegistry()
    tool = build_echo_runtime_context_tool()

    registry.register(tool)
    with pytest.raises(ValueError, match=tool.name):
        registry.register(tool)


def test_tool_registry_rejects_sensitive_args_schema_fields() -> None:
    class UnsafeArgs(BaseModel):
        api_key: str

    def unsafe_tool(api_key: str) -> dict[str, str]:
        return {"received": api_key}

    tool = StructuredTool.from_function(
        func=unsafe_tool,
        name="unsafe_api_key_tool",
        description="Tool with a sensitive argument that must not be model-visible.",
        args_schema=UnsafeArgs,
    )

    with pytest.raises(ValueError, match="api_key"):
        ToolRegistry().register(tool)


def test_tool_registry_rejects_token_args_schema_fields() -> None:
    class UnsafeArgs(BaseModel):
        access_token: str

    def unsafe_tool(access_token: str) -> dict[str, str]:
        return {"received": access_token}

    tool = StructuredTool.from_function(
        func=unsafe_tool,
        name="unsafe_token_tool",
        description="Tool with a token argument that must not be model-visible.",
        args_schema=UnsafeArgs,
    )

    with pytest.raises(ValueError, match="access_token"):
        ToolRegistry().register(tool)


def test_registered_builtin_tool_invokes_with_structured_redacted_result() -> None:
    registry = build_default_tool_registry()
    args = {
        "run_id": "run_smoke",
        "thread_id": "thread_smoke",
        "workspace_id": "default",
        "user_id": "default_user",
        "role": "owner",
    }

    result = registry.invoke("echo_runtime_context", args)

    assert isinstance(result, dict)
    assert result == args
    _assert_no_secret_material(result)


def test_tool_registry_blocks_approval_required_tool_and_writes_operation_plan(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    invoked = {"value": False}

    class DeleteArgs(BaseModel):
        target_id: str

    def destructive_tool(target_id: str) -> dict[str, str]:
        invoked["value"] = True
        return {"deleted": target_id}

    tool = StructuredTool.from_function(
        func=destructive_tool,
        name="delete_remote_record",
        description="Delete a remote record after user approval.",
        args_schema=DeleteArgs,
        metadata={
            "source": "mcp",
            "risk_level": "high",
            "requires_approval": True,
        },
    )
    registry = ToolRegistry([tool], object_store=object_store)

    result = registry.invoke(
        "delete_remote_record",
        {"target_id": "doc_001"},
        runtime_context={
            "run_id": "run_tool_hook",
            "thread_id": "thread_tool_hook",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
        },
    )

    plan_key = result["data"]["operation_plan_object_key"]
    plan = JsonObjectStore(object_store).read_json(plan_key)
    assert invoked["value"] is False
    assert result["ok"] is False
    assert result["error_type"] == "approval_required"
    assert result["data"]["approval_kind"] == "tool_invocation"
    assert object_store.exists(plan_key)
    assert plan["approval_id"] == result["data"]["approval_id"]
    assert plan["approval_ready"] is True
    assert plan["tool_name"] == "delete_remote_record"
    _assert_no_secret_material([result, plan])


def test_runtime_redacts_sensitive_material_hidden_in_string_values() -> None:
    value = {
        "message": (
            "Authorization: Bearer sk-hidden-token "
            "OPENAI_API_KEY=sk-openai-secret-value "
            "secret_ref=secret_abc123"
        ),
        "output": "Cookie: session=secret-cookie; theme=dark",
    }

    redacted = redact_runtime_value(value)
    serialized = json.dumps(redacted, ensure_ascii=False).lower()

    assert "sk-hidden-token" not in serialized
    assert "sk-openai-secret-value" not in serialized
    assert "secret_abc123" not in serialized
    assert "secret-cookie" not in serialized
    assert "openai_api_key" not in serialized
    assert "authorization: bearer ***" in serialized
    assert "secret_ref=***" in serialized


def test_runtime_redacts_sensitive_label_value_free_text() -> None:
    value = {
        "message": (
            "api_key=abc password=hunter plaintext: clear ciphertext: enc "
            "nonce: n tag: t master key raw payload provider raw payload"
        )
    }

    redacted = redact_runtime_value(value)
    serialized = json.dumps(redacted, ensure_ascii=False).lower()

    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "plaintext" not in serialized
    assert "ciphertext" not in serialized
    assert "nonce" not in serialized
    assert "tag" not in serialized
    assert "master key" not in serialized
    assert "provider raw payload" not in serialized


def test_runtime_graph_invocation_completes_with_default_tool_registry() -> None:
    graph = build_runtime_graph(
        tool_registry=build_default_tool_registry(),
        llm_connector=LLMConnector(),
        model_config=ModelConfig(provider="fake"),
    )
    result = graph.invoke(
        {
            "run_id": "run_smoke",
            "thread_id": "thread_smoke",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": [],
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "model_error": None,
        }
    )

    assert result["status"] == "completed"
    assert result["requires_approval"] is False
    assert result["tool_results"][0]["ok"] is True
    _assert_no_secret_material(result)


def test_runtime_graph_writes_model_and_tool_observability_logs(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    observability = ObservabilityService(
        object_store,
        runtime_instance_id="rt_runtime_unit",
        environment="test",
    )
    graph = build_runtime_graph(
        tool_registry=build_default_tool_registry(object_store),
        llm_connector=LLMConnector(),
        model_config=ModelConfig(provider="fake"),
        observability_service=observability,
    )

    result = graph.invoke(
        {
            "run_id": "run_observe",
            "thread_id": "thread_observe",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": [],
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "model_error": None,
        }
    )

    logs = observability.query_logs(
        workspace_id="default",
        trace_id="run_observe",
        limit=20,
    )
    event_types = {log["event_type"] for log in logs}

    assert result["status"] == "completed"
    assert {
        "model_call_started",
        "model_call_completed",
        "tool_call_started",
        "tool_call_completed",
    } <= event_types
    for log in logs:
        assert log["trace_id"] == "run_observe"
        assert log["run_id"] == "run_observe"
        assert log["thread_id"] == "thread_observe"
        assert log["workspace_id"] == "default"
        assert log["redacted"] is True
    _assert_no_secret_material(logs)


def test_default_registry_exposes_session_history_lookup_tools(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    registry = build_default_tool_registry(object_store)
    specs = registry.model_safe_specs()
    names = {spec["name"] for spec in specs}

    assert {"session_search", "session_message_get"} <= names
    _assert_no_secret_material(specs)


def test_default_registry_exposes_langchain_local_file_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ENABLED", "true")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ROOT", str(tmp_path / "local_workspace"))
    object_store = LocalObjectStore(tmp_path / "objects")
    registry = build_default_tool_registry(object_store)
    specs = registry.model_safe_specs()
    by_name = {spec["name"]: spec for spec in specs}

    assert {
        "read_file",
        "list_directory",
        "file_search",
        "write_file",
        "copy_file",
        "move_file",
        "file_delete",
    } <= set(by_name)
    assert by_name["read_file"]["source"] == "langchain_file_management"
    assert by_name["read_file"]["requires_approval"] is False
    assert by_name["write_file"]["requires_approval"] is True
    assert by_name["file_delete"]["risk_level"] == "high"
    _assert_no_secret_material(specs)


def test_local_file_read_inside_workspace_does_not_require_approval(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "local_workspace"
    workspace_root.mkdir()
    (workspace_root / "inside.txt").write_text("inside", encoding="utf-8")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ENABLED", "true")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ROOT", str(workspace_root))
    registry = build_default_tool_registry(LocalObjectStore(tmp_path / "objects"))

    result = registry.invoke(
        "list_directory",
        {"dir_path": "."},
        runtime_context={
            "workspace_id": "default",
            "thread_id": "thread_local",
            "run_id": "run_local",
            "user_id": "user_local",
            "role": "owner",
            "tool_call_id": "call_local",
        },
    )

    assert "inside.txt" in result


def test_local_file_read_outside_workspace_requires_approval_then_executes(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "local_workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    (outside_root / "outside.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ENABLED", "true")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ROOT", str(workspace_root))
    object_store = LocalObjectStore(tmp_path / "objects")
    registry = build_default_tool_registry(object_store)
    runtime_context = {
        "workspace_id": "default",
        "thread_id": "thread_outside",
        "run_id": "run_outside",
        "user_id": "user_outside",
        "role": "owner",
        "tool_call_id": "call_outside",
    }

    approval = registry.invoke(
        "list_directory",
        {"dir_path": str(outside_root)},
        runtime_context=runtime_context,
    )

    assert approval["error_type"] == "approval_required"
    assert approval["data"]["risk_level"] == "high"
    assert approval["data"]["approval_reason"] == "outside_local_file_root_read"
    assert approval["data"]["target_path"] == str(outside_root.resolve())
    plan = JsonObjectStore(object_store).read_json(approval["data"]["operation_plan_object_key"])
    assert plan["target_path"] == str(outside_root.resolve())

    listed = registry.invoke(
        "list_directory",
        {"dir_path": str(outside_root)},
        runtime_context=runtime_context,
        skip_approval=True,
    )

    assert "outside.txt" in listed


def test_local_file_absolute_path_maps_to_host_root_after_approval(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "local_workspace"
    workspace_root.mkdir()
    host_root = tmp_path / "host"
    host_opt = host_root / "opt"
    host_opt.mkdir(parents=True)
    (host_opt / "agent-system").mkdir()
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ENABLED", "true")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ROOT", str(workspace_root))
    monkeypatch.setenv("LOCAL_FILE_HOST_ROOT", str(host_root))
    object_store = LocalObjectStore(tmp_path / "objects")
    registry = build_default_tool_registry(object_store)
    runtime_context = {
        "workspace_id": "default",
        "thread_id": "thread_host_file",
        "run_id": "run_host_file",
        "user_id": "user_host_file",
        "role": "owner",
        "tool_call_id": "call_host_file",
    }

    approval = registry.invoke(
        "list_directory",
        {"dir_path": "/opt"},
        runtime_context=runtime_context,
    )
    listed = registry.invoke(
        "list_directory",
        {"dir_path": "/opt"},
        runtime_context=runtime_context,
        skip_approval=True,
    )

    assert approval["error_type"] == "approval_required"
    assert approval["data"]["target_path"].endswith("/opt") or approval["data"][
        "target_path"
    ].endswith("\\opt")
    assert "Directory: /opt" in listed
    assert "agent-system/" in listed


def test_runtime_runner_streaming_local_file_tool_string_result_serializes(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "local_workspace"
    workspace_root.mkdir()
    (workspace_root / "inside.txt").write_text("inside", encoding="utf-8")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ENABLED", "true")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ROOT", str(workspace_root))

    class StreamingLocalFileConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config, request
            raise AssertionError("streaming local file test must use stream")

        def stream(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ):
            _ = workspace_id, config
            self.requests.append(request)
            yield ModelStreamEvent(type="message_start", request_id=request.request_id)
            if len(self.requests) == 1:
                yield ModelStreamEvent(
                    type="tool_call_delta",
                    request_id=request.request_id,
                    tool_call_delta=ToolCallDelta(
                        index=0,
                        tool_call_id="call_list_inside",
                        name="list_directory",
                        args={"dir_path": "."},
                    ),
                )
            else:
                yield ModelStreamEvent(
                    type="content_delta",
                    request_id=request.request_id,
                    delta="Listed workspace files.",
                )
            yield ModelStreamEvent(
                type="usage_delta",
                request_id=request.request_id,
                usage=ModelUsage(input_tokens=2, output_tokens=2, total_tokens=4),
            )
            yield ModelStreamEvent(type="message_completed", request_id=request.request_id)
            yield ModelStreamEvent(type="stream_closed", request_id=request.request_id)

    connector = StreamingLocalFileConnector()
    response = RuntimeRunner(
        llm_connector=connector,  # type: ignore[arg-type]
        model_config=ModelConfig(provider="fake", model="stream-local-file-test"),
        object_store=LocalObjectStore(tmp_path / "objects"),
    ).invoke_for_run(
        workspace_id="default",
        identity=RuntimeIdentity(),
        run_id="run_stream_local_file",
        thread_id="thread_stream_local_file",
        user_message="List local files.",
        model_stream_callback=lambda event: None,
    )

    assert response.status == "completed"
    assert len(connector.requests) == 2
    assert response.tool_results[0].ok is True
    assert isinstance(response.tool_results[0].content, str)
    assert "inside.txt" in response.tool_results[0].content


def test_runtime_runner_streaming_local_file_outside_workspace_waits_for_approval(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "local_workspace"
    workspace_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ENABLED", "true")
    monkeypatch.setenv("LOCAL_FILE_TOOLS_ROOT", str(workspace_root))

    class StreamingOutsideFileConnector:
        def stream(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ):
            _ = workspace_id, config
            yield ModelStreamEvent(type="message_start", request_id=request.request_id)
            yield ModelStreamEvent(
                type="tool_call_delta",
                request_id=request.request_id,
                tool_call_delta=ToolCallDelta(
                    index=0,
                    tool_call_id="call_list_outside",
                    name="list_directory",
                    args={"dir_path": str(outside_root)},
                ),
            )
            yield ModelStreamEvent(
                type="usage_delta",
                request_id=request.request_id,
                usage=ModelUsage(input_tokens=2, output_tokens=2, total_tokens=4),
            )
            yield ModelStreamEvent(type="message_completed", request_id=request.request_id)
            yield ModelStreamEvent(type="stream_closed", request_id=request.request_id)

    response = RuntimeRunner(
        llm_connector=StreamingOutsideFileConnector(),  # type: ignore[arg-type]
        model_config=ModelConfig(provider="fake", model="stream-local-file-approval-test"),
        object_store=LocalObjectStore(tmp_path / "objects"),
    ).invoke_for_run(
        workspace_id="default",
        identity=RuntimeIdentity(),
        run_id="run_stream_outside_file",
        thread_id="thread_stream_outside_file",
        user_message="List outside files.",
        model_stream_callback=lambda event: None,
    )

    approval = response.tool_results[0].content
    assert response.status == "waiting_approval"
    assert response.requires_approval is True
    assert approval["error_type"] == "approval_required"
    assert approval["data"]["approval_reason"] == "outside_local_file_root_read"
    assert approval["data"]["target_path"] == str(outside_root.resolve())


def test_runtime_request_includes_compact_model_visible_tool_inventory() -> None:
    class CapturingConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            return ModelResult(
                content="Tools are visible.",
                tool_calls=[],
                usage=ModelUsage(input_tokens=2, output_tokens=2, total_tokens=4),
            )

    connector = CapturingConnector()
    graph = build_runtime_graph(
        tool_registry=build_default_tool_registry(),
        llm_connector=connector,
        model_config=ModelConfig(provider="openai_compatible", model="test-model"),
    )

    result = graph.invoke(
        {
            "run_id": "run_tool_inventory",
            "thread_id": "thread_tool_inventory",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": [HumanMessage(content="What tools can you use?")],
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "tool_iteration_count": 0,
            "max_tool_iterations": 2,
            "model_error": None,
        }
    )

    assert result["status"] == "completed"
    assert len(connector.requests) == 1
    inventory_message = connector.requests[0].messages[1]
    payload = json.loads(inventory_message.content)
    assert inventory_message.role == "system"
    assert "model_visible_tools" in payload
    assert "echo_runtime_context" in {
        tool["name"] for tool in payload["model_visible_tools"]
    }
    assert payload["tool_policy"].startswith("Only call tools")
    _assert_no_secret_material(payload)


def test_session_history_tools_search_and_get_bounded_message_window(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    identity = RuntimeIdentity(
        user_id="default_user",
        role="owner",
        workspace_id="default",
        workspace_role="owner",
    )
    service = ConversationService(
        object_store,
        runtime_runner=RuntimeRunner(object_store=object_store),
    )
    thread = service.create_thread(
        "default",
        identity,
        CreateThreadRequest(title="Architecture decisions"),
    )
    run = service.create_run(
        "default",
        thread["thread_id"],
        identity,
        CreateRunRequest(
            user_message="We decided the data layer is MinIO plus Milvus and Neo4j.",
        ),
        execute_inline=False,
    )
    registry = build_default_tool_registry(object_store)

    search_result = registry.invoke(
        "session_search",
        {
            "workspace_id": "default",
            "user_id": "default_user",
            "query": "Milvus Neo4j",
            "thread_status": ["active"],
            "limit": 5,
        },
    )
    hit = search_result["data"]["hits"][0]
    message_result = registry.invoke(
        "session_message_get",
        {
            "workspace_id": "default",
            "user_id": "default_user",
            "thread_id": hit["thread_id"],
            "message_id": run["user_message_id"],
            "include_neighbor": False,
            "max_chars": 200,
        },
    )

    assert search_result["ok"] is True
    assert hit["thread_id"] == thread["thread_id"]
    assert hit["message_id"] == run["user_message_id"]
    assert "does not create long-term memory" in hit["warning"]
    assert message_result["ok"] is True
    assert message_result["data"]["messages"][0]["message_id"] == run["user_message_id"]
    assert "Milvus and Neo4j" in message_result["data"]["messages"][0]["content"]
    assert "does not inject the entire old thread" in message_result["data"]["warning"]
    _assert_no_secret_material(search_result)
    _assert_no_secret_material(message_result)


def test_runtime_overrides_model_forged_memory_tool_scope(tmp_path) -> None:
    class ForgedScopeConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResult(
                    content="Trying to write memory with forged identity.",
                    tool_calls=[
                        ModelToolCall(
                            tool_call_id="call_memory",
                            name="memory_upsert",
                            args={
                                "workspace_id": "evil_workspace",
                                "user_id": "evil_user",
                                "type": "user_preference",
                                "summary": "User prefers direct answers.",
                                "content": "The user prefers direct answers.",
                            },
                        )
                    ],
                    usage=ModelUsage(input_tokens=2, output_tokens=2, total_tokens=4),
                )
            return ModelResult(
                content="Memory saved under runtime identity.",
                tool_calls=[],
                usage=ModelUsage(input_tokens=3, output_tokens=3, total_tokens=6),
            )

    object_store = LocalObjectStore(tmp_path / "objects")
    connector = ForgedScopeConnector()
    graph = build_runtime_graph(
        tool_registry=build_default_tool_registry(object_store),
        llm_connector=connector,
        model_config=ModelConfig(provider="fake", model="scope-test"),
    )

    result = graph.invoke(
        {
            "run_id": "run_scope",
            "thread_id": "thread_scope",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": [],
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "tool_iteration_count": 0,
            "max_tool_iterations": 2,
            "model_error": None,
        }
    )
    memory_result = result["tool_results"][0]["content"]["data"]

    assert result["status"] == "completed"
    assert memory_result["user_id"] == "default_user"
    assert memory_result["workspace_id"] is None
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert "evil_user" not in serialized
    assert "evil_workspace" not in serialized


def test_project_memory_tool_write_creates_review_candidate(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    registry = build_default_tool_registry(object_store)

    proposed = registry.invoke(
        "memory_upsert",
        {
            "workspace_id": "default",
            "user_id": "default_user",
            "type": "project_fact",
            "field": "storage_stack",
            "summary": "Storage stack uses MinIO, Milvus, and Neo4j.",
            "content": "The project stores objects in MinIO and indexes Milvus/Neo4j.",
        },
    )
    review = registry.invoke(
        "memory_review",
        {
            "workspace_id": "default",
            "user_id": "default_user",
            "limit": 10,
        },
    )

    assert proposed["ok"] is False
    assert proposed["error_type"] == "approval_required"
    assert proposed["data"]["status"] == "pending_approval"
    assert proposed["data"]["enabled_for_model_context"] is False
    assert review["data"]["candidates"][0]["memory_id"] == proposed["data"]["memory_id"]


def test_context_preflight_injects_activated_skill_context(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    JsonObjectStore(object_store).write_json(
        run_skill_context_key("default", "run_skill", "contract_cleaner"),
        {
            "schema_version": 1,
            "workspace_id": "default",
            "run_id": "run_skill",
            "thread_id": "thread_skill",
            "skill_id": "contract_cleaner",
            "version": "0.1.0",
            "display_name": "Contract cleaner",
            "summary": "Normalize contract clauses.",
            "workflow_summary": ["Normalize headings.", "Extract obligations."],
            "knowledge_notes": ["Party B can be called supplier."],
            "entrypoint_tools": ["skill_contract_cleaner_normalize_contract"],
            "created_at": "2026-05-31T00:00:00.000Z",
        },
    )
    preflight = RuntimeContextPreflight(object_store=object_store, memory_service=None)

    state = preflight.invoke(
        {
            "run_id": "run_skill",
            "thread_id": "thread_skill",
            "workspace_id": "default",
            "user_id": "default_user",
            "messages": [HumanMessage(content="Clean this contract.")],
            "context_usage": {},
            "warnings": [],
            "context_window_tokens": 200000,
            "max_output_tokens": 8192,
            "compaction": None,
        }
    )

    assert state["skill_context"]["activated_skills"][0]["skill_id"] == "contract_cleaner"
    assert "activated_skill_context" in str(state["messages"][0].content)
    assert "Normalize headings." in str(state["messages"][0].content)
    assert "Only call tools that are present" in str(state["messages"][0].content)


def test_context_preflight_persists_latest_compaction_pointer_and_releases_lock(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    preflight = RuntimeContextPreflight(object_store=object_store, memory_service=None)
    messages = [
        HumanMessage(content=f"historical message {index} " + ("x" * 80))
        for index in range(32)
    ]

    state = preflight.invoke(
        {
            "run_id": "run_compaction_latest",
            "thread_id": "thread_compaction_latest",
            "workspace_id": "default",
            "user_id": "default_user",
            "messages": messages,
            "context_usage": {},
            "warnings": [],
            "context_window_tokens": 500,
            "max_output_tokens": 50,
            "compaction": None,
        }
    )
    latest = JsonObjectStore(object_store).read_json(
        thread_compaction_latest_key("default", "thread_compaction_latest")
    )
    lock = JsonObjectStore(object_store).read_json(
        thread_compaction_lock_key("default", "thread_compaction_latest")
    )

    assert state["compaction"]["compaction_id"] == latest["compaction_id"]
    assert latest["compaction_object_key"]
    assert latest["strategy"] == "hermes_style_head_summary_tail"
    assert lock["holder_run_id"] is None
    assert "session_hygiene_compaction_triggered" in state["warnings"]


def test_runtime_graph_returns_tool_result_to_model_before_final_answer() -> None:
    class ToolLoopConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResult(
                    content="I need to inspect runtime context.",
                    tool_calls=[
                        ModelToolCall(
                            tool_call_id="call_context",
                            name="echo_runtime_context",
                            args={
                                "run_id": "run_tool_loop",
                                "thread_id": "thread_tool_loop",
                                "workspace_id": "default",
                                "user_id": "default_user",
                                "role": "owner",
                            },
                        )
                    ],
                    usage=ModelUsage(input_tokens=2, output_tokens=4, total_tokens=6),
                )
            return ModelResult(
                content="Final answer after reading the tool result.",
                tool_calls=[],
                usage=ModelUsage(input_tokens=4, output_tokens=8, total_tokens=12),
            )

    connector = ToolLoopConnector()
    graph = build_runtime_graph(
        tool_registry=build_default_tool_registry(),
        llm_connector=connector,
        model_config=ModelConfig(provider="openai_compatible", model="test-model"),
    )

    result = graph.invoke(
        {
            "run_id": "run_tool_loop",
            "thread_id": "thread_tool_loop",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": [],
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "model_error": None,
        }
    )

    assert result["status"] == "completed"
    assert len(connector.requests) == 2
    assert any(message.role == "tool" for message in connector.requests[1].messages)
    assert any(
        message.role == "assistant" and message.tool_calls
        for message in connector.requests[1].messages
    )
    assert result["messages"][-1].content == "Final answer after reading the tool result."
    assert "Runtime smoke completed" not in result["messages"][-1].content
    assert result["tool_results"][0]["ok"] is True


def test_runtime_graph_pauses_when_tool_requires_approval() -> None:
    class ApprovalArgs(BaseModel):
        request_id: str

    def approval_tool(request_id: str) -> dict[str, object]:
        return {
            "ok": False,
            "error_type": "approval_required",
            "retryable": False,
            "message_for_model": "User approval is required before continuing.",
            "data": {
                "approval_id": f"approval_{request_id}",
                "operation_plan_object_key": "runs/run_approval/artifacts/operation_plan.json",
            },
        }

    class ApprovalConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            return ModelResult(
                content="I need approval before continuing.",
                tool_calls=[
                    ModelToolCall(
                        tool_call_id="call_approval",
                        name="approval_tool",
                        args={"request_id": "001"},
                    )
                ],
                usage=ModelUsage(input_tokens=2, output_tokens=3, total_tokens=5),
            )

    connector = ApprovalConnector()
    graph = build_runtime_graph(
        tool_registry=ToolRegistry(
            [
                StructuredTool.from_function(
                    func=approval_tool,
                    name="approval_tool",
                    description="Return a pending approval envelope for runtime testing.",
                    args_schema=ApprovalArgs,
                )
            ]
        ),
        llm_connector=connector,
        model_config=ModelConfig(provider="openai_compatible", model="test-model"),
    )

    result = graph.invoke(
        {
            "run_id": "run_approval",
            "thread_id": "thread_approval",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": [],
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "tool_iteration_count": 0,
            "max_tool_iterations": 2,
            "model_error": None,
        }
    )

    assert result["status"] == "waiting_approval"
    assert result["requires_approval"] is True
    assert len(connector.requests) == 1
    assert result["tool_results"][0]["ok"] is False
    assert result["tool_results"][0]["error_type"] == "approval_required"
    _assert_no_secret_material(result)


def test_runtime_graph_skill_entrypoint_call_executes_validated_script(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    json_store = JsonObjectStore(object_store)
    service = SkillService(object_store)
    identity = RuntimeIdentity(
        user_id="default_user",
        role="owner",
        workspace_id="default",
        workspace_role="owner",
    )
    proposal = service.create_proposal(
        "default",
        identity,
        SkillProposalRequest(
            display_name="Runtime script skill",
            description="Run a validated script from the runtime graph.",
            when_to_use=["Runtime needs a scripted normalization."],
            workflow_steps=["Normalize the document id."],
            knowledge_notes=[],
            entrypoints=[
                SkillEntrypointSpec(
                    name="normalize_contract",
                    type="script",
                    runtime="python",
                    args_schema={
                        "type": "object",
                        "required": ["document_id"],
                        "properties": {"document_id": {"type": "string"}},
                    },
                    risk_level="medium",
                    script_required=True,
                    script_content=(
                        "def main(args):\n"
                        "    return {'normalized_document_id': args['document_id']}\n"
                    ),
                )
            ],
            permissions=SkillPermissions(network=False),
            script_required=True,
            source=SkillSource(thread_id="thread_skill_runtime", run_id="run_skill_runtime"),
        ),
    )
    skill = service.materialize_proposal(
        "default",
        identity,
        SkillCreateFromProposalRequest(
            proposal_id=proposal["proposal_id"],
            approval_id=proposal["approval_id"],
            skill_id="runtime_script_skill",
            version="0.1.0",
        ),
    )
    service.validate_skill_scripts(
        "default",
        skill["skill_id"],
        SkillValidateRequest(version="0.1.0"),
    )
    now = "2026-05-31T00:00:00.000Z"
    json_store.write_json(
        thread_manifest_key("default", "thread_skill_runtime"),
        {
            "workspace_id": "default",
            "thread_id": "thread_skill_runtime",
            "user_id": "default_user",
            "title": "Runtime script skill",
            "status": "active",
            "current_run_id": "run_skill_runtime",
            "current_run_status": "running",
            "created_at": now,
            "updated_at": now,
        },
    )
    json_store.write_json(
        run_manifest_key("default", "run_skill_runtime"),
        {
            "workspace_id": "default",
            "thread_id": "thread_skill_runtime",
            "run_id": "run_skill_runtime",
            "status": "running",
            "idempotency_key": "runtime-script-skill",
            "created_at": now,
            "updated_at": now,
        },
    )
    activation = service.activate_skill(
        "default",
        skill["skill_id"],
        SkillActivateRequest(
            run_id="run_skill_runtime",
            thread_id="thread_skill_runtime",
            reason="Runtime needs this Skill.",
        ),
    )

    class SkillToolConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResult(
                    content="Calling activated Skill.",
                    tool_calls=[
                        ModelToolCall(
                            tool_call_id="call_skill_entrypoint",
                            name="skill_entrypoint_call",
                            args={
                                "workspace_id": "default",
                                "user_id": "default_user",
                                "run_id": "run_skill_runtime",
                                "thread_id": "thread_skill_runtime",
                                "entrypoint_tool_name": activation[
                                    "activated_entrypoint_tools"
                                ][0],
                                "args": {"document_id": "doc_001"},
                                "tool_call_id": "call_skill_entrypoint",
                            },
                        )
                    ],
                    usage=ModelUsage(input_tokens=2, output_tokens=4, total_tokens=6),
                )
            return ModelResult(
                content="Final answer after Skill result.",
                tool_calls=[],
                usage=ModelUsage(input_tokens=4, output_tokens=8, total_tokens=12),
            )

    connector = SkillToolConnector()
    graph = build_runtime_graph(
        tool_registry=build_default_tool_registry(object_store),
        llm_connector=connector,
        model_config=ModelConfig(provider="openai_compatible", model="test-model"),
    )

    result = graph.invoke(
        {
            "run_id": "run_skill_runtime",
            "thread_id": "thread_skill_runtime",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": [],
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "tool_iteration_count": 0,
            "max_tool_iterations": 2,
            "model_error": None,
        }
    )

    tool_content = result["tool_results"][0]["content"]
    assert result["status"] == "completed"
    assert len(connector.requests) == 2
    assert any(message.role == "tool" for message in connector.requests[1].messages)
    assert tool_content["ok"] is True
    assert tool_content["data"]["result"]["normalized_document_id"] == "doc_001"
    assert object_store.exists(tool_content["artifacts"]["result_object_key"])
    assert result["messages"][-1].content == "Final answer after Skill result."
    _assert_no_secret_material(result)


def test_runtime_graph_stops_repeated_tool_call_loop_at_configured_limit() -> None:
    class RepeatingToolConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            return ModelResult(
                content="Still requesting the same tool.",
                tool_calls=[
                    ModelToolCall(
                        tool_call_id=f"call_context_{len(self.requests)}",
                        name="echo_runtime_context",
                        args={
                            "run_id": "run_loop_limit",
                            "thread_id": "thread_loop_limit",
                            "workspace_id": "default",
                            "user_id": "default_user",
                            "role": "owner",
                        },
                    )
                ],
                usage=ModelUsage(input_tokens=2, output_tokens=2, total_tokens=4),
            )

    connector = RepeatingToolConnector()
    graph = build_runtime_graph(
        tool_registry=build_default_tool_registry(),
        llm_connector=connector,
        model_config=ModelConfig(provider="openai_compatible", model="test-model"),
    )

    result = graph.invoke(
        {
            "run_id": "run_loop_limit",
            "thread_id": "thread_loop_limit",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": [],
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "tool_iteration_count": 0,
            "max_tool_iterations": 2,
            "model_error": None,
        }
    )

    assert result["status"] == "failed"
    assert result["model_error"] == "max_tool_iterations_exceeded"
    assert len(result["tool_results"]) == 2
    assert len(connector.requests) == 3


def test_runtime_retries_once_after_context_overflow_with_forced_compaction(tmp_path) -> None:
    class OverflowOnceConnector:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def call(
            self,
            workspace_id: str,
            config: ModelConfig,
            request: ModelRequest,
        ) -> ModelResult:
            _ = workspace_id, config
            self.requests.append(request)
            if len(self.requests) == 1:
                raise ModelError("context_overflow", "Provider context limit exceeded.")
            return ModelResult(
                content="Recovered after forced compaction.",
                tool_calls=[],
                usage=ModelUsage(input_tokens=10, output_tokens=3, total_tokens=13),
            )

    messages = [
        HumanMessage(content=f"historical user message {index} " + ("x" * 40))
        for index in range(30)
    ]
    connector = OverflowOnceConnector()
    object_store = LocalObjectStore(tmp_path / "objects")
    graph = build_runtime_graph(
        tool_registry=build_default_tool_registry(object_store),
        llm_connector=connector,
        model_config=ModelConfig(provider="openai_compatible", model="test-model"),
        context_preflight=RuntimeContextPreflight(object_store=object_store, memory_service=None),
    )

    result = graph.invoke(
        {
            "run_id": "run_overflow_retry",
            "thread_id": "thread_overflow_retry",
            "workspace_id": "default",
            "user_id": "default_user",
            "role": "owner",
            "messages": messages,
            "tool_results": [],
            "status": "created",
            "context_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usage_estimated": True,
            },
            "requires_approval": False,
            "pending_tool_calls": [],
            "tool_iteration_count": 0,
            "max_tool_iterations": 2,
            "context_window_tokens": 200000,
            "max_output_tokens": 8192,
            "compaction": None,
            "warnings": [],
            "model_error": None,
            "context_overflow_retried": False,
        }
    )

    assert result["status"] == "completed"
    assert len(connector.requests) == 2
    assert result["compaction"]["strategy"] == "hermes_style_head_summary_tail"
    assert "context_overflow_retry_compacted" in result["warnings"]
    assert result["messages"][-1].content == "Recovered after forced compaction."
