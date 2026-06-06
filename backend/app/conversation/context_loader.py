from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.runtime.context.engine import SUMMARY_PREFIX
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import thread_compactions_prefix, thread_messages_prefix


class ConversationContextLoader:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)

    def load_for_run(self, workspace_id: str, thread_id: str) -> dict[str, Any]:
        records = JsonlSegmentStore(
            self.object_store,
            thread_messages_prefix(workspace_id, thread_id),
        ).read_all()
        latest_compaction = self.latest_compaction(workspace_id, thread_id)
        messages = self._messages_with_compaction(records, latest_compaction)
        return {
            "messages": messages,
            "compaction": latest_compaction,
            "loaded_message_count": len(messages),
            "raw_message_count": len(records),
        }

    def latest_compaction(self, workspace_id: str, thread_id: str) -> dict[str, Any] | None:
        prefix = thread_compactions_prefix(workspace_id, thread_id)
        candidates: list[dict[str, Any]] = []
        for key in self.object_store.list_keys(prefix):
            if (
                not key.endswith(".json")
                or key.endswith("/latest.json")
                or key.endswith("/lock.json")
            ):
                continue
            try:
                candidates.append(self.json_store.read_json(key))
            except Exception:  # noqa: BLE001 - malformed historical compaction is ignored.
                continue
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item.get("created_at") or "")[-1]

    def _messages_with_compaction(
        self,
        records: list[dict[str, Any]],
        compaction: dict[str, Any] | None,
    ) -> list[BaseMessage]:
        if not compaction:
            return [self._to_message(record) for record in records]
        head_ids = set(map(str, compaction.get("head_message_ids") or []))
        tail_ids = set(map(str, compaction.get("tail_message_ids") or []))
        source_ids = set(map(str, compaction.get("source_message_ids") or []))
        if not (head_ids or tail_ids or source_ids):
            return [self._to_message(record) for record in records]
        head: list[BaseMessage] = []
        tail_and_new: list[BaseMessage] = []
        for record in records:
            message_id = str(record.get("message_id") or "")
            if message_id in head_ids:
                head.append(self._to_message(record))
            elif message_id in tail_ids or message_id not in source_ids:
                tail_and_new.append(self._to_message(record))
        summary = str(compaction.get("summary") or "").strip()
        content = (
            summary
            if summary.startswith(SUMMARY_PREFIX)
            else f"{SUMMARY_PREFIX}\n{summary}"
        )
        summary_message = SystemMessage(
            content=content,
            id=f"{compaction.get('compaction_id') or 'latest'}_summary",
        )
        return [*head, summary_message, *tail_and_new]

    @staticmethod
    def _to_message(record: dict[str, Any]) -> BaseMessage:
        role = str(record.get("role") or "user")
        content = str(record.get("content") or "")
        message_id = str(record.get("message_id") or "")
        if role == "assistant":
            tool_calls = record.get("tool_calls")
            return AIMessage(
                content=content,
                id=message_id,
                tool_calls=tool_calls if isinstance(tool_calls, list) else [],
            )
        if role == "system":
            return SystemMessage(content=content, id=message_id)
        if role == "tool":
            return ToolMessage(
                content=content,
                tool_call_id=str(record.get("tool_call_id") or message_id or "tool_call"),
                id=message_id,
            )
        return HumanMessage(content=content, id=message_id)
