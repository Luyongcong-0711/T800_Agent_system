from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure the packaged stdio MCP smoke server for P0 acceptance."
    )
    parser.add_argument("--backend-url", default="http://localhost:8000")
    parser.add_argument("--workspace-id", default="default")
    parser.add_argument("--server-name", default="agent_smoke")
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument(
        "--process-refresh",
        action="store_true",
        help="Also queue and process one MCP capability refresh job.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backend_url = str(args.backend_url).rstrip("/")
    workspace = quote(str(args.workspace_id), safe="")
    server = quote(str(args.server_name), safe="")
    payload = {
        "transport": "stdio",
        "enabled": True,
        "scope": "workspace",
        "timeout_ms": int(args.timeout_ms),
        "command": "python",
        "args": ["-m", "app.mcp_smoke_server"],
        "cwd": None,
        "env": {},
        "secret_env_refs": {},
        "url": None,
        "public_headers": {},
        "headers_ref": None,
        "auth_type": None,
        "oauth_credential_ref": None,
    }
    detail = request_json(
        "PUT",
        f"{backend_url}/workspaces/{workspace}/mcp/servers/{server}",
        payload,
    )
    reconnect = request_json(
        "POST",
        f"{backend_url}/workspaces/{workspace}/mcp/servers/{server}/reconnect",
        {"reason": "p0_smoke_bootstrap"},
    )
    processed: dict[str, Any] | None = None
    if args.process_refresh:
        processed = request_json(
            "POST",
            f"{backend_url}/workspaces/{workspace}/jobs/process-next?job_type=mcp_capability_refresh_job",
            {},
        )
    health = request_json(
        "GET",
        f"{backend_url}/workspaces/{workspace}/mcp/servers/{server}/health?live_probe=true",
        None,
    )
    print(
        json.dumps(
            {
                "server_name": args.server_name,
                "configured": bool(health.get("runtime_configured")),
                "reconnect_job_id": reconnect.get("job_id")
                or reconnect.get("refresh_job", {}).get("job_id"),
                "processed_refresh": processed,
                "health": health,
                "acceptance_flag": f"--mcp-server-name {args.server_name}",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if health.get("connected") and int(health.get("tool_count") or 0) >= 1 else 1


def request_json(method: str, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed with HTTP {exc.code}: {raw[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc}") from exc
    return json.loads(raw) if raw else {}


if __name__ == "__main__":
    raise SystemExit(main())
