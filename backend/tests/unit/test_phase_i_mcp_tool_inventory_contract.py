from __future__ import annotations

from typing import Any

import pytest


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _call_first(module: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    for name in names:
        func = getattr(module, name, None)
        if callable(func):
            return func(*args, **kwargs)
    pytest.fail(f"{module.__name__} must expose one of: {', '.join(names)}")


def _tools(value: Any) -> list[dict[str, Any]]:
    dumped = _dump(value)
    if isinstance(dumped, list):
        return dumped
    assert isinstance(dumped, dict)
    for key in ("tools", "items", "model_safe_specs"):
        items = dumped.get(key)
        if isinstance(items, list):
            return items
    pytest.fail(f"Expected a tool list or response with tools/items/model_safe_specs: {dumped}")


def _tool_names(value: Any) -> set[str]:
    return {str(tool["name"]) for tool in _tools(value)}


def test_name_conflict_disables_conflicting_mcp_tool() -> None:
    policy = pytest.importorskip(
        "app.tools.policy",
        reason="Phase I tool policy has not landed yet.",
    )
    snapshot = {
        "server_name": "github",
        "tools": [
            {
                "name": "echo_runtime_context",
                "normalized_name": "echo_runtime_context",
                "enabled": True,
                "name_conflict": False,
            },
            {
                "name": "echo_runtime_context",
                "normalized_name": "echo_runtime_context",
                "enabled": True,
                "name_conflict": False,
            },
        ],
    }

    result = _call_first(
        policy,
        (
            "apply_name_conflict_policy",
            "detect_name_conflicts",
            "apply_mcp_tool_policy",
        ),
        snapshot,
        reserved_tool_names={"echo_runtime_context"},
    )

    conflicting = [
        tool
        for tool in _tools(result)
        if tool.get("normalized_name") == "echo_runtime_context"
        and tool.get("source", "mcp") == "mcp"
    ]
    assert conflicting
    assert all(tool["enabled"] is False for tool in conflicting)
    assert all(tool["name_conflict"] is True for tool in conflicting)


def test_model_inventory_contains_only_enabled_non_conflicting_tools() -> None:
    inventory = pytest.importorskip(
        "app.tools.inventory",
        reason="Phase I tool inventory has not landed yet.",
    )
    candidates = [
        {
            "name": "echo_runtime_context",
            "source": "builtin",
            "enabled": True,
            "name_conflict": False,
            "description": "safe built-in tool",
            "args_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "mcp_github_search_issues",
            "source": "mcp",
            "server_name": "github",
            "original_tool_name": "search_issues",
            "enabled": True,
            "name_conflict": False,
            "description": "search issues",
            "args_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "mcp_filesystem_delete_file",
            "source": "mcp",
            "server_name": "filesystem",
            "enabled": False,
            "name_conflict": False,
            "description": "delete file",
            "args_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "mcp_github_echo_runtime_context",
            "source": "mcp",
            "server_name": "github",
            "enabled": True,
            "name_conflict": True,
            "description": "conflicting tool",
            "args_schema": {"type": "object", "properties": {}},
        },
    ]

    result = _call_first(
        inventory,
        (
            "build_model_tool_inventory",
            "build_effective_tool_inventory",
            "model_safe_specs",
        ),
        candidates,
    )

    assert _tool_names(result) == {"echo_runtime_context", "mcp_github_search_issues"}
    tools = {tool["name"]: tool for tool in _tools(result)}
    assert tools["mcp_github_search_issues"]["original_tool_name"] == "search_issues"


def test_stale_snapshot_is_preserved_when_refresh_fails(tmp_path) -> None:
    snapshot_store_module = pytest.importorskip(
        "app.mcp_client.snapshot_store",
        reason="Phase I MCP snapshot store has not landed yet.",
    )
    from app.storage.local_object_store import LocalObjectStore

    store_cls = getattr(snapshot_store_module, "McpSnapshotStore", None)
    if store_cls is None:
        pytest.fail("app.mcp_client.snapshot_store must expose McpSnapshotStore.")

    store = store_cls(LocalObjectStore(tmp_path / "objects"))
    previous = {
        "server_name": "github",
        "status": "connected",
        "stale": False,
        "snapshot_hash": "sha256:old",
        "tools": [
            {
                "name": "mcp_github_search_issues",
                "enabled": True,
                "name_conflict": False,
            }
        ],
    }

    _call_first(
        store,
        ("save_snapshot", "save", "upsert_snapshot"),
        "default",
        "github",
        previous,
    )
    _call_first(
        store,
        ("mark_stale_on_refresh_failure", "mark_refresh_failed", "mark_stale"),
        "default",
        "github",
        error_type="tool_list_failed",
    )
    current = _call_first(store, ("get_snapshot", "get", "load_snapshot"), "default", "github")

    dumped = _dump(current)
    assert dumped["server_name"] == "github"
    assert dumped["stale"] is True
    assert dumped["snapshot_hash"] == "sha256:old"
    assert dumped["tools"] == previous["tools"]
    assert dumped.get("last_refresh_error", {}).get("error_type") == "tool_list_failed"
