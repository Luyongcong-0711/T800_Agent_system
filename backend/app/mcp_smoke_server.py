from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = "2025-03-26"

TOOLS = [
    {
        "name": "ping",
        "description": "Return a deterministic MCP smoke-test response.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Optional message to echo in the smoke response.",
                }
            },
        },
    }
]


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_message(_decode(line))
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = str(message.get("method") or "")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if request_id is None:
        return None
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-system-smoke", "version": "1.0.0"},
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if tool_name != "ping":
            return _error(request_id, -32602, f"Unknown smoke tool: {tool_name}")
        message_text = str(arguments.get("message") or "pong")
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": f"pong:{message_text}"}],
                "structuredContent": {"ok": True, "message": message_text},
                "isError": False,
            },
        )
    return _error(request_id, -32601, f"Unsupported MCP method: {method}")


def _decode(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": f"Invalid JSON: {exc.msg}"},
        }
    return value if isinstance(value, dict) else {}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
