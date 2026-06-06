from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.core.errors import AgentSystemError
from app.memory.sync_service import MemorySyncService
from app.schemas.identity import RuntimeIdentity
from app.schemas.memory import MemorySource, PatchMemoryRequest, UpsertMemoryRequest
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore
from app.storage.path_builder import (
    memory_sync_event_index_key,
    memory_sync_events_prefix,
    memory_sync_state_key,
    user_disabled_memory_patterns_key,
    user_memory_index_key,
    workspace_disabled_memory_patterns_key,
    workspace_memory_index_key,
)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_dump(item) for item in value)
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _require_module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Phase J must expose {name}: {exc}")


def _call_first(target: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    errors: list[str] = []
    for name in names:
        func = getattr(target, name, None)
        if not callable(func):
            continue
        try:
            return func(*args, **kwargs)
        except TypeError as exc:
            errors.append(f"{name}: {exc}")
            if kwargs and not args:
                try:
                    return func(kwargs)
                except TypeError as dict_exc:
                    errors.append(f"{name}(dict): {dict_exc}")
    detail = f" Signature errors: {'; '.join(errors)}" if errors else ""
    pytest.fail(f"{target!r} must expose one of: {', '.join(names)}.{detail}")


def _make_memory_service(tmp_path: Any) -> Any:
    module = _require_module("app.memory.service")
    service_cls = getattr(module, "MemoryService", None)
    if service_cls is None:
        pytest.fail("app.memory.service must expose MemoryService.")
    object_store = LocalObjectStore(tmp_path / "objects")
    try:
        return service_cls(object_store=object_store)
    except TypeError:
        return service_cls(object_store)


def _memory_id(value: Any) -> str:
    dumped = _dump(value)
    if isinstance(dumped, dict):
        for key in ("memory_id", "id"):
            if dumped.get(key):
                return str(dumped[key])
        data = dumped.get("data")
        if isinstance(data, dict) and data.get("memory_id"):
            return str(data["memory_id"])
    pytest.fail(f"Memory result must include memory_id: {dumped}")


def _hits(value: Any) -> list[dict[str, Any]]:
    dumped = _dump(value)
    if isinstance(dumped, list):
        return dumped
    assert isinstance(dumped, dict)
    for key in ("hits", "items", "memories"):
        items = dumped.get(key)
        if isinstance(items, list):
            return items
    data = dumped.get("data")
    if isinstance(data, dict) and isinstance(data.get("hits"), list):
        return data["hits"]
    pytest.fail(f"Expected memory search result with hits/items/memories: {dumped}")


def _upsert(service: Any, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "default",
        "user_id": "default_user",
        "memory_type": "user_preference",
        "type": "user_preference",
        "field": "answer_style",
        "summary": "User prefers concise Chinese answers.",
        "content": "The user wants answers in Chinese with direct conclusions.",
        "source_thread_id": "thread_001",
        "source_message_id": "msg_001",
        "source": {
            "thread_id": "thread_001",
            "message_id": "msg_001",
            "evidence": "User explicitly requested Chinese output.",
        },
        "evidence": "User explicitly requested Chinese output.",
        "confidence": 0.95,
        "enabled_for_model_context": True,
    }
    payload.update(overrides)
    if hasattr(service, "upsert_memory"):
        identity = RuntimeIdentity(
            user_id=payload["user_id"],
            role="owner",
            workspace_id=payload["workspace_id"],
            workspace_role="owner",
        )
        request = UpsertMemoryRequest(
            memory_id=payload.get("memory_id"),
            scope=payload.get("scope"),
            type=payload.get("memory_type") or payload["type"],
            field=payload.get("field"),
            value=payload.get("value"),
            summary=payload["summary"],
            content=payload["content"],
            source=MemorySource(
                thread_id=payload["source"].get("thread_id"),
                message_id=payload["source"].get("message_id"),
                evidence=payload["source"].get("evidence"),
            ),
            confidence=payload["confidence"],
            enabled_for_model_context=payload["enabled_for_model_context"],
        )
        return _dump(service.upsert_memory(payload["workspace_id"], identity, request))
    return _dump(
        _call_first(
            service,
            ("upsert", "upsert_memory", "memory_upsert", "upsert_canonical_json"),
            **payload,
        )
    )


def _propose(service: Any, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "default",
        "user_id": "default_user",
        "memory_type": "project_fact",
        "field": "storage_stack",
        "summary": "Storage stack uses MinIO.",
        "content": "The current project stores object payloads in MinIO.",
        "source": {
            "thread_id": "thread_001",
            "message_id": "msg_001",
            "evidence": "Project docs describe MinIO object storage.",
        },
        "confidence": 0.9,
        "enabled_for_model_context": True,
    }
    payload.update(overrides)
    identity = RuntimeIdentity(
        user_id=payload["user_id"],
        role="owner",
        workspace_id=payload["workspace_id"],
        workspace_role="owner",
    )
    request = UpsertMemoryRequest(
        type=payload.get("memory_type") or payload["type"],
        field=payload.get("field"),
        value=payload.get("value"),
        summary=payload["summary"],
        content=payload["content"],
        source=MemorySource(
            thread_id=payload["source"].get("thread_id"),
            message_id=payload["source"].get("message_id"),
            evidence=payload["source"].get("evidence"),
        ),
        confidence=payload["confidence"],
        enabled_for_model_context=payload["enabled_for_model_context"],
    )
    return _dump(service.propose_memory(payload["workspace_id"], identity, request))


def _search(service: Any, **overrides: Any) -> list[dict[str, Any]]:
    payload = {
        "workspace_id": "default",
        "user_id": "default_user",
        "query": "Chinese answer preference",
        "memory_types": [],
        "limit": 10,
    }
    payload.update(overrides)
    if hasattr(service, "search"):
        return _hits(
            service.search(
                payload["workspace_id"],
                payload["user_id"],
                query=payload["query"],
                memory_types=payload["memory_types"],
                limit=payload["limit"],
            )
        )
    return _hits(_call_first(service, ("search", "search_memories", "memory_search"), **payload))


def _get(service: Any, memory_id: str) -> dict[str, Any]:
    if hasattr(service, "get_memory"):
        return _dump(service.get_memory("default", "default_user", memory_id))
    return _dump(
        _call_first(
            service,
            ("get", "get_memory", "memory_get"),
            workspace_id="default",
            user_id="default_user",
            memory_id=memory_id,
            include_source=True,
        )
    )


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def embed_query(
        self,
        *,
        text: str,
        model: str,
        dimension: int | None = None,
        provider: str | None = None,
    ) -> list[float]:
        self.calls.append(
            {"text": text, "model": model, "dimension": dimension, "provider": provider}
        )
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def __init__(self, *, fail_upsert: bool = False) -> None:
        self.fail_upsert = fail_upsert
        self.collections: list[dict[str, Any]] = []
        self.upserts: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []

    def ensure_collection(self, *, collection: str, dimension: int) -> None:
        self.collections.append({"collection": collection, "dimension": dimension})

    def upsert(self, *, collection: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        if self.fail_upsert:
            raise RuntimeError("milvus unavailable")
        self.upserts.append({"collection": collection, "records": records})
        return {"ok": True}

    def delete_by_ids(self, *, collection: str, ids: list[str]) -> dict[str, Any]:
        self.deletes.append({"collection": collection, "ids": ids})
        return {"ok": True}


class _FakeGraphWriter:
    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []

    def upsert_memory(self, *, record: dict[str, Any], operation_id: str | None = None) -> None:
        self.upserts.append({"record": record, "operation_id": operation_id})

    def delete_memory(self, *, memory_id: str, operation_id: str | None = None) -> None:
        self.deletes.append({"memory_id": memory_id, "operation_id": operation_id})


def _disable_or_delete(service: Any, memory_id: str, mode: str) -> dict[str, Any]:
    if mode == "disable" and hasattr(service, "patch_memory"):
        return _dump(
            service.patch_memory(
                "default",
                "default_user",
                memory_id,
                PatchMemoryRequest(status="disabled"),
            )
        )
    if mode == "delete" and hasattr(service, "delete_memory"):
        return _dump(service.delete_memory("default", "default_user", memory_id))
    return _dump(
        _call_first(
            service,
            ("disable_or_delete", "delete", "delete_memory", "memory_delete"),
            workspace_id="default",
            user_id="default_user",
            memory_id=memory_id,
            mode=mode,
            reason=f"contract {mode}",
        )
    )


def _build_snapshot(service: Any, **overrides: Any) -> dict[str, Any]:
    payload = {
        "workspace_id": "default",
        "user_id": "default_user",
        "thread_id": "thread_001",
        "query": "respect saved preferences",
    }
    payload.update(overrides)
    if any(
        hasattr(service, name)
        for name in ("build_memory_snapshot", "build_snapshot", "memory_snapshot")
    ):
        return _dump(
            _call_first(
                service,
                ("build_memory_snapshot", "build_snapshot", "memory_snapshot"),
                **payload,
            )
        )

    module = _require_module("app.memory.snapshot")
    builder_cls = getattr(module, "MemorySnapshotBuilder", None)
    if builder_cls is None:
        pytest.fail("app.memory.snapshot must expose MemorySnapshotBuilder.")
    try:
        builder = builder_cls(memory_service=service)
    except TypeError:
        builder = builder_cls(service)
    return _dump(_call_first(builder, ("build", "build_snapshot"), **payload))


def _make_compressor(**overrides: Any) -> Any:
    module = _require_module("app.runtime.context.compressor")
    compressor_cls = getattr(module, "HermesStyleContextCompressor", None) or getattr(
        module,
        "ContextCompressor",
        None,
    )
    if compressor_cls is None:
        pytest.fail(
            "app.runtime.context.compressor must expose HermesStyleContextCompressor "
            "or ContextCompressor."
        )
    kwargs = {
        "threshold": 0.50,
        "protect_first_n": 3,
        "protect_last_n": 20,
        "summary_target_ratio": 0.20,
    }
    kwargs.update(overrides)
    try:
        return compressor_cls(**kwargs)
    except TypeError:
        return compressor_cls()


def _message(index: int, role: str = "user", content: str | None = None) -> dict[str, Any]:
    return {
        "message_id": f"msg_{index:03d}",
        "role": role,
        "content": content or f"{role} message {index}",
    }


def _message_ids(messages: list[dict[str, Any]]) -> set[str]:
    return {str(message["message_id"]) for message in messages if message.get("message_id")}


def test_user_profile_and_preference_upsert_are_frontend_visible_without_approval(tmp_path) -> None:
    service = _make_memory_service(tmp_path)

    profile = _upsert(
        service,
        memory_type="user_profile",
        type="user_profile",
        field="name",
        value="Zhang San",
        summary="User name is Zhang San.",
        content="The user said their name is Zhang San.",
    )
    preference = _upsert(service)

    for memory in (profile, preference):
        assert memory["frontend_visible"] is True
        assert memory["requires_approval"] is False
        assert memory["enabled_for_model_context"] is True
        assert memory["source"]["thread_id"] == "thread_001"
        assert memory["source"]["message_id"] == "msg_001"


def test_project_fact_and_rule_are_workspace_scoped(tmp_path) -> None:
    service = _make_memory_service(tmp_path)

    fact = _upsert(
        service,
        memory_type="project_fact",
        type="project_fact",
        field="backend_stack",
        scope="global",
        summary="Backend uses Python and LangGraph.",
        content="The current workspace backend is Python + LangChain + LangGraph.",
    )
    rule = _upsert(
        service,
        memory_type="project_rule",
        type="project_rule",
        field="allowed_edits",
        scope="global",
        summary="Only edit explicitly owned paths.",
        content="Implementation must not overwrite unrelated user changes.",
    )

    assert fact["scope"] == "workspace"
    assert fact["workspace_id"] == "default"
    assert rule["scope"] == "workspace"
    assert rule["workspace_id"] == "default"


def test_pending_project_memory_candidates_can_be_approved_or_rejected(tmp_path) -> None:
    service = _make_memory_service(tmp_path)
    approved_candidate = _propose(
        service,
        memory_type="project_fact",
        summary="Backend object store is MinIO.",
        content="The backend object store is MinIO.",
    )
    rejected_candidate = _propose(
        service,
        memory_type="project_rule",
        field="edit_scope",
        summary="Rejected rule should not be injected.",
        content="This candidate should be rejected before model injection.",
    )

    approved = _dump(
        service.approve_memory("default", "default_user", _memory_id(approved_candidate))
    )
    rejected = _dump(
        service.reject_memory("default", "default_user", _memory_id(rejected_candidate))
    )
    snapshot = _build_snapshot(service)

    assert approved["status"] == "active"
    assert approved["enabled_for_model_context"] is True
    assert approved["requires_approval"] is False
    assert rejected["status"] == "rejected"
    assert rejected["enabled_for_model_context"] is False
    assert rejected["requires_approval"] is False
    assert _memory_id(approved) in set(snapshot["included_memory_ids"])
    assert _memory_id(rejected) not in set(snapshot["included_memory_ids"])


def test_memory_search_returns_summaries_and_memory_get_returns_details(tmp_path) -> None:
    service = _make_memory_service(tmp_path)
    full_content = "Full canonical memory content with detailed preference and source evidence."
    created = _upsert(
        service,
        summary="User prefers concise Chinese answers.",
        content=full_content,
    )

    hits = _search(service, query="concise Chinese")

    assert hits
    first_hit = hits[0]
    assert first_hit["memory_id"] == _memory_id(created)
    assert first_hit["summary"] == "User prefers concise Chinese answers."
    assert "content" not in first_hit
    assert "source" not in first_hit

    detail = _get(service, _memory_id(created))
    assert detail["content"] == full_content
    assert detail["source"]["evidence"]


def test_patch_memory_updates_editable_metadata_and_moves_scope_index(tmp_path) -> None:
    service = _make_memory_service(tmp_path)
    object_store = service.object_store
    created = _upsert(
        service,
        field="answer_style",
        scope="global",
        value="concise",
        summary="User prefers concise Chinese answers.",
        content="The user wants concise Chinese answers.",
    )
    memory_id = _memory_id(created)

    patched = service.patch_memory(
        "default",
        "default_user",
        memory_id,
        PatchMemoryRequest(
            confidence=0.8,
            content="The user wants compact Chinese responses.",
            field="answer_format",
            scope="workspace",
            summary="User wants compact Chinese responses.",
            value=None,
        ),
    )
    store = JsonObjectStore(object_store)
    global_index = store.read_json(user_memory_index_key("default_user"))
    workspace_index = store.read_json(workspace_memory_index_key("default"))

    assert patched["scope"] == "workspace"
    assert patched["workspace_id"] == "default"
    assert patched["field"] == "answer_format"
    assert patched["value"] is None
    assert patched["confidence"] == 0.8
    assert patched["content_object_key"].startswith("workspaces/default/memory/")
    assert all(item["memory_id"] != memory_id for item in global_index["memories"])
    assert [item["memory_id"] for item in workspace_index["memories"]] == [memory_id]


def test_disabled_and_deleted_memories_are_excluded_from_model_snapshot(tmp_path) -> None:
    service = _make_memory_service(tmp_path)
    active = _upsert(service, field="active_pref", summary="Active preference.", content="Use it.")
    disabled = _upsert(
        service,
        field="disabled_pref",
        summary="Disabled preference.",
        content="Do not inject it.",
    )
    deleted = _upsert(
        service,
        field="deleted_pref",
        summary="Deleted preference.",
        content="Do not inject it either.",
    )

    _disable_or_delete(service, _memory_id(disabled), mode="disable")
    _disable_or_delete(service, _memory_id(deleted), mode="delete")
    snapshot = _build_snapshot(service)

    included = set(snapshot["included_memory_ids"])
    assert _memory_id(active) in included
    assert _memory_id(disabled) not in included
    assert _memory_id(deleted) not in included


def test_disabled_memory_patterns_block_silent_rewrite(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = _make_memory_service(tmp_path)
    service.object_store = object_store
    service.json_store = JsonObjectStore(object_store)
    created = _upsert(
        service,
        field="answer_style",
        summary="User prefers concise Chinese answers.",
        content="The user wants answers in Chinese with direct conclusions.",
    )

    _disable_or_delete(service, _memory_id(created), mode="disable")

    with pytest.raises(AgentSystemError) as exc_info:
        _upsert(
            service,
            field="answer_style",
            summary="User prefers concise Chinese answers.",
            content="The user wants answers in Chinese with direct conclusions.",
        )
    patterns = JsonObjectStore(object_store).read_json(
        user_disabled_memory_patterns_key("default_user")
    )

    assert exc_info.value.error_type == "memory_previously_disabled"
    assert patterns["patterns"][0]["memory_id"] == _memory_id(created)
    assert patterns["patterns"][0]["field"] == "answer_style"


def test_disabled_memory_id_cannot_be_silently_recreated_with_changed_content(tmp_path) -> None:
    service = _make_memory_service(tmp_path)
    created = _upsert(
        service,
        field="answer_style",
        summary="User prefers concise Chinese answers.",
        content="The user wants answers in Chinese with direct conclusions.",
    )

    _disable_or_delete(service, _memory_id(created), mode="disable")

    with pytest.raises(AgentSystemError) as exc_info:
        _upsert(
            service,
            memory_id=_memory_id(created),
            field="rewritten_answer_style",
            summary="User now prefers verbose English explanations.",
            content="The model tried to revive a disabled memory under the same id.",
        )

    assert exc_info.value.error_type == "memory_previously_disabled"
    assert exc_info.value.details["reason"] == "memory_id_blocked"


def test_workspace_disabled_memory_patterns_are_isolated_by_user_id(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = _make_memory_service(tmp_path)
    service.object_store = object_store
    service.json_store = JsonObjectStore(object_store)
    created = _upsert(
        service,
        memory_type="project_rule",
        type="project_rule",
        field="storage_stack",
        summary="Project database stack is Milvus plus Neo4j.",
        content="This project uses Milvus for vector search and Neo4j for graph expansion.",
    )

    _disable_or_delete(service, _memory_id(created), mode="delete")
    other_user_memory = _upsert(
        service,
        user_id="other_user",
        memory_type="project_rule",
        type="project_rule",
        field="storage_stack",
        summary="Project database stack is Milvus plus Neo4j.",
        content="This project uses Milvus for vector search and Neo4j for graph expansion.",
    )
    patterns = JsonObjectStore(object_store).read_json(
        workspace_disabled_memory_patterns_key("default")
    )

    assert _memory_id(other_user_memory)
    assert patterns["patterns"][0]["user_id"] == "default_user"


def test_reenable_active_memory_restores_context_flag_and_clears_disabled_pattern(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = _make_memory_service(tmp_path)
    service.object_store = object_store
    service.json_store = JsonObjectStore(object_store)
    created = _upsert(
        service,
        field="answer_style",
        summary="User prefers concise Chinese answers.",
        content="The user wants answers in Chinese with direct conclusions.",
    )
    memory_id = _memory_id(created)

    service.patch_memory(
        "default",
        "default_user",
        memory_id,
        PatchMemoryRequest(enabled_for_model_context=False),
    )
    reenabled = service.patch_memory(
        "default",
        "default_user",
        memory_id,
        PatchMemoryRequest(status="active"),
    )
    patterns = JsonObjectStore(object_store).read_json(
        user_disabled_memory_patterns_key("default_user")
    )

    assert reenabled["status"] == "active"
    assert reenabled["enabled_for_model_context"] is True
    assert patterns["patterns"] == []


def test_memory_upsert_writes_minio_sync_outbox_for_external_indexes(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = _make_memory_service(tmp_path)
    service.object_store = object_store
    service.json_store = JsonObjectStore(object_store)
    created = _upsert(
        service,
        memory_type="user_preference",
        type="user_preference",
        field="answer_style",
        summary="User prefers concise Chinese answers.",
        content="The user wants answers in Chinese with direct conclusions.",
    )

    events = JsonlSegmentStore(
        object_store,
        memory_sync_events_prefix("default"),
    ).read_all()
    event_index = JsonObjectStore(object_store).read_json(memory_sync_event_index_key("default"))
    state = JsonObjectStore(object_store).read_json(memory_sync_state_key("default"))

    assert events[-1]["type"] == "memory_upserted"
    assert events[-1]["memory_id"] == _memory_id(created)
    assert events[-1]["targets"]["milvus"]["action"] == "upsert"
    assert events[-1]["targets"]["neo4j"]["action"] == "skip"
    assert "content" not in events[-1]
    assert event_index["last_event_id"] == events[-1]["event_id"]
    assert state["pending_targets"] == [
        {
            "target": "milvus",
            "action": "upsert",
            "workspace_id": "default",
            "memory_id": _memory_id(created),
            "memory_type": "user_preference",
            "scope": "global",
            "user_id": "default_user",
            "content_object_key": created["content_object_key"],
            "event_id": events[-1]["event_id"],
            "event_seq": events[-1]["event_seq"],
            "status": "pending",
            "reason": "semantic_memory_index",
            "updated_at": events[-1]["created_at"],
        }
    ]


def test_memory_approval_and_delete_update_sync_outbox_targets(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = _make_memory_service(tmp_path)
    service.object_store = object_store
    service.json_store = JsonObjectStore(object_store)
    candidate = _propose(
        service,
        memory_type="project_fact",
        type="project_fact",
        field="storage_stack",
        summary="Project database stack is Milvus plus Neo4j.",
        content="This project uses Milvus for vector search and Neo4j for graph expansion.",
    )

    approved = service.approve_memory("default", "default_user", _memory_id(candidate))
    state_after_approve = JsonObjectStore(object_store).read_json(memory_sync_state_key("default"))
    pending_after_approve = {
        item["target"]: item for item in state_after_approve["pending_targets"]
    }
    assert pending_after_approve["milvus"]["action"] == "upsert"
    assert pending_after_approve["neo4j"]["action"] == "upsert"

    service.delete_memory("default", "default_user", _memory_id(approved))
    events = JsonlSegmentStore(
        object_store,
        memory_sync_events_prefix("default"),
    ).read_all()
    state_after_delete = JsonObjectStore(object_store).read_json(memory_sync_state_key("default"))
    pending_after_delete = {
        item["target"]: item for item in state_after_delete["pending_targets"]
    }

    assert events[-1]["type"] == "memory_deleted"
    assert events[-1]["targets"]["milvus"]["action"] == "delete"
    assert events[-1]["targets"]["neo4j"]["action"] == "delete"
    assert pending_after_delete["milvus"]["action"] == "delete"
    assert pending_after_delete["neo4j"]["action"] == "delete"


def test_memory_sync_service_acks_successful_milvus_targets(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = _make_memory_service(tmp_path)
    service.object_store = object_store
    service.json_store = JsonObjectStore(object_store)
    created = _upsert(
        service,
        field="answer_style",
        summary="User prefers concise Chinese answers.",
        content="The user wants answers in Chinese with direct conclusions.",
    )
    embedding = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore()

    result = MemorySyncService(
        object_store,
        embedding_client=embedding,
        vector_store=vector_store,
    ).process_pending(
        "default",
        collection="default_memory",
        embedding_model="embedding-test",
        embedding_dimension=3,
    )
    state = JsonObjectStore(object_store).read_json(memory_sync_state_key("default"))

    assert result["processed_count"] == 1
    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 0
    assert state["pending_targets"] == []
    assert embedding.calls[0]["model"] == "embedding-test"
    assert vector_store.collections == [{"collection": "default_memory", "dimension": 3}]
    assert vector_store.upserts[0]["records"][0]["chunk_id"] == _memory_id(created)
    assert vector_store.upserts[0]["records"][0]["metadata"]["memory_type"] == "user_preference"


def test_memory_sync_service_keeps_failed_target_and_acks_successful_target(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = _make_memory_service(tmp_path)
    service.object_store = object_store
    service.json_store = JsonObjectStore(object_store)
    candidate = _propose(
        service,
        memory_type="project_fact",
        type="project_fact",
        field="storage_stack",
        summary="Project database stack is Milvus plus Neo4j.",
        content="This project uses Milvus for vector search and Neo4j for graph expansion.",
    )
    approved = service.approve_memory("default", "default_user", _memory_id(candidate))
    graph_writer = _FakeGraphWriter()

    result = MemorySyncService(
        object_store,
        embedding_client=_FakeEmbeddingClient(),
        vector_store=_FakeVectorStore(fail_upsert=True),
        graph_writer=graph_writer,
    ).process_pending(
        "default",
        collection="default_memory",
        embedding_model="embedding-test",
        embedding_dimension=3,
    )
    state = JsonObjectStore(object_store).read_json(memory_sync_state_key("default"))

    assert result["processed_count"] == 2
    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 1
    assert graph_writer.upserts[0]["record"]["memory_id"] == _memory_id(approved)
    assert len(state["pending_targets"]) == 1
    assert state["pending_targets"][0]["target"] == "milvus"
    assert state["pending_targets"][0]["status"] == "waiting_retry"
    assert state["pending_targets"][0]["last_error"]["error_type"] == "RuntimeError"


def test_deleted_workspace_memory_patterns_block_project_memory_rewrite(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = _make_memory_service(tmp_path)
    service.object_store = object_store
    service.json_store = JsonObjectStore(object_store)
    created = _upsert(
        service,
        memory_type="project_rule",
        type="project_rule",
        field="storage_stack",
        summary="Project database stack is Milvus plus Neo4j.",
        content="This project uses Milvus for vector search and Neo4j for graph expansion.",
    )

    _disable_or_delete(service, _memory_id(created), mode="delete")

    with pytest.raises(AgentSystemError) as exc_info:
        _upsert(
            service,
            memory_type="project_rule",
            type="project_rule",
            field="storage_stack",
            summary="Project database stack is Milvus plus Neo4j.",
            content="This project uses Milvus for vector search and Neo4j for graph expansion.",
        )
    patterns = JsonObjectStore(object_store).read_json(
        workspace_disabled_memory_patterns_key("default")
    )

    assert exc_info.value.error_type == "memory_previously_disabled"
    assert patterns["patterns"][0]["memory_id"] == _memory_id(created)
    assert patterns["patterns"][0]["scope"] == "workspace"


def test_hermes_compressor_triggers_at_half_context_window() -> None:
    compressor = _make_compressor()

    assert compressor.should_compress(prompt_tokens=49, model_context_limit=100) is False
    assert compressor.should_compress(prompt_tokens=50, model_context_limit=100) is True


def test_hermes_compressor_keeps_head_tail_reference_summary_and_tool_pairs() -> None:
    compressor = _make_compressor()
    messages = [_message(1, "system"), _message(2, "user"), _message(3, "assistant")]
    messages.extend(_message(index) for index in range(4, 12))
    messages.extend(
        [
            {
                "message_id": "msg_012",
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_mid", "name": "memory_search", "args": {}}],
            },
            {
                "message_id": "msg_013",
                "role": "tool",
                "tool_call_id": "call_mid",
                "content": "large middle result",
            },
        ]
    )
    messages.extend(_message(index) for index in range(14, 45))

    compacted, compaction = _dump(
        compressor.compress(
            messages=messages,
            current_tokens=60,
            focus_topic="Phase J memory and context tests",
        )
    )

    compacted_ids = _message_ids(compacted)
    assert {"msg_001", "msg_002", "msg_003"} <= compacted_ids
    assert {f"msg_{index:03d}" for index in range(25, 45)} <= compacted_ids
    assert "REFERENCE ONLY" in str(compaction.get("summary", "")).upper()
    assert compaction["strategy"] == "hermes_style_head_summary_tail"

    tool_result_ids = {
        str(message["tool_call_id"])
        for message in compacted
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    assistant_call_ids = {
        str(tool_call["id"])
        for message in compacted
        for tool_call in message.get("tool_calls", [])
        if tool_call.get("id")
    }
    assert tool_result_ids <= assistant_call_ids
