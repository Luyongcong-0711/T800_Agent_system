from __future__ import annotations

from typing import Any

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.runtime.tools import redact_runtime_value
from app.schemas.identity import RuntimeIdentity
from app.schemas.subagent import (
    SubAgentCompleteRequest,
    SubAgentReviewRequest,
    SubAgentTaskRequest,
)
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import (
    run_event_index_key,
    run_events_prefix,
    run_leaf_state_key,
    run_manifest_key,
    run_prefix,
    subagent_index_key,
    subagent_task_manifest_key,
    subagent_task_result_key,
    subagent_task_review_key,
)

ACTIVE_WRITE_STATUSES = {"created", "queued", "running", "completed"}
TERMINAL_STATUSES = {"failed", "reviewed"}
DEFAULT_AGENT_TYPES = (
    "code_reviewer",
    "researcher",
    "log_analyst",
    "database_checker",
)


class SubAgentService:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)

    def list_tasks(
        self,
        workspace_id: str,
        *,
        parent_run_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        tasks = list(self._index(workspace_id).get("tasks", []))
        if parent_run_id:
            tasks = [task for task in tasks if task.get("parent_run_id") == parent_run_id]
        if status:
            tasks = [task for task in tasks if task.get("status") == status]
        return sorted(tasks, key=lambda item: item.get("updated_at") or "", reverse=True)

    def create_task(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: SubAgentTaskRequest,
    ) -> dict[str, Any]:
        self._validate_task_request(workspace_id, request)
        parent_run_id = self._require_parent_run_id(request.parent_run_id)
        now = utc_now_iso()
        task_id = new_id("subtask")
        manifest = {
            "schema_version": 1,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "parent_run_id": parent_run_id,
            "parent_thread_id": request.parent_thread_id,
            "agent_type": self._normalize_agent_type(request.agent_type),
            "objective": redact_runtime_value(request.objective),
            "mode": request.mode,
            "read_scope": redact_runtime_value(request.read_scope),
            "write_scope": redact_runtime_value(request.write_scope),
            "allowed_tools": redact_runtime_value(request.allowed_tools),
            "forbidden_tools": redact_runtime_value(request.forbidden_tools),
            "timeout_ms": request.timeout_ms,
            "token_budget": request.token_budget,
            "expected_output": redact_runtime_value(request.expected_output),
            "status": "created",
            "created_by": identity.user_id,
            "needs_main_review": True,
            "result": None,
            "review": None,
            "object_keys": {
                "manifest": subagent_task_manifest_key(workspace_id, task_id),
                "result": subagent_task_result_key(workspace_id, task_id),
                "review": subagent_task_review_key(workspace_id, task_id),
            },
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        self.json_store.write_json(subagent_task_manifest_key(workspace_id, task_id), manifest)
        self._write_run_scoped_json(
            workspace_id,
            parent_run_id,
            task_id,
            "manifest.json",
            manifest,
        )
        self._upsert_index(workspace_id, manifest)
        self._write_run_leaf_state(workspace_id, parent_run_id)
        return self.get_task(workspace_id, task_id)

    def get_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        manifest = self._manifest(workspace_id, task_id)
        result = None
        if self.object_store.exists(subagent_task_result_key(workspace_id, task_id)):
            result = self.json_store.read_json(subagent_task_result_key(workspace_id, task_id))
        review = None
        if self.object_store.exists(subagent_task_review_key(workspace_id, task_id)):
            review = self.json_store.read_json(subagent_task_review_key(workspace_id, task_id))
        return {
            **self._public_summary(manifest),
            "schema_version": manifest.get("schema_version", 1),
            "expected_output": manifest.get("expected_output") or "",
            "result": redact_runtime_value(result),
            "review": redact_runtime_value(review),
            "object_keys": manifest.get("object_keys") or {},
        }

    def complete_task(
        self,
        workspace_id: str,
        task_id: str,
        request: SubAgentCompleteRequest,
    ) -> dict[str, Any]:
        manifest = self._manifest(workspace_id, task_id)
        now = utc_now_iso()
        extra = request.model_extra or {}
        result = {
            "schema_version": 1,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "parent_run_id": manifest["parent_run_id"],
            "agent_type": manifest["agent_type"],
            "status": request.status,
            "summary": redact_runtime_value(request.summary),
            "findings": redact_runtime_value([item.model_dump() for item in request.findings]),
            "changed_files": redact_runtime_value(request.changed_files),
            "risks": redact_runtime_value(request.risks),
            "open_questions": redact_runtime_value(request.open_questions),
            "created_job_id": request.created_job_id,
            "error_type": request.error_type,
            "needs_main_review": True,
            "can_directly_finalize": False,
            "created_at": now,
        }
        if isinstance(extra.get("execution"), dict):
            result["execution"] = redact_runtime_value(extra["execution"])
        if isinstance(extra.get("evidence"), dict):
            result["evidence"] = redact_runtime_value(extra["evidence"])
        self.json_store.write_json(subagent_task_result_key(workspace_id, task_id), result)
        self._write_run_scoped_json(
            workspace_id,
            manifest["parent_run_id"],
            task_id,
            "result.json",
            result,
        )
        manifest["status"] = request.status
        manifest["result"] = {
            "summary": result["summary"],
            "needs_main_review": True,
            "can_directly_finalize": False,
        }
        manifest["updated_at"] = now
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(subagent_task_manifest_key(workspace_id, task_id), manifest)
        self._write_run_scoped_json(
            workspace_id,
            manifest["parent_run_id"],
            task_id,
            "manifest.json",
            manifest,
        )
        self._upsert_index(workspace_id, manifest)
        self._write_run_leaf_state(workspace_id, manifest["parent_run_id"])
        return result

    def review_result(
        self,
        workspace_id: str,
        task_id: str,
        identity: RuntimeIdentity,
        request: SubAgentReviewRequest,
    ) -> dict[str, Any]:
        manifest = self._manifest(workspace_id, task_id)
        if not self.object_store.exists(subagent_task_result_key(workspace_id, task_id)):
            raise AgentSystemError(
                "subagent_result_missing",
                "SubAgent result must exist before review.",
                status_code=409,
            )
        result = self.json_store.read_json(subagent_task_result_key(workspace_id, task_id))
        now = utc_now_iso()
        decision = self._review_decision(request)
        notes = request.review_notes if request.review_notes is not None else request.reviewer_notes
        review = {
            "schema_version": 1,
            "task_id": task_id,
            "workspace_id": workspace_id,
            "parent_run_id": manifest["parent_run_id"],
            "decision": decision,
            "review_status": decision,
            "reviewer_id": identity.user_id,
            "reviewer_notes": redact_runtime_value(notes),
            "reviewed_subagent_result": redact_runtime_value(result),
            "reviewed_at": now,
        }
        self.json_store.write_json(subagent_task_review_key(workspace_id, task_id), review)
        self._write_run_scoped_json(
            workspace_id,
            manifest["parent_run_id"],
            task_id,
            "review.json",
            review,
        )
        manifest["status"] = "reviewed"
        manifest["review"] = {
            "decision": decision,
            "reviewer_notes": redact_runtime_value(notes),
            "reviewed_at": now,
        }
        manifest["updated_at"] = now
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(subagent_task_manifest_key(workspace_id, task_id), manifest)
        self._write_run_scoped_json(
            workspace_id,
            manifest["parent_run_id"],
            task_id,
            "manifest.json",
            manifest,
        )
        self._upsert_index(workspace_id, manifest)
        self._write_run_leaf_state(workspace_id, manifest["parent_run_id"])
        self._append_run_event_if_present(
            workspace_id,
            manifest["parent_run_id"],
            "subagent_result_reviewed",
            {
                "task_id": task_id,
                "agent_type": manifest["agent_type"],
                "decision": decision,
                "reviewer_id": identity.user_id,
                "needs_main_review": True,
                "can_directly_finalize": False,
            },
        )
        return review

    def run_deterministic_subagent(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: SubAgentTaskRequest,
    ) -> dict[str, Any]:
        return self.run_subagent(workspace_id, identity, request)

    def run_subagent(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: SubAgentTaskRequest,
    ) -> dict[str, Any]:
        task = self.create_task(workspace_id, identity, request)
        return self.execute_task(workspace_id, task["task_id"])

    def enqueue_subagent_job(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: SubAgentTaskRequest,
        *,
        job_service: Any,
    ) -> dict[str, Any]:
        from app.schemas.job import CreateJobRequest

        task = self.create_task(workspace_id, identity, request)
        self._set_task_status(workspace_id, task["task_id"], "queued")
        parent_run_id = str(task["parent_run_id"])
        job = job_service.create_job(
            workspace_id,
            identity,
            CreateJobRequest(
                job_type="subagent_execution_job",
                target_scope={
                    "scope_type": "subagent_task",
                    "task_id": task["task_id"],
                },
                input={"task_id": task["task_id"]},
                idempotency_key=f"subagent-execution:{task['task_id']}",
                title=f"Execute SubAgent {task['agent_type']}",
                related_run_id=parent_run_id,
                related_thread_id=task.get("parent_thread_id"),
            ),
        )
        result = {
            "schema_version": 1,
            "task_id": task["task_id"],
            "workspace_id": workspace_id,
            "parent_run_id": parent_run_id,
            "agent_type": task["agent_type"],
            "status": "queued",
            "summary": "SubAgent task queued for JobWorker execution.",
            "findings": [],
            "changed_files": [],
            "risks": [],
            "open_questions": [],
            "created_job_id": job["job_id"],
            "job_id": job["job_id"],
            "needs_main_review": True,
            "can_directly_finalize": False,
            "created_at": utc_now_iso(),
        }
        return redact_runtime_value(result)

    def execute_task(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        self._set_task_status(workspace_id, task_id, "running")
        manifest = self._manifest(workspace_id, task_id)
        try:
            from app.subagents.executor import SubAgentExecutor

            executed = SubAgentExecutor(self.object_store).execute(manifest)
            return self.complete_task(
                workspace_id,
                task_id,
                SubAgentCompleteRequest(
                    status="completed",
                    summary=str(executed["summary"]),
                    findings=executed.get("findings") or [],
                    changed_files=executed.get("changed_files") or [],
                    risks=executed.get("risks") or [],
                    open_questions=executed.get("open_questions") or [],
                    execution=executed.get("execution"),
                    evidence=executed.get("evidence"),
                ),
            )
        except Exception as exc:  # noqa: BLE001 - SubAgent boundary returns structured failure.
            return self.complete_task(
                workspace_id,
                task_id,
                SubAgentCompleteRequest(
                    status="failed",
                    summary="SubAgent executor failed before producing a reviewed result.",
                    findings=[],
                    changed_files=[],
                    risks=[exc.__class__.__name__],
                    open_questions=[],
                    error_type="subagent_executor_failed",
                ),
            )

    def run_leaf_state(self, workspace_id: str, parent_run_id: str) -> dict[str, Any]:
        tasks = self.list_tasks(workspace_id, parent_run_id=parent_run_id)
        results: list[dict[str, Any]] = []
        reviewed: list[dict[str, Any]] = []
        for task in tasks:
            task_id = task["task_id"]
            if self.object_store.exists(subagent_task_result_key(workspace_id, task_id)):
                result_key = subagent_task_result_key(workspace_id, task_id)
                results.append(self.json_store.read_json(result_key))
            if self.object_store.exists(subagent_task_review_key(workspace_id, task_id)):
                review_key = subagent_task_review_key(workspace_id, task_id)
                reviewed.append(self.json_store.read_json(review_key))
        return {
            "subagent_tasks": tasks,
            "subagent_results": redact_runtime_value(results),
            "reviewed_subagent_results": redact_runtime_value(reviewed),
        }

    def _validate_task_request(self, workspace_id: str, request: SubAgentTaskRequest) -> None:
        parent_run_id = self._require_parent_run_id(request.parent_run_id)
        if request.mode == "write" and not request.write_scope:
            raise AgentSystemError(
                "subagent_write_scope_required",
                "Write-mode SubAgent tasks must declare write_scope.",
                status_code=422,
            )
        if request.mode != "write":
            return
        for task in self.list_tasks(workspace_id, parent_run_id=parent_run_id):
            if task.get("mode") != "write" or task.get("status") not in ACTIVE_WRITE_STATUSES:
                continue
            overlap = self._overlapping_scopes(task.get("write_scope") or [], request.write_scope)
            if overlap:
                raise AgentSystemError(
                    "subagent_write_scope_conflict",
                    "Another active SubAgent task owns an overlapping write_scope.",
                    status_code=409,
                    retryable=False,
                    details={
                        "existing_task_id": task["task_id"],
                        "parent_run_id": parent_run_id,
                        "overlap": overlap,
                    },
                )

    @staticmethod
    def _require_parent_run_id(parent_run_id: str | None) -> str:
        if not parent_run_id:
            raise AgentSystemError(
                "subagent_parent_run_required",
                "SubAgent tasks must be scoped to a parent run.",
                status_code=422,
            )
        return parent_run_id

    def _manifest(self, workspace_id: str, task_id: str) -> dict[str, Any]:
        key = subagent_task_manifest_key(workspace_id, task_id)
        if not self.object_store.exists(key):
            raise AgentSystemError("subagent_task_not_found", "SubAgent task was not found.", 404)
        return self.json_store.read_json(key)

    def _index(self, workspace_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            subagent_index_key(workspace_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "tasks": [],
                "updated_at": None,
                "revision": 0,
            },
        )

    def _upsert_index(self, workspace_id: str, manifest: dict[str, Any]) -> None:
        index = self._index(workspace_id)
        summary = self._public_summary(manifest)
        index["tasks"] = [
            task for task in index.get("tasks", []) if task["task_id"] != manifest["task_id"]
        ]
        index["tasks"].append(summary)
        index["tasks"] = sorted(index["tasks"], key=lambda item: item["updated_at"], reverse=True)
        index["updated_at"] = utc_now_iso()
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(subagent_index_key(workspace_id), index)

    def _set_task_status(self, workspace_id: str, task_id: str, status: str) -> None:
        manifest = self._manifest(workspace_id, task_id)
        now = utc_now_iso()
        manifest["status"] = status
        manifest["updated_at"] = now
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(subagent_task_manifest_key(workspace_id, task_id), manifest)
        self._write_run_scoped_json(
            workspace_id,
            manifest["parent_run_id"],
            task_id,
            "manifest.json",
            manifest,
        )
        self._upsert_index(workspace_id, manifest)
        self._write_run_leaf_state(workspace_id, manifest["parent_run_id"])

    @staticmethod
    def _public_summary(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": manifest["task_id"],
            "workspace_id": manifest["workspace_id"],
            "parent_run_id": manifest["parent_run_id"],
            "parent_thread_id": manifest.get("parent_thread_id"),
            "agent_type": manifest["agent_type"],
            "objective": manifest["objective"],
            "mode": manifest["mode"],
            "read_scope": manifest.get("read_scope") or [],
            "write_scope": manifest.get("write_scope") or [],
            "allowed_tools": manifest.get("allowed_tools") or [],
            "forbidden_tools": manifest.get("forbidden_tools") or [],
            "timeout_ms": int(manifest.get("timeout_ms") or 300000),
            "token_budget": int(manifest.get("token_budget") or 12000),
            "status": manifest.get("status") or "created",
            "needs_main_review": bool(manifest.get("needs_main_review", True)),
            "requires_main_review": bool(manifest.get("needs_main_review", True)),
            "output_schema": "SubAgentResult",
            "created_at": manifest["created_at"],
            "updated_at": manifest["updated_at"],
        }

    def _write_run_leaf_state(self, workspace_id: str, parent_run_id: str) -> None:
        previous = self.json_store.read_json_or_default(
            run_leaf_state_key(workspace_id, parent_run_id),
            {
                "schema_version": 1,
                "run_id": parent_run_id,
                "workspace_id": workspace_id,
                "status": "running",
            },
        )
        state = self.run_leaf_state(workspace_id, parent_run_id)
        leaf_state = {
            **previous,
            **state,
            "schema_version": 1,
            "run_id": parent_run_id,
            "workspace_id": workspace_id,
            "updated_at": utc_now_iso(),
            "revision": int(previous.get("revision") or 0) + 1,
        }
        self.json_store.write_json(run_leaf_state_key(workspace_id, parent_run_id), leaf_state)

    def _write_run_scoped_json(
        self,
        workspace_id: str,
        parent_run_id: str,
        task_id: str,
        file_name: str,
        value: dict[str, Any],
    ) -> None:
        key = (
            f"{run_prefix(workspace_id, parent_run_id)}/subagent_tasks/"
            f"{task_id}/{file_name}"
        )
        self.json_store.write_json(key, redact_runtime_value(value))

    def _append_run_event_if_present(
        self,
        workspace_id: str,
        parent_run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        manifest_key = run_manifest_key(workspace_id, parent_run_id)
        if not self.object_store.exists(manifest_key):
            return
        manifest = self.json_store.read_json(manifest_key)
        event_seq = int(manifest.get("last_event_seq") or 0) + 1
        event = {
            "schema_version": 1,
            "event_id": f"evt_{parent_run_id}_{event_seq:012d}",
            "event_seq": event_seq,
            "workspace_id": workspace_id,
            "thread_id": manifest.get("thread_id"),
            "run_id": parent_run_id,
            "type": event_type,
            "payload": redact_runtime_value(payload),
            "created_at": utc_now_iso(),
        }
        store = JsonlSegmentStore(self.object_store, run_events_prefix(workspace_id, parent_run_id))
        store.append(event)
        store.rebuild_event_index(run_event_index_key(workspace_id, parent_run_id), parent_run_id)
        manifest["last_event_id"] = event["event_id"]
        manifest["last_event_seq"] = event_seq
        manifest["updated_at"] = utc_now_iso()
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(manifest_key, manifest)

    @staticmethod
    def _review_decision(request: SubAgentReviewRequest) -> str:
        if request.decision:
            return request.decision
        if request.accepted is False:
            return "rejected"
        return "accepted"

    @staticmethod
    def _normalize_agent_type(agent_type: str) -> str:
        return agent_type.strip().lower().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _overlapping_scopes(left: list[str], right: list[str]) -> list[str]:
        overlaps: list[str] = []
        for left_scope in left:
            left_normalized = left_scope.strip().replace("\\", "/").rstrip("/")
            for right_scope in right:
                right_normalized = right_scope.strip().replace("\\", "/").rstrip("/")
                if not left_normalized or not right_normalized:
                    continue
                if (
                    left_normalized == right_normalized
                    or left_normalized.startswith(f"{right_normalized}/")
                    or right_normalized.startswith(f"{left_normalized}/")
                ):
                    overlaps.append(f"{left_scope} <-> {right_scope}")
        return overlaps
