from __future__ import annotations

import hashlib
from typing import Any

from app.core.errors import AgentSystemError
from app.core.time import utc_now_iso
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import workspace_file_backup_key, workspace_file_object_key


class WorkspaceFileService:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)

    def apply_staged_files(
        self,
        *,
        workspace_id: str,
        run_id: str,
        operation_id: str,
        rollback_token: str,
        files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            normalized_files = [self._normalize_file_item(item) for item in files]
            if not normalized_files:
                return {
                    "ok": True,
                    "workspace_commit_status": "not_committed",
                    "rollback_token": None,
                    "committed_files": [],
                    "backup_records": [],
                }

            backup_records = [
                self._write_backup_record(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    operation_id=operation_id,
                    rollback_token=rollback_token,
                    file_item=item,
                )
                for item in normalized_files
            ]
            committed_files = []
            for item, backup in zip(normalized_files, backup_records, strict=True):
                object_key = workspace_file_object_key(workspace_id, item["path"])
                self.object_store.write_text(object_key, item["new_content"])
                committed_files.append(
                    {
                        "path": item["path"],
                        "object_key": object_key,
                        "backup_object_key": backup["backup_object_key"],
                        "new_sha256": _sha256_text(item["new_content"]),
                        "previous_sha256": backup.get("previous_sha256"),
                        "previous_exists": backup["previous_exists"],
                    }
                )

            return {
                "ok": True,
                "workspace_commit_status": "committed",
                "rollback_token": rollback_token,
                "committed_files": committed_files,
                "backup_records": backup_records,
            }
        except AgentSystemError:
            raise
        except Exception as exc:  # noqa: BLE001 - storage boundary should return structured error.
            raise AgentSystemError(
                "workspace_commit_failed",
                "Workspace staged file commit failed.",
                500,
                retryable=True,
                details={"error_type": exc.__class__.__name__},
            ) from exc

    def rollback_staged_files(
        self,
        *,
        workspace_id: str,
        rollback_token: str,
        backup_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not rollback_token:
            raise AgentSystemError(
                "rollback_token_missing",
                "Rollback token is missing.",
                409,
            )
        restored_files = []
        try:
            for backup_summary in backup_records:
                backup_key = str(backup_summary.get("backup_object_key") or "")
                if not backup_key:
                    raise AgentSystemError(
                        "rollback_backup_missing",
                        "Rollback backup object key is missing.",
                        409,
                    )
                backup = self.json_store.read_json(backup_key)
                if backup.get("workspace_id") != workspace_id:
                    raise AgentSystemError(
                        "rollback_workspace_mismatch",
                        "Rollback backup does not belong to this workspace.",
                        409,
                    )
                if backup.get("rollback_token") != rollback_token:
                    raise AgentSystemError(
                        "rollback_token_mismatch",
                        "Rollback token does not match the backup record.",
                        409,
                    )
                object_key = str(backup["object_key"])
                if backup.get("previous_exists"):
                    previous_content = str(backup.get("previous_content") or "")
                    self.object_store.write_text(object_key, previous_content)
                    rollback_action = "restore_previous_content"
                else:
                    if self.object_store.exists(object_key):
                        self.object_store.delete(object_key)
                    rollback_action = "delete_created_file"
                restored_files.append(
                    {
                        "path": backup.get("path"),
                        "object_key": object_key,
                        "rollback_action": rollback_action,
                        "previous_exists": bool(backup.get("previous_exists")),
                    }
                )
        except AgentSystemError:
            raise
        except Exception as exc:  # noqa: BLE001 - storage boundary should return structured error.
            raise AgentSystemError(
                "workspace_rollback_failed",
                "Workspace staged file rollback failed.",
                500,
                retryable=True,
                details={"error_type": exc.__class__.__name__},
            ) from exc
        return {
            "ok": True,
            "rollback_token": rollback_token,
            "restored_files": restored_files,
            "workspace_commit_status": "rolled_back",
        }

    def _write_backup_record(
        self,
        *,
        workspace_id: str,
        run_id: str,
        operation_id: str,
        rollback_token: str,
        file_item: dict[str, str],
    ) -> dict[str, Any]:
        object_key = workspace_file_object_key(workspace_id, file_item["path"])
        previous_exists = self.object_store.exists(object_key)
        previous_content = self.object_store.read_text(object_key) if previous_exists else None
        backup_key = workspace_file_backup_key(
            workspace_id,
            run_id,
            operation_id,
            file_item["path"],
        )
        backup = {
            "schema_version": 1,
            "operation_id": operation_id,
            "rollback_token": rollback_token,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "path": file_item["path"],
            "object_key": object_key,
            "backup_object_key": backup_key,
            "previous_exists": previous_exists,
            "previous_content": previous_content,
            "previous_sha256": (
                _sha256_text(previous_content) if previous_content is not None else None
            ),
            "new_sha256": _sha256_text(file_item["new_content"]),
            "created_at": utc_now_iso(),
        }
        self.json_store.write_json(backup_key, backup)
        return {key: value for key, value in backup.items() if key != "previous_content"}

    @staticmethod
    def _normalize_file_item(item: dict[str, Any]) -> dict[str, str]:
        path = str(item.get("path") or "").replace("\\", "/").strip().removeprefix("./")
        if not path:
            raise AgentSystemError(
                "workspace_file_path_missing",
                "Workspace staged file is missing a path.",
                409,
            )
        if path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise AgentSystemError(
                "workspace_file_path_invalid",
                "Workspace staged file path is invalid.",
                409,
            )
        return {
            "path": path,
            "old_content": str(item.get("old_content") or ""),
            "new_content": str(item.get("new_content") or ""),
        }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
