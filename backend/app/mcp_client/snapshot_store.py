from __future__ import annotations

from typing import Any

from app.core.time import utc_now_iso
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import mcp_capability_snapshot_key


class McpSnapshotStore:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)

    def save_snapshot(
        self,
        workspace_id: str,
        server_name: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        previous = self.get_snapshot(workspace_id, server_name, default={})
        next_snapshot = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "server_name": server_name,
            "status": "connected",
            "stale": False,
            "tools": [],
            "resources": [],
            "prompts": [],
            "server_info": {},
            **snapshot,
            "updated_at": utc_now_iso(),
            "revision": int(previous.get("revision") or 0) + 1,
        }
        self.json_store.write_json(
            mcp_capability_snapshot_key(workspace_id, server_name),
            next_snapshot,
        )
        return next_snapshot

    def get_snapshot(
        self,
        workspace_id: str,
        server_name: str,
        default: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            mcp_capability_snapshot_key(workspace_id, server_name),
            default or {},
        )

    def mark_stale_on_refresh_failure(
        self,
        workspace_id: str,
        server_name: str,
        *,
        error_type: str,
        message: str | None = None,
        status: str = "tool_list_failed",
        retryable: bool = True,
    ) -> dict[str, Any]:
        previous = self.get_snapshot(
            workspace_id,
            server_name,
            default={
                "schema_version": 1,
                "workspace_id": workspace_id,
                "server_name": server_name,
                "status": "tool_list_failed",
                "stale": True,
                "tools": [],
                "resources": [],
                "prompts": [],
                "server_info": {},
                "snapshot_hash": None,
                "revision": 0,
            },
        )
        error = {
            "error_type": error_type,
            "message": message or error_type,
            "retryable": retryable,
            "failed_at": utc_now_iso(),
        }
        stale = {
            **previous,
            "stale": True,
            "status": status or previous.get("status") or "tool_list_failed",
            "last_error": error,
            "last_refresh_error": error,
            "updated_at": utc_now_iso(),
            "revision": int(previous.get("revision") or 0) + 1,
        }
        self.json_store.write_json(
            mcp_capability_snapshot_key(workspace_id, server_name),
            stale,
        )
        return stale

    save = save_snapshot
    upsert_snapshot = save_snapshot
    get = get_snapshot
    load_snapshot = get_snapshot
    mark_refresh_failed = mark_stale_on_refresh_failure
    mark_stale = mark_stale_on_refresh_failure
