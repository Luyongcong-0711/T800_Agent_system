from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from app.mcp.service import McpService


class SnapshotMcpTool(BaseTool):
    server_name: str
    original_tool_name: str
    object_store: Any
    workspace_id: str = "default"
    source: str = "mcp"

    def _run(self, **kwargs: Any) -> dict[str, Any]:
        from app.mcp.configured_provider import build_configured_mcp_capability_provider

        service = McpService(
            self.object_store,
            capability_provider=build_configured_mcp_capability_provider(self.object_store),
        )
        return service.invoke_tool(
            self.workspace_id,
            self.server_name,
            self.original_tool_name,
            kwargs,
        )


def build_mcp_snapshot_tools(
    object_store: Any,
    *,
    workspace_id: str = "default",
) -> list[BaseTool]:
    from app.mcp.configured_provider import build_configured_mcp_capability_provider

    service = McpService(
        object_store,
        capability_provider=build_configured_mcp_capability_provider(object_store),
    )
    tools: list[BaseTool] = []
    for spec in service.build_mcp_tool_specs(workspace_id):
        tools.append(
            SnapshotMcpTool(
                name=spec["name"],
                description=spec.get("description") or "Call an enabled MCP tool.",
                args_schema=spec.get("args_schema") or {"type": "object", "properties": {}},
                metadata={
                    "source": "mcp",
                    "server_name": spec.get("server_name"),
                    "original_tool_name": spec.get("original_tool_name"),
                    "risk_level": spec.get("risk_level"),
                    "requires_approval": spec.get("requires_approval"),
                },
                server_name=str(spec.get("server_name") or ""),
                original_tool_name=str(spec.get("original_tool_name") or spec["name"]),
                object_store=object_store,
                workspace_id=workspace_id,
            )
        )
    return tools
