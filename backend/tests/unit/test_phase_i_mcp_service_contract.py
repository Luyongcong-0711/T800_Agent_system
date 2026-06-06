from __future__ import annotations

from typing import Any

import pytest

from app.jobs.handlers import build_mcp_capability_refresh_handler
from app.jobs.service import JobService
from app.jobs.worker import JobWorker
from app.mcp.service import McpCapabilityProvider, McpService
from app.mcp_client.invocation import McpInvocationError
from app.runtime.tools import build_echo_runtime_context_tool
from app.schemas.identity import RuntimeIdentity
from app.storage.local_object_store import LocalObjectStore


class _FakeProvider(McpCapabilityProvider):
    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload

    def fetch_capabilities(self, server: dict[str, Any]) -> dict[str, Any]:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id="default_user",
        role="owner",
        workspace_id="default",
        workspace_role="owner",
    )


def _capability(*, tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "server_info": {"name": "filesystem", "version": "1.0.0"},
        "tools": tools,
        "resources": [],
        "prompts": [],
    }


def _service(
    tmp_path,
    provider_payload: dict[str, Any] | Exception,
    builtin_tool_names: set[str] | None = None,
) -> McpService:
    object_store = LocalObjectStore(tmp_path / "objects")
    return McpService(
        object_store=object_store,
        job_service=JobService(object_store),
        capability_provider=_FakeProvider(provider_payload),
        builtin_tool_names=builtin_tool_names or {build_echo_runtime_context_tool().name},
    )


def _worker_for(service: McpService) -> JobWorker:
    return JobWorker(
        service.job_service,
        {
            "mcp_capability_refresh_job": build_mcp_capability_refresh_handler(
                service.object_store,
                capability_provider=service.capability_provider,
                builtin_tool_names=service.builtin_tool_names,
            )
        },
    )


def _refresh_with_worker(
    service: McpService,
    *,
    idempotency_key: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    queued = service.refresh_server(
        "default",
        "filesystem",
        _identity(),
        idempotency_key=idempotency_key,
    )
    processed = _worker_for(service).process_next(
        "default",
        job_types=["mcp_capability_refresh_job"],
    )
    snapshot = service.snapshot_store.get_snapshot("default", "filesystem", default={})
    return queued, processed, snapshot


def test_refresh_creates_job_and_writes_capability_snapshot(tmp_path) -> None:
    service = _service(
        tmp_path,
        _capability(
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a workspace file.",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ]
        ),
    )

    queued, processed, snapshot = _refresh_with_worker(
        service,
        idempotency_key="refresh-snapshot",
    )

    assert queued["refresh_job"]["job_type"] == "mcp_capability_refresh_job"
    assert queued["refresh_job"]["status"] == "queued"
    assert queued["snapshot"] == {}
    assert processed["claimed"] is True
    assert processed["job"]["job_id"] == queued["job_id"]
    assert processed["job"]["status"] == "succeeded"
    assert snapshot["stale"] is False
    assert snapshot["tools"][0]["model_name"] == "mcp_filesystem_read_file"
    assert snapshot["tools"][0]["enabled"] is True


def test_refresh_failure_preserves_old_snapshot_and_marks_stale(tmp_path) -> None:
    service = _service(
        tmp_path,
        _capability(
            tools=[
                {
                    "name": "search",
                    "description": "Search previous content.",
                    "input_schema": {"type": "object"},
                }
            ]
        ),
    )
    _, _, first_snapshot = _refresh_with_worker(
        service,
        idempotency_key="refresh-success-before-failure",
    )

    failing_service = McpService(
        object_store=service.object_store,
        job_service=JobService(service.object_store),
        capability_provider=_FakeProvider(TimeoutError("server timeout")),
        builtin_tool_names={build_echo_runtime_context_tool().name},
    )
    queued, processed, failed_snapshot = _refresh_with_worker(
        failing_service,
        idempotency_key="refresh-failure",
    )

    assert queued["refresh_job"]["status"] == "queued"
    assert processed["claimed"] is True
    assert processed["job"]["status"] == "failed"
    assert processed["job"]["current_stage"] == "mcp_capability_refresh"
    assert failed_snapshot["stale"] is True
    assert failed_snapshot["status"] == "tool_list_failed"
    assert failed_snapshot["tools"] == first_snapshot["tools"]
    assert failed_snapshot["last_error"]["error_type"] == "TimeoutError"
    assert (
        failing_service.get_server_health("default", "filesystem")["status"]
        == "tool_list_failed"
    )


def test_refresh_failure_classifies_auth_and_transport_health(tmp_path) -> None:
    auth_service = _service(
        tmp_path / "auth",
        McpInvocationError(
            "mcp_oauth_credential_invalid",
            "OAuth credential is invalid.",
            retryable=False,
        ),
    )
    _, auth_processed, auth_snapshot = _refresh_with_worker(
        auth_service,
        idempotency_key="refresh-auth-failure",
    )

    transport_service = _service(
        tmp_path / "transport",
        McpInvocationError(
            "mcp_http_transport_failed",
            "MCP HTTP transport failed.",
            retryable=True,
        ),
    )
    _, transport_processed, transport_snapshot = _refresh_with_worker(
        transport_service,
        idempotency_key="refresh-transport-failure",
    )

    assert auth_processed["job"]["status"] == "failed"
    assert auth_processed["job"]["manifest"]["owner"] is None
    assert auth_snapshot["status"] == "auth_failed"
    assert auth_snapshot["last_error"]["retryable"] is False
    assert auth_service.get_server_health("default", "filesystem")["status"] == "auth_failed"
    assert transport_processed["job"]["status"] == "failed"
    assert transport_snapshot["status"] == "disconnected"
    assert transport_snapshot["last_error"]["retryable"] is True
    assert (
        transport_service.get_server_health("default", "filesystem")["status"]
        == "disconnected"
    )


def test_refresh_failure_redacts_secret_like_error_messages(tmp_path) -> None:
    service = _service(
        tmp_path,
        McpInvocationError(
            "mcp_http_request_failed",
            "Authorization: Bearer private-refresh-token",
            retryable=False,
            details={"status_code": 401},
        ),
    )

    _, processed, snapshot = _refresh_with_worker(
        service,
        idempotency_key="refresh-redacted-error",
    )
    health = service.get_server_health("default", "filesystem")
    serialized = str({"processed": processed, "snapshot": snapshot, "health": health})

    assert processed["job"]["status"] == "failed"
    assert snapshot["last_error"]["message"] == "Authorization: Bearer ***"
    assert health["last_error"]["message"] == "Authorization: Bearer ***"
    assert "private-refresh-token" not in serialized


def test_mcp_server_health_and_reconnect_queue_refresh_job(tmp_path) -> None:
    service = _service(
        tmp_path,
        _capability(
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a workspace file.",
                    "input_schema": {"type": "object"},
                }
            ]
        ),
    )
    manifest = service.get_server("default", "stdio_real")
    manifest.update(
        {
            "transport": "stdio",
            "type": "stdio",
            "command": "python",
            "args": ["server.py"],
        }
    )
    service.save_server_manifest("default", "stdio_real", manifest)

    before = service.get_server_health("default", "stdio_real")
    reconnect = service.reconnect_server(
        "default",
        "stdio_real",
        _identity(),
        idempotency_key="reconnect-stdio",
    )
    after = service.get_server_health("default", "stdio_real")

    assert before["runtime_configured"] is True
    assert before["next_action"] == "reconnect"
    assert reconnect["refresh_job"]["job_type"] == "mcp_capability_refresh_job"
    assert reconnect["refresh_job"]["status"] == "queued"
    assert reconnect["health"]["status"] == "restarting"
    assert after["status"] == "restarting"
    assert after["reconnect"]["supported"] is True


def test_reconnect_rejects_unconfigured_server_without_queueing_job(tmp_path) -> None:
    service = _service(tmp_path, _capability(tools=[]))
    health = service.get_server_health("default", "filesystem")

    with pytest.raises(Exception, match="transport is not configured"):
        service.reconnect_server(
            "default",
            "filesystem",
            _identity(),
            idempotency_key="reconnect-unconfigured",
        )

    jobs = service.job_service.list_jobs("default")
    after = service.get_server_health("default", "filesystem")
    assert health["reconnect"]["supported"] is False
    assert health["next_action"] == "configure_transport"
    assert jobs == []
    assert after["status"] == "configured"


def test_refresh_job_does_not_commit_snapshot_after_worker_lease_loss(tmp_path) -> None:
    service = _service(
        tmp_path,
        _capability(
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a workspace file.",
                    "input_schema": {"type": "object"},
                }
            ]
        ),
    )

    with pytest.raises(Exception, match="lost its worker lease"):
        service.execute_refresh_job(
            "default",
            "filesystem",
            before_snapshot_commit=lambda: {
                "status": "running",
                "current_stage": "claimed",
            },
        )

    assert service.snapshot_store.get_snapshot("default", "filesystem", default={}) == {}


def test_name_conflict_disables_conflicting_mcp_tool(tmp_path) -> None:
    service = _service(
        tmp_path,
        _capability(
            tools=[
                {
                    "name": "echo_runtime_context",
                    "description": "Conflicts with a built in tool after MCP normalization.",
                    "input_schema": {"type": "object"},
                }
            ]
        ),
        builtin_tool_names={"mcp_filesystem_echo_runtime_context"},
    )

    _, processed, snapshot = _refresh_with_worker(
        service,
        idempotency_key="refresh-name-conflict",
    )
    tool = snapshot["tools"][0]

    assert processed["job"]["status"] == "succeeded"
    assert tool["model_name"] == "mcp_filesystem_echo_runtime_context"
    assert tool["enabled"] is False
    assert tool["disabled_reason"] == "name_conflict"


def test_tool_policy_enable_disable_controls_model_visible_inventory(tmp_path) -> None:
    service = _service(
        tmp_path,
        _capability(
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a workspace file.",
                    "input_schema": {"type": "object"},
                },
                {
                    "name": "list_files",
                    "description": "List workspace files.",
                    "input_schema": {"type": "object"},
                },
            ]
        ),
    )
    _refresh_with_worker(service, idempotency_key="refresh-tool-policy")

    disabled = service.set_tool_policy(
        "default",
        "filesystem",
        "read_file",
        enabled=False,
        identity=_identity(),
    )
    inventory = service.build_mcp_tool_specs("default")

    assert disabled["tool_name"] == "read_file"
    assert {tool["name"] for tool in inventory} == {"mcp_filesystem_list_files"}

    service.set_tool_policy(
        "default",
        "filesystem",
        "read_file",
        enabled=True,
        identity=_identity(),
    )
    inventory = service.build_mcp_tool_specs("default")

    assert {tool["name"] for tool in inventory} == {
        "mcp_filesystem_read_file",
        "mcp_filesystem_list_files",
    }


def test_model_visible_specs_exclude_disabled_and_conflicted_tools(tmp_path) -> None:
    service = _service(
        tmp_path,
        _capability(
            tools=[
                {"name": "safe", "description": "Safe tool.", "input_schema": {"type": "object"}},
                {
                    "name": "conflict",
                    "description": "Conflicting tool.",
                    "input_schema": {"type": "object"},
                },
            ]
        ),
        builtin_tool_names={"mcp_filesystem_conflict"},
    )
    _refresh_with_worker(service, idempotency_key="refresh-model-visible")

    specs = service.build_mcp_tool_specs("default")

    assert [spec["name"] for spec in specs] == ["mcp_filesystem_safe"]
    serialized = str(specs).lower()
    assert "api_key" not in serialized
    assert "token" not in serialized


def test_runtime_tool_registry_loads_enabled_mcp_snapshot_tools(tmp_path) -> None:
    from app.runtime.tools import build_default_tool_registry

    service = _service(
        tmp_path,
        _capability(
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a workspace file.",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ]
        ),
    )
    _refresh_with_worker(service, idempotency_key="refresh-registry")

    registry = build_default_tool_registry(service.object_store)
    specs = registry.model_safe_specs()

    assert "mcp_filesystem_read_file" in {spec["name"] for spec in specs}
    result = registry.invoke("mcp_filesystem_read_file", {"path": "README.md"})
    assert result["ok"] is False
    assert result["error_type"] == "mcp_connector_not_configured"


def test_unconfigured_fallback_mcp_snapshot_is_not_model_visible(tmp_path) -> None:
    from app.mcp.configured_provider import build_configured_mcp_capability_provider
    from app.runtime.tools import build_default_tool_registry

    object_store = LocalObjectStore(tmp_path / "objects")
    service = McpService(
        object_store=object_store,
        job_service=JobService(object_store),
        capability_provider=build_configured_mcp_capability_provider(object_store),
        builtin_tool_names={build_echo_runtime_context_tool().name},
    )

    refreshed = service.execute_refresh_job("default", "filesystem")
    registry = build_default_tool_registry(object_store)
    health = service.get_server_health("default", "filesystem")

    assert refreshed["snapshot"]["capability_source"] == "fallback_unconfigured"
    assert refreshed["snapshot"]["runtime_configured"] is False
    assert refreshed["snapshot"]["status"] == "configured"
    assert refreshed["server"]["status"] == "configured"
    assert refreshed["server"]["tool_count"] == 0
    assert health["status"] == "configured"
    assert health["connected"] is False
    assert health["tool_count"] == 0
    assert service.build_mcp_tool_specs("default") == []
    assert "mcp_filesystem_read_file" not in {
        spec["name"] for spec in registry.model_safe_specs()
    }


def test_runtime_mcp_snapshot_tools_are_workspace_scoped(tmp_path) -> None:
    from app.runtime.tools import build_default_tool_registry

    service = _service(
        tmp_path,
        _capability(
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a workspace file.",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ]
        ),
    )
    service.execute_refresh_job("project_alpha", "filesystem")

    default_registry = build_default_tool_registry(service.object_store)
    scoped_registry = build_default_tool_registry(
        service.object_store,
        workspace_id="project_alpha",
    )

    assert "mcp_filesystem_read_file" not in {
        spec["name"] for spec in default_registry.model_safe_specs()
    }
    assert "mcp_filesystem_read_file" in {
        spec["name"] for spec in scoped_registry.model_safe_specs()
    }
    result = scoped_registry.invoke("mcp_filesystem_read_file", {"path": "README.md"})
    assert result["server_name"] == "filesystem"
    assert result["error_type"] == "mcp_connector_not_configured"


def test_invalid_tool_policy_for_unknown_tool_returns_clear_error(tmp_path) -> None:
    service = _service(tmp_path, _capability(tools=[]))
    _refresh_with_worker(service, idempotency_key="refresh-empty")

    with pytest.raises(Exception, match="MCP tool was not found"):
        service.set_tool_policy(
            "default",
            "filesystem",
            "missing",
            enabled=False,
            identity=_identity(),
        )
