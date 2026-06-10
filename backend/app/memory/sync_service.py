from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.time import utc_now_iso
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import memory_sync_state_key


class MemorySyncService:
    def __init__(
        self,
        object_store: ObjectStore,
        *,
        embedding_client: Any | None = None,
        vector_store: Any | None = None,
        graph_writer: Any | None = None,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.embedding_client = embedding_client
        self.vector_store = vector_store
        self.graph_writer = graph_writer

    def process_pending(
        self,
        workspace_id: str,
        *,
        limit: int = 50,
        collection: str | None = None,
        embedding_model: str | None = None,
        embedding_dimension: int | None = None,
        embedding_provider: str | None = None,
    ) -> dict[str, Any]:
        state_key = memory_sync_state_key(workspace_id)
        state = self.json_store.read_json_or_default(
            state_key,
            self._empty_state(workspace_id),
        )
        pending = list(state.get("pending_targets", []))
        selected = pending[: max(1, min(int(limit), 100))]
        remaining = pending[len(selected) :]
        results: list[dict[str, Any]] = []
        now = utc_now_iso()

        for item in selected:
            result = self._process_one(
                item,
                collection=collection,
                embedding_model=embedding_model,
                embedding_dimension=embedding_dimension,
                embedding_provider=embedding_provider,
            )
            results.append(result)
            if result["ok"]:
                continue
            retryable = bool(result.get("retryable", True))
            remaining.append(
                {
                    **item,
                    "status": "waiting_retry" if retryable else "failed",
                    "attempt_count": int(item.get("attempt_count") or 0) + 1,
                    "last_error": {
                        "error_type": result["error_type"],
                        "message": result["message"],
                        "retryable": retryable,
                        **(
                            {"details": result["details"]}
                            if isinstance(result.get("details"), dict)
                            else {}
                        ),
                    },
                    "last_attempt_at": now,
                    "next_retry_at": _next_retry_at() if retryable else None,
                    "updated_at": now,
                }
            )

        state["pending_targets"] = remaining
        state["last_processed_at"] = now if selected else state.get("last_processed_at")
        state["last_result"] = {
            "processed_count": len(selected),
            "succeeded_count": len([item for item in results if item["ok"]]),
            "failed_count": len([item for item in results if not item["ok"]]),
        }
        state["updated_at"] = now
        state["revision"] = int(state.get("revision") or 0) + 1
        self.json_store.write_json(state_key, state)
        return {
            "workspace_id": workspace_id,
            "processed_count": state["last_result"]["processed_count"],
            "succeeded_count": state["last_result"]["succeeded_count"],
            "failed_count": state["last_result"]["failed_count"],
            "pending_count": len(remaining),
            "results": results,
        }

    def _process_one(
        self,
        item: dict[str, Any],
        *,
        collection: str | None,
        embedding_model: str | None,
        embedding_dimension: int | None,
        embedding_provider: str | None,
    ) -> dict[str, Any]:
        target = str(item.get("target") or "")
        action = str(item.get("action") or "")
        try:
            if target == "milvus":
                self._sync_milvus(
                    item,
                    action=action,
                    collection=collection,
                    embedding_model=embedding_model,
                    embedding_dimension=embedding_dimension,
                    embedding_provider=embedding_provider,
                )
            elif target == "neo4j":
                self._sync_neo4j(item, action=action)
            else:
                raise MemorySyncTargetError(
                    "memory_sync_target_unknown",
                    f"Unsupported memory sync target: {target}",
                    retryable=False,
                )
        except Exception as exc:  # noqa: BLE001 - per-target failure must not block ack of other targets.
            return {
                "ok": False,
                "target": target,
                "action": action,
                "memory_id": item.get("memory_id"),
                "error_type": getattr(exc, "error_type", exc.__class__.__name__),
                "message": str(exc) or exc.__class__.__name__,
                "retryable": bool(getattr(exc, "retryable", True)),
                **(
                    {"details": getattr(exc, "details")}
                    if isinstance(getattr(exc, "details", None), dict)
                    else {}
                ),
            }
        return {
            "ok": True,
            "target": target,
            "action": action,
            "memory_id": item.get("memory_id"),
        }

    def _sync_milvus(
        self,
        item: dict[str, Any],
        *,
        action: str,
        collection: str | None,
        embedding_model: str | None,
        embedding_dimension: int | None,
        embedding_provider: str | None,
    ) -> None:
        if self.vector_store is None:
            raise MemorySyncTargetError("memory_milvus_not_configured", "Milvus is not configured.")
        if not collection:
            raise MemorySyncTargetError(
                "memory_milvus_collection_missing",
                "Memory sync requires a Milvus collection.",
                retryable=False,
            )
        memory_id = str(item["memory_id"])
        if action == "delete":
            if not hasattr(self.vector_store, "delete_by_ids"):
                raise MemorySyncTargetError(
                    "memory_milvus_delete_not_supported",
                    "Milvus adapter does not support memory delete.",
                    retryable=False,
                )
            self.vector_store.delete_by_ids(collection=collection, ids=[memory_id])
            return
        if action != "upsert":
            return
        if self.embedding_client is None:
            raise MemorySyncTargetError(
                "memory_embedding_not_configured",
                "Embedding client is not configured.",
            )
        if not embedding_model or not embedding_dimension:
            raise MemorySyncTargetError(
                "memory_embedding_config_missing",
                "Memory sync requires embedding model and dimension.",
                retryable=False,
            )
        record = self.json_store.read_json(str(item["content_object_key"]))
        text = _memory_index_text(record)
        vector = self.embedding_client.embed_query(
            text=text,
            model=embedding_model,
            dimension=int(embedding_dimension),
            provider=embedding_provider,
        )
        if hasattr(self.vector_store, "ensure_collection"):
            self.vector_store.ensure_collection(
                collection=collection,
                dimension=int(embedding_dimension),
            )
        self.vector_store.upsert(
            collection=collection,
            records=[
                {
                    "chunk_id": memory_id,
                    "doc_id": memory_id,
                    "doc_version_id": str(record.get("revision") or 1),
                    "knowledge_base_id": "__memory__",
                    "metadata": {
                        "memory_id": memory_id,
                        "memory_type": record.get("type"),
                        "scope": record.get("scope"),
                        "user_id": record.get("user_id"),
                    },
                    "object_key": record["content_object_key"],
                    "text": text,
                    "vector": vector,
                    "workspace_id": item.get("workspace_id") or record.get("workspace_id") or "",
                }
            ],
        )

    def _sync_neo4j(self, item: dict[str, Any], *, action: str) -> None:
        if self.graph_writer is None:
            raise MemorySyncTargetError("memory_neo4j_not_configured", "Neo4j is not configured.")
        memory_id = str(item["memory_id"])
        if action == "delete":
            if not hasattr(self.graph_writer, "delete_memory"):
                raise MemorySyncTargetError(
                    "memory_neo4j_delete_not_supported",
                    "Neo4j writer does not support memory delete.",
                    retryable=False,
                )
            self.graph_writer.delete_memory(memory_id=memory_id, operation_id=item.get("event_id"))
            return
        if action != "upsert":
            return
        if not hasattr(self.graph_writer, "upsert_memory"):
            raise MemorySyncTargetError(
                "memory_neo4j_upsert_not_supported",
                "Neo4j writer does not support memory upsert.",
                retryable=False,
            )
        record = self.json_store.read_json(str(item["content_object_key"]))
        self.graph_writer.upsert_memory(record=record, operation_id=item.get("event_id"))

    @staticmethod
    def _empty_state(workspace_id: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "pending_targets": [],
            "last_event_id": None,
            "last_event_seq": 0,
            "updated_at": None,
            "revision": 0,
        }


class MemorySyncTargetError(Exception):
    def __init__(self, error_type: str, message: str, *, retryable: bool = True) -> None:
        self.error_type = error_type
        self.retryable = retryable
        super().__init__(message)


def _memory_index_text(record: dict[str, Any]) -> str:
    parts = [
        record.get("summary"),
        record.get("field"),
        record.get("value"),
        record.get("content"),
    ]
    return "\n".join(str(part) for part in parts if part)


def _next_retry_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
