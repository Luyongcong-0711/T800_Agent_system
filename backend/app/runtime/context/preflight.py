from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.memory.service import MemoryService
from app.runtime.context.engine import HermesStyleContextCompressor
from app.storage.object_store import JsonObjectStore, ObjectStore, RevisionConflictError
from app.storage.path_builder import (
    run_prefix,
    thread_compaction_key,
    thread_compaction_latest_key,
    thread_compaction_lock_key,
)


class RuntimeContextPreflight:
    def __init__(
        self,
        *,
        object_store: ObjectStore | None = None,
        memory_service: MemoryService | None = None,
        context_engine: HermesStyleContextCompressor | None = None,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store) if object_store is not None else None
        self.memory_service = memory_service
        self.context_engine = context_engine or HermesStyleContextCompressor()

    def invoke(self, state: dict[str, Any]) -> dict[str, Any]:
        messages = list(state["messages"])
        warnings = list(state.get("warnings") or [])
        latest_user = self._latest_user_text(messages)
        memory_snapshot = self._build_memory_snapshot(state, latest_user, warnings)
        messages = self._inject_memory_snapshot(messages, memory_snapshot)
        skill_context = self._build_skill_context(state, warnings)
        messages = self._inject_skill_context(messages, skill_context)
        prompt_tokens = self.count_messages(messages)
        context_window_tokens = int(state.get("context_window_tokens") or 200000)
        max_output_tokens = int(state.get("max_output_tokens") or 8192)
        usable_input_budget = max(1, context_window_tokens - max_output_tokens)
        compaction = state.get("compaction")

        force_compress = self.context_engine.should_force_compress(
            prompt_tokens,
            context_window_tokens,
            max_output_tokens,
        )
        threshold_compress = self.context_engine.should_compress(
            prompt_tokens,
            usable_input_budget,
        )
        hygiene_compress = self.context_engine.should_session_hygiene_compress(
            prompt_tokens,
            context_window_tokens,
        )
        if force_compress or threshold_compress or hygiene_compress:
            if hygiene_compress:
                warnings.append("session_hygiene_compaction_triggered")
            if self._try_acquire_compaction_lock(state):
                try:
                    result = self.context_engine.compress(
                        workspace_id=state["workspace_id"],
                        thread_id=state["thread_id"],
                        run_id=state["run_id"],
                        messages=messages,
                        current_tokens=prompt_tokens,
                        context_window_tokens=context_window_tokens,
                        focus_topic=latest_user,
                        previous_summary=(
                            compaction.get("summary")
                            if isinstance(compaction, dict)
                            else None
                        ),
                    )
                    messages = result.messages
                    warnings.extend(result.warnings)
                    if result.compaction:
                        compaction = result.compaction
                        self._persist_compaction(state, compaction)
                    prompt_tokens = self.count_messages(messages)
                finally:
                    self._release_compaction_lock(state)
            else:
                warnings.append("context_compaction_skipped_lock_held")

        next_usage = {
            **dict(state.get("context_usage") or {}),
            "input_tokens": prompt_tokens,
            "usage_estimated": True,
        }
        if "output_tokens" in next_usage:
            next_usage["total_tokens"] = int(next_usage["output_tokens"]) + prompt_tokens

        return {
            **state,
            "messages": messages,
            "memory_snapshot": memory_snapshot,
            "skill_context": skill_context,
            "compaction": compaction,
            "warnings": warnings,
            "context_usage": next_usage,
            "context_window_tokens": context_window_tokens,
            "max_output_tokens": max_output_tokens,
        }

    def force_compress_after_overflow(self, state: dict[str, Any]) -> dict[str, Any]:
        messages = list(state["messages"])
        warnings = [
            *list(state.get("warnings") or []),
            "context_overflow_retry_requested",
        ]
        compaction = state.get("compaction")
        prompt_tokens = self.count_messages(messages)
        context_window_tokens = int(state.get("context_window_tokens") or 200000)
        result = self.context_engine.compress(
            workspace_id=state["workspace_id"],
            thread_id=state["thread_id"],
            run_id=state["run_id"],
            messages=messages,
            current_tokens=prompt_tokens,
            context_window_tokens=context_window_tokens,
            focus_topic=self._latest_user_text(messages),
            previous_summary=(
                compaction.get("summary") if isinstance(compaction, dict) else None
            ),
        )
        warnings.extend(result.warnings)
        if not result.compaction:
            return {
                **state,
                "warnings": warnings,
                "context_overflow_retried": True,
            }
        if self._try_acquire_compaction_lock(state):
            try:
                self._persist_compaction(state, result.compaction)
            finally:
                self._release_compaction_lock(state)
        else:
            warnings.append("context_compaction_skipped_lock_held")
        prompt_tokens_after = self.count_messages(result.messages)
        return {
            **state,
            "messages": result.messages,
            "compaction": result.compaction,
            "warnings": [*warnings, "context_overflow_retry_compacted"],
            "context_usage": {
                **dict(state.get("context_usage") or {}),
                "input_tokens": prompt_tokens_after,
                "usage_estimated": True,
            },
            "context_overflow_retried": True,
        }

    def count_messages(self, messages: list[BaseMessage]) -> int:
        return sum(max(1, len(str(message.content)) // 4) for message in messages)

    def _build_memory_snapshot(
        self,
        state: dict[str, Any],
        latest_user: str | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        if self.memory_service is None:
            return {
                "memory_snapshot_id": None,
                "workspace_id": state["workspace_id"],
                "user_id": state["user_id"],
                "thread_id": state["thread_id"],
                "included_memory_ids": [],
                "profile": {},
                "preferences": [],
                "project_facts": [],
                "project_rules": [],
            }
        try:
            return self.memory_service.build_memory_snapshot(
                state["workspace_id"],
                state["user_id"],
                state["thread_id"],
                query=latest_user,
            )
        except Exception as exc:  # noqa: BLE001 - context preflight degrades to empty memory.
            warnings.append(f"memory_snapshot_failed:{exc.__class__.__name__}")
            return {
                "memory_snapshot_id": None,
                "workspace_id": state["workspace_id"],
                "user_id": state["user_id"],
                "thread_id": state["thread_id"],
                "included_memory_ids": [],
                "profile": {},
                "preferences": [],
                "project_facts": [],
                "project_rules": [],
            }

    @staticmethod
    def _inject_memory_snapshot(
        messages: list[BaseMessage],
        snapshot: dict[str, Any],
    ) -> list[BaseMessage]:
        if not snapshot.get("included_memory_ids"):
            return messages
        content = {
            "type": "long_term_memory_snapshot",
            "memory_snapshot_id": snapshot.get("memory_snapshot_id"),
            "profile": snapshot.get("profile") or {},
            "preferences": snapshot.get("preferences") or [],
            "project_facts": snapshot.get("project_facts") or [],
            "project_rules": snapshot.get("project_rules") or [],
            "included_memory_ids": snapshot.get("included_memory_ids") or [],
        }
        return [SystemMessage(content=str(content)), *messages]

    def _build_skill_context(
        self,
        state: dict[str, Any],
        warnings: list[str],
    ) -> dict[str, Any]:
        if self.json_store is None or self.object_store is None:
            return {"activated_skills": []}
        base_prefix = f"{run_prefix(state['workspace_id'], state['run_id'])}/skills"
        contexts: list[dict[str, Any]] = []
        try:
            keys = sorted(self.object_store.list_keys(base_prefix))
        except Exception as exc:  # noqa: BLE001 - skill context is additive.
            warnings.append(f"skill_context_scan_failed:{exc.__class__.__name__}")
            return {"activated_skills": []}
        for key in keys:
            if not key.endswith("/context_block.json"):
                continue
            try:
                block = self.json_store.read_json(key)
            except Exception as exc:  # noqa: BLE001 - skip malformed activated skill block.
                warnings.append(f"skill_context_read_failed:{exc.__class__.__name__}")
                continue
            contexts.append(
                {
                    "skill_id": block.get("skill_id"),
                    "version": block.get("version"),
                    "display_name": block.get("display_name"),
                    "summary": block.get("summary"),
                    "workflow_summary": block.get("workflow_summary") or [],
                    "knowledge_notes": block.get("knowledge_notes") or [],
                    "entrypoint_tools": block.get("entrypoint_tools") or [],
                    "context_block_object_key": key,
                }
            )
        return {"activated_skills": contexts}

    @staticmethod
    def _inject_skill_context(
        messages: list[BaseMessage],
        skill_context: dict[str, Any],
    ) -> list[BaseMessage]:
        activated = skill_context.get("activated_skills") or []
        if not activated:
            return messages
        content = {
            "type": "activated_skill_context",
            "activated_skills": activated,
            "usage_note": (
                "Apply these user-approved Skill workflows and knowledge notes during this run. "
                "To execute an activated entrypoint, call skill_entrypoint_call with the "
                "entrypoint_tool_name value. Only call tools that are present in the "
                "model-visible tool list."
            ),
        }
        return [SystemMessage(content=str(content)), *messages]

    def _persist_compaction(self, state: dict[str, Any], compaction: dict[str, Any]) -> None:
        if self.json_store is None:
            return
        object_key = thread_compaction_key(
            state["workspace_id"],
            state["thread_id"],
            compaction["compaction_id"],
        )
        self.json_store.write_json(
            object_key,
            compaction,
        )
        self._write_latest_compaction_pointer(state, compaction, object_key)

    def _write_latest_compaction_pointer(
        self,
        state: dict[str, Any],
        compaction: dict[str, Any],
        object_key: str,
    ) -> None:
        if self.json_store is None:
            return
        key = thread_compaction_latest_key(state["workspace_id"], state["thread_id"])
        current = self.json_store.read_json_or_default(key, {"revision": 0})
        latest = {
            "schema_version": 1,
            "workspace_id": state["workspace_id"],
            "thread_id": state["thread_id"],
            "run_id": state["run_id"],
            "compaction_id": compaction["compaction_id"],
            "compaction_object_key": object_key,
            "strategy": compaction.get("strategy"),
            "summary": compaction.get("summary"),
            "created_at": compaction.get("created_at"),
            "updated_at": compaction.get("created_at"),
            "revision": int(current.get("revision") or 0) + 1,
        }
        try:
            expected_revision = (
                int(current.get("revision") or 0)
                if self.object_store is not None and self.object_store.exists(key)
                else None
            )
            self.json_store.write_json(key, latest, expected_revision=expected_revision)
        except RevisionConflictError:
            self.json_store.write_json(key, latest)

    def _try_acquire_compaction_lock(
        self,
        state: dict[str, Any],
        ttl_seconds: int = 300,
    ) -> bool:
        if self.json_store is None:
            return True
        key = thread_compaction_lock_key(state["workspace_id"], state["thread_id"])
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        current = self.json_store.read_json_or_default(key, {"revision": 0})
        holder = current.get("holder_run_id")
        current_expires_at = _parse_iso_datetime(current.get("expires_at"))
        if holder and holder != state["run_id"] and current_expires_at and current_expires_at > now:
            return False
        next_lock = {
            "schema_version": 1,
            "workspace_id": state["workspace_id"],
            "thread_id": state["thread_id"],
            "holder_run_id": state["run_id"],
            "acquired_at": now.isoformat(),
            "expires_at": expires_at,
            "revision": int(current.get("revision") or 0) + 1,
        }
        try:
            expected_revision = (
                int(current.get("revision") or 0)
                if self.object_store is not None and self.object_store.exists(key)
                else None
            )
            self.json_store.write_json(key, next_lock, expected_revision=expected_revision)
        except RevisionConflictError:
            return False
        return True

    def _release_compaction_lock(self, state: dict[str, Any]) -> None:
        if self.json_store is None:
            return
        key = thread_compaction_lock_key(state["workspace_id"], state["thread_id"])
        current = self.json_store.read_json_or_default(key, {"revision": 0})
        if current.get("holder_run_id") != state["run_id"]:
            return
        current.update(
            {
                "holder_run_id": None,
                "released_at": datetime.now(timezone.utc).isoformat(),
                "revision": int(current.get("revision") or 0) + 1,
            }
        )
        self.json_store.write_json(key, current)

    @staticmethod
    def _latest_user_text(messages: list[BaseMessage]) -> str | None:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return str(message.content)
        return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
