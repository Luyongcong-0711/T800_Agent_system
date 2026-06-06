from __future__ import annotations

from typing import Any

from app.core.errors import AgentSystemError
from app.runtime.tools import redact_runtime_value
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import (
    thread_manifest_key,
    thread_messages_prefix,
    threads_index_key,
)


class SessionHistoryService:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)

    def search(
        self,
        *,
        workspace_id: str,
        user_id: str,
        query: str,
        thread_status: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        allowed_status = set(thread_status or ["active", "archived"])
        terms = _query_terms(query)
        hits: list[dict[str, Any]] = []
        for thread in self._list_user_threads(workspace_id, user_id, allowed_status):
            messages = self._read_messages(workspace_id, str(thread["thread_id"]))
            best_message = self._best_message_match(messages, terms)
            haystack = " ".join(
                [
                    str(thread.get("title") or ""),
                    str(thread.get("last_message_preview") or ""),
                    *(str(message.get("content") or "") for message in messages),
                ]
            ).lower()
            if terms and not any(term in haystack for term in terms):
                continue
            score = _score_text(haystack, terms)
            hits.append(
                {
                    "thread_id": thread["thread_id"],
                    "message_id": best_message.get("message_id") if best_message else None,
                    "title": thread.get("title") or "Untitled thread",
                    "thread_status": thread.get("status") or "active",
                    "summary": _session_summary(thread, best_message),
                    "score": score,
                    "created_at": thread.get("created_at"),
                    "updated_at": thread.get("updated_at"),
                    "warning": (
                        "session_search returns historical conversation snippets only; "
                        "it does not create long-term memory."
                    ),
                }
            )
        hits.sort(key=lambda item: (item["score"], item.get("updated_at") or ""), reverse=True)
        return hits[: max(1, min(limit, 20))]

    def get_message_window(
        self,
        *,
        workspace_id: str,
        user_id: str,
        thread_id: str,
        message_id: str,
        include_neighbor: bool = True,
        max_chars: int = 2000,
    ) -> dict[str, Any]:
        thread = self._thread_manifest(workspace_id, thread_id)
        if thread.get("user_id") != user_id:
            raise AgentSystemError(
                "thread_access_denied",
                "Thread does not belong to the current user.",
                status_code=403,
            )
        messages = self._read_messages(workspace_id, thread_id)
        index = next(
            (
                idx
                for idx, message in enumerate(messages)
                if message.get("message_id") == message_id
            ),
            None,
        )
        if index is None:
            raise AgentSystemError(
                "message_not_found",
                "Message was not found in the requested thread.",
                status_code=404,
            )
        window = messages[max(0, index - 1) : index + 2] if include_neighbor else [messages[index]]
        return {
            "thread_id": thread_id,
            "message_id": message_id,
            "title": thread.get("title") or "Untitled thread",
            "messages": [_public_message(message, max_chars=max_chars) for message in window],
            "warning": (
                "session_message_get returns bounded historical context; "
                "it does not inject the entire old thread."
            ),
        }

    def _list_user_threads(
        self,
        workspace_id: str,
        user_id: str,
        allowed_status: set[str],
    ) -> list[dict[str, Any]]:
        index = self.json_store.read_json_or_default(
            threads_index_key(workspace_id),
            {"threads": []},
        )
        threads: list[dict[str, Any]] = []
        for item in index.get("threads", []):
            thread_id = str(item.get("thread_id") or "")
            if not thread_id:
                continue
            try:
                manifest = self._thread_manifest(workspace_id, thread_id)
            except AgentSystemError:
                continue
            if manifest.get("user_id") != user_id:
                continue
            if str(manifest.get("status") or "active") not in allowed_status:
                continue
            threads.append(manifest)
        return threads

    def _thread_manifest(self, workspace_id: str, thread_id: str) -> dict[str, Any]:
        key = thread_manifest_key(workspace_id, thread_id)
        if not self.object_store.exists(key):
            raise AgentSystemError("thread_not_found", "Thread was not found.", status_code=404)
        return self.json_store.read_json(key)

    def _read_messages(self, workspace_id: str, thread_id: str) -> list[dict[str, Any]]:
        return JsonlSegmentStore(
            self.object_store,
            thread_messages_prefix(workspace_id, thread_id),
        ).read_all()

    @staticmethod
    def _best_message_match(
        messages: list[dict[str, Any]],
        terms: list[str],
    ) -> dict[str, Any] | None:
        if not messages:
            return None
        if not terms:
            return messages[-1]
        ranked = sorted(
            messages,
            key=lambda message: _score_text(str(message.get("content") or "").lower(), terms),
            reverse=True,
        )
        return ranked[0]


def _query_terms(query: str) -> list[str]:
    return [term for term in str(query).lower().split() if term]


def _score_text(text: str, terms: list[str]) -> float:
    if not terms:
        return 0.5
    matches = sum(1 for term in terms if term in text)
    return matches / max(1, len(terms))


def _session_summary(thread: dict[str, Any], message: dict[str, Any] | None) -> str:
    title = str(thread.get("title") or "Untitled thread")
    snippet = str((message or {}).get("content") or thread.get("last_message_preview") or "")
    preview = str(redact_runtime_value(snippet)).replace("\r", " ").replace("\n", " ")[:240]
    return f"{title}: {preview}" if preview else title


def _public_message(message: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    content = str(redact_runtime_value(message.get("content") or ""))
    max_len = max(200, min(max_chars, 6000))
    if len(content) > max_len:
        content = f"{content[:max_len]}..."
    return {
        "message_id": message.get("message_id"),
        "role": message.get("role"),
        "content": content,
        "run_id": message.get("run_id"),
        "created_at": message.get("created_at"),
    }
