from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Protocol

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.jobs.service import JobService
from app.mcp_client.invocation import McpInvocationError
from app.mcp_client.snapshot_store import McpSnapshotStore
from app.runtime.tools import redact_runtime_value
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest
from app.secret_store.secret_service import SecretNotFoundError, SecretService
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import (
    mcp_server_manifest_key,
    mcp_servers_index_key,
)
from app.tools.inventory import build_model_tool_inventory
from app.tools.policy import apply_name_conflict_policy

FORBIDDEN_MODEL_SURFACE_TERMS = (
    "api_key",
    "apikey",
    "password",
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "authorization",
    "cookie",
    "token",
    "secret",
)
MCP_AUTH_ERROR_TYPES = {
    "mcp_oauth_credential_invalid",
    "mcp_secret_headers_invalid",
    "mcp_secret_resolver_missing",
}
MCP_DISCONNECTED_ERROR_TYPES = {
    "mcp_http_empty_response",
    "mcp_http_transport_failed",
    "mcp_http_transient_failure",
    "mcp_invalid_json",
    "mcp_invalid_message",
    "mcp_sse_response_empty",
    "mcp_stdio_process_unavailable",
    "mcp_stdio_timeout",
}
SECRET_REF_PREFIX = "secret_ref://"
MCP_SECRET_REF_FIELDS = {
    "headers_ref": {"mcp_headers"},
    "oauth_credential_ref": {"mcp_oauth_credential"},
}
MCP_SECRET_ENV_ALLOWED_TYPES = {"mcp_headers", "mcp_oauth_credential"}


class McpCapabilityProvider(Protocol):
    def fetch_capabilities(self, server: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def invoke_tool(
        self,
        server: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


class DefaultMcpCapabilityProvider:
    def fetch_capabilities(self, server: dict[str, Any]) -> dict[str, Any]:
        server_name = str(server.get("server_name") or "")
        if server_name == "github":
            return {
                "server_info": {"name": "github", "version": "p0-local-snapshot"},
                "tools": [
                    {
                        "name": "search_issues",
                        "description": "Search GitHub issues through the configured MCP server.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                ],
                "resources": [],
                "prompts": [],
            }
        if server_name == "filesystem":
            return {
                "server_info": {"name": "filesystem", "version": "p0-local-snapshot"},
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read a file from the configured workspace scope.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "list_files",
                        "description": "List files from the configured workspace scope.",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                ],
                "resources": [],
                "prompts": [],
            }
        return {
            "server_info": {"name": server_name, "version": "p0-empty-snapshot"},
            "tools": [],
            "resources": [],
            "prompts": [],
        }

    def invoke_tool(
        self,
        server: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise McpInvocationError(
            "mcp_connector_not_configured",
            "MCP connector is not configured for real invocation.",
            retryable=True,
        )


class McpService:
    def __init__(
        self,
        object_store: ObjectStore,
        job_service: JobService | None = None,
        capability_provider: McpCapabilityProvider | None = None,
        builtin_tool_names: set[str] | None = None,
        secret_service: SecretService | None = None,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.job_service = job_service or JobService(object_store)
        self.capability_provider = capability_provider or DefaultMcpCapabilityProvider()
        self.snapshot_store = McpSnapshotStore(object_store)
        self.builtin_tool_names = builtin_tool_names or set()
        self.secret_service = secret_service

    def list_servers(self, workspace_id: str) -> list[dict[str, Any]]:
        return list(self._servers_index(workspace_id).get("servers", []))

    def get_server(self, workspace_id: str, server_name: str) -> dict[str, Any]:
        return self._server_manifest(workspace_id, self._normalize_component(server_name))

    def list_tools(self, workspace_id: str, server_name: str) -> list[dict[str, Any]]:
        normalized_server = self._normalize_component(server_name)
        snapshot = self.snapshot_store.get_snapshot(workspace_id, normalized_server, default={})
        return list(snapshot.get("tools", []))

    def get_server_health(
        self,
        workspace_id: str,
        server_name: str,
        *,
        live_probe: bool = False,
    ) -> dict[str, Any]:
        normalized_server = self._normalize_component(server_name)
        server = self._server_manifest(workspace_id, normalized_server)
        snapshot = self.snapshot_store.get_snapshot(workspace_id, normalized_server, default={})
        runtime_configured = self._has_runtime_config(server)
        enabled = bool(server.get("enabled", True))
        status = str(server.get("status") or snapshot.get("status") or "configured")
        last_error = server.get("last_error") or snapshot.get("last_error")
        live_probe_result: dict[str, Any] | None = None
        if live_probe and enabled and runtime_configured:
            try:
                raw_capability = self.capability_provider.fetch_capabilities(server)
                live_tool_count = len(raw_capability.get("tools") or [])
                status = "connected"
                last_error = None
                live_probe_result = {
                    "attempted": True,
                    "ok": True,
                    "tool_count": live_tool_count,
                    "probed_at": utc_now_iso(),
                }
            except McpInvocationError as exc:
                status = self._status_for_error(exc.error_type)
                last_error = {
                    "error_type": exc.error_type,
                    "message": str(exc),
                    "retryable": exc.retryable,
                    "failed_at": utc_now_iso(),
                    "status": status,
                }
                live_probe_result = {
                    "attempted": True,
                    "ok": False,
                    "error_type": exc.error_type,
                    "retryable": exc.retryable,
                    "probed_at": last_error["failed_at"],
                }
            except Exception as exc:  # noqa: BLE001 - public health reports connector boundary failures.
                status = "tool_list_failed"
                last_error = {
                    "error_type": exc.__class__.__name__,
                    "message": "MCP live probe failed.",
                    "retryable": True,
                    "failed_at": utc_now_iso(),
                    "status": status,
                }
                live_probe_result = {
                    "attempted": True,
                    "ok": False,
                    "error_type": exc.__class__.__name__,
                    "retryable": True,
                    "probed_at": last_error["failed_at"],
                }
        elif live_probe:
            live_probe_result = {
                "attempted": False,
                "ok": False,
                "reason": "server_disabled" if not enabled else "transport_not_configured",
            }
        reconnectable = enabled and runtime_configured
        next_action = "none"
        if not enabled:
            next_action = "enable_server"
            reconnectable = False
        elif not runtime_configured:
            next_action = "configure_transport"
            reconnectable = False
        elif status != "connected" or (
            snapshot.get("stale")
            and not (live_probe_result and live_probe_result.get("ok") is True)
        ):
            next_action = "reconnect"
        tool_count = (
            int(server["tool_count"])
            if server.get("tool_count") is not None
            else len(snapshot.get("tools", []))
        )
        if live_probe_result and live_probe_result.get("ok") is True:
            tool_count = int(live_probe_result.get("tool_count") or 0)

        return {
            "workspace_id": workspace_id,
            "server_name": normalized_server,
            "enabled": enabled,
            "transport": server["transport"],
            "status": status,
            "runtime_configured": runtime_configured,
            "connected": status == "connected"
            and (
                (live_probe_result is not None and live_probe_result.get("ok") is True)
                or (live_probe_result is None and not bool(snapshot.get("stale")))
            ),
            "stale": False
            if live_probe_result and live_probe_result.get("ok") is True
            else bool(snapshot.get("stale") or server.get("stale")),
            "tool_count": tool_count,
            "last_seen": server.get("last_seen"),
            "last_error": self._public_error(last_error) if isinstance(last_error, dict) else None,
            "snapshot_hash": snapshot.get("snapshot_hash"),
            "snapshot_updated_at": snapshot.get("updated_at"),
            "next_action": next_action,
            "live_probe": live_probe_result,
            "reconnect": {
                "supported": reconnectable,
                "mode": "queued_mcp_capability_refresh_job",
                "refresh_reason": "reconnect",
                "uses_sse_job_progress": True,
            },
        }

    def save_server_manifest(
        self,
        workspace_id: str,
        server_name: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_server = self._normalize_component(server_name)
        now = utc_now_iso()
        normalized_manifest = {
            **manifest,
            "workspace_id": workspace_id,
            "server_name": normalized_server,
            "type": manifest.get("type") or manifest.get("transport") or "http",
            "transport": manifest.get("transport") or manifest.get("type") or "http",
            "updated_at": now,
            "revision": int(manifest.get("revision") or 0) + 1,
        }
        if not normalized_manifest.get("created_at"):
            normalized_manifest["created_at"] = now
        self._validate_secret_refs(workspace_id, normalized_manifest)
        self._write_server_manifest(workspace_id, normalized_server, normalized_manifest)
        self._upsert_server_index(workspace_id, normalized_manifest)
        return normalized_manifest

    def refresh_server(
        self,
        workspace_id: str,
        server_name: str,
        identity: RuntimeIdentity,
        *,
        capability_override: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        refresh_reason: str = "manual",
    ) -> dict[str, Any]:
        normalized_server = self._normalize_component(server_name)
        if capability_override is not None:
            raise AgentSystemError(
                "mcp_capability_override_async_unsupported",
                "Capability override cannot be persisted into an async refresh job.",
                status_code=400,
                retryable=False,
            )
        server = self._server_manifest(workspace_id, normalized_server)
        job = self.job_service.create_job(
            workspace_id,
            identity,
            CreateJobRequest(
                job_type="mcp_capability_refresh_job",
                title=f"MCP capability refresh ({normalized_server})",
                target_scope={
                    "scope_type": "mcp_server",
                    "server_name": normalized_server,
                },
                input={
                    "refresh_reason": refresh_reason,
                    "transport": server["transport"],
                },
                idempotency_key=idempotency_key or new_id("idem_mcp_refresh"),
            ),
        )
        job_id = job["job_id"]
        snapshot = self.snapshot_store.get_snapshot(workspace_id, normalized_server, default={})
        return {
            "workspace_id": workspace_id,
            "server_name": normalized_server,
            "server": server,
            "snapshot": snapshot,
            "refresh_job": job,
            "job_id": job_id,
        }

    def reconnect_server(
        self,
        workspace_id: str,
        server_name: str,
        identity: RuntimeIdentity,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        normalized_server = self._normalize_component(server_name)
        server = self._server_manifest(workspace_id, normalized_server)
        if server.get("enabled") is False:
            raise AgentSystemError(
                "mcp_reconnect_unsupported",
                "MCP server cannot reconnect while disabled.",
                status_code=409,
                retryable=False,
                details={"server_name": normalized_server, "next_action": "enable_server"},
            )
        if not self._has_runtime_config(server):
            raise AgentSystemError(
                "mcp_reconnect_unsupported",
                "MCP server transport is not configured.",
                status_code=400,
                retryable=False,
                details={"server_name": normalized_server, "next_action": "configure_transport"},
            )
        result = self.refresh_server(
            workspace_id,
            normalized_server,
            identity,
            idempotency_key=idempotency_key,
            refresh_reason="reconnect",
        )
        snapshot = self.snapshot_store.get_snapshot(workspace_id, normalized_server, default={})
        server = self._update_server_status(
            workspace_id,
            normalized_server,
            status="restarting",
            tool_count=len(snapshot.get("tools", [])),
            stale=True,
            reconnect_requested=True,
        )
        return {
            **result,
            "server": server,
            "snapshot": snapshot,
            "health": self.get_server_health(workspace_id, normalized_server),
        }

    def execute_refresh_job(
        self,
        workspace_id: str,
        server_name: str,
        *,
        before_snapshot_commit: Callable[[], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_server = self._normalize_component(server_name)
        server = self._server_manifest(workspace_id, normalized_server)
        previous_snapshot = self.snapshot_store.get_snapshot(
            workspace_id,
            normalized_server,
            default={},
        )
        self._update_server_status(
            workspace_id,
            normalized_server,
            status="initializing",
            tool_count=len(previous_snapshot.get("tools", [])),
            stale=True,
        )
        raw_capability = self.capability_provider.fetch_capabilities(server)
        snapshot = self._build_snapshot(workspace_id, server, raw_capability)
        if before_snapshot_commit is not None:
            heartbeat = before_snapshot_commit()
            if (
                heartbeat.get("status") != "running"
                or heartbeat.get("current_stage") != "mcp_capability_refresh_commit"
            ):
                raise AgentSystemError(
                    "mcp_refresh_job_lease_lost",
                    "MCP capability refresh lost its worker lease before snapshot commit.",
                    status_code=409,
                    retryable=True,
                )
        self.snapshot_store.save_snapshot(workspace_id, normalized_server, snapshot)
        snapshot_connected = (
            snapshot.get("status") == "connected"
            and snapshot.get("capability_source") != "fallback_unconfigured"
        )
        server = self._update_server_status(
            workspace_id,
            normalized_server,
            status="connected" if snapshot_connected else "configured",
            tool_count=len(snapshot["tools"]) if snapshot_connected else 0,
            stale=False,
        )
        return {
            "workspace_id": workspace_id,
            "server_name": normalized_server,
            "server": server,
            "snapshot": snapshot,
        }

    def invoke_tool(
        self,
        workspace_id: str,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_server = self._normalize_component(server_name)
        manifest = self._server_manifest(workspace_id, normalized_server)
        if manifest.get("enabled") is False:
            return self._invoke_failure(
                "mcp_server_disabled",
                "MCP server is disabled.",
                server_name=normalized_server,
                tool_name=tool_name,
                retryable=False,
            )

        snapshot = self.snapshot_store.get_snapshot(workspace_id, normalized_server, default={})
        tool = self._find_tool(snapshot, tool_name)
        if tool is None:
            return self._invoke_failure(
                "mcp_tool_not_found",
                "MCP tool was not found in the current capability snapshot.",
                server_name=normalized_server,
                tool_name=tool_name,
                retryable=False,
            )
        if not tool.get("enabled") or tool.get("name_conflict"):
            return self._invoke_failure(
                "mcp_tool_disabled",
                "MCP tool is disabled.",
                server_name=normalized_server,
                tool_name=str(tool.get("tool_name") or tool_name),
                retryable=False,
                details={"disabled_reason": tool.get("disabled_reason")},
            )

        original_tool_name = str(tool.get("tool_name") or tool_name)
        try:
            result = self.capability_provider.invoke_tool(
                manifest,
                original_tool_name,
                arguments or {},
            )
        except McpInvocationError as exc:
            error_type = (
                "mcp_connector_not_configured"
                if exc.error_type in {"mcp_stdio_command_missing", "mcp_http_url_missing"}
                else exc.error_type
            )
            return self._invoke_failure(
                error_type,
                str(exc),
                server_name=normalized_server,
                tool_name=original_tool_name,
                retryable=exc.retryable,
                details=exc.details,
            )
        except Exception as exc:
            return self._invoke_failure(
                exc.__class__.__name__,
                "MCP tool invocation failed.",
                server_name=normalized_server,
                tool_name=original_tool_name,
                retryable=True,
            )
        return {
            "ok": bool(result.get("ok", True)),
            "server_name": normalized_server,
            "tool_name": original_tool_name,
            "content": result.get("content") or [],
            "structured_content": result.get("structured_content")
            or result.get("structuredContent")
            or {},
            "retryable": bool(result.get("retryable", False)),
            **({"error_type": result["error_type"]} if result.get("error_type") else {}),
        }

    def mark_refresh_failed(
        self,
        workspace_id: str,
        server_name: str,
        *,
        error_type: str,
        message: str,
        retryable: bool = True,
    ) -> dict[str, Any]:
        normalized_server = self._normalize_component(server_name)
        status = self._status_for_error(error_type)
        public_message = str(redact_runtime_value(message))
        snapshot = self.snapshot_store.mark_stale_on_refresh_failure(
            workspace_id,
            normalized_server,
            error_type=error_type,
            message=public_message,
            status=status,
            retryable=retryable,
        )
        server = self._update_server_status(
            workspace_id,
            normalized_server,
            status=status,
            tool_count=len(snapshot.get("tools", [])),
            stale=True,
            last_error={
                "error_type": error_type,
                "message": public_message,
                "retryable": retryable,
                "failed_at": snapshot["last_refresh_error"]["failed_at"],
            },
        )
        return {
            "workspace_id": workspace_id,
            "server_name": normalized_server,
            "server": server,
            "snapshot": snapshot,
            "health": self.get_server_health(workspace_id, normalized_server),
        }

    def set_tool_policy(
        self,
        workspace_id: str,
        server_name: str,
        tool_name: str,
        *,
        enabled: bool,
        identity: RuntimeIdentity,
        risk_level: str | None = None,
    ) -> dict[str, Any]:
        normalized_server = self._normalize_component(server_name)
        manifest = self._server_manifest(workspace_id, normalized_server)
        snapshot = self.snapshot_store.get_snapshot(workspace_id, normalized_server, default={})
        tool = self._find_tool(snapshot, tool_name)
        if tool is None:
            raise AgentSystemError(
                "mcp_tool_not_found",
                "MCP tool was not found.",
                status_code=404,
                retryable=False,
                details={"server_name": normalized_server, "tool_name": tool_name},
            )

        original_tool_name = tool["tool_name"]
        policies = dict(manifest.get("tool_policies") or {})
        previous_policy = policies.get(original_tool_name, {})
        policies[original_tool_name] = {
            **previous_policy,
            "enabled": enabled,
            "risk_level": risk_level or previous_policy.get("risk_level") or tool["risk_level"],
            "updated_by": identity.user_id,
            "updated_at": utc_now_iso(),
            "policy_version": int(previous_policy.get("policy_version") or 0) + 1,
        }
        manifest["tool_policies"] = policies
        manifest["updated_at"] = utc_now_iso()
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self._write_server_manifest(workspace_id, normalized_server, manifest)

        snapshot = self._apply_policy_to_snapshot(snapshot, policies)
        self.snapshot_store.save_snapshot(workspace_id, normalized_server, snapshot)
        self._update_server_status(
            workspace_id,
            normalized_server,
            status=manifest.get("status") or "configured",
            tool_count=len(snapshot.get("tools", [])),
            stale=bool(snapshot.get("stale")),
        )
        updated_tool = self._find_tool(snapshot, original_tool_name)
        return {
            "workspace_id": workspace_id,
            "server_name": normalized_server,
            "tool_name": original_tool_name,
            "model_name": updated_tool.get("model_name") if updated_tool else tool["model_name"],
            "enabled": bool(updated_tool.get("enabled")) if updated_tool else enabled,
            "risk_level": policies[original_tool_name]["risk_level"],
            "updated_by": identity.user_id,
            "updated_at": policies[original_tool_name]["updated_at"],
            "policy_version": policies[original_tool_name]["policy_version"],
        }

    def build_mcp_tool_specs(self, workspace_id: str) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for server in self.list_servers(workspace_id):
            if server.get("enabled") is False:
                continue
            snapshot = self.snapshot_store.get_snapshot(
                workspace_id,
                str(server["server_name"]),
                default={},
            )
            if (
                snapshot.get("stale")
                or snapshot.get("status") not in {"connected", "configured"}
                or snapshot.get("capability_source") == "fallback_unconfigured"
            ):
                continue
            for tool in snapshot.get("tools", []):
                candidates.append(
                    {
                        "name": tool["model_name"],
                        "description": tool.get("description") or "",
                        "args_schema": tool.get("args_schema") or {"type": "object"},
                        "source": "mcp",
                        "server_name": server["server_name"],
                        "original_tool_name": tool["tool_name"],
                        "enabled": bool(tool.get("enabled")),
                        "name_conflict": bool(tool.get("name_conflict")),
                        "disabled_reason": tool.get("disabled_reason"),
                        "risk_level": tool.get("risk_level") or "medium",
                        "requires_approval": bool(tool.get("requires_approval") or False),
                    }
                )
        return build_model_tool_inventory(candidates)

    def _bootstrap_snapshot_from_provider(
        self,
        workspace_id: str,
        server_name: str,
    ) -> dict[str, Any]:
        server = self._server_manifest(workspace_id, server_name)
        raw_capability = self.capability_provider.fetch_capabilities(server)
        snapshot = self._build_snapshot(workspace_id, server, raw_capability)
        self.snapshot_store.save_snapshot(workspace_id, server_name, snapshot)
        self._update_server_status(
            workspace_id,
            server_name,
            status="configured",
            tool_count=len(snapshot.get("tools", [])),
            stale=False,
        )
        return snapshot

    def _build_snapshot(
        self,
        workspace_id: str,
        server: dict[str, Any],
        raw_capability: dict[str, Any],
    ) -> dict[str, Any]:
        server_name = server["server_name"]
        policies = server.get("tool_policies") or {}
        raw_tools = raw_capability.get("tools") or []
        tools = [
            self._normalize_tool(server, raw_tool, policies)
            for raw_tool in raw_tools
            if isinstance(raw_tool, dict)
        ]
        runtime_configured = self._has_runtime_config(server)
        capability_source = raw_capability.get("_capability_source") or "mcp_runtime"
        snapshot_connected = capability_source != "fallback_unconfigured"
        snapshot = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "server_name": server_name,
            "transport": server["transport"],
            "status": "connected" if snapshot_connected else "configured",
            "stale": False,
            "runtime_configured": runtime_configured,
            "capability_source": capability_source,
            "server_info": raw_capability.get("server_info") or {"name": server_name},
            "tools": tools,
            "resources": raw_capability.get("resources") or [],
            "prompts": raw_capability.get("prompts") or [],
            "last_error": None,
            "last_refresh_error": None,
            "updated_at": utc_now_iso(),
        }
        snapshot = apply_name_conflict_policy(snapshot, self.builtin_tool_names)
        snapshot = self._apply_policy_to_snapshot(snapshot, policies)
        snapshot["snapshot_hash"] = self._snapshot_hash(snapshot)
        return snapshot

    def _normalize_tool(
        self,
        server: dict[str, Any],
        raw_tool: dict[str, Any],
        policies: dict[str, Any],
    ) -> dict[str, Any]:
        server_name = server["server_name"]
        original_name = str(raw_tool.get("name") or raw_tool.get("tool_name") or "")
        normalized_tool_name = self._normalize_component(original_name)
        model_name = f"mcp_{server_name}_{normalized_tool_name}"
        args_schema = self._safe_args_schema(
            raw_tool.get("input_schema")
            or raw_tool.get("inputSchema")
            or raw_tool.get("args_schema")
            or raw_tool.get("arguments")
        )
        policy = policies.get(original_name) or policies.get(normalized_tool_name) or {}
        policy_enabled = bool(policy.get("enabled", True))
        unsafe_schema = self._contains_forbidden_surface(args_schema)
        enabled = policy_enabled and not unsafe_schema
        disabled_reason = None
        if unsafe_schema:
            disabled_reason = "unsafe_schema"
        elif not policy_enabled:
            disabled_reason = "tool_disabled_by_user"
        return {
            "name": model_name,
            "tool_name": original_name,
            "original_tool_name": original_name,
            "normalized_tool_name": normalized_tool_name,
            "normalized_name": model_name,
            "model_name": model_name,
            "description": str(raw_tool.get("description") or ""),
            "args_schema": args_schema,
            "args_schema_hash": self._value_hash(args_schema),
            "input_schema_hash": self._value_hash(args_schema),
            "source": "mcp",
            "server_name": server_name,
            "transport": server["transport"],
            "risk_level": str(policy.get("risk_level") or raw_tool.get("risk_level") or "medium"),
            "side_effect": bool(raw_tool.get("side_effect") or False),
            "requires_approval": bool(raw_tool.get("requires_approval") or False),
            "enabled": enabled,
            "policy_enabled": policy_enabled,
            "disabled_reason": disabled_reason,
            "name_conflict": False,
            "schema_changed": False,
            "timeout_ms": int(raw_tool.get("timeout_ms") or server.get("timeout_ms") or 30000),
        }

    def _apply_policy_to_snapshot(
        self,
        snapshot: dict[str, Any],
        policies: dict[str, Any],
    ) -> dict[str, Any]:
        next_tools = []
        for tool in snapshot.get("tools", []):
            next_tool = dict(tool)
            policy = policies.get(next_tool["tool_name"]) or {}
            if policy:
                next_tool["policy_enabled"] = bool(policy.get("enabled", True))
                next_tool["risk_level"] = str(policy.get("risk_level") or next_tool["risk_level"])
            if next_tool.get("name_conflict"):
                next_tool["enabled"] = False
                next_tool["disabled_reason"] = "name_conflict"
            elif next_tool.get("policy_enabled") is False:
                next_tool["enabled"] = False
                next_tool["disabled_reason"] = "tool_disabled_by_user"
            elif next_tool.get("disabled_reason") == "unsafe_schema":
                next_tool["enabled"] = False
            else:
                next_tool["enabled"] = True
                next_tool["disabled_reason"] = None
            next_tools.append(next_tool)
        return {**snapshot, "tools": next_tools}

    def _find_tool(self, snapshot: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
        candidates = {tool_name, self._normalize_component(tool_name)}
        for tool in snapshot.get("tools", []):
            values = {
                str(tool.get("tool_name") or ""),
                str(tool.get("original_tool_name") or ""),
                str(tool.get("normalized_tool_name") or ""),
                str(tool.get("model_name") or ""),
                str(tool.get("normalized_name") or ""),
                str(tool.get("name") or ""),
            }
            if candidates & values:
                return tool
        return None

    @staticmethod
    def _invoke_failure(
        error_type: str,
        message: str,
        *,
        server_name: str,
        tool_name: str,
        retryable: bool,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = {
            "ok": False,
            "error_type": error_type,
            "message": str(redact_runtime_value(message)),
            "retryable": retryable,
            "server_name": server_name,
            "tool_name": tool_name,
        }
        if details:
            public_details = redact_runtime_value(details)
            result["details"] = public_details
            if isinstance(public_details, dict) and "disabled_reason" in public_details:
                result["disabled_reason"] = public_details["disabled_reason"]
        return result

    def _server_manifest(self, workspace_id: str, server_name: str) -> dict[str, Any]:
        key = mcp_server_manifest_key(workspace_id, server_name)
        if self.object_store.exists(key):
            return self.json_store.read_json(key)
        manifest = self._default_server_manifest(workspace_id, server_name)
        self._write_server_manifest(workspace_id, server_name, manifest)
        self._upsert_server_index(workspace_id, manifest)
        return manifest

    def _default_server_manifest(self, workspace_id: str, server_name: str) -> dict[str, Any]:
        transport = "stdio" if server_name == "filesystem" else "http"
        now = utc_now_iso()
        return {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "server_name": server_name,
            "type": transport,
            "transport": transport,
            "enabled": True,
            "status": "configured",
            "scope": "workspace",
            "timeout_ms": 30000,
            "tool_policies": {},
            "tool_count": 0,
            "stale": False,
            "last_seen": None,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }

    def _write_server_manifest(
        self,
        workspace_id: str,
        server_name: str,
        manifest: dict[str, Any],
    ) -> None:
        self.json_store.write_json(mcp_server_manifest_key(workspace_id, server_name), manifest)

    def _validate_secret_refs(self, workspace_id: str, manifest: dict[str, Any]) -> None:
        if self.secret_service is None:
            return
        invalid: list[dict[str, str]] = []
        for field, allowed_types in MCP_SECRET_REF_FIELDS.items():
            secret_ref = manifest.get(field)
            if not secret_ref:
                continue
            self._append_secret_ref_validation(
                workspace_id,
                str(secret_ref),
                field=field,
                allowed_types=allowed_types,
                invalid=invalid,
            )
        secret_env_refs = manifest.get("secret_env_refs") or {}
        if isinstance(secret_env_refs, dict):
            for env_name, secret_ref in secret_env_refs.items():
                if not secret_ref:
                    continue
                self._append_secret_ref_validation(
                    workspace_id,
                    str(secret_ref),
                    field=f"secret_env_refs.{env_name}",
                    allowed_types=MCP_SECRET_ENV_ALLOWED_TYPES,
                    invalid=invalid,
                )
        else:
            invalid.append({"field": "secret_env_refs", "reason": "invalid_ref_container"})
        if invalid:
            raise AgentSystemError(
                "mcp_secret_ref_invalid",
                "MCP secret reference is missing, inactive, or has the wrong type.",
                status_code=400,
                retryable=False,
                details={"invalid_refs": invalid},
            )

    def _append_secret_ref_validation(
        self,
        workspace_id: str,
        secret_ref: str,
        *,
        field: str,
        allowed_types: set[str],
        invalid: list[dict[str, str]],
    ) -> None:
        if not secret_ref.startswith(SECRET_REF_PREFIX):
            invalid.append({"field": field, "reason": "invalid_ref_format"})
            return
        secret_id = secret_ref.removeprefix(SECRET_REF_PREFIX)
        assert self.secret_service is not None
        try:
            summary = self.secret_service.get_secret_summary(workspace_id, secret_id)
        except SecretNotFoundError:
            invalid.append({"field": field, "reason": "secret_not_found"})
            return
        if summary.status != "active":
            invalid.append({"field": field, "reason": "secret_not_active"})
            return
        if summary.type not in allowed_types:
            invalid.append({"field": field, "reason": "secret_type_not_allowed"})

    def _update_server_status(
        self,
        workspace_id: str,
        server_name: str,
        *,
        status: str,
        tool_count: int,
        stale: bool,
        last_error: dict[str, Any] | None = None,
        reconnect_requested: bool = False,
    ) -> dict[str, Any]:
        manifest = self._server_manifest(workspace_id, server_name)
        now = utc_now_iso()
        manifest.update(
            {
                "status": status,
                "tool_count": tool_count,
                "stale": stale,
                "last_seen": now if status == "connected" else manifest.get("last_seen"),
                "updated_at": now,
                "revision": int(manifest.get("revision") or 0) + 1,
            }
        )
        if status == "connected":
            manifest["last_error"] = None
            manifest["last_reconnect_requested_at"] = None
        elif last_error is not None:
            manifest["last_error"] = self._public_error(last_error)
        if reconnect_requested:
            manifest["last_reconnect_requested_at"] = now
        self._write_server_manifest(workspace_id, server_name, manifest)
        self._upsert_server_index(workspace_id, manifest)
        return manifest

    def _servers_index(self, workspace_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            mcp_servers_index_key(workspace_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "servers": [],
                "updated_at": None,
                "revision": 0,
            },
        )

    def _upsert_server_index(self, workspace_id: str, manifest: dict[str, Any]) -> None:
        index = self._servers_index(workspace_id)
        summary = {
            "server_name": manifest["server_name"],
            "type": manifest["type"],
            "transport": manifest["transport"],
            "enabled": bool(manifest.get("enabled", True)),
            "status": manifest.get("status") or "configured",
            "scope": manifest.get("scope") or "workspace",
            "tool_count": int(manifest.get("tool_count") or 0),
            "stale": bool(manifest.get("stale") or False),
            "last_seen": manifest.get("last_seen"),
            "last_error": self._public_error(manifest.get("last_error"))
            if isinstance(manifest.get("last_error"), dict)
            else None,
            "updated_at": manifest.get("updated_at") or utc_now_iso(),
        }
        index["servers"] = [
            server
            for server in index.get("servers", [])
            if server["server_name"] != manifest["server_name"]
        ]
        index["servers"].append(summary)
        index["servers"] = sorted(index["servers"], key=lambda item: item["server_name"])
        index["updated_at"] = utc_now_iso()
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(mcp_servers_index_key(workspace_id), index)

    @staticmethod
    def _safe_args_schema(value: Any) -> dict[str, Any]:
        if isinstance(value, dict) and value:
            return value
        return {"type": "object", "properties": {}}

    @staticmethod
    def _normalize_component(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value).strip().lower())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized or "unnamed"

    @staticmethod
    def _has_runtime_config(server: dict[str, Any]) -> bool:
        transport = str(server.get("transport") or server.get("type") or "").lower()
        if transport == "stdio":
            return bool(server.get("command"))
        if transport in {"http", "streamable_http", "sse"}:
            return bool(server.get("url"))
        return False

    @staticmethod
    def _status_for_error(error_type: str) -> str:
        if error_type in MCP_AUTH_ERROR_TYPES:
            return "auth_failed"
        if error_type in MCP_DISCONNECTED_ERROR_TYPES:
            return "disconnected"
        return "tool_list_failed"

    @staticmethod
    def _public_error(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not value:
            return None
        return redact_runtime_value(
            {
                "error_type": value.get("error_type"),
                "message": value.get("message"),
                "retryable": bool(value.get("retryable", True)),
                "failed_at": value.get("failed_at"),
                "status": value.get("status"),
            }
        )

    @staticmethod
    def _value_hash(value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _snapshot_hash(self, snapshot: dict[str, Any]) -> str:
        hashable = {
            "server_name": snapshot["server_name"],
            "capability_source": snapshot.get("capability_source"),
            "runtime_configured": bool(snapshot.get("runtime_configured")),
            "tools": [
                {
                    "model_name": tool["model_name"],
                    "args_schema_hash": tool["args_schema_hash"],
                    "enabled": tool["enabled"],
                    "disabled_reason": tool.get("disabled_reason"),
                }
                for tool in snapshot.get("tools", [])
            ],
            "resources": snapshot.get("resources", []),
            "prompts": snapshot.get("prompts", []),
            "server_info": snapshot.get("server_info", {}),
        }
        return self._value_hash(hashable)

    @staticmethod
    def _contains_forbidden_surface(value: Any) -> bool:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
        normalized = serialized.replace("-", "_").replace(" ", "_")
        return any(term in normalized for term in FORBIDDEN_MODEL_SURFACE_TERMS)


McpInventoryService = McpService
