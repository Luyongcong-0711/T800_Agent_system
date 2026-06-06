from __future__ import annotations

from typing import Any

from app.core.settings import Settings, get_settings
from app.mcp.service import DefaultMcpCapabilityProvider, McpCapabilityProvider
from app.mcp_client.invocation import McpJsonRpcInvocationProvider
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_resolver import SecretResolver
from app.secret_store.secret_service import SecretService
from app.storage.object_store import ObjectStore


class ConfiguredMcpCapabilityProvider:
    def __init__(
        self,
        *,
        real_provider: McpCapabilityProvider,
        fallback_provider: McpCapabilityProvider,
    ) -> None:
        self.real_provider = real_provider
        self.fallback_provider = fallback_provider

    def fetch_capabilities(self, server: dict[str, Any]) -> dict[str, Any]:
        if has_mcp_runtime_config(server):
            return self.real_provider.fetch_capabilities(server)
        capability = dict(self.fallback_provider.fetch_capabilities(server))
        capability["_capability_source"] = "fallback_unconfigured"
        capability["_runtime_configured"] = False
        return capability

    def invoke_tool(
        self,
        server: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if has_mcp_runtime_config(server):
            return self.real_provider.invoke_tool(server, tool_name, arguments)
        return self.fallback_provider.invoke_tool(server, tool_name, arguments)


def build_configured_mcp_capability_provider(
    object_store: ObjectStore,
    settings: Settings | None = None,
    *,
    fallback_provider: McpCapabilityProvider | None = None,
) -> ConfiguredMcpCapabilityProvider:
    current_settings = settings or get_settings()
    secret_service = SecretService(object_store, MasterKeyProvider(current_settings))
    secret_resolver = SecretResolver(secret_service, MasterKeyProvider(current_settings))
    return ConfiguredMcpCapabilityProvider(
        real_provider=McpJsonRpcInvocationProvider(secret_resolver=secret_resolver),
        fallback_provider=fallback_provider or DefaultMcpCapabilityProvider(),
    )


def has_mcp_runtime_config(server: dict[str, Any]) -> bool:
    transport = str(server.get("transport") or server.get("type") or "").lower()
    if transport == "stdio":
        return bool(server.get("command"))
    if transport in {"http", "streamable_http", "sse"}:
        return bool(server.get("url"))
    return False
