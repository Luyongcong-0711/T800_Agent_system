from __future__ import annotations

import hashlib
import re
from typing import Any

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.runtime.tools import redact_runtime_value
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest
from app.schemas.memory import PatchMemoryRequest, UpsertMemoryRequest
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import (
    memory_snapshot_key,
    memory_sync_event_index_key,
    memory_sync_events_prefix,
    memory_sync_state_key,
    user_disabled_memory_patterns_key,
    user_memory_index_key,
    user_memory_object_key,
    workspace_disabled_memory_patterns_key,
    workspace_memory_index_key,
    workspace_memory_object_key,
)

GLOBAL_MEMORY_TYPES = {"user_profile"}
WORKSPACE_MEMORY_TYPES = {"project_fact", "project_rule"}
PROFILE_AND_PREFERENCE_TYPES = {"user_profile", "user_preference"}
CANDIDATE_MEMORY_TYPES = WORKSPACE_MEMORY_TYPES
MILVUS_INDEXABLE_MEMORY_TYPES = {
    "user_profile",
    "user_preference",
    "project_fact",
    "project_rule",
    "tool_usage_preference",
    "correction",
    "safety_boundary",
    "relationship_fact",
}
NEO4J_INDEXABLE_MEMORY_TYPES = {
    "user_profile",
    "project_fact",
    "project_rule",
    "correction",
    "relationship_fact",
}
NEO4J_PREFERENCE_FIELDS = {
    "hobby",
    "hobbies",
    "interest",
    "interests",
    "likes",
    "preferred_name",
}
SENSITIVE_MEMORY_TERMS = (
    "api key",
    "api_key",
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "bank card",
    "credit card",
    "身份证",
)


class MemoryService:
    def __init__(self, object_store: ObjectStore, job_service: Any | None = None) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.job_service = job_service

    def upsert_memory(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: UpsertMemoryRequest,
        *,
        emit_sync_event: bool = True,
    ) -> dict[str, Any]:
        scope = self._normalize_scope(request.type, request.scope)
        memory_id = request.memory_id or new_id("mem")
        existing = self._read_memory_or_none(workspace_id, identity.user_id, scope, memory_id)
        if existing and self._blocks_model_upsert(existing):
            raise AgentSystemError(
                "memory_previously_disabled",
                "This memory was previously disabled and must be re-enabled by user action.",
                status_code=409,
                retryable=False,
                details={
                    "memory_id": existing.get("memory_id"),
                    "scope": existing.get("scope"),
                    "type": existing.get("type"),
                    "field": existing.get("field"),
                    "reason": "memory_id_blocked",
                },
            )
        now = utc_now_iso()
        content = str(redact_runtime_value(request.content))
        summary = str(redact_runtime_value(request.summary))
        value = str(redact_runtime_value(request.value)) if request.value is not None else None
        deny_match = self._disabled_pattern_match(
            workspace_id=workspace_id,
            user_id=identity.user_id,
            scope=scope,
            memory_type=request.type,
            field=request.field,
            summary=summary,
            content=content,
            value=value,
        )
        if deny_match:
            raise AgentSystemError(
                "memory_previously_disabled",
                "This memory matches a user-disabled memory pattern.",
                status_code=409,
                retryable=False,
                details={
                    "memory_id": deny_match.get("memory_id"),
                    "scope": deny_match.get("scope"),
                    "type": deny_match.get("type"),
                    "field": deny_match.get("field"),
                    "reason": deny_match.get("reason"),
                },
            )
        sensitive = self._is_sensitive(content) or self._is_sensitive(summary)
        if sensitive and request.type in PROFILE_AND_PREFERENCE_TYPES:
            raise AgentSystemError(
                "sensitive_memory_rejected",
                "Sensitive content cannot be automatically stored as user memory.",
                status_code=400,
                retryable=False,
            )
        workspace_value = workspace_id if scope == "workspace" else None
        content_object_key = self._memory_object_key(
            workspace_id,
            identity.user_id,
            scope,
            memory_id,
        )
        record = {
            "schema_version": 1,
            "memory_id": memory_id,
            "workspace_id": workspace_value,
            "user_id": identity.user_id,
            "scope": scope,
            "type": request.type,
            "field": request.field,
            "value": value,
            "summary": summary,
            "content": content,
            "content_object_key": content_object_key,
            "visibility": "user_visible",
            "status": "active",
            "sensitive": sensitive,
            "source": request.source.model_dump(),
            "confidence": request.confidence,
            "enabled_for_model_context": request.enabled_for_model_context,
            "frontend_visible": True,
            "requires_approval": request.type not in PROFILE_AND_PREFERENCE_TYPES,
            "created_at": existing.get("created_at") if existing else now,
            "updated_at": now,
            "deleted_at": None,
            "revision": int(existing.get("revision") or 0) + 1 if existing else 1,
        }
        self.json_store.write_json(content_object_key, record)
        self._upsert_index_record(workspace_id, identity.user_id, scope, record)
        if emit_sync_event:
            self._append_memory_sync_event(
                workspace_id,
                identity.user_id,
                record,
                event_type="memory_upserted",
                reason="canonical_memory_upsert",
            )
        return record

    def propose_memory(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: UpsertMemoryRequest,
    ) -> dict[str, Any]:
        record = self.upsert_memory(workspace_id, identity, request, emit_sync_event=False)
        record["status"] = "pending_approval"
        record["enabled_for_model_context"] = False
        record["requires_approval"] = True
        record["updated_at"] = utc_now_iso()
        record["revision"] = int(record.get("revision") or 0) + 1
        self.json_store.write_json(record["content_object_key"], record)
        self._upsert_index_record(workspace_id, identity.user_id, record["scope"], record)
        self._append_memory_sync_event(
            workspace_id,
            identity.user_id,
            record,
            event_type="memory_candidate_created",
            reason="approval_required",
        )
        return record

    def list_memories(
        self,
        workspace_id: str,
        user_id: str,
        *,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        records = [
            *self._index_records(self._global_index(user_id)),
            *self._index_records(self._workspace_index(workspace_id)),
        ]
        records = [record for record in records if record.get("user_id") == user_id]
        if not include_deleted:
            records = [record for record in records if record.get("status") != "deleted"]
        return sorted(records, key=lambda item: item.get("updated_at") or "", reverse=True)

    def search(
        self,
        workspace_id: str,
        user_id: str,
        *,
        query: str,
        memory_types: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        allowed_types = set(memory_types or [])
        query_terms = [term for term in query.lower().split() if term]
        hits: list[dict[str, Any]] = []
        for summary in self.list_memories(workspace_id, user_id):
            if summary.get("status") != "active":
                continue
            if allowed_types and summary.get("type") not in allowed_types:
                continue
            haystack = " ".join(
                [
                    str(summary.get("summary") or ""),
                    str(summary.get("field") or ""),
                    str(summary.get("type") or ""),
                ]
            ).lower()
            if query_terms and not any(term in haystack for term in query_terms):
                continue
            score = 1.0 if query_terms else float(summary.get("confidence") or 0.5)
            hits.append(
                {
                    "memory_id": summary["memory_id"],
                    "type": summary["type"],
                    "scope": summary["scope"],
                    "summary": summary["summary"],
                    "score": score,
                    "sensitive": bool(summary.get("sensitive")),
                    "enabled_for_model_context": bool(
                        summary.get("enabled_for_model_context")
                    ),
                }
            )
        return hits[: max(1, min(limit, 20))]

    def get_memory(self, workspace_id: str, user_id: str, memory_id: str) -> dict[str, Any]:
        for summary in self.list_memories(workspace_id, user_id, include_deleted=True):
            if summary["memory_id"] == memory_id:
                return self.json_store.read_json(summary["content_object_key"])
        raise AgentSystemError("memory_not_found", "Memory was not found.", status_code=404)

    def patch_memory(
        self,
        workspace_id: str,
        user_id: str,
        memory_id: str,
        request: PatchMemoryRequest,
    ) -> dict[str, Any]:
        record = self.get_memory(workspace_id, user_id, memory_id)
        changed_fields = set(getattr(request, "model_fields_set", set()))
        old_scope = str(record["scope"])
        if request.summary is not None:
            record["summary"] = str(redact_runtime_value(request.summary))
        if request.content is not None:
            record["content"] = str(redact_runtime_value(request.content))
        if "field" in changed_fields:
            record["field"] = request.field
        if "value" in changed_fields:
            record["value"] = (
                str(redact_runtime_value(request.value)) if request.value is not None else None
            )
        if request.confidence is not None:
            record["confidence"] = request.confidence
        if "scope" in changed_fields:
            next_scope = self._normalize_scope(str(record["type"]), request.scope)
            record["scope"] = next_scope
            record["workspace_id"] = workspace_id if next_scope == "workspace" else None
            record["content_object_key"] = self._memory_object_key(
                workspace_id,
                user_id,
                next_scope,
                memory_id,
            )
        if request.enabled_for_model_context is not None:
            record["enabled_for_model_context"] = request.enabled_for_model_context
        if request.status is not None:
            record["status"] = request.status
            if request.status == "disabled":
                record["enabled_for_model_context"] = False
            elif (
                request.status == "active"
                and request.enabled_for_model_context is None
                and record.get("sensitive") is not True
            ):
                record["enabled_for_model_context"] = True
        if record.get("status") == "active" and record.get("enabled_for_model_context") is True:
            self._remove_disabled_pattern_for_memory(
                workspace_id,
                user_id,
                record["scope"],
                memory_id,
            )
            if old_scope != record["scope"]:
                self._remove_disabled_pattern_for_memory(
                    workspace_id,
                    user_id,
                    old_scope,
                    memory_id,
                )
        elif (
            request.status == "disabled"
            or request.enabled_for_model_context is False
            or record.get("status") == "disabled"
        ):
            self._record_disabled_pattern(
                workspace_id,
                user_id,
                record,
                reason="memory_disabled",
            )
            if old_scope != record["scope"]:
                self._remove_disabled_pattern_for_memory(
                    workspace_id,
                    user_id,
                    old_scope,
                    memory_id,
                )
        record["updated_at"] = utc_now_iso()
        record["revision"] = int(record.get("revision") or 0) + 1
        self.json_store.write_json(record["content_object_key"], record)
        if old_scope != record["scope"]:
            self._remove_index_record(workspace_id, user_id, old_scope, memory_id)
        self._upsert_index_record(workspace_id, user_id, record["scope"], record)
        self._append_memory_sync_event(
            workspace_id,
            user_id,
            record,
            event_type="memory_patched",
            reason="user_patch",
        )
        return record

    def approve_memory(self, workspace_id: str, user_id: str, memory_id: str) -> dict[str, Any]:
        record = self._get_pending_candidate(workspace_id, user_id, memory_id)
        record["status"] = "active"
        record["enabled_for_model_context"] = record.get("sensitive") is not True
        record["requires_approval"] = False
        return self._persist_record(
            workspace_id,
            user_id,
            record,
            event_type="memory_approved",
            reason="user_approved_candidate",
        )

    def reject_memory(self, workspace_id: str, user_id: str, memory_id: str) -> dict[str, Any]:
        record = self._get_pending_candidate(workspace_id, user_id, memory_id)
        record["status"] = "rejected"
        record["enabled_for_model_context"] = False
        record["requires_approval"] = False
        self._record_disabled_pattern(
            workspace_id,
            user_id,
            record,
            reason="memory_rejected",
        )
        return self._persist_record(
            workspace_id,
            user_id,
            record,
            event_type="memory_rejected",
            reason="user_rejected_candidate",
        )

    def delete_memory(self, workspace_id: str, user_id: str, memory_id: str) -> dict[str, Any]:
        record = self.get_memory(workspace_id, user_id, memory_id)
        record["status"] = "deleted"
        record["enabled_for_model_context"] = False
        record["deleted_at"] = utc_now_iso()
        record["updated_at"] = record["deleted_at"]
        record["revision"] = int(record.get("revision") or 0) + 1
        self._record_disabled_pattern(
            workspace_id,
            user_id,
            record,
            reason="memory_deleted",
        )
        self.json_store.write_json(record["content_object_key"], record)
        self._upsert_index_record(workspace_id, user_id, record["scope"], record)
        self._append_memory_sync_event(
            workspace_id,
            user_id,
            record,
            event_type="memory_deleted",
            reason="user_deleted_memory",
        )
        return record

    def _get_pending_candidate(
        self,
        workspace_id: str,
        user_id: str,
        memory_id: str,
    ) -> dict[str, Any]:
        record = self.get_memory(workspace_id, user_id, memory_id)
        if record.get("type") not in CANDIDATE_MEMORY_TYPES:
            raise AgentSystemError(
                "memory_candidate_type_not_reviewable",
                "Only project memory candidates can be approved or rejected.",
                status_code=400,
                retryable=False,
            )
        if record.get("status") != "pending_approval":
            raise AgentSystemError(
                "memory_candidate_not_pending",
                "Memory candidate is not pending approval.",
                status_code=409,
                retryable=False,
            )
        return record

    def _persist_record(
        self,
        workspace_id: str,
        user_id: str,
        record: dict[str, Any],
        *,
        event_type: str = "memory_persisted",
        reason: str = "memory_persisted",
    ) -> dict[str, Any]:
        record["updated_at"] = utc_now_iso()
        record["revision"] = int(record.get("revision") or 0) + 1
        self.json_store.write_json(record["content_object_key"], record)
        self._upsert_index_record(workspace_id, user_id, record["scope"], record)
        self._append_memory_sync_event(
            workspace_id,
            user_id,
            record,
            event_type=event_type,
            reason=reason,
        )
        return record

    def build_memory_snapshot(
        self,
        workspace_id: str,
        user_id: str,
        thread_id: str,
        *,
        query: str | None = None,
    ) -> dict[str, Any]:
        _ = query
        included = [
            record
            for record in self.list_memories(workspace_id, user_id)
            if record.get("status") == "active"
            and record.get("enabled_for_model_context") is True
            and record.get("sensitive") is not True
        ]
        profile: dict[str, Any] = {}
        preferences: list[str] = []
        facts: list[str] = []
        rules: list[str] = []
        for memory in included:
            if memory["type"] == "user_profile" and memory.get("field"):
                profile[str(memory["field"])] = memory.get("value") or memory["summary"]
            elif memory["type"] == "user_preference":
                preferences.append(memory["summary"])
            elif memory["type"] == "project_fact":
                facts.append(memory["summary"])
            elif memory["type"] == "project_rule":
                rules.append(memory["summary"])
        snapshot_id = new_id("memsnap")
        snapshot = {
            "schema_version": 1,
            "memory_snapshot_id": snapshot_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "thread_id": thread_id,
            "included_memory_ids": [memory["memory_id"] for memory in included],
            "profile": profile,
            "preferences": preferences,
            "project_facts": facts,
            "project_rules": rules,
            "created_at": utc_now_iso(),
        }
        self.json_store.write_json(memory_snapshot_key(workspace_id, snapshot_id), snapshot)
        return snapshot

    def get_memory_snapshot(self, workspace_id: str, snapshot_id: str) -> dict[str, Any]:
        key = memory_snapshot_key(workspace_id, snapshot_id)
        if not self.object_store.exists(key):
            raise AgentSystemError(
                "memory_snapshot_not_found",
                "Memory snapshot was not found.",
                status_code=404,
            )
        return self.json_store.read_json(key)

    def get_sync_state(self, workspace_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            memory_sync_state_key(workspace_id),
            self._empty_memory_sync_state(workspace_id),
        )

    def _normalize_scope(self, memory_type: str, requested_scope: str | None) -> str:
        if memory_type in GLOBAL_MEMORY_TYPES:
            return "global"
        if memory_type in WORKSPACE_MEMORY_TYPES:
            return "workspace"
        return requested_scope or "global"

    def _read_memory_or_none(
        self,
        workspace_id: str,
        user_id: str,
        scope: str,
        memory_id: str,
    ) -> dict[str, Any] | None:
        key = self._memory_object_key(workspace_id, user_id, scope, memory_id)
        if not self.object_store.exists(key):
            return None
        return self.json_store.read_json(key)

    def _memory_object_key(
        self,
        workspace_id: str,
        user_id: str,
        scope: str,
        memory_id: str,
    ) -> str:
        if scope == "global":
            return user_memory_object_key(user_id, memory_id)
        return workspace_memory_object_key(workspace_id, memory_id)

    def _disabled_patterns_key(self, workspace_id: str, user_id: str, scope: str) -> str:
        if scope == "global":
            return user_disabled_memory_patterns_key(user_id)
        return workspace_disabled_memory_patterns_key(workspace_id)

    def _disabled_patterns(self, workspace_id: str, user_id: str, scope: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            self._disabled_patterns_key(workspace_id, user_id, scope),
            {
                "schema_version": 1,
                "workspace_id": workspace_id if scope == "workspace" else None,
                "user_id": user_id if scope == "global" else None,
                "scope": scope,
                "patterns": [],
                "updated_at": None,
                "revision": 0,
            },
        )

    def _record_disabled_pattern(
        self,
        workspace_id: str,
        user_id: str,
        record: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        scope = str(record["scope"])
        index = self._disabled_patterns(workspace_id, user_id, scope)
        pattern = self._disabled_pattern_from_record(record, reason=reason)
        index["patterns"] = [
            item
            for item in index.get("patterns", [])
            if item.get("memory_id") != record["memory_id"]
        ]
        index["patterns"].append(pattern)
        index["updated_at"] = utc_now_iso()
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(
            self._disabled_patterns_key(workspace_id, user_id, scope),
            index,
        )

    def _remove_disabled_pattern_for_memory(
        self,
        workspace_id: str,
        user_id: str,
        scope: str,
        memory_id: str,
    ) -> None:
        index = self._disabled_patterns(workspace_id, user_id, scope)
        next_patterns = [
            item for item in index.get("patterns", []) if item.get("memory_id") != memory_id
        ]
        if len(next_patterns) == len(index.get("patterns", [])):
            return
        index["patterns"] = next_patterns
        index["updated_at"] = utc_now_iso()
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(
            self._disabled_patterns_key(workspace_id, user_id, scope),
            index,
        )

    def _append_memory_sync_event(
        self,
        workspace_id: str,
        user_id: str,
        record: dict[str, Any],
        *,
        event_type: str,
        reason: str,
    ) -> None:
        targets = self._memory_sync_targets(record, reason=reason)
        now = utc_now_iso()
        event_store = JsonlSegmentStore(
            self.object_store,
            memory_sync_events_prefix(workspace_id),
        )
        event_index_key = memory_sync_event_index_key(workspace_id)
        index = self.json_store.read_json_or_default(
            event_index_key,
            {
                "schema_version": 1,
                "stream_id": f"{workspace_id}:memory_sync",
                "last_event_seq": 0,
            },
        )
        event_seq = int(index.get("last_event_seq") or 0) + 1
        event = {
            "schema_version": 1,
            "event_id": f"evt_memsync_{event_seq:012d}",
            "event_seq": event_seq,
            "type": event_type,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "memory_id": record["memory_id"],
            "scope": record["scope"],
            "memory_type": record["type"],
            "field": record.get("field"),
            "status": record.get("status") or "active",
            "enabled_for_model_context": bool(record.get("enabled_for_model_context")),
            "sensitive": bool(record.get("sensitive")),
            "content_object_key": record["content_object_key"],
            "summary_hash": _stable_text_hash(record.get("summary")),
            "content_hash": _stable_text_hash(record.get("content")),
            "targets": targets,
            "reason": reason,
            "created_at": now,
        }
        event_store.append(event)
        event_store.rebuild_event_index(
            event_index_key,
            stream_id=f"{workspace_id}:memory_sync",
        )
        state = self._update_memory_sync_state(workspace_id, event)
        enqueue_result = self._enqueue_memory_sync_job(
            workspace_id,
            user_id,
            event,
            state,
        )
        if enqueue_result:
            state = self.get_sync_state(workspace_id)
            state["last_enqueue"] = enqueue_result
            state["updated_at"] = utc_now_iso()
            state["revision"] = int(state.get("revision") or 0) + 1
            self.json_store.write_json(memory_sync_state_key(workspace_id), state)

    def _update_memory_sync_state(self, workspace_id: str, event: dict[str, Any]) -> dict[str, Any]:
        state_key = memory_sync_state_key(workspace_id)
        state = self.json_store.read_json_or_default(
            state_key,
            self._empty_memory_sync_state(workspace_id),
        )
        pending = [
            item
            for item in state.get("pending_targets", [])
            if item.get("memory_id") != event["memory_id"]
            or item.get("target") not in event["targets"]
        ]
        for target, target_action in event["targets"].items():
            if target_action.get("action") == "skip":
                continue
            pending.append(
                {
                    "target": target,
                    "action": target_action["action"],
                    "workspace_id": event["workspace_id"],
                    "memory_id": event["memory_id"],
                    "memory_type": event["memory_type"],
                    "scope": event["scope"],
                    "user_id": event["user_id"],
                    "content_object_key": event["content_object_key"],
                    "event_id": event["event_id"],
                    "event_seq": event["event_seq"],
                    "status": "pending",
                    "reason": target_action.get("reason") or event["reason"],
                    "updated_at": event["created_at"],
                }
            )
        state["pending_targets"] = sorted(
            pending,
            key=lambda item: (
                str(item.get("target") or ""),
                str(item.get("memory_id") or ""),
            ),
        )
        state["last_event_id"] = event["event_id"]
        state["last_event_seq"] = event["event_seq"]
        state["updated_at"] = event["created_at"]
        state["revision"] = int(state.get("revision") or 0) + 1
        self.json_store.write_json(state_key, state)
        return state

    def _enqueue_memory_sync_job(
        self,
        workspace_id: str,
        user_id: str,
        event: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.job_service is None:
            return None
        pending_targets = list(state.get("pending_targets") or [])
        if not pending_targets:
            return None

        request = CreateJobRequest(
            job_type="memory_sync_job",
            priority="normal",
            title="Sync long-term memory indexes",
            target_scope={
                "scope_type": "memory_sync",
                "sync_stream": f"{workspace_id}:memory_sync",
            },
            input={"limit": 50},
            idempotency_key=f"memory-sync:{workspace_id}:{event['event_id']}",
        )
        identity = RuntimeIdentity(
            user_id=user_id,
            role="owner",
            workspace_id=workspace_id,
            workspace_role="owner",
        )
        try:
            job = self.job_service.create_job(workspace_id, identity, request)
        except AgentSystemError as exc:
            if exc.error_type == "job_target_scope_conflict":
                return {
                    "status": "already_queued",
                    "event_id": event["event_id"],
                    "pending_target_count": len(pending_targets),
                    "existing_job_id": exc.details.get("existing_job_id")
                    if isinstance(exc.details, dict)
                    else None,
                    "updated_at": utc_now_iso(),
                }
            raise
        return {
            "status": "queued",
            "event_id": event["event_id"],
            "job_id": job["job_id"],
            "pending_target_count": len(pending_targets),
            "updated_at": utc_now_iso(),
        }

    def _memory_sync_targets(
        self,
        record: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, dict[str, Any]]:
        if record.get("status") == "pending_approval":
            return {
                "milvus": {"action": "skip", "reason": "pending_user_approval"},
                "neo4j": {"action": "skip", "reason": "pending_user_approval"},
            }
        active_for_index = (
            record.get("status") == "active"
            and record.get("enabled_for_model_context") is True
            and record.get("sensitive") is not True
        )
        if not active_for_index:
            action = "delete"
            return {
                "milvus": {"action": action, "reason": reason},
                "neo4j": {"action": action, "reason": reason},
            }
        memory_type = str(record.get("type") or "")
        targets = {
            "milvus": {
                "action": "upsert"
                if memory_type in MILVUS_INDEXABLE_MEMORY_TYPES
                else "skip",
                "reason": "semantic_memory_index",
            },
            "neo4j": {
                "action": "upsert"
                if self._should_index_memory_to_neo4j(record)
                else "skip",
                "reason": "relationship_memory_index",
            },
        }
        return targets

    @staticmethod
    def _empty_memory_sync_state(workspace_id: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "pending_targets": [],
            "last_event_id": None,
            "last_event_seq": 0,
            "last_enqueue": None,
            "updated_at": None,
            "revision": 0,
        }

    @staticmethod
    def _should_index_memory_to_neo4j(record: dict[str, Any]) -> bool:
        memory_type = str(record.get("type") or "")
        if memory_type in NEO4J_INDEXABLE_MEMORY_TYPES:
            return True
        if memory_type == "user_preference":
            field = str(record.get("field") or "").casefold()
            return field in NEO4J_PREFERENCE_FIELDS
        return False

    def _disabled_pattern_match(
        self,
        *,
        workspace_id: str,
        user_id: str,
        scope: str,
        memory_type: str,
        field: str | None,
        summary: str,
        content: str,
        value: str | None,
    ) -> dict[str, Any] | None:
        candidate = {
            "type": memory_type,
            "scope": scope,
            "field": field,
            "summary_hash": _stable_text_hash(summary),
            "content_hash": _stable_text_hash(content),
            "value_hash": _stable_text_hash(value),
        }
        for pattern in self._disabled_patterns(workspace_id, user_id, scope).get("patterns", []):
            if pattern.get("type") != candidate["type"] or pattern.get("scope") != scope:
                continue
            pattern_user_id = pattern.get("user_id")
            if pattern_user_id and pattern_user_id != user_id:
                continue
            if pattern.get("field") and pattern.get("field") == candidate["field"]:
                return pattern
            for key in ("summary_hash", "content_hash", "value_hash"):
                if pattern.get(key) and pattern.get(key) == candidate[key]:
                    return pattern
        return None

    @staticmethod
    def _disabled_pattern_from_record(record: dict[str, Any], *, reason: str) -> dict[str, Any]:
        return {
            "pattern_id": f"disabled_{record['memory_id']}",
            "memory_id": record["memory_id"],
            "workspace_id": record.get("workspace_id"),
            "user_id": record["user_id"],
            "scope": record["scope"],
            "type": record["type"],
            "field": record.get("field"),
            "summary_hash": _stable_text_hash(record.get("summary")),
            "content_hash": _stable_text_hash(record.get("content")),
            "value_hash": _stable_text_hash(record.get("value")),
            "source_status": record.get("status"),
            "reason": reason,
            "created_at": utc_now_iso(),
        }

    @staticmethod
    def _blocks_model_upsert(record: dict[str, Any]) -> bool:
        status = str(record.get("status") or "active")
        return status in {"disabled", "deleted", "rejected"} or (
            record.get("enabled_for_model_context") is False
        )

    def _global_index(self, user_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            user_memory_index_key(user_id),
            {
                "schema_version": 1,
                "user_id": user_id,
                "memories": [],
                "updated_at": None,
                "revision": 0,
            },
        )

    def _workspace_index(self, workspace_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            workspace_memory_index_key(workspace_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "memories": [],
                "updated_at": None,
                "revision": 0,
            },
        )

    def _upsert_index_record(
        self,
        workspace_id: str,
        user_id: str,
        scope: str,
        record: dict[str, Any],
    ) -> None:
        if scope == "global":
            key = user_memory_index_key(user_id)
            index = self._global_index(user_id)
        else:
            key = workspace_memory_index_key(workspace_id)
            index = self._workspace_index(workspace_id)
        summary = self.public_summary(record)
        index["memories"] = [
            item for item in index.get("memories", []) if item["memory_id"] != record["memory_id"]
        ]
        index["memories"].append(summary)
        index["memories"] = sorted(
            index["memories"],
            key=lambda item: item.get("updated_at") or "",
            reverse=True,
        )
        index["updated_at"] = utc_now_iso()
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(key, index)

    def _remove_index_record(
        self,
        workspace_id: str,
        user_id: str,
        scope: str,
        memory_id: str,
    ) -> None:
        if scope == "global":
            key = user_memory_index_key(user_id)
            index = self._global_index(user_id)
        else:
            key = workspace_memory_index_key(workspace_id)
            index = self._workspace_index(workspace_id)
        next_memories = [
            item for item in index.get("memories", []) if item["memory_id"] != memory_id
        ]
        if len(next_memories) == len(index.get("memories", [])):
            return
        index["memories"] = next_memories
        index["updated_at"] = utc_now_iso()
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(key, index)

    @staticmethod
    def _index_records(index: dict[str, Any]) -> list[dict[str, Any]]:
        return list(index.get("memories", []))

    @staticmethod
    def public_summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "memory_id": record["memory_id"],
            "workspace_id": record.get("workspace_id"),
            "user_id": record["user_id"],
            "scope": record["scope"],
            "type": record["type"],
            "field": record.get("field"),
            "summary": record["summary"],
            "content_object_key": record["content_object_key"],
            "sensitive": bool(record.get("sensitive")),
            "status": record.get("status") or "active",
            "enabled_for_model_context": bool(record.get("enabled_for_model_context")),
            "frontend_visible": bool(record.get("frontend_visible", True)),
            "requires_approval": bool(record.get("requires_approval")),
            "confidence": float(record.get("confidence") or 0),
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }

    _public_summary = public_summary

    @staticmethod
    def _is_sensitive(value: str) -> bool:
        normalized = value.lower().replace("-", "_")
        return any(term in normalized for term in SENSITIVE_MEMORY_TERMS)


def _stable_text_hash(value: Any) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", str(value).strip().lower())
    if not normalized:
        return None
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
