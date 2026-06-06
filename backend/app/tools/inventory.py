from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.time import utc_now_iso


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_model_tool_inventory(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        name = str(candidate.get("name") or "")
        if not name or name in seen:
            continue
        if candidate.get("enabled") is False:
            continue
        if candidate.get("name_conflict") is True:
            continue
        if candidate.get("disabled_reason"):
            continue
        seen.add(name)
        visible.append(
            {
                "name": name,
                "description": str(candidate.get("description") or ""),
                "args_schema": candidate.get("args_schema") or {"type": "object"},
                "source": str(candidate.get("source") or "built_in"),
                "enabled": True,
                "risk_level": str(candidate.get("risk_level") or "low"),
                "requires_approval": bool(candidate.get("requires_approval") or False),
                **(
                    {"server_name": candidate["server_name"]}
                    if candidate.get("server_name")
                    else {}
                ),
                **(
                    {"original_tool_name": candidate["original_tool_name"]}
                    if candidate.get("original_tool_name")
                    else {}
                ),
            }
        )
    return visible


def build_effective_tool_inventory(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    tools = build_model_tool_inventory(candidates)
    return {
        "schema_version": 1,
        "visible_to_model": True,
        "inventory_hash": _stable_hash({"tools": tools}),
        "tools": tools,
        "created_at": utc_now_iso(),
    }


def model_safe_specs(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return build_model_tool_inventory(candidates)
