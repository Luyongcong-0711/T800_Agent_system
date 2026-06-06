from __future__ import annotations

from collections import Counter
from typing import Any


def _tool_name(tool: dict[str, Any]) -> str:
    return str(
        tool.get("model_name")
        or tool.get("normalized_name")
        or tool.get("name")
        or tool.get("tool_name")
        or ""
    )


def apply_name_conflict_policy(
    snapshot: dict[str, Any],
    reserved_tool_names: set[str] | None = None,
) -> dict[str, Any]:
    reserved = reserved_tool_names or set()
    tools = [dict(tool) for tool in snapshot.get("tools", [])]
    counts = Counter(_tool_name(tool) for tool in tools)
    for tool in tools:
        name = _tool_name(tool)
        has_conflict = bool(name) and (name in reserved or counts[name] > 1)
        tool["name_conflict"] = has_conflict
        if has_conflict:
            tool["enabled"] = False
            tool["disabled_reason"] = "name_conflict"
    return {**snapshot, "tools": tools}


detect_name_conflicts = apply_name_conflict_policy
apply_mcp_tool_policy = apply_name_conflict_policy
