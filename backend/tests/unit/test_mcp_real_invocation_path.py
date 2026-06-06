from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.settings import Settings
from app.jobs.service import JobService
from app.mcp.configured_provider import build_configured_mcp_capability_provider
from app.mcp.service import McpService
from app.mcp_client.invocation import McpInvocationError, McpJsonRpcInvocationProvider
from app.runtime.tools import build_default_tool_registry
from app.schemas.identity import RuntimeIdentity
from app.schemas.secret import CreateSecretRequest
from app.secret_store.crypto import generate_master_key
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_service import SecretService
from app.storage.local_object_store import LocalObjectStore


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id="default_user",
        role="owner",
        workspace_id="default",
        workspace_role="owner",
    )


def _stdio_server_script(tmp_path: Path) -> Path:
    script = tmp_path / "mcp_stdio_server.py"
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
            "serverInfo": {"name": "stdio-test", "version": "1.0.0"},
        }
    elif method == "tools/list":
        response["result"] = {"tools": TOOLS}
    elif method == "tools/call":
        args = request["params"].get("arguments") or {}
        response["result"] = {
            "content": [{"type": "text", "text": "echo:" + args.get("message", "")}],
            "structuredContent": {"message": args.get("message", "")},
            "isError": False,
        }
    else:
        response["error"] = {"code": -32601, "message": "unknown method"}
    print(json.dumps(response), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _stdio_secret_env_script(tmp_path: Path) -> Path:
    script = tmp_path / "mcp_stdio_secret_env_server.py"
    script.write_text(
        """
from __future__ import annotations

import json
import os
import sys

TOOLS = [
    {
        "name": "env_status",
        "description": "Return whether the private MCP env secret is present.",
        "inputSchema": {"type": "object", "properties": {}},
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
            "serverInfo": {"name": "stdio-secret-test", "version": "1.0.0"},
        }
    elif method == "tools/list":
        response["result"] = {"tools": TOOLS}
    elif method == "tools/call":
        response["result"] = {
            "content": [{"type": "text", "text": "secret_present"}],
            "structuredContent": {"present": bool(os.environ.get("MCP_PRIVATE_TOKEN"))},
            "isError": False,
        }
    else:
        response["error"] = {"code": -32601, "message": "unknown method"}
    print(json.dumps(response), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _stdio_noisy_stderr_script(tmp_path: Path) -> Path:
    script = tmp_path / "mcp_stdio_noisy_stderr_server.py"
    script.write_text(
        """
from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "ping",
        "description": "Return pong.",
        "inputSchema": {"type": "object", "properties": {}},
    }
]

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "notifications/initialized":
        continue
    response = {"jsonrpc": "2.0", "id": request.get("id")}
    if method == "initialize":
        for idx in range(5000):
            print("stderr-line-" + str(idx).zfill(5), file=sys.stderr, flush=True)
        response["result"] = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stdio-noisy-test", "version": "1.0.0"},
        }
    elif method == "tools/list":
        response["result"] = {"tools": TOOLS}
    elif method == "tools/call":
        response["result"] = {
            "content": [{"type": "text", "text": "pong"}],
            "structuredContent": {"ok": True},
            "isError": False,
        }
    else:
        response["error"] = {"code": -32601, "message": "unknown method"}
    print(json.dumps(response), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _stdio_timeout_secret_stderr_script(tmp_path: Path) -> Path:
    script = tmp_path / "mcp_stdio_timeout_stderr_server.py"
    script.write_text(
        """
from __future__ import annotations

import sys
import time

print("MCP_PRIVATE_TOKEN=private-stderr-token", file=sys.stderr, flush=True)
time.sleep(5)
""".lstrip(),
        encoding="utf-8",
    )
    return script


def _configured_stdio_service(tmp_path: Path) -> tuple[McpService, Path]:
    object_store = LocalObjectStore(tmp_path / "objects")
    script = _stdio_server_script(tmp_path)
    service = McpService(
        object_store=object_store,
        job_service=JobService(object_store),
        capability_provider=McpJsonRpcInvocationProvider(),
    )
    manifest = service.get_server("default", "stdio_demo")
    manifest.update(
        {
            "transport": "stdio",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(script)],
            "timeout_ms": 2000,
        }
    )
    service.save_server_manifest("default", "stdio_demo", manifest)
    return service, script


def _secret_service(
    object_store: LocalObjectStore,
    settings: Settings,
) -> SecretService:
    return SecretService(object_store, MasterKeyProvider(settings))


def test_stdio_provider_lists_and_invokes_mcp_tool(tmp_path: Path) -> None:
    service, _ = _configured_stdio_service(tmp_path)

    refreshed = service.execute_refresh_job("default", "stdio_demo")
    result = service.invoke_tool(
        "default",
        "stdio_demo",
        "echo",
        {"message": "hello"},
    )

    assert refreshed["snapshot"]["tools"][0]["model_name"] == "mcp_stdio_demo_echo"
    assert result["ok"] is True
    assert result["server_name"] == "stdio_demo"
    assert result["tool_name"] == "echo"
    assert result["content"][0]["text"] == "echo:hello"
    assert result["structured_content"] == {"message": "hello"}


def test_packaged_mcp_smoke_server_is_available_to_runtime() -> None:
    provider = McpJsonRpcInvocationProvider()
    server = {
        "server_name": "agent_smoke",
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "app.mcp_smoke_server"],
        "timeout_ms": 2000,
    }

    capability = provider.fetch_capabilities(server)
    result = provider.invoke_tool(server, "ping", {"message": "ready"})

    assert capability["server_info"]["name"] == "agent-system-smoke"
    assert capability["tools"][0]["name"] == "ping"
    assert result["ok"] is True
    assert result["content"][0]["text"] == "pong:ready"
    assert result["structured_content"] == {"message": "ready", "ok": True}


def test_streamable_http_provider_retries_transient_tool_call() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        method = payload["method"]
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "session-001"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "http-test", "version": "1.0.0"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "search",
                                "description": "Search remotely.",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    },
                },
            )
        tool_call_count = len(
            [call for call in calls if call["method"] == "tools/call"]
        )
        if method == "tools/call" and tool_call_count == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "remote result"}],
                    "structuredContent": {"ok": True},
                },
            },
        )

    provider = McpJsonRpcInvocationProvider(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
    )
    server = {
        "server_name": "remote",
        "transport": "streamable_http",
        "url": "https://mcp.example.test/mcp",
        "timeout_ms": 2000,
    }

    capability = provider.fetch_capabilities(server)
    result = provider.invoke_tool(server, "search", {"query": "p0"})

    assert capability["tools"][0]["name"] == "search"
    assert result["ok"] is True
    assert result["content"][0]["text"] == "remote result"
    assert [call["method"] for call in calls].count("tools/call") == 2


def test_http_provider_failure_details_are_actionable_and_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            text="Authorization: Bearer private-http-token",
            headers={"content-type": "text/plain"},
        )

    provider = McpJsonRpcInvocationProvider(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    server = {
        "server_name": "remote",
        "transport": "streamable_http",
        "url": "https://mcp.example.test/mcp",
        "timeout_ms": 2000,
    }

    with pytest.raises(McpInvocationError) as exc_info:
        provider.fetch_capabilities(server)

    assert exc_info.value.error_type == "mcp_http_request_failed"
    assert exc_info.value.details["method"] == "initialize"
    assert exc_info.value.details["status_code"] == 401
    assert "private-http-token" not in json.dumps(exc_info.value.details)


def test_http_provider_resolves_headers_ref_without_leaking_secret(tmp_path: Path) -> None:
    settings = Settings(agent_master_key=generate_master_key())
    object_store = LocalObjectStore(tmp_path / "objects")
    secret = _secret_service(object_store, settings).create_secret(
        "default",
        CreateSecretRequest(
            type="mcp_headers",
            display_name="MCP test headers",
            plaintext=json.dumps({"X-MCP-Token": "private-http-token"}),
        ),
        created_by="tester",
    )
    captured_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers.get("x-mcp-token"))
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "serverInfo": {"name": "http-secret-test"},
                    },
                },
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        if payload["method"] == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "content": [{"type": "text", "text": "done"}],
                        "structuredContent": {"ok": True},
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"tools": [{"name": "remote", "inputSchema": {"type": "object"}}]},
            },
        )

    provider = McpJsonRpcInvocationProvider(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        secret_resolver=build_configured_mcp_capability_provider(
            object_store,
            settings,
        ).real_provider.secret_resolver,
    )
    server = {
        "workspace_id": "default",
        "server_name": "remote",
        "transport": "streamable_http",
        "url": "https://mcp.example.test/mcp",
        "headers_ref": secret.secret_ref,
    }

    result = provider.invoke_tool(server, "remote", {})

    assert "private-http-token" in captured_headers
    assert "private-http-token" not in json.dumps(result)
    assert result["ok"] is True


def test_stdio_provider_resolves_secret_env_without_leaking_value(tmp_path: Path) -> None:
    settings = Settings(agent_master_key=generate_master_key())
    object_store = LocalObjectStore(tmp_path / "objects")
    secret = _secret_service(object_store, settings).create_secret(
        "default",
        CreateSecretRequest(
            type="mcp_headers",
            display_name="MCP env token",
            plaintext="private-env-token",
        ),
        created_by="tester",
    )
    service = McpService(
        object_store=object_store,
        job_service=JobService(object_store),
        capability_provider=build_configured_mcp_capability_provider(object_store, settings),
    )
    script = _stdio_secret_env_script(tmp_path)
    manifest = service.get_server("default", "stdio_secret_demo")
    manifest.update(
        {
            "transport": "stdio",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(script)],
            "secret_env_refs": {"MCP_PRIVATE_TOKEN": secret.secret_ref},
            "timeout_ms": 2000,
        }
    )
    service.save_server_manifest("default", "stdio_secret_demo", manifest)

    service.execute_refresh_job("default", "stdio_secret_demo")
    result = service.invoke_tool("default", "stdio_secret_demo", "env_status", {})

    assert result["structured_content"] == {"present": True}
    assert "private-env-token" not in json.dumps(result)


def test_stdio_provider_drains_stderr_without_blocking(tmp_path: Path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = McpService(
        object_store=object_store,
        job_service=JobService(object_store),
        capability_provider=McpJsonRpcInvocationProvider(),
    )
    script = _stdio_noisy_stderr_script(tmp_path)
    manifest = service.get_server("default", "stdio_noisy_demo")
    manifest.update(
        {
            "transport": "stdio",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(script)],
            "timeout_ms": 3000,
        }
    )
    service.save_server_manifest("default", "stdio_noisy_demo", manifest)

    refreshed = service.execute_refresh_job("default", "stdio_noisy_demo")
    result = service.invoke_tool("default", "stdio_noisy_demo", "ping", {})

    assert refreshed["snapshot"]["tools"][0]["model_name"] == "mcp_stdio_noisy_demo_ping"
    assert result["ok"] is True
    assert result["content"][0]["text"] == "pong"


def test_stdio_timeout_exposes_redacted_stderr_tail(tmp_path: Path) -> None:
    provider = McpJsonRpcInvocationProvider()
    script = _stdio_timeout_secret_stderr_script(tmp_path)
    server = {
        "server_name": "stdio_timeout",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(script)],
        "timeout_ms": 500,
    }

    with pytest.raises(McpInvocationError) as exc_info:
        provider.fetch_capabilities(server)

    details = exc_info.value.details
    assert exc_info.value.error_type == "mcp_stdio_timeout"
    assert details["method"] == "initialize"
    assert details["stderr_lines_drained"] >= 1
    assert "private-stderr-token" not in json.dumps(details)


def test_service_blocks_disabled_mcp_tool_before_invocation(tmp_path: Path) -> None:
    service, _ = _configured_stdio_service(tmp_path)
    service.execute_refresh_job("default", "stdio_demo")
    service.set_tool_policy(
        "default",
        "stdio_demo",
        "echo",
        enabled=False,
        identity=_identity(),
    )

    result = service.invoke_tool("default", "stdio_demo", "echo", {"message": "blocked"})

    assert result["ok"] is False
    assert result["error_type"] == "mcp_tool_disabled"
    assert result["retryable"] is False


def test_runtime_registry_invokes_enabled_mcp_stdio_tool(tmp_path: Path) -> None:
    service, _ = _configured_stdio_service(tmp_path)
    service.execute_refresh_job("default", "stdio_demo")

    registry = build_default_tool_registry(service.object_store)
    result = registry.invoke("mcp_stdio_demo_echo", {"message": "runtime"})

    assert result["ok"] is True
    assert result["content"][0]["text"] == "echo:runtime"
