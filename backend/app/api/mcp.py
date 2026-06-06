from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_mcp_service, require_workspace_role
from app.core.time import utc_now_iso
from app.mcp.service import McpService
from app.schemas.identity import RuntimeIdentity
from app.schemas.mcp import (
    McpReconnectRequest,
    McpReconnectResponse,
    McpRefreshRequest,
    McpRefreshResponse,
    McpServerConfigRequest,
    McpServerHealthResponse,
    McpServerDetailResponse,
    McpServersResponse,
    McpToolPolicyUpdateRequest,
    McpToolPolicyUpdateResponse,
    McpToolsResponse,
    ToolInventoryResponse,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/mcp", tags=["mcp"])
tool_inventory_router = APIRouter(prefix="/workspaces/{workspace_id}/tools", tags=["tools"])


@router.get("/servers", response_model=McpServersResponse)
async def list_mcp_servers(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
) -> dict[str, Any]:
    return {"workspace_id": workspace_id, "servers": service.list_servers(workspace_id)}


@router.get("/servers/{server_name}", response_model=McpServerDetailResponse)
async def get_mcp_server(
    workspace_id: str,
    server_name: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
) -> dict[str, Any]:
    server = service.get_server(workspace_id, server_name)
    snapshot = service.snapshot_store.get_snapshot(
        workspace_id,
        server["server_name"],
        default={},
    )
    return {
        "workspace_id": workspace_id,
        "server_name": server["server_name"],
        "server": server,
        "snapshot": snapshot or None,
    }


@router.put("/servers/{server_name}", response_model=McpServerDetailResponse)
async def save_mcp_server(
    workspace_id: str,
    server_name: str,
    request: McpServerConfigRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
) -> dict[str, Any]:
    current = service.get_server(workspace_id, server_name)
    payload = request.model_dump()
    manifest = {
        **current,
        **payload,
        "last_error": None,
        "stale": True,
        "status": "configured",
        "tool_count": 0,
        "type": payload["transport"],
        "transport": payload["transport"],
    }
    if payload["transport"] == "stdio":
        manifest.update(
            {
                "url": None,
                "public_headers": {},
                "headers_ref": None,
                "auth_type": None,
                "oauth_credential_ref": None,
            }
        )
    else:
        manifest.update(
            {
                "command": None,
                "args": [],
                "cwd": None,
                "env": {},
                "secret_env_refs": {},
            }
        )
    server = service.save_server_manifest(workspace_id, server_name, manifest)
    if service.snapshot_store.get_snapshot(workspace_id, server["server_name"], default={}):
        service.snapshot_store.mark_stale_on_refresh_failure(
            workspace_id,
            server["server_name"],
            error_type="mcp_config_changed",
            message="MCP server configuration changed; refresh required.",
            retryable=True,
            status="configured",
        )
    snapshot = service.snapshot_store.get_snapshot(
        workspace_id,
        server["server_name"],
        default={},
    )
    return {
        "workspace_id": workspace_id,
        "server_name": server["server_name"],
        "server": server,
        "snapshot": snapshot or None,
    }


@router.get("/servers/{server_name}/tools", response_model=McpToolsResponse)
async def list_mcp_tools(
    workspace_id: str,
    server_name: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
) -> dict[str, Any]:
    server = service.get_server(workspace_id, server_name)
    return {
        "workspace_id": workspace_id,
        "server_name": server["server_name"],
        "tools": service.list_tools(workspace_id, server["server_name"]),
    }


@router.get("/servers/{server_name}/health", response_model=McpServerHealthResponse)
async def get_mcp_server_health(
    workspace_id: str,
    server_name: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
    live_probe: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    return service.get_server_health(workspace_id, server_name, live_probe=live_probe)


@router.post("/servers/{server_name}/refresh", response_model=McpRefreshResponse)
async def refresh_mcp_server(
    workspace_id: str,
    server_name: str,
    request: McpRefreshRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
) -> dict[str, Any]:
    return service.refresh_server(
        workspace_id,
        server_name,
        identity,
        capability_override=request.capability_override,
        idempotency_key=request.idempotency_key,
        refresh_reason=request.refresh_reason,
    )


@router.post("/servers/{server_name}/reconnect", response_model=McpReconnectResponse)
async def reconnect_mcp_server(
    workspace_id: str,
    server_name: str,
    request: McpReconnectRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
) -> dict[str, Any]:
    return service.reconnect_server(
        workspace_id,
        server_name,
        identity,
        idempotency_key=request.idempotency_key,
    )


@router.post("/tools/policy", response_model=McpToolPolicyUpdateResponse)
async def update_mcp_tool_policy(
    workspace_id: str,
    request: McpToolPolicyUpdateRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
) -> dict[str, Any]:
    return service.set_tool_policy(
        workspace_id,
        request.server_name,
        request.tool_name,
        enabled=request.enabled,
        risk_level=request.risk_level,
        identity=identity,
    )


@tool_inventory_router.get("/inventory", response_model=ToolInventoryResponse)
async def get_tool_inventory(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[McpService, Depends(get_mcp_service)],
) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "tools": service.build_mcp_tool_specs(workspace_id),
        "created_at": utc_now_iso(),
    }
