from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_object_store
from app.core.settings import Settings
from app.jobs.service import JobService
from app.main import app
from app.mcp.service import McpService
from app.schemas.secret import CreateSecretRequest
from app.secret_store.crypto import generate_master_key
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_service import SecretService
from app.storage.local_object_store import LocalObjectStore


@pytest.fixture()
def object_store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture()
def client(object_store: LocalObjectStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()


def _capability() -> dict[str, Any]:
    return {
        "server_info": {"name": "filesystem", "version": "1.0.0"},
        "tools": [
            {
                "name": "read_file",
                "description": "Read a workspace file.",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
            {
                "name": "list_files",
                "description": "List workspace files.",
                "input_schema": {"type": "object"},
            },
        ],
        "resources": [],
        "prompts": [],
    }


def _items(body: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    assert isinstance(body, dict)
    value = body.get(key)
    assert isinstance(value, list)
    return value


def _stdio_server_script(tmp_path: Path) -> Path:
    script = tmp_path / "mcp_stdio_refresh_server.py"
    script.write_text(
        """
from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo one message.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    }
]

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "notifications/initialized":
        continue
    response = {"jsonrpc": "2.0", "id": request.get("id")}
    if method == "initialize":
        response["result"] = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stdio-refresh-test", "version": "1.0.0"},
        }
    elif method == "tools/list":
        response["result"] = {"tools": TOOLS}
    else:
        response["error"] = {"code": -32601, "message": "unknown method"}
    print(json.dumps(response), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _create_mcp_secret(
    object_store: LocalObjectStore,
    *,
    secret_type: str,
    display_name: str,
    plaintext: str,
    as_ref: bool = True,
) -> str:
    service = SecretService(
        object_store,
        MasterKeyProvider(Settings(agent_master_key=generate_master_key())),
    )
    created = service.create_secret(
        "default",
        CreateSecretRequest(
            type=secret_type,  # type: ignore[arg-type]
            display_name=display_name,
            plaintext=plaintext,
        ),
        created_by="default_user",
    )
    if as_ref:
        return f"secret_ref://{created.secret_id}"
    return created.secret_id


def test_mcp_refresh_endpoint_creates_job_and_exposes_tools(client: TestClient) -> None:
    response = client.post(
        "/workspaces/default/mcp/servers/filesystem/refresh",
        json={"idempotency_key": "refresh-filesystem"},
    )
    servers_response = client.get("/workspaces/default/mcp/servers")
    pre_worker_tools_response = client.get("/workspaces/default/mcp/servers/filesystem/tools")
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "mcp_capability_refresh_job"},
    )
    tools_response = client.get("/workspaces/default/mcp/servers/filesystem/tools")
    inventory_response = client.get("/workspaces/default/tools/inventory")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"]
    assert body["refresh_job"]["job_type"] == "mcp_capability_refresh_job"
    assert body["refresh_job"]["target_scope"]["scope_type"] == "mcp_server"
    assert body["refresh_job"]["target_scope"]["server_name"] == "filesystem"
    assert body["refresh_job"]["status"] == "queued"
    assert body["snapshot"] == {}
    assert pre_worker_tools_response.status_code == 200
    assert _items(pre_worker_tools_response.json(), "tools") == []
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["job_id"] == body["job_id"]
    assert worker_body["job"]["status"] == "succeeded"
    assert worker_body["job"]["manifest"]["owner"] is None
    assert servers_response.status_code == 200
    assert servers_response.json()["servers"][0]["server_name"] == "filesystem"
    assert tools_response.status_code == 200
    assert len(_items(tools_response.json(), "tools")) == 2
    assert inventory_response.status_code == 200
    assert {
        tool["name"]
        for tool in inventory_response.json()["tools"]
        if tool["source"] == "mcp"
    } == {"mcp_filesystem_read_file", "mcp_filesystem_list_files"}


def test_mcp_health_and_reconnect_endpoint_queue_refresh_job(
    client: TestClient,
    object_store: LocalObjectStore,
    tmp_path: Path,
) -> None:
    service = McpService(object_store=object_store, job_service=JobService(object_store))
    manifest = service.get_server("default", "stdio_real")
    manifest.update(
        {
            "transport": "stdio",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(_stdio_server_script(tmp_path))],
            "timeout_ms": 2000,
        }
    )
    service.save_server_manifest("default", "stdio_real", manifest)

    health_response = client.get("/workspaces/default/mcp/servers/stdio_real/health")
    live_probe_response = client.get(
        "/workspaces/default/mcp/servers/stdio_real/health",
        params={"live_probe": "true"},
    )
    reconnect_response = client.post(
        "/workspaces/default/mcp/servers/stdio_real/reconnect",
        json={"idempotency_key": "reconnect-real-stdio"},
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "mcp_capability_refresh_job"},
    )
    connected_health_response = client.get("/workspaces/default/mcp/servers/stdio_real/health")

    assert health_response.status_code == 200
    health = health_response.json()
    assert health["runtime_configured"] is True
    assert health["reconnect"]["supported"] is True
    assert live_probe_response.status_code == 200
    live_probe = live_probe_response.json()
    assert live_probe["connected"] is True
    assert live_probe["live_probe"]["ok"] is True
    assert live_probe["tool_count"] == 1
    assert reconnect_response.status_code == 200
    reconnect = reconnect_response.json()
    assert reconnect["refresh_job"]["status"] == "queued"
    assert reconnect["health"]["status"] == "restarting"
    assert worker_response.status_code == 200
    assert worker_response.json()["job"]["status"] == "succeeded"
    connected_health = connected_health_response.json()
    assert connected_health["status"] == "connected"
    assert connected_health["connected"] is True
    assert connected_health["tool_count"] == 1


def test_mcp_server_config_endpoint_saves_transport_specific_manifest(
    client: TestClient,
    object_store: LocalObjectStore,
    tmp_path: Path,
) -> None:
    script = _stdio_server_script(tmp_path)
    headers_ref = _create_mcp_secret(
        object_store,
        secret_type="mcp_headers",
        display_name="Custom MCP headers",
        plaintext='{"X-MCP-Token":"private-token"}',
    )
    oauth_ref = _create_mcp_secret(
        object_store,
        secret_type="mcp_oauth_credential",
        display_name="Custom MCP OAuth",
        plaintext='{"access_token":"private-oauth-token"}',
    )

    stdio_response = client.put(
        "/workspaces/default/mcp/servers/custom_runtime",
        json={
            "args": [str(script)],
            "command": sys.executable,
            "cwd": str(tmp_path),
            "enabled": True,
            "env": {"MCP_TEST_MODE": "1"},
            "scope": "workspace",
            "timeout_ms": 2000,
            "transport": "stdio",
        },
    )
    stdio_health_response = client.get("/workspaces/default/mcp/servers/custom_runtime/health")
    http_response = client.put(
        "/workspaces/default/mcp/servers/custom_runtime",
        json={
            "auth_type": "bearer",
            "enabled": True,
            "headers_ref": headers_ref,
            "oauth_credential_ref": oauth_ref,
            "public_headers": {"X-Agent-System": "p0"},
            "scope": "workspace",
            "timeout_ms": 5000,
            "transport": "streamable_http",
            "url": "http://localhost:3939/mcp",
        },
    )

    assert stdio_response.status_code == 200
    stdio_server = stdio_response.json()["server"]
    assert stdio_server["transport"] == "stdio"
    assert stdio_server["command"] == sys.executable
    assert stdio_server["args"] == [str(script)]
    assert stdio_server["url"] is None
    assert stdio_health_response.status_code == 200
    assert stdio_health_response.json()["runtime_configured"] is True

    assert http_response.status_code == 200
    http_server = http_response.json()["server"]
    assert http_server["transport"] == "streamable_http"
    assert http_server["url"] == "http://localhost:3939/mcp"
    assert http_server["public_headers"] == {"X-Agent-System": "p0"}
    assert http_server["command"] is None
    assert http_server["args"] == []


def test_mcp_server_config_rejects_invalid_secret_refs(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    wrong_type_ref = _create_mcp_secret(
        object_store,
        secret_type="model_api_key",
        display_name="Wrong MCP secret type",
        plaintext="sk-not-for-mcp",
    )
    valid_headers_secret_id = _create_mcp_secret(
        object_store,
        secret_type="mcp_headers",
        display_name="Headers secret without URI prefix",
        plaintext='{"Authorization":"Bearer test"}',
        as_ref=False,
    )

    plaintext_response = client.put(
        "/workspaces/default/mcp/servers/bad_runtime",
        json={
            "enabled": True,
            "headers_ref": "raw-token-value",
            "scope": "workspace",
            "transport": "streamable_http",
            "url": "http://localhost:3939/mcp",
        },
    )
    wrong_type_response = client.put(
        "/workspaces/default/mcp/servers/bad_runtime",
        json={
            "enabled": True,
            "headers_ref": wrong_type_ref,
            "scope": "workspace",
            "transport": "streamable_http",
            "url": "http://localhost:3939/mcp",
        },
    )
    missing_prefix_response = client.put(
        "/workspaces/default/mcp/servers/bad_runtime",
        json={
            "enabled": True,
            "headers_ref": valid_headers_secret_id,
            "scope": "workspace",
            "transport": "streamable_http",
            "url": "http://localhost:3939/mcp",
        },
    )

    assert plaintext_response.status_code == 400
    assert plaintext_response.json()["error_type"] == "mcp_secret_ref_invalid"
    assert plaintext_response.json()["details"]["invalid_refs"] == [
        {"field": "headers_ref", "reason": "invalid_ref_format"}
    ]
    assert wrong_type_response.status_code == 400
    assert wrong_type_response.json()["details"]["invalid_refs"] == [
        {"field": "headers_ref", "reason": "secret_type_not_allowed"}
    ]
    assert missing_prefix_response.status_code == 400
    assert missing_prefix_response.json()["details"]["invalid_refs"] == [
        {"field": "headers_ref", "reason": "invalid_ref_format"}
    ]


def test_mcp_refresh_worker_uses_configured_real_stdio_provider(
    client: TestClient,
    object_store: LocalObjectStore,
    tmp_path: Path,
) -> None:
    service = McpService(object_store=object_store, job_service=JobService(object_store))
    manifest = service.get_server("default", "stdio_real")
    manifest.update(
        {
            "transport": "stdio",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(_stdio_server_script(tmp_path))],
            "timeout_ms": 2000,
        }
    )
    service.save_server_manifest("default", "stdio_real", manifest)

    refresh_response = client.post(
        "/workspaces/default/mcp/servers/stdio_real/refresh",
        json={"idempotency_key": "refresh-real-stdio"},
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "mcp_capability_refresh_job"},
    )
    tools_response = client.get("/workspaces/default/mcp/servers/stdio_real/tools")

    assert refresh_response.status_code == 200
    assert worker_response.status_code == 200
    worker_body = worker_response.json()
    assert worker_body["claimed"] is True
    assert worker_body["job"]["status"] == "succeeded"
    tools = _items(tools_response.json(), "tools")
    assert tools[0]["tool_name"] == "echo"
    assert tools[0]["model_name"] == "mcp_stdio_real_echo"


def test_mcp_refresh_rejects_capability_override_for_async_job(client: TestClient) -> None:
    response = client.post(
        "/workspaces/default/mcp/servers/filesystem/refresh",
        json={
            "capability_override": {
                **_capability(),
                "tools": [
                    {
                        "name": "leaky",
                        "description": "Should not be persisted into a queued job.",
                        "input_schema": {
                            "type": "object",
                            "properties": {"api_key": {"type": "string", "default": "sk-secret"}},
                        },
                    }
                ],
            },
            "idempotency_key": "refresh-override-rejected",
        },
    )
    jobs_response = client.get("/workspaces/default/jobs")

    assert response.status_code == 400
    assert response.json()["error_type"] == "mcp_capability_override_async_unsupported"
    assert "sk-secret" not in str(response.json())
    assert jobs_response.status_code == 200
    assert jobs_response.json()["jobs"] == []


def test_mcp_tool_policy_hides_disabled_tool_from_inventory(client: TestClient) -> None:
    refresh_response = client.post(
        "/workspaces/default/mcp/servers/filesystem/refresh",
        json={"idempotency_key": "refresh-policy"},
    )
    worker_response = client.post(
        "/workspaces/default/jobs/process-next",
        params={"job_type": "mcp_capability_refresh_job"},
    )
    assert refresh_response.status_code == 200
    assert refresh_response.json()["refresh_job"]["status"] == "queued"
    assert worker_response.status_code == 200
    assert worker_response.json()["job"]["status"] == "succeeded"

    policy_response = client.post(
        "/workspaces/default/mcp/tools/policy",
        json={"server_name": "filesystem", "tool_name": "read_file", "enabled": False},
    )
    tools_response = client.get("/workspaces/default/mcp/servers/filesystem/tools")
    inventory_response = client.get("/workspaces/default/tools/inventory")

    assert policy_response.status_code == 200
    assert policy_response.json()["enabled"] is False
    read_tool = [
        tool for tool in _items(tools_response.json(), "tools") if tool["tool_name"] == "read_file"
    ][0]
    assert read_tool["enabled"] is False
    assert {
        tool["name"]
        for tool in inventory_response.json()["tools"]
        if tool["source"] == "mcp"
    } == {"mcp_filesystem_list_files"}

    enable_response = client.post(
        "/workspaces/default/mcp/tools/policy",
        json={"server_name": "filesystem", "tool_name": "read_file", "enabled": True},
    )
    assert enable_response.status_code == 200
    assert enable_response.json()["enabled"] is True
