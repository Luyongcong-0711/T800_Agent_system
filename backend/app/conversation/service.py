from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.conversation.context_loader import ConversationContextLoader
from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.runtime.runner import RuntimeRunner
from app.runtime.tools import redact_runtime_value
from app.schemas.conversation import (
    CreateRunRequest,
    CreateThreadRequest,
    PatchThreadRequest,
    RunOperationRollbackRequest,
)
from app.schemas.identity import RuntimeIdentity
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore, RevisionConflictError
from app.storage.path_builder import (
    run_event_index_key,
    run_events_prefix,
    run_leaf_state_key,
    run_manifest_key,
    run_operations_prefix,
    run_prefix,
    thread_manifest_key,
    thread_message_index_key,
    thread_messages_prefix,
    thread_runs_index_key,
    threads_index_key,
    workspace_prefix,
    workspace_runs_index_key,
)
from app.workspace_files.service import WorkspaceFileService

TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
NON_EXECUTABLE_RUN_STATUSES = {*TERMINAL_RUN_STATUSES, "waiting_approval"}
EVENT_ID_RE = re.compile(r"^evt_(?P<run_id>[A-Za-z0-9_.-]+)_(?P<seq>[0-9]{12})$")


def _stable_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _revision_next(value: dict[str, Any]) -> int:
    return int(value.get("revision", 0)) + 1


def _preview(content: str, limit: int = 160) -> str:
    redacted = str(redact_runtime_value(content)).replace("\r", " ").replace("\n", " ")
    return redacted[:limit]


def _event_id(run_id: str, event_seq: int) -> str:
    return f"evt_{run_id}_{event_seq:012d}"


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class ConversationService:
    def __init__(
        self,
        object_store: ObjectStore,
        runtime_runner: RuntimeRunner,
        runtime_instance_id: str = "rt_local",
        run_lease_ttl_seconds: int = 300,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.runtime_runner = runtime_runner
        self.runtime_instance_id = runtime_instance_id
        self.run_lease_ttl_seconds = max(1, run_lease_ttl_seconds)

    def create_thread(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: CreateThreadRequest,
    ) -> dict[str, Any]:
        request_hash = _stable_hash(
            {
                "action": "create_thread",
                "workspace_id": workspace_id,
                "user_id": identity.user_id,
                "title": request.title,
            }
        )
        if request.idempotency_key:
            existing = self._find_thread_by_idempotency_key(
                workspace_id,
                request.idempotency_key,
                request_hash,
            )
            if existing:
                return existing

        now = utc_now_iso()
        thread_id = new_id("thread")
        title = request.title or "Untitled thread"
        manifest = {
            "schema_version": 1,
            "thread_id": thread_id,
            "workspace_id": workspace_id,
            "user_id": identity.user_id,
            "title": title,
            "status": "active",
            "pinned": False,
            "current_run_id": None,
            "current_run_status": None,
            "last_message_id": None,
            "last_message_preview": None,
            "last_message_at": None,
            "message_count": 0,
            "run_count": 0,
            "created_at": now,
            "updated_at": now,
            "idempotency": {
                request.idempotency_key: {
                    "thread_id": thread_id,
                    "request_hash": request_hash,
                }
            }
            if request.idempotency_key
            else {},
            "revision": 1,
        }
        self.json_store.write_json(thread_manifest_key(workspace_id, thread_id), manifest)
        self.json_store.write_json(
            thread_message_index_key(workspace_id, thread_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "thread_id": thread_id,
                "messages": [],
                "message_count": 0,
                "revision": 1,
            },
        )
        self.json_store.write_json(
            thread_runs_index_key(workspace_id, thread_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "thread_id": thread_id,
                "runs": [],
                "idempotency": {},
                "revision": 1,
            },
        )
        self._upsert_thread_index(workspace_id, manifest)
        return self._public_thread(manifest)

    def list_threads(self, workspace_id: str) -> list[dict[str, Any]]:
        index = self._threads_index(workspace_id)
        threads = [
            thread
            for thread in index["threads"]
            if thread.get("status") != "soft_deleted"
        ]
        return sorted(
            threads,
            key=lambda item: (
                bool(item.get("pinned")),
                item.get("last_message_at") or item.get("updated_at") or "",
                item.get("updated_at") or "",
            ),
            reverse=True,
        )

    def get_thread(self, workspace_id: str, thread_id: str) -> dict[str, Any]:
        return self._public_thread(self._thread_manifest(workspace_id, thread_id))

    def patch_thread(
        self,
        workspace_id: str,
        thread_id: str,
        request: PatchThreadRequest,
    ) -> dict[str, Any]:
        manifest = self._thread_manifest(workspace_id, thread_id)
        if request.title is not None:
            manifest["title"] = request.title
        if request.pinned is not None:
            manifest["pinned"] = request.pinned
        if request.status is not None:
            manifest["status"] = request.status
        manifest["updated_at"] = utc_now_iso()
        manifest["revision"] = _revision_next(manifest)
        self.json_store.write_json(thread_manifest_key(workspace_id, thread_id), manifest)
        self._upsert_thread_index(workspace_id, manifest)
        return self._public_thread(manifest)

    def list_messages(
        self,
        workspace_id: str,
        thread_id: str,
        after_message_id: str | None = None,
        before_message_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._thread_manifest(workspace_id, thread_id)
        records = self._message_segment_store(workspace_id, thread_id).read_all()
        if after_message_id:
            records = self._records_after(records, "message_id", after_message_id)
        if before_message_id:
            records = self._records_before(records, "message_id", before_message_id)
        return records[: max(1, min(limit, 1000))]

    def create_run(
        self,
        workspace_id: str,
        thread_id: str,
        identity: RuntimeIdentity,
        request: CreateRunRequest,
        execute_inline: bool = True,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        thread = self._thread_manifest(workspace_id, thread_id)
        if thread["status"] != "active":
            raise AgentSystemError(
                "thread_not_active",
                "Thread is not active.",
                status_code=409,
            )

        idempotency_key = request.idempotency_key or new_id("idem")
        request_hash = _stable_hash(
            {
                "action": "create_run",
                "thread_id": thread_id,
                "user_message": request.user_message,
            }
        )
        thread_runs = self._thread_runs_index(workspace_id, thread_id)
        existing_record = thread_runs["idempotency"].get(idempotency_key)
        if existing_record:
            if existing_record["request_hash"] != request_hash:
                raise AgentSystemError(
                    "idempotency_conflict",
                    "Idempotency key was reused with different payload.",
                    status_code=409,
                )
            return self.get_run(workspace_id, existing_record["run_id"])

        now = utc_now_iso()
        run_id = new_id("run")
        active_trace_id = trace_id or request.trace_id or run_id
        user_message_id = new_id("msg")
        user_message = self._append_message(
            workspace_id=workspace_id,
            thread_id=thread_id,
            role="user",
            content=request.user_message,
            run_id=run_id,
            message_id=user_message_id,
            created_at=now,
        )
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "status": "running",
            "idempotency_key": idempotency_key,
            "user_message_id": user_message_id,
            "last_event_id": None,
            "last_event_seq": 0,
            "assistant_message_id": None,
            "model_error": None,
            "trace_id": active_trace_id,
            "owner": None,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        self._write_run_manifest(workspace_id, run_id, manifest)
        self._write_leaf_state(
            workspace_id,
            run_id,
            {
                "schema_version": 1,
                "run_id": run_id,
                "trace_id": active_trace_id,
                "status": "running",
                "context_usage": {},
                "tool_results": [],
                "requires_approval": False,
                "model_error": None,
                "revision": 1,
            },
        )
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "run_started",
            {"status": "running", "trace_id": active_trace_id},
        )
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "user_message",
            {"message_id": user_message["message_id"], "role": "user"},
        )

        self._register_new_run(workspace_id, thread_id, manifest, idempotency_key, request_hash)
        self._mark_thread_current_run(workspace_id, thread_id, run_id, "running")

        if not execute_inline:
            return self.get_run(workspace_id, run_id)

        return self.execute_run(
            workspace_id=workspace_id,
            thread_id=thread_id,
            identity=identity,
            run_id=run_id,
            user_message=request.user_message,
        )

    def execute_run(
        self,
        workspace_id: str,
        thread_id: str,
        identity: RuntimeIdentity,
        run_id: str,
        user_message: str,
    ) -> dict[str, Any]:
        manifest = self._run_manifest(workspace_id, run_id)
        if manifest["status"] in NON_EXECUTABLE_RUN_STATUSES:
            return self.get_run(workspace_id, run_id)
        lease = self._try_acquire_run_lease(workspace_id, run_id)
        if lease is None:
            return self.get_run(workspace_id, run_id)

        fencing_token = lease["fencing_token"]
        trace_id = str(manifest.get("trace_id") or run_id)
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "model_call_started",
            {"trace_id": trace_id},
        )
        try:
            runtime_context = ConversationContextLoader(self.object_store).load_for_run(
                workspace_id,
                thread_id,
            )
            runtime_result = self.runtime_runner.invoke_for_run(
                workspace_id=workspace_id,
                identity=identity,
                run_id=run_id,
                thread_id=thread_id,
                user_message=user_message,
                initial_messages=runtime_context["messages"],
                previous_compaction=runtime_context["compaction"],
                trace_id=trace_id,
                model_stream_callback=lambda event: self._append_model_stream_event(
                    workspace_id,
                    thread_id,
                    run_id,
                    event,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - run boundary must not leave stale running state.
            return self._fail_run_after_runtime_exception(
                workspace_id=workspace_id,
                thread_id=thread_id,
                run_id=run_id,
                exc=exc,
                fencing_token=fencing_token,
            )
        if not self._run_lease_is_active(workspace_id, run_id, fencing_token):
            return self.get_run(workspace_id, run_id)
        subagent_leaf_state = self._subagent_leaf_state(workspace_id, run_id)
        if runtime_result.model_error:
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "model_call_failed",
                {"error_type": runtime_result.model_error},
            )
        else:
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "model_call_completed",
                {"context_usage": runtime_result.context_usage},
            )
        if runtime_result.memory_snapshot.get("memory_snapshot_id"):
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "memory_snapshot_created",
                {
                    "memory_snapshot_id": runtime_result.memory_snapshot["memory_snapshot_id"],
                    "included_memory_ids": runtime_result.memory_snapshot.get(
                        "included_memory_ids",
                        [],
                    ),
                },
            )
        if runtime_result.compaction:
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "context_compaction_created",
                {
                    "compaction_id": runtime_result.compaction.get("compaction_id"),
                    "strategy": runtime_result.compaction.get("strategy"),
                },
            )
        for result in runtime_result.tool_results:
            tool_payload = {
                "tool_call_id": result.tool_call_id,
                "name": result.name,
                "ok": result.ok,
                "error_type": result.error_type,
            }
            self._append_event(workspace_id, thread_id, run_id, "tool_call_started", tool_payload)
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "tool_call_completed" if result.ok else "tool_call_failed",
                tool_payload,
            )
            self._append_subagent_tool_events(workspace_id, thread_id, run_id, result)

        if not self._run_lease_is_active(workspace_id, run_id, fencing_token):
            return self.get_run(workspace_id, run_id)

        if runtime_result.status == "waiting_approval":
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "run_waiting_approval",
                {"status": "waiting_approval"},
            )
            manifest = self._run_manifest(workspace_id, run_id)
            if not self._run_lease_is_active(workspace_id, run_id, fencing_token):
                return self.get_run(workspace_id, run_id)
            manifest.update(
                {
                    "status": "waiting_approval",
                    "assistant_message_id": None,
                    "model_error": runtime_result.model_error,
                    "owner": None,
                    "updated_at": utc_now_iso(),
                    "revision": _revision_next(manifest),
                }
            )
            self._write_run_manifest(workspace_id, run_id, manifest)
            self._write_leaf_state(
                workspace_id,
                run_id,
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "status": "waiting_approval",
                    "context_usage": runtime_result.context_usage,
                    "memory_snapshot": runtime_result.memory_snapshot,
                    "compaction": runtime_result.compaction,
                    "warnings": runtime_result.warnings,
                    **subagent_leaf_state,
                    "tool_results": [item.model_dump() for item in runtime_result.tool_results],
                    "requires_approval": True,
                    "model_error": runtime_result.model_error,
                    "revision": 1,
                },
            )
            self._upsert_run_indexes(workspace_id, thread_id, manifest)
            self._mark_thread_current_run(workspace_id, thread_id, run_id, "waiting_approval")
            return self.get_run(workspace_id, run_id)

        assistant_message_id = new_id("msg")
        assistant_content = self._last_assistant_content(runtime_result.messages)
        self._append_message(
            workspace_id=workspace_id,
            thread_id=thread_id,
            role="assistant",
            content=assistant_content,
            run_id=run_id,
            message_id=assistant_message_id,
            created_at=utc_now_iso(),
        )
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "assistant_message",
            {"message_id": assistant_message_id, "role": "assistant"},
        )

        final_status = "completed" if runtime_result.status == "completed" else "failed"
        final_event_type = "run_completed" if final_status == "completed" else "run_failed"
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            final_event_type,
            {"status": final_status},
        )
        manifest = self._run_manifest(workspace_id, run_id)
        if not self._run_lease_is_active(workspace_id, run_id, fencing_token):
            return self.get_run(workspace_id, run_id)
        manifest.update(
            {
                "status": final_status,
                "assistant_message_id": assistant_message_id,
                "model_error": runtime_result.model_error,
                "owner": None,
                "updated_at": utc_now_iso(),
                "revision": _revision_next(manifest),
            }
        )
        self._write_run_manifest(workspace_id, run_id, manifest)
        self._write_leaf_state(
            workspace_id,
            run_id,
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": final_status,
                "context_usage": runtime_result.context_usage,
                "memory_snapshot": runtime_result.memory_snapshot,
                "compaction": runtime_result.compaction,
                "warnings": runtime_result.warnings,
                **subagent_leaf_state,
                "tool_results": [item.model_dump() for item in runtime_result.tool_results],
                "requires_approval": runtime_result.requires_approval,
                "model_error": runtime_result.model_error,
                "revision": 1,
            },
        )
        self._upsert_run_indexes(workspace_id, thread_id, manifest)
        self._mark_thread_current_run(workspace_id, thread_id, run_id, final_status)
        return self.get_run(workspace_id, run_id)

    def _fail_run_after_runtime_exception(
        self,
        workspace_id: str,
        thread_id: str,
        run_id: str,
        exc: Exception,
        fencing_token: str,
    ) -> dict[str, Any]:
        error_type = getattr(exc, "error_type", None) or exc.__class__.__name__
        manifest = self._run_manifest(workspace_id, run_id)
        if manifest["status"] in TERMINAL_RUN_STATUSES:
            return self.get_run(workspace_id, run_id)
        if not self._run_lease_is_active(workspace_id, run_id, fencing_token):
            return self.get_run(workspace_id, run_id)
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "model_call_failed",
            {"error_type": str(error_type)},
        )
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "run_failed",
            {"status": "failed", "error_type": str(error_type)},
        )
        manifest = self._run_manifest(workspace_id, run_id)
        manifest.update(
            {
                "status": "failed",
                "assistant_message_id": None,
                "model_error": str(error_type),
                "owner": None,
                "updated_at": utc_now_iso(),
                "revision": _revision_next(manifest),
            }
        )
        self._write_run_manifest(workspace_id, run_id, manifest)
        self._write_leaf_state(
            workspace_id,
            run_id,
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": "failed",
                "context_usage": {},
                "tool_results": [],
                "requires_approval": False,
                "model_error": str(error_type),
                "revision": 1,
            },
        )
        self._upsert_run_indexes(workspace_id, thread_id, manifest)
        self._mark_thread_current_run(workspace_id, thread_id, run_id, "failed")
        return self.get_run(workspace_id, run_id)

    def _subagent_leaf_state(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        from app.subagents.service import SubAgentService

        state = SubAgentService(self.object_store).run_leaf_state(workspace_id, run_id)
        has_subagent_state = (
            state["subagent_tasks"]
            or state["subagent_results"]
            or state["reviewed_subagent_results"]
        )
        if has_subagent_state:
            return state
        return {
            "subagent_tasks": [],
            "subagent_results": [],
            "reviewed_subagent_results": [],
        }

    def _append_subagent_tool_events(
        self,
        workspace_id: str,
        thread_id: str,
        run_id: str,
        result: Any,
    ) -> None:
        if not str(getattr(result, "name", "")).startswith("call_subagent_"):
            return
        content = getattr(result, "content", None)
        if not isinstance(content, dict):
            return
        data = content.get("data", content)
        if not isinstance(data, dict) or not data.get("task_id"):
            return
        payload = {
            "tool_call_id": getattr(result, "tool_call_id", None),
            "task_id": data.get("task_id"),
            "parent_run_id": data.get("parent_run_id") or run_id,
            "agent_type": data.get("agent_type"),
            "status": data.get("status"),
            "read_scope": data.get("read_scope") or [],
            "write_scope": data.get("write_scope") or [],
            "changed_files": data.get("changed_files") or [],
            "result_summary": data.get("summary"),
            "needs_main_review": bool(data.get("needs_main_review", True)),
            "can_directly_finalize": bool(data.get("can_directly_finalize", False)),
            "error_type": data.get("error_type") or getattr(result, "error_type", None),
        }
        payload = redact_runtime_value(payload)
        self._append_event(workspace_id, thread_id, run_id, "subagent_task_created", payload)
        if data.get("status") == "queued":
            completed_type = "subagent_task_queued"
        else:
            completed_type = (
                "subagent_task_completed"
                if bool(getattr(result, "ok", False)) and data.get("status") == "completed"
                else "subagent_task_failed"
            )
        self._append_event(workspace_id, thread_id, run_id, completed_type, payload)

    def _append_model_stream_event(
        self,
        workspace_id: str,
        thread_id: str,
        run_id: str,
        event: Any,
    ) -> None:
        if getattr(event, "type", None) == "content_delta":
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "assistant_delta",
                {"delta": redact_runtime_value(getattr(event, "delta", ""))},
            )
            return
        if getattr(event, "type", None) == "usage_delta" and getattr(event, "usage", None):
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "model_usage_delta",
                {"usage": redact_runtime_value(event.usage.model_dump())},
            )
            return
        if getattr(event, "tool_call_delta", None) is not None:
            self._append_event(
                workspace_id,
                thread_id,
                run_id,
                "model_tool_call_delta",
                redact_runtime_value(event.model_dump(exclude_none=True)),
            )

    def get_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        manifest = self._run_manifest(workspace_id, run_id)
        leaf_state = self.json_store.read_json_or_default(
            run_leaf_state_key(workspace_id, run_id),
            {"schema_version": 1, "run_id": run_id, "status": manifest["status"]},
        )
        return {**self._public_run(manifest), "leaf_state": redact_runtime_value(leaf_state)}

    def cancel_run(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        manifest = self._run_manifest(workspace_id, run_id)
        if manifest["status"] in TERMINAL_RUN_STATUSES:
            return self._public_run(manifest)
        self._append_event(
            workspace_id,
            manifest["thread_id"],
            run_id,
            "run_cancel_requested",
            {"status": "cancelled"},
        )
        self._append_event(
            workspace_id,
            manifest["thread_id"],
            run_id,
            "run_cancelled",
            {"status": "cancelled"},
        )
        manifest.update(
            {
                "status": "cancelled",
                "owner": None,
                "updated_at": utc_now_iso(),
                "revision": _revision_next(manifest),
            }
        )
        self._write_run_manifest(workspace_id, run_id, manifest)
        self._upsert_run_indexes(workspace_id, manifest["thread_id"], manifest)
        self._mark_thread_current_run(workspace_id, manifest["thread_id"], run_id, "cancelled")
        return self._public_run(manifest)

    def rollback_run_operation(
        self,
        workspace_id: str,
        run_id: str,
        operation_id: str,
        request: RunOperationRollbackRequest,
        identity: RuntimeIdentity,
    ) -> dict[str, Any]:
        manifest = self._run_manifest(workspace_id, run_id)
        operations = JsonlSegmentStore(
            self.object_store,
            run_operations_prefix(workspace_id, run_id),
        )
        operation = self._find_staged_patch_operation_record(
            operations.read_all(),
            operation_id,
        )
        rollback_token = str(operation.get("rollback_token") or "")
        if rollback_token != request.rollback_token:
            raise AgentSystemError(
                "rollback_token_mismatch",
                "Rollback token does not match this operation.",
                status_code=409,
            )

        existing_rollback = self._find_staged_patch_rollback_record(
            operations.read_all(),
            operation_id,
        )
        if existing_rollback:
            return self._rollback_response(
                manifest=manifest,
                operation_id=operation_id,
                rollback_record=existing_rollback,
            )

        if operation.get("workspace_commit_status") != "committed":
            raise AgentSystemError(
                "operation_not_committed",
                "Only committed staged patch operations can be rolled back.",
                status_code=409,
            )
        backup_records = operation.get("backup_records")
        if not isinstance(backup_records, list) or not backup_records:
            raise AgentSystemError(
                "rollback_backup_missing",
                "Committed operation has no rollback backup records.",
                status_code=409,
            )

        rollback_result = WorkspaceFileService(self.object_store).rollback_staged_files(
            workspace_id=workspace_id,
            rollback_token=request.rollback_token,
            backup_records=backup_records,
        )
        now = utc_now_iso()
        rollback_record = {
            "schema_version": 1,
            "operation_id": new_id("op"),
            "operation_action": "rollback",
            "target_operation_id": operation_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "thread_id": manifest["thread_id"],
            "requested_by": identity.user_id,
            "idempotency_key": request.idempotency_key,
            "reason": request.reason,
            "operation_type": "skill_staged_patch",
            "workspace_commit_status": "rolled_back",
            "rollback_token": request.rollback_token,
            "status": "rolled_back",
            "restored_files": rollback_result["restored_files"],
            "created_at": now,
            "updated_at": now,
        }
        operation_plan_key = (
            operation.get("artifacts", {}).get("operation_plan_object_key")
            if isinstance(operation.get("artifacts"), dict)
            else None
        )
        if isinstance(operation_plan_key, str) and self.object_store.exists(operation_plan_key):
            plan = self.json_store.read_json(operation_plan_key)
            plan["workspace_commit_status"] = "rolled_back"
            plan["rollback_status"] = "completed"
            plan["rollback_operation_id"] = rollback_record["operation_id"]
            plan["rollback_completed_at"] = now
            plan["rollback_reason"] = request.reason
            plan["updated_at"] = now
            plan["revision"] = _revision_next(plan)
            self.json_store.write_json(operation_plan_key, redact_runtime_value(plan))

        event = self._append_event(
            workspace_id,
            manifest["thread_id"],
            run_id,
            "operation_rolled_back",
            {
                "operation_id": operation_id,
                "rollback_operation_id": rollback_record["operation_id"],
                "workspace_commit_status": "rolled_back",
                "restored_files": rollback_result["restored_files"],
            },
        )
        rollback_record["event_id"] = event["event_id"]
        operations.append(redact_runtime_value(rollback_record))
        return self._rollback_response(
            manifest=self._run_manifest(workspace_id, run_id),
            operation_id=operation_id,
            rollback_record=rollback_record,
        )

    def resolve_run_approval(
        self,
        workspace_id: str,
        run_id: str,
        approval_id: str,
        decision: str,
        identity: RuntimeIdentity,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise AgentSystemError(
                "invalid_approval_decision",
                "Approval decision must be approved or rejected.",
                status_code=400,
            )
        manifest = self._run_manifest(workspace_id, run_id)
        operation_plan_key, plan = self._find_run_approval_plan(
            workspace_id,
            run_id,
            approval_id,
        )
        target_status = "approved_pending_execution" if decision == "approved" else "rejected"
        current_status = str(plan.get("status") or "")
        current_decision = (plan.get("decision") or {}).get("decision")
        if (
            decision == "approved"
            and current_decision == decision
            and current_status in {"executed", "execution_failed"}
        ):
            return self._run_approval_response(
                manifest=manifest,
                operation_plan_key=operation_plan_key,
                plan=plan,
                decision=decision,
            )
        if current_status == target_status and current_decision == decision:
            return self._run_approval_response(
                manifest=manifest,
                operation_plan_key=operation_plan_key,
                plan=plan,
                decision=decision,
            )
        if manifest["status"] != "waiting_approval":
            raise AgentSystemError(
                "approval_not_pending",
                "Run is not waiting for approval.",
                status_code=409,
            )
        if current_status != "waiting_approval":
            raise AgentSystemError(
                "approval_already_resolved",
                "Approval has already been resolved.",
                status_code=409,
            )
        if decision == "approved" and not bool(plan.get("approval_ready")):
            raise AgentSystemError(
                "approval_not_ready",
                "Approval plan is not ready to approve.",
                status_code=409,
            )

        now = utc_now_iso()
        plan["status"] = target_status
        if plan.get("approval_kind") == "tool_invocation":
            plan.pop("stage", None)
            plan["phase"] = target_status
        else:
            plan["stage"] = target_status
        plan["decision"] = redact_runtime_value(
            {
                "decision": decision,
                "decided_by": identity.user_id,
                "decided_at": now,
                "reason": reason,
            }
        )
        plan["updated_at"] = now
        plan["revision"] = _revision_next(plan)
        self.json_store.write_json(operation_plan_key, plan)
        self._update_skill_run_manifest_after_approval(
            workspace_id=workspace_id,
            run_id=run_id,
            plan=plan,
            decision=decision,
            status=target_status,
            decided_at=now,
        )
        self._append_event(
            workspace_id,
            manifest["thread_id"],
            run_id,
            f"approval_{decision}",
            {
                "approval_id": approval_id,
                "decision": decision,
                "status": target_status,
                "operation_plan_object_key": operation_plan_key,
                "skill_run_id": plan.get("skill_run_id"),
            },
        )

        manifest = self._run_manifest(workspace_id, run_id)
        if decision == "rejected":
            self._append_event(
                workspace_id,
                manifest["thread_id"],
                run_id,
                "run_cancelled",
                {"status": "cancelled", "reason": "approval_rejected"},
            )
            manifest = self._run_manifest(workspace_id, run_id)
            manifest.update(
                {
                    "status": "cancelled",
                    "model_error": "approval_rejected",
                    "owner": None,
                    "updated_at": now,
                    "revision": _revision_next(manifest),
                }
            )
            self._write_run_manifest(workspace_id, run_id, manifest)
            leaf_state = self.json_store.read_json_or_default(
                run_leaf_state_key(workspace_id, run_id),
                {"schema_version": 1, "run_id": run_id},
            )
            leaf_state.update(
                {
                    "status": "cancelled",
                    "requires_approval": False,
                    "model_error": "approval_rejected",
                    "approval_decision": plan["decision"],
                    "revision": _revision_next(leaf_state),
                }
            )
            self._write_leaf_state(workspace_id, run_id, leaf_state)
            self._upsert_run_indexes(workspace_id, manifest["thread_id"], manifest)
            self._mark_thread_current_run(
                workspace_id,
                manifest["thread_id"],
                run_id,
                "cancelled",
            )
        elif plan.get("approval_kind") == "tool_invocation":
            return self._resume_after_approved_tool_invocation(
                workspace_id=workspace_id,
                run_id=run_id,
                operation_plan_key=operation_plan_key,
                plan=plan,
                identity=identity,
            )
        elif plan.get("approval_kind") == "skill_script_staged_patch":
            return self._resume_after_approved_skill_staged_patch(
                workspace_id=workspace_id,
                run_id=run_id,
                operation_plan_key=operation_plan_key,
                plan=plan,
                identity=identity,
            )
        else:
            leaf_state = self.json_store.read_json_or_default(
                run_leaf_state_key(workspace_id, run_id),
                {"schema_version": 1, "run_id": run_id},
            )
            leaf_state.update(
                {
                    "approval_decision": plan["decision"],
                    "approval_execution_pending": True,
                    "revision": _revision_next(leaf_state),
                }
            )
            self._write_leaf_state(workspace_id, run_id, leaf_state)

        latest_manifest = self._run_manifest(workspace_id, run_id)
        return self._run_approval_response(
            manifest=latest_manifest,
            operation_plan_key=operation_plan_key,
            plan=plan,
            decision=decision,
        )

    def _resume_after_approved_tool_invocation(
        self,
        *,
        workspace_id: str,
        run_id: str,
        operation_plan_key: str,
        plan: dict[str, Any],
        identity: RuntimeIdentity,
    ) -> dict[str, Any]:
        manifest = self._run_manifest(workspace_id, run_id)
        thread_id = manifest["thread_id"]
        now = utc_now_iso()
        plan["status"] = "executing"
        plan.pop("stage", None)
        plan["phase"] = "approval_execution"
        plan["execution_started_at"] = now
        plan["revision"] = _revision_next(plan)
        self.json_store.write_json(operation_plan_key, plan)
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "approval_execution_started",
            {
                "approval_id": plan["approval_id"],
                "approval_kind": "tool_invocation",
                "tool_name": plan.get("tool_name"),
                "operation_plan_object_key": operation_plan_key,
            },
        )

        tool_result = self._execute_approved_tool_from_plan(
            workspace_id=workspace_id,
            run_id=run_id,
            thread_id=thread_id,
            plan=plan,
            operation_plan_key=operation_plan_key,
            identity=identity,
        )
        self._append_message(
            workspace_id=workspace_id,
            thread_id=thread_id,
            role="assistant",
            content="",
            run_id=run_id,
            message_id=new_id("msg"),
            created_at=utc_now_iso(),
            tool_calls=[
                {
                    "id": tool_result["tool_call_id"],
                    "name": tool_result["name"],
                    "args": redact_runtime_value(
                        plan.get("args") if isinstance(plan.get("args"), dict) else {}
                    ),
                }
            ],
        )
        self._append_message(
            workspace_id=workspace_id,
            thread_id=thread_id,
            role="tool",
            content=json.dumps(tool_result["content"], ensure_ascii=False, default=str),
            run_id=run_id,
            message_id=new_id("msg"),
            created_at=utc_now_iso(),
            tool_call_id=tool_result["tool_call_id"],
        )

        manifest = self._run_manifest(workspace_id, run_id)
        manifest.update(
            {
                "status": "running",
                "model_error": None,
                "owner": None,
                "updated_at": utc_now_iso(),
                "revision": _revision_next(manifest),
            }
        )
        self._write_run_manifest(workspace_id, run_id, manifest)
        leaf_state = self.json_store.read_json_or_default(
            run_leaf_state_key(workspace_id, run_id),
            {"schema_version": 1, "run_id": run_id},
        )
        leaf_state.update(
            {
                "status": "running",
                "requires_approval": False,
                "approval_execution_pending": False,
                "approved_tool_result": tool_result,
                "revision": _revision_next(leaf_state),
            }
        )
        self._write_leaf_state(workspace_id, run_id, leaf_state)
        self._upsert_run_indexes(workspace_id, thread_id, manifest)
        self._mark_thread_current_run(workspace_id, thread_id, run_id, "running")

        resumed = self.execute_run(
            workspace_id=workspace_id,
            thread_id=thread_id,
            identity=identity,
            run_id=run_id,
            user_message="Continue after approved tool invocation.",
        )
        leaf_state = self.json_store.read_json_or_default(
            run_leaf_state_key(workspace_id, run_id),
            {"schema_version": 1, "run_id": run_id},
        )
        leaf_state.update(
            {
                "approval_decision": plan["decision"],
                "approval_execution_pending": False,
                "approved_tool_result": tool_result,
                "revision": _revision_next(leaf_state),
            }
        )
        self._write_leaf_state(workspace_id, run_id, leaf_state)
        plan = self.json_store.read_json(operation_plan_key)
        return self._run_approval_response(
            manifest=self._run_manifest(workspace_id, run_id),
            operation_plan_key=operation_plan_key,
            plan=plan,
            decision="approved",
            extra_artifacts={
                "approved_tool_result": tool_result,
                "resumed_run_status": resumed["status"],
            },
        )

    def _resume_after_approved_skill_staged_patch(
        self,
        *,
        workspace_id: str,
        run_id: str,
        operation_plan_key: str,
        plan: dict[str, Any],
        identity: RuntimeIdentity,
    ) -> dict[str, Any]:
        from app.skills.service import SkillService

        manifest = self._run_manifest(workspace_id, run_id)
        thread_id = manifest["thread_id"]
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "approval_execution_started",
            {
                "approval_id": plan["approval_id"],
                "approval_family": "skill_patch",
                "skill_run_id": plan.get("skill_run_id"),
                "operation_plan_object_key": operation_plan_key,
            },
        )
        skill_result = SkillService(self.object_store).execute_approved_staged_patch(
            workspace_id=workspace_id,
            run_id=run_id,
            operation_plan_key=operation_plan_key,
            plan=plan,
        )
        plan = self.json_store.read_json(operation_plan_key)
        tool_call_id = str(plan.get("tool_call_id") or plan.get("approval_id"))
        self._append_message(
            workspace_id=workspace_id,
            thread_id=thread_id,
            role="assistant",
            content="",
            run_id=run_id,
            message_id=new_id("msg"),
            created_at=utc_now_iso(),
            tool_calls=[
                {
                    "id": tool_call_id,
                    "name": "skill_entrypoint_call",
                    "args": redact_runtime_value(
                        {
                            "entrypoint_tool_name": plan.get("entrypoint_tool_name"),
                            "skill_run_id": plan.get("skill_run_id"),
                            "approved_execution": True,
                        }
                    ),
                }
            ],
        )
        self._append_message(
            workspace_id=workspace_id,
            thread_id=thread_id,
            role="tool",
            content=json.dumps(skill_result, ensure_ascii=False, default=str),
            run_id=run_id,
            message_id=new_id("msg"),
            created_at=utc_now_iso(),
            tool_call_id=tool_call_id,
        )
        manifest = self._run_manifest(workspace_id, run_id)
        manifest.update(
            {
                "status": "running",
                "model_error": None,
                "owner": None,
                "updated_at": utc_now_iso(),
                "revision": _revision_next(manifest),
            }
        )
        self._write_run_manifest(workspace_id, run_id, manifest)
        leaf_state = self.json_store.read_json_or_default(
            run_leaf_state_key(workspace_id, run_id),
            {"schema_version": 1, "run_id": run_id},
        )
        leaf_state.update(
            {
                "status": "running",
                "requires_approval": False,
                "approval_execution_pending": False,
                "approved_skill_result": skill_result,
                "revision": _revision_next(leaf_state),
            }
        )
        self._write_leaf_state(workspace_id, run_id, leaf_state)
        self._upsert_run_indexes(workspace_id, thread_id, manifest)
        self._mark_thread_current_run(workspace_id, thread_id, run_id, "running")
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            (
                "approval_execution_completed"
                if plan.get("status") == "executed"
                else "approval_execution_failed"
            ),
            {
                "approval_id": plan["approval_id"],
                "approval_family": "skill_patch",
                "skill_run_id": plan.get("skill_run_id"),
                "status": plan.get("status"),
                "operation_plan_object_key": operation_plan_key,
            },
        )
        resumed = self.execute_run(
            workspace_id=workspace_id,
            thread_id=thread_id,
            identity=identity,
            run_id=run_id,
            user_message="Continue after approved Skill patch execution.",
        )
        leaf_state = self.json_store.read_json_or_default(
            run_leaf_state_key(workspace_id, run_id),
            {"schema_version": 1, "run_id": run_id},
        )
        leaf_state.update(
            {
                "approval_decision": plan["decision"],
                "approval_execution_pending": False,
                "approved_skill_result": skill_result,
                "revision": _revision_next(leaf_state),
            }
        )
        self._write_leaf_state(workspace_id, run_id, leaf_state)
        plan = self.json_store.read_json(operation_plan_key)
        return self._run_approval_response(
            manifest=self._run_manifest(workspace_id, run_id),
            operation_plan_key=operation_plan_key,
            plan=plan,
            decision="approved",
            extra_artifacts={
                "approved_skill_result": skill_result,
                "resumed_run_status": resumed["status"],
            },
        )

    def _execute_approved_tool_from_plan(
        self,
        *,
        workspace_id: str,
        run_id: str,
        thread_id: str,
        plan: dict[str, Any],
        operation_plan_key: str,
        identity: RuntimeIdentity,
    ) -> dict[str, Any]:
        tool_name = str(plan.get("tool_name") or "")
        tool_call_id = str(plan.get("tool_call_id") or plan["approval_id"])
        args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
        runtime_context = (
            dict(plan.get("runtime_context"))
            if isinstance(plan.get("runtime_context"), dict)
            else {}
        )
        runtime_context.update(
            {
                "workspace_id": workspace_id,
                "thread_id": thread_id,
                "run_id": run_id,
                "user_id": identity.user_id,
                "role": identity.role,
                "tool_call_id": tool_call_id,
            }
        )
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "tool_call_started",
            {
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "approval_id": plan["approval_id"],
                "approved_execution": True,
            },
        )
        try:
            content = redact_runtime_value(
                self.runtime_runner.tool_registry.invoke(
                    tool_name,
                    args,
                    runtime_context=runtime_context,
                    skip_approval=True,
                )
            )
            ok = not (isinstance(content, dict) and content.get("ok") is False)
            error_type = (
                str(content.get("error_type"))
                if isinstance(content, dict) and content.get("error_type")
                else None
            )
        except Exception as exc:  # noqa: BLE001 - approved tool execution is still a tool boundary.
            content = {
                "ok": False,
                "error_type": exc.__class__.__name__,
                "message_for_model": "Approved tool execution failed.",
                "retryable": True,
            }
            ok = False
            error_type = exc.__class__.__name__

        event_payload = {
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "ok": ok,
            "error_type": error_type,
            "approval_id": plan["approval_id"],
            "approved_execution": True,
        }
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "tool_call_completed" if ok else "tool_call_failed",
            event_payload,
        )
        completed_at = utc_now_iso()
        plan["status"] = "executed" if ok else "execution_failed"
        plan.pop("stage", None)
        plan["phase"] = plan["status"]
        plan["execution_completed_at"] = completed_at
        plan["execution_result"] = redact_runtime_value(
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "ok": ok,
                "error_type": error_type,
            }
        )
        plan["revision"] = _revision_next(plan)
        plan["updated_at"] = completed_at
        self.json_store.write_json(operation_plan_key, plan)
        self._append_event(
            workspace_id,
            thread_id,
            run_id,
            "approval_execution_completed" if ok else "approval_execution_failed",
            {
                "approval_id": plan["approval_id"],
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "ok": ok,
                "error_type": error_type,
                "status": plan["status"],
                "operation_plan_object_key": operation_plan_key,
            },
        )
        return {
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "ok": ok,
            "content": content,
            "error_type": error_type,
        }

    def list_run_events(
        self,
        workspace_id: str,
        run_id: str,
        after_event_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        manifest = self._run_manifest(workspace_id, run_id)
        after_seq = self._event_seq_from_id(run_id, after_event_id)
        events = [
            event
            for event in self._event_segment_store(workspace_id, run_id).read_all()
            if int(event["event_seq"]) > after_seq
        ]
        events = sorted(events, key=lambda item: int(item["event_seq"]))
        return events[: max(1, min(limit, 1000))], manifest

    def _find_run_approval_plan(
        self,
        workspace_id: str,
        run_id: str,
        approval_id: str,
    ) -> tuple[str, dict[str, Any]]:
        prefixes = [
            f"{run_prefix(workspace_id, run_id)}/skill_runs/",
            f"{run_prefix(workspace_id, run_id)}/tool_approvals/",
        ]
        for prefix in prefixes:
            for key in self.object_store.list_keys(prefix):
                if not key.endswith("/operation_plan.json"):
                    continue
                plan = self.json_store.read_json(key)
                if plan.get("approval_id") == approval_id:
                    return key, plan
        raise AgentSystemError(
            "approval_not_found",
            "Approval plan was not found.",
            status_code=404,
        )

    @staticmethod
    def _find_staged_patch_operation_record(
        records: list[dict[str, Any]],
        operation_id: str,
    ) -> dict[str, Any]:
        for record in records:
            if record.get("operation_id") != operation_id:
                continue
            if record.get("operation_action") == "rollback":
                continue
            if record.get("operation_type") != "skill_staged_patch":
                continue
            return record
        raise AgentSystemError(
            "operation_not_found",
            "Staged patch operation was not found.",
            status_code=404,
        )

    @staticmethod
    def _find_staged_patch_rollback_record(
        records: list[dict[str, Any]],
        operation_id: str,
    ) -> dict[str, Any] | None:
        for record in reversed(records):
            if record.get("operation_action") != "rollback":
                continue
            if record.get("target_operation_id") != operation_id:
                continue
            if record.get("operation_type") != "skill_staged_patch":
                continue
            return record
        return None

    @staticmethod
    def _rollback_response(
        *,
        manifest: dict[str, Any],
        operation_id: str,
        rollback_record: dict[str, Any],
    ) -> dict[str, Any]:
        restored_files = rollback_record.get("restored_files")
        return {
            "run_id": manifest["run_id"],
            "workspace_id": manifest["workspace_id"],
            "thread_id": manifest["thread_id"],
            "operation_id": operation_id,
            "rollback_token": rollback_record["rollback_token"],
            "status": rollback_record["status"],
            "restored_files": restored_files if isinstance(restored_files, list) else [],
            "event_id": rollback_record.get("event_id"),
            "updated_at": rollback_record.get("updated_at") or manifest["updated_at"],
        }

    def _update_skill_run_manifest_after_approval(
        self,
        *,
        workspace_id: str,
        run_id: str,
        plan: dict[str, Any],
        decision: str,
        status: str,
        decided_at: str,
    ) -> None:
        skill_run_id = plan.get("skill_run_id")
        if not skill_run_id:
            return
        manifest_key = f"{run_prefix(workspace_id, run_id)}/skill_runs/{skill_run_id}/manifest.json"
        if not self.object_store.exists(manifest_key):
            return
        manifest = self.json_store.read_json(manifest_key)
        manifest["status"] = status
        manifest["approval_decision"] = {
            "decision": decision,
            "decided_at": decided_at,
        }
        manifest["revision"] = _revision_next(manifest)
        self.json_store.write_json(manifest_key, manifest)

    @staticmethod
    def _run_approval_response(
        *,
        manifest: dict[str, Any],
        operation_plan_key: str,
        plan: dict[str, Any],
        decision: str,
        extra_artifacts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "run_id": manifest["run_id"],
            "workspace_id": manifest["workspace_id"],
            "thread_id": manifest["thread_id"],
            "approval_id": plan["approval_id"],
            "decision": decision,
            "status": plan["status"],
            "run_status": manifest["status"],
            "operation_plan_object_key": operation_plan_key,
            "skill_run_id": plan.get("skill_run_id"),
            "artifacts": {**(plan.get("artifacts") or {}), **(extra_artifacts or {})},
            "updated_at": plan.get("updated_at") or manifest["updated_at"],
        }

    def stream_closed_event(self, manifest: dict[str, Any]) -> dict[str, Any]:
        event_seq = int(manifest.get("last_event_seq") or 0) + 1
        return {
            "event_id": _event_id(manifest["run_id"], event_seq),
            "event_seq": event_seq,
            "workspace_id": manifest["workspace_id"],
            "thread_id": manifest["thread_id"],
            "run_id": manifest["run_id"],
            "status": manifest["status"],
            "type": "stream_closed",
            "created_at": utc_now_iso(),
            "payload": {"run_id": manifest["run_id"], "status": manifest["status"]},
        }

    def recover_stale_running_runs(
        self,
        workspace_id: str,
        stale_after_seconds: int = 3600,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        stale_after = timedelta(seconds=max(1, stale_after_seconds))
        recovered: list[dict[str, Any]] = []
        for key in self.object_store.list_keys(f"{workspace_prefix(workspace_id)}/runs"):
            if not key.endswith("/manifest.json"):
                continue
            manifest = self.json_store.read_json(key)
            if manifest.get("status") != "running":
                continue
            owner = manifest.get("owner") or {}
            owner_expires_at = _parse_iso_datetime(owner.get("expires_at"))
            if owner_expires_at and owner_expires_at > now:
                continue
            updated_at = _parse_iso_datetime(manifest.get("updated_at"))
            if updated_at and now - updated_at < stale_after:
                continue
            recovered.append(
                self._mark_stale_run_failed(
                    workspace_id=workspace_id,
                    manifest=manifest,
                    recovered_at=now.isoformat(),
                    stale_after_seconds=stale_after_seconds,
                )
            )
        return {
            "workspace_id": workspace_id,
            "recovered_count": len(recovered),
            "recovered_runs": recovered,
        }

    def _mark_stale_run_failed(
        self,
        workspace_id: str,
        manifest: dict[str, Any],
        recovered_at: str,
        stale_after_seconds: int,
    ) -> dict[str, Any]:
        run_id = manifest["run_id"]
        thread_id = manifest["thread_id"]
        if self._run_manifest(workspace_id, run_id)["status"] in TERMINAL_RUN_STATUSES:
            return self.get_run(workspace_id, run_id)
        payload = {
            "status": "failed",
            "error_type": "stale_running_recovered",
            "previous_updated_at": manifest.get("updated_at"),
            "recovered_at": recovered_at,
            "stale_after_seconds": stale_after_seconds,
        }
        self._append_event(workspace_id, thread_id, run_id, "run_recovery_started", payload)
        self._append_event(workspace_id, thread_id, run_id, "run_failed", payload)
        latest = self._run_manifest(workspace_id, run_id)
        latest.update(
            {
                "status": "failed",
                "model_error": "stale_running_recovered",
                "owner": None,
                "updated_at": recovered_at,
                "revision": _revision_next(latest),
            }
        )
        self._write_run_manifest(workspace_id, run_id, latest)
        self._write_leaf_state(
            workspace_id,
            run_id,
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": "failed",
                "context_usage": {},
                "tool_results": [],
                "requires_approval": False,
                "model_error": "stale_running_recovered",
                "warnings": ["Recovered stale running run after process restart or worker loss."],
                "recovered_at": recovered_at,
                "revision": 1,
            },
        )
        self._upsert_run_indexes(workspace_id, thread_id, latest)
        self._mark_thread_current_run(workspace_id, thread_id, run_id, "failed")
        return self.get_run(workspace_id, run_id)

    def _append_message(
        self,
        workspace_id: str,
        thread_id: str,
        role: str,
        content: str,
        run_id: str,
        message_id: str,
        created_at: str,
        tool_call_id: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        record = {
            "message_id": message_id,
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "role": role,
            "content": redact_runtime_value(content),
            "run_id": run_id,
            "created_at": created_at,
        }
        if tool_call_id:
            record["tool_call_id"] = tool_call_id
        if tool_calls:
            record["tool_calls"] = redact_runtime_value(tool_calls)
        segment = self._message_segment_store(workspace_id, thread_id).append(record)
        index_key = thread_message_index_key(workspace_id, thread_id)
        index = self.json_store.read_json_or_default(
            index_key,
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "thread_id": thread_id,
                "messages": [],
                "message_count": 0,
                "revision": 0,
            },
        )
        index["messages"].append(
            {
                "message_id": message_id,
                "role": role,
                "run_id": run_id,
                "object_key": segment.object_key,
                "segment_no": segment.segment_no,
                "created_at": created_at,
            }
        )
        index["message_count"] = len(index["messages"])
        index["revision"] = _revision_next(index)
        self.json_store.write_json(index_key, index)
        self._touch_thread_after_message(
            workspace_id,
            thread_id,
            message_id,
            role,
            content,
            created_at,
        )
        return record

    def _append_event(
        self,
        workspace_id: str,
        thread_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = self._run_manifest_or_none(workspace_id, run_id)
        trace_id = manifest.get("trace_id") if manifest else None
        current = self.json_store.read_json_or_default(
            run_event_index_key(workspace_id, run_id),
            {
                "schema_version": 1,
                "stream_id": run_id,
                "segments": [],
                "event_count": 0,
                "last_event_seq": 0,
                "last_event_id": None,
                "revision": 0,
            },
        )
        event_seq = int(current.get("last_event_seq") or 0) + 1
        event = {
            "schema_version": 1,
            "event_seq": event_seq,
            "event_id": _event_id(run_id, event_seq),
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "type": event_type,
            "created_at": utc_now_iso(),
            "payload": redact_runtime_value(payload),
        }
        store = self._event_segment_store(workspace_id, run_id)
        store.append(event)
        index = store.rebuild_event_index(run_event_index_key(workspace_id, run_id), run_id)
        if manifest:
            manifest["last_event_id"] = index["last_event_id"]
            manifest["last_event_seq"] = index["last_event_seq"]
            manifest["updated_at"] = utc_now_iso()
            manifest["revision"] = _revision_next(manifest)
            self._write_run_manifest(workspace_id, run_id, manifest)
            self._upsert_run_indexes(workspace_id, thread_id, manifest)
        return event

    def _thread_manifest(self, workspace_id: str, thread_id: str) -> dict[str, Any]:
        key = thread_manifest_key(workspace_id, thread_id)
        if not self.object_store.exists(key):
            raise AgentSystemError("thread_not_found", "Thread was not found.", status_code=404)
        return self.json_store.read_json(key)

    def _run_manifest(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        key = run_manifest_key(workspace_id, run_id)
        if not self.object_store.exists(key):
            raise AgentSystemError("run_not_found", "Run was not found.", status_code=404)
        return self.json_store.read_json(key)

    def _run_manifest_or_none(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        key = run_manifest_key(workspace_id, run_id)
        if not self.object_store.exists(key):
            return None
        return self.json_store.read_json(key)

    def _write_run_manifest(self, workspace_id: str, run_id: str, manifest: dict[str, Any]) -> None:
        self.json_store.write_json(run_manifest_key(workspace_id, run_id), manifest)

    def _try_acquire_run_lease(self, workspace_id: str, run_id: str) -> dict[str, Any] | None:
        key = run_manifest_key(workspace_id, run_id)
        for _ in range(2):
            manifest = self._run_manifest(workspace_id, run_id)
            if manifest["status"] in NON_EXECUTABLE_RUN_STATUSES:
                return None
            owner = manifest.get("owner")
            owner_expires_at = _parse_iso_datetime(owner.get("expires_at")) if owner else None
            now = datetime.now(timezone.utc)
            if owner and owner_expires_at and owner_expires_at > now:
                return None
            acquired_at = now.isoformat()
            lease = {
                "runtime_instance_id": self.runtime_instance_id,
                "fencing_token": new_id("fence"),
                "acquired_at": acquired_at,
                "expires_at": (now + timedelta(seconds=self.run_lease_ttl_seconds)).isoformat(),
            }
            next_manifest = {
                **manifest,
                "owner": lease,
                "updated_at": acquired_at,
                "revision": _revision_next(manifest),
            }
            try:
                self.json_store.write_json(
                    key,
                    next_manifest,
                    expected_revision=int(manifest.get("revision", 0)),
                )
                return lease
            except RevisionConflictError:
                continue
        return None

    def _run_lease_is_active(
        self,
        workspace_id: str,
        run_id: str,
        fencing_token: str,
    ) -> bool:
        manifest = self._run_manifest(workspace_id, run_id)
        if manifest["status"] in TERMINAL_RUN_STATUSES:
            return False
        owner = manifest.get("owner") or {}
        return (
            owner.get("runtime_instance_id") == self.runtime_instance_id
            and owner.get("fencing_token") == fencing_token
        )

    def _write_leaf_state(self, workspace_id: str, run_id: str, value: dict[str, Any]) -> None:
        if not value.get("trace_id"):
            manifest = self._run_manifest_or_none(workspace_id, run_id)
            if manifest and manifest.get("trace_id"):
                value = {**value, "trace_id": manifest["trace_id"]}
        self.json_store.write_json(
            run_leaf_state_key(workspace_id, run_id),
            redact_runtime_value(value),
        )

    def _threads_index(self, workspace_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            threads_index_key(workspace_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "threads": [],
                "revision": 0,
            },
        )

    def _thread_runs_index(self, workspace_id: str, thread_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            thread_runs_index_key(workspace_id, thread_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "thread_id": thread_id,
                "runs": [],
                "idempotency": {},
                "revision": 0,
            },
        )

    def _workspace_runs_index(self, workspace_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            workspace_runs_index_key(workspace_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "runs": [],
                "revision": 0,
            },
        )

    def _upsert_thread_index(self, workspace_id: str, manifest: dict[str, Any]) -> None:
        index = self._threads_index(workspace_id)
        summary = self._public_thread(manifest)
        index["threads"] = [
            thread for thread in index["threads"] if thread["thread_id"] != manifest["thread_id"]
        ]
        index["threads"].append(summary)
        index["revision"] = _revision_next(index)
        self.json_store.write_json(threads_index_key(workspace_id), index)

    def _upsert_run_indexes(
        self,
        workspace_id: str,
        thread_id: str,
        manifest: dict[str, Any],
    ) -> None:
        summary = self._public_run(manifest)
        workspace_index = self._workspace_runs_index(workspace_id)
        workspace_index["runs"] = [
            run for run in workspace_index["runs"] if run["run_id"] != manifest["run_id"]
        ]
        workspace_index["runs"].append(summary)
        workspace_index["revision"] = _revision_next(workspace_index)
        self.json_store.write_json(workspace_runs_index_key(workspace_id), workspace_index)

        thread_index = self._thread_runs_index(workspace_id, thread_id)
        thread_index["runs"] = [
            run for run in thread_index["runs"] if run["run_id"] != manifest["run_id"]
        ]
        thread_index["runs"].append(summary)
        thread_index["revision"] = _revision_next(thread_index)
        self.json_store.write_json(thread_runs_index_key(workspace_id, thread_id), thread_index)

    def _register_new_run(
        self,
        workspace_id: str,
        thread_id: str,
        manifest: dict[str, Any],
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        self._upsert_run_indexes(workspace_id, thread_id, manifest)
        thread_index = self._thread_runs_index(workspace_id, thread_id)
        thread_index["idempotency"][idempotency_key] = {
            "request_hash": request_hash,
            "run_id": manifest["run_id"],
            "user_message_id": manifest["user_message_id"],
        }
        thread_index["revision"] = _revision_next(thread_index)
        self.json_store.write_json(thread_runs_index_key(workspace_id, thread_id), thread_index)

    def _touch_thread_after_message(
        self,
        workspace_id: str,
        thread_id: str,
        message_id: str,
        role: str,
        content: str,
        created_at: str,
    ) -> None:
        manifest = self._thread_manifest(workspace_id, thread_id)
        manifest["message_count"] = int(manifest.get("message_count") or 0) + 1
        manifest["last_message_id"] = message_id
        manifest["last_message_preview"] = _preview(content)
        manifest["last_message_at"] = created_at
        manifest["updated_at"] = created_at
        if role == "user" and manifest["title"] == "Untitled thread":
            manifest["title"] = _preview(content, 60) or "Untitled thread"
        manifest["revision"] = _revision_next(manifest)
        self.json_store.write_json(thread_manifest_key(workspace_id, thread_id), manifest)
        self._upsert_thread_index(workspace_id, manifest)

    def _mark_thread_current_run(
        self,
        workspace_id: str,
        thread_id: str,
        run_id: str,
        status: str,
    ) -> None:
        manifest = self._thread_manifest(workspace_id, thread_id)
        if manifest.get("current_run_id") != run_id:
            manifest["run_count"] = int(manifest.get("run_count") or 0) + 1
        manifest["current_run_id"] = run_id
        manifest["current_run_status"] = status
        manifest["updated_at"] = utc_now_iso()
        manifest["revision"] = _revision_next(manifest)
        self.json_store.write_json(thread_manifest_key(workspace_id, thread_id), manifest)
        self._upsert_thread_index(workspace_id, manifest)

    def _find_thread_by_idempotency_key(
        self,
        workspace_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        for thread in self._threads_index(workspace_id)["threads"]:
            manifest = self._thread_manifest(workspace_id, thread["thread_id"])
            record = manifest.get("idempotency", {}).get(idempotency_key)
            if record:
                if record.get("request_hash") and record["request_hash"] != request_hash:
                    raise AgentSystemError(
                        "idempotency_conflict",
                        "Idempotency key was reused with different payload.",
                        status_code=409,
                    )
                return self._public_thread(manifest)
        return None

    def _message_segment_store(self, workspace_id: str, thread_id: str) -> JsonlSegmentStore:
        return JsonlSegmentStore(self.object_store, thread_messages_prefix(workspace_id, thread_id))

    def _event_segment_store(self, workspace_id: str, run_id: str) -> JsonlSegmentStore:
        return JsonlSegmentStore(self.object_store, run_events_prefix(workspace_id, run_id))

    def _event_seq_from_id(self, run_id: str, event_id: str | None) -> int:
        if not event_id:
            return 0
        match = EVENT_ID_RE.fullmatch(event_id)
        if not match or match.group("run_id") != run_id:
            raise AgentSystemError(
                "invalid_event_cursor",
                "Event cursor does not belong to this run.",
                status_code=400,
            )
        return int(match.group("seq"))

    @staticmethod
    def _records_after(
        records: list[dict[str, Any]],
        id_field: str,
        item_id: str,
    ) -> list[dict[str, Any]]:
        for index, record in enumerate(records):
            if record.get(id_field) == item_id:
                return records[index + 1 :]
        return records

    @staticmethod
    def _records_before(
        records: list[dict[str, Any]],
        id_field: str,
        item_id: str,
    ) -> list[dict[str, Any]]:
        for index, record in enumerate(records):
            if record.get(id_field) == item_id:
                return records[:index]
        return records

    @staticmethod
    def _last_assistant_content(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("type") == "ai":
                content = str(message.get("content") or "").strip()
                if content:
                    return content
        return "Run completed."

    @staticmethod
    def _public_thread(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "thread_id": manifest["thread_id"],
            "workspace_id": manifest["workspace_id"],
            "user_id": manifest["user_id"],
            "title": manifest["title"],
            "status": manifest["status"],
            "pinned": bool(manifest.get("pinned", False)),
            "current_run_id": manifest.get("current_run_id"),
            "current_run_status": manifest.get("current_run_status"),
            "last_message_id": manifest.get("last_message_id"),
            "last_message_preview": manifest.get("last_message_preview"),
            "last_message_at": manifest.get("last_message_at"),
            "message_count": int(manifest.get("message_count") or 0),
            "run_count": int(manifest.get("run_count") or 0),
            "created_at": manifest["created_at"],
            "updated_at": manifest["updated_at"],
        }

    @staticmethod
    def _public_run(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": manifest["run_id"],
            "workspace_id": manifest["workspace_id"],
            "thread_id": manifest["thread_id"],
            "status": manifest["status"],
            "idempotency_key": manifest["idempotency_key"],
            "user_message_id": manifest.get("user_message_id"),
            "last_event_id": manifest.get("last_event_id"),
            "last_event_seq": int(manifest.get("last_event_seq") or 0),
            "assistant_message_id": manifest.get("assistant_message_id"),
            "model_error": manifest.get("model_error"),
            "trace_id": manifest.get("trace_id"),
            "created_at": manifest["created_at"],
            "updated_at": manifest["updated_at"],
        }
