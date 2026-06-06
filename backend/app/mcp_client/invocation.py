from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from typing import Any

import httpx

from app.runtime.tools import redact_runtime_value


class McpInvocationError(RuntimeError):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.error_type = error_type
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)


class McpJsonRpcInvocationProvider:
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        max_retries: int = 1,
        secret_resolver: Any | None = None,
    ) -> None:
        self.http_client = http_client or httpx.Client()
        self.max_retries = max(0, max_retries)
        self.secret_resolver = secret_resolver

    def fetch_capabilities(self, server: dict[str, Any]) -> dict[str, Any]:
        client = self._client_for(server)
        with client:
            listed = client.list_tools()
        return {
            "server_info": listed.get("server_info") or {"name": server.get("server_name")},
            "tools": listed.get("tools") or [],
            "resources": listed.get("resources") or [],
            "prompts": listed.get("prompts") or [],
        }

    def invoke_tool(
        self,
        server: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._client_for(server)
        with client:
            return client.call_tool(tool_name, arguments or {})

    def _client_for(self, server: dict[str, Any]) -> _McpSession:
        transport = str(server.get("transport") or server.get("type") or "").lower()
        timeout_ms = int(server.get("timeout_ms") or 30000)
        if transport == "stdio":
            command = str(server.get("command") or "")
            if not command:
                raise McpInvocationError(
                    "mcp_stdio_command_missing",
                    "MCP stdio server command is not configured.",
                    retryable=False,
                )
            return _StdioMcpSession(
                server,
                secret_resolver=self.secret_resolver,
                timeout_ms=timeout_ms,
            )
        if transport in {"http", "streamable_http", "sse"}:
            url = str(server.get("url") or "")
            if not url:
                raise McpInvocationError(
                    "mcp_http_url_missing",
                    "MCP HTTP server URL is not configured.",
                    retryable=False,
                )
            return _HttpMcpSession(
                server,
                http_client=self.http_client,
                max_retries=self.max_retries,
                secret_resolver=self.secret_resolver,
                timeout_ms=timeout_ms,
            )
        raise McpInvocationError(
            "mcp_transport_unsupported",
            "MCP transport is not supported.",
            retryable=False,
            details={"transport": transport},
        )


class _McpSession:
    def __enter__(self) -> _McpSession:
        self.initialize()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def initialize(self) -> None:
        raise NotImplementedError

    def list_tools(self) -> dict[str, Any]:
        raise NotImplementedError

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        return None


class _StdioMcpSession(_McpSession):
    def __init__(
        self,
        server: dict[str, Any],
        *,
        timeout_ms: int,
        secret_resolver: Any | None = None,
    ) -> None:
        self.server = server
        self.secret_resolver = secret_resolver
        self.timeout_seconds = timeout_ms / 1000
        self.request_id = 0
        self.process: subprocess.Popen[str] | None = None
        self.stdout_queue: queue.Queue[str] = queue.Queue()
        self.stderr_tail: deque[str] = deque(maxlen=20)
        self.stderr_lines_drained = 0

    def initialize(self) -> None:
        env = None
        if isinstance(self.server.get("env"), dict) and self.server["env"]:
            import os

            env = {**os.environ, **{str(k): str(v) for k, v in self.server["env"].items()}}
        secret_env = self._resolved_secret_env()
        if secret_env:
            import os

            env = {**os.environ, **(env or {}), **secret_env}
        self.process = subprocess.Popen(
            [str(self.server["command"]), *[str(arg) for arg in self.server.get("args") or []]],
            cwd=str(self.server["cwd"]) if self.server.get("cwd") else None,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        response = self._request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "agent-system", "version": "p0"},
            },
        )
        self.server_info = response.get("serverInfo") or response.get("server_info") or {}
        self._notify("notifications/initialized", {})

    def list_tools(self) -> dict[str, Any]:
        response = self._request("tools/list", {})
        return {"server_info": self.server_info, "tools": response.get("tools") or []}

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        return _tool_result(response, self.server, tool_name)

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self.stdout_queue.put(line)

    def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_tail.append(line.rstrip())
            self.stderr_lines_drained += 1

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.request_id += 1
        request_id = self.request_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                line = self.stdout_queue.get(timeout=remaining)
            except queue.Empty as exc:
                raise McpInvocationError(
                    "mcp_stdio_timeout",
                    "MCP stdio request timed out.",
                    retryable=True,
                    details=self._stdio_diagnostics(method=method),
                ) from exc
            message = _parse_json_message(line)
            if message.get("id") != request_id:
                continue
            return _json_rpc_result(message)
        raise McpInvocationError(
            "mcp_stdio_timeout",
            "MCP stdio request timed out.",
            retryable=True,
            details=self._stdio_diagnostics(method=method),
        )

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            raise McpInvocationError(
                "mcp_stdio_process_unavailable",
                "MCP stdio process is unavailable.",
                retryable=True,
                details=self._stdio_diagnostics(),
            )
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _resolved_secret_env(self) -> dict[str, str]:
        refs = self.server.get("secret_env_refs") or {}
        if not isinstance(refs, dict) or not refs:
            return {}
        resolved: dict[str, str] = {}
        for env_name, secret_ref in refs.items():
            resolved[str(env_name)] = _resolve_mcp_secret(
                self.secret_resolver,
                self.server,
                str(secret_ref),
            )
        return resolved

    def _stdio_diagnostics(self, *, method: str | None = None) -> dict[str, Any]:
        details: dict[str, Any] = {
            "stderr_lines_drained": self.stderr_lines_drained,
            "stderr_tail": list(self.stderr_tail),
        }
        if method:
            details["method"] = method
        if self.process is not None:
            details["exit_code"] = self.process.poll()
        return redact_runtime_value(details)


class _HttpMcpSession(_McpSession):
    def __init__(
        self,
        server: dict[str, Any],
        *,
        http_client: httpx.Client,
        max_retries: int,
        secret_resolver: Any | None,
        timeout_ms: int,
    ) -> None:
        self.server = server
        self.http_client = http_client
        self.max_retries = max_retries
        self.secret_resolver = secret_resolver
        self.timeout_seconds = timeout_ms / 1000
        self.request_id = 0
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}

    def initialize(self) -> None:
        response, headers = self._request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "agent-system", "version": "p0"},
            },
        )
        self.session_id = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
        self.server_info = response.get("serverInfo") or response.get("server_info") or {}
        self._notification("notifications/initialized", {})

    def list_tools(self) -> dict[str, Any]:
        response, _ = self._request("tools/list", {})
        return {"server_info": self.server_info, "tools": response.get("tools") or []}

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response, _ = self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        return _tool_result(response, self.server, tool_name)

    def _notification(self, method: str, params: dict[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self._post(payload, expect_response=False)

    def _request(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], httpx.Headers]:
        self.request_id += 1
        response = self._post(
            {"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params},
            expect_response=True,
        )
        message = _decode_http_json_rpc(response)
        return _json_rpc_result(message), response.headers

    def _post(self, payload: dict[str, Any], *, expect_response: bool) -> httpx.Response:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **{
                str(key): str(value)
                for key, value in (self.server.get("public_headers") or {}).items()
            },
            **_resolved_mcp_headers(self.secret_resolver, self.server),
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self.http_client.post(
                    str(self.server["url"]),
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code in {408, 429} or response.status_code >= 500:
                    if attempt < self.max_retries:
                        continue
                    raise McpInvocationError(
                        "mcp_http_transient_failure",
                        "MCP HTTP request failed transiently.",
                        retryable=True,
                        details=_http_error_details(
                            payload,
                            response=response,
                            attempt=attempt,
                        ),
                    )
                if response.status_code >= 400:
                    raise McpInvocationError(
                        "mcp_http_request_failed",
                        "MCP HTTP request failed.",
                        retryable=False,
                        details=_http_error_details(
                            payload,
                            response=response,
                            attempt=attempt,
                        ),
                    )
                if expect_response and not response.content:
                    raise McpInvocationError(
                        "mcp_http_empty_response",
                        "MCP HTTP response was empty.",
                        retryable=True,
                        details=_http_error_details(
                            payload,
                            response=response,
                            attempt=attempt,
                        ),
                    )
                return response
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    continue
                raise McpInvocationError(
                    "mcp_http_transport_failed",
                    "MCP HTTP transport failed.",
                    retryable=True,
                    details=redact_runtime_value(
                        {
                            "method": payload.get("method"),
                            "attempts": attempt + 1,
                            "exception_type": exc.__class__.__name__,
                        }
                    ),
                ) from exc
        raise McpInvocationError(
            "mcp_http_transport_failed",
            "MCP HTTP transport failed.",
            retryable=True,
        ) from last_error


def _parse_json_message(raw: str) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpInvocationError(
            "mcp_invalid_json",
            "MCP server returned invalid JSON.",
            retryable=True,
        ) from exc
    if not isinstance(message, dict):
        raise McpInvocationError(
            "mcp_invalid_message",
            "MCP server returned an invalid JSON-RPC message.",
            retryable=True,
        )
    return message


def _http_error_details(
    payload: dict[str, Any],
    *,
    response: httpx.Response,
    attempt: int,
) -> dict[str, Any]:
    response_excerpt = response.text[:512] if response.text else ""
    return redact_runtime_value(
        {
            "method": payload.get("method"),
            "attempts": attempt + 1,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "response_excerpt": response_excerpt,
        }
    )


def _resolve_mcp_secret(
    secret_resolver: Any | None,
    server: dict[str, Any],
    secret_ref: str,
) -> str:
    if not secret_ref:
        return ""
    if secret_resolver is None:
        raise McpInvocationError(
            "mcp_secret_resolver_missing",
            "MCP secret resolver is not configured.",
            retryable=False,
        )
    resolved = secret_resolver.resolve(
        workspace_id=str(server.get("workspace_id") or "default"),
        secret_ref=secret_ref.removeprefix("secret_ref://"),
        purpose="mcp_connect",
        caller="mcp_connector",
    )
    return str(resolved.plaintext)


def _resolved_mcp_headers(
    secret_resolver: Any | None,
    server: dict[str, Any],
) -> dict[str, str]:
    headers: dict[str, str] = {}
    headers_ref = server.get("headers_ref")
    if headers_ref:
        headers.update(
            _parse_secret_headers(
                _resolve_mcp_secret(secret_resolver, server, str(headers_ref))
            )
        )
    oauth_ref = server.get("oauth_credential_ref")
    if oauth_ref:
        headers.update(
            _oauth_headers(
                _resolve_mcp_secret(secret_resolver, server, str(oauth_ref)),
                existing_headers=headers,
            )
        )
    return headers


def _parse_secret_headers(plaintext: str) -> dict[str, str]:
    try:
        parsed = json.loads(plaintext)
    except json.JSONDecodeError:
        parsed = _parse_header_lines(plaintext)
    if not isinstance(parsed, dict):
        raise McpInvocationError(
            "mcp_secret_headers_invalid",
            "MCP headers secret must be a JSON object or header lines.",
            retryable=False,
        )
    headers = {str(key): str(value) for key, value in parsed.items() if value is not None}
    if any(not key.strip() for key in headers):
        raise McpInvocationError(
            "mcp_secret_headers_invalid",
            "MCP headers secret contains an empty header name.",
            retryable=False,
        )
    return headers


def _parse_header_lines(plaintext: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_line in plaintext.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise McpInvocationError(
                "mcp_secret_headers_invalid",
                "MCP headers secret must be a JSON object or header lines.",
                retryable=False,
            )
        headers[name.strip()] = value.strip()
    return headers


def _oauth_headers(
    plaintext: str,
    *,
    existing_headers: dict[str, str],
) -> dict[str, str]:
    if _has_authorization_header(existing_headers):
        return {}
    token = plaintext.strip()
    token_type = "Bearer"
    try:
        parsed = json.loads(plaintext)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        token = str(parsed.get("access_token") or parsed.get("token") or "").strip()
        token_type = str(parsed.get("token_type") or "Bearer").strip() or "Bearer"
    if not token:
        raise McpInvocationError(
            "mcp_oauth_credential_invalid",
            "MCP OAuth credential does not contain an access token.",
            retryable=False,
        )
    return {"Authorization": f"{token_type} {token}"}


def _has_authorization_header(headers: dict[str, str]) -> bool:
    return any(name.lower() == "authorization" for name in headers)


def _decode_http_json_rpc(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return _parse_json_message(line.partition(":")[2].strip())
        raise McpInvocationError(
            "mcp_sse_response_empty",
            "MCP SSE response did not contain data.",
            retryable=True,
        )
    return _parse_json_message(response.text)


def _json_rpc_result(message: dict[str, Any]) -> dict[str, Any]:
    if isinstance(message.get("error"), dict):
        error = message["error"]
        raise McpInvocationError(
            "mcp_json_rpc_error",
            str(redact_runtime_value(error.get("message") or "MCP JSON-RPC error.")),
            retryable=False,
            details={"code": error.get("code")},
        )
    result = message.get("result")
    return result if isinstance(result, dict) else {}


def _tool_result(
    response: dict[str, Any],
    server: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    is_error = bool(response.get("isError") or response.get("is_error"))
    return {
        "ok": not is_error,
        "server_name": server.get("server_name"),
        "tool_name": tool_name,
        "content": response.get("content") or [],
        "structured_content": response.get("structuredContent")
        or response.get("structured_content")
        or {},
        "retryable": False,
        **({"error_type": "mcp_tool_error"} if is_error else {}),
    }
