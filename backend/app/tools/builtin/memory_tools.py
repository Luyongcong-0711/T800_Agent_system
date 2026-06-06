from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.memory.service import PROFILE_AND_PREFERENCE_TYPES, MemoryService
from app.schemas.identity import RuntimeIdentity
from app.schemas.memory import MemorySource, UpsertMemoryRequest


class MemoryScopeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class MemorySearchArgs(MemoryScopeArgs):
    query: str = Field(min_length=1)
    memory_types: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=20)


class MemoryGetArgs(MemoryScopeArgs):
    memory_id: str = Field(min_length=1)
    include_source: bool = True


class MemoryUpsertArgs(MemoryScopeArgs):
    memory_id: str | None = None
    scope: str | None = None
    type: str = Field(min_length=1)
    field: str | None = None
    value: str | None = None
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)
    thread_id: str | None = None
    message_id: str | None = None
    evidence: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    enabled_for_model_context: bool = True


class MemoryDeleteArgs(MemoryScopeArgs):
    memory_id: str = Field(min_length=1)


class MemoryReviewArgs(MemoryScopeArgs):
    limit: int = Field(default=20, ge=1, le=50)


class MemoryPromoteFromSessionArgs(MemoryScopeArgs):
    type: str = Field(min_length=1)
    field: str | None = None
    value: str | None = None
    summary: str = Field(min_length=1)
    content: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    message_id: str | None = None
    evidence: str | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)


def _upsert_or_propose_memory(
    *,
    memory_service: MemoryService,
    workspace_id: str,
    user_id: str,
    request: UpsertMemoryRequest,
) -> dict[str, Any]:
    identity = RuntimeIdentity(
        user_id=user_id,
        role="owner",
        workspace_id=workspace_id,
        workspace_role="owner",
    )
    if request.type in PROFILE_AND_PREFERENCE_TYPES:
        record = memory_service.upsert_memory(workspace_id, identity, request)
        return {"ok": True, "data": memory_service.public_summary(record)}
    record = memory_service.propose_memory(workspace_id, identity, request)
    return {
        "ok": False,
        "error_type": "approval_required",
        "retryable": False,
        "message_for_model": (
            "Memory candidate created. User approval is required before model injection."
        ),
        "data": memory_service.public_summary(record),
    }


def build_memory_search_tool(*, memory_service: MemoryService) -> StructuredTool:
    def memory_search(
        workspace_id: str,
        user_id: str,
        query: str,
        memory_types: list[str] | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        hits = memory_service.search(
            workspace_id,
            user_id,
            query=query,
            memory_types=memory_types or [],
            limit=limit,
        )
        return {"ok": True, "data": {"hits": hits}}

    return StructuredTool.from_function(
        func=memory_search,
        name="memory_search",
        description="Search long-term memory summaries by query; returns IDs and summaries only.",
        args_schema=MemorySearchArgs,
    )


def build_memory_get_tool(*, memory_service: MemoryService) -> StructuredTool:
    def memory_get(
        workspace_id: str,
        user_id: str,
        memory_id: str,
        include_source: bool = True,
    ) -> dict[str, Any]:
        record = memory_service.get_memory(workspace_id, user_id, memory_id)
        if not include_source:
            record = {**record, "source": {}}
        return {"ok": True, "data": record}

    return StructuredTool.from_function(
        func=memory_get,
        name="memory_get",
        description="Read one long-term memory by ID after scoped access checks.",
        args_schema=MemoryGetArgs,
    )


def build_memory_upsert_tool(*, memory_service: MemoryService) -> StructuredTool:
    def memory_upsert(
        workspace_id: str,
        user_id: str,
        type: str,
        summary: str,
        content: str,
        memory_id: str | None = None,
        scope: str | None = None,
        field: str | None = None,
        value: str | None = None,
        thread_id: str | None = None,
        message_id: str | None = None,
        evidence: str | None = None,
        confidence: float = 1.0,
        enabled_for_model_context: bool = True,
    ) -> dict[str, Any]:
        request = UpsertMemoryRequest(
            memory_id=memory_id,
            scope=scope,
            type=type,
            field=field,
            value=value,
            summary=summary,
            content=content,
            source=MemorySource(
                thread_id=thread_id,
                message_id=message_id,
                evidence=evidence,
            ),
            confidence=confidence,
            enabled_for_model_context=enabled_for_model_context,
        )
        return _upsert_or_propose_memory(
            memory_service=memory_service,
            workspace_id=workspace_id,
            user_id=user_id,
            request=request,
        )

    return StructuredTool.from_function(
        func=memory_upsert,
        name="memory_upsert",
        description=(
            "Create or update long-term memory through controlled storage. "
            "user_profile and user_preference are user-visible and do not require approval."
        ),
        args_schema=MemoryUpsertArgs,
    )


def build_memory_delete_tool(*, memory_service: MemoryService) -> StructuredTool:
    def memory_delete(workspace_id: str, user_id: str, memory_id: str) -> dict[str, Any]:
        record = memory_service.delete_memory(workspace_id, user_id, memory_id)
        return {
            "ok": True,
            "data": {
                "memory_id": record["memory_id"],
                "status": record["status"],
                "enabled_for_model_context": record["enabled_for_model_context"],
            },
        }

    return StructuredTool.from_function(
        func=memory_delete,
        name="memory_delete",
        description="Disable long-term memory injection by marking a memory deleted.",
        args_schema=MemoryDeleteArgs,
    )


def build_memory_review_tool(*, memory_service: MemoryService) -> StructuredTool:
    def memory_review(
        workspace_id: str,
        user_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        candidates = [
            memory
            for memory in memory_service.list_memories(workspace_id, user_id)
            if memory.get("status") == "pending_approval"
        ]
        return {"ok": True, "data": {"candidates": candidates[:limit]}}

    return StructuredTool.from_function(
        func=memory_review,
        name="memory_review",
        description="List long-term memory candidates waiting for user approval.",
        args_schema=MemoryReviewArgs,
    )


def build_memory_promote_from_session_tool(*, memory_service: MemoryService) -> StructuredTool:
    def memory_promote_from_session(
        workspace_id: str,
        user_id: str,
        type: str,
        summary: str,
        content: str,
        thread_id: str,
        field: str | None = None,
        value: str | None = None,
        message_id: str | None = None,
        evidence: str | None = None,
        confidence: float = 0.8,
    ) -> dict[str, Any]:
        request = UpsertMemoryRequest(
            type=type,
            field=field,
            value=value,
            summary=summary,
            content=content,
            source=MemorySource(
                thread_id=thread_id,
                message_id=message_id,
                evidence=evidence,
            ),
            confidence=confidence,
            enabled_for_model_context=True,
        )
        return _upsert_or_propose_memory(
            memory_service=memory_service,
            workspace_id=workspace_id,
            user_id=user_id,
            request=request,
        )

    return StructuredTool.from_function(
        func=memory_promote_from_session,
        name="memory_promote_from_session",
        description="Promote a bounded session fact into long-term memory or a review candidate.",
        args_schema=MemoryPromoteFromSessionArgs,
    )


def build_default_memory_tools(object_store: Any) -> list[StructuredTool]:
    service = _build_runtime_memory_service(object_store)
    return [
        build_memory_search_tool(memory_service=service),
        build_memory_get_tool(memory_service=service),
        build_memory_upsert_tool(memory_service=service),
        build_memory_delete_tool(memory_service=service),
        build_memory_review_tool(memory_service=service),
        build_memory_promote_from_session_tool(memory_service=service),
    ]


def _build_runtime_memory_service(object_store: Any) -> MemoryService:
    try:
        from app.core.settings import get_settings
        from app.jobs.service import JobService

        settings = get_settings()
        return MemoryService(
            object_store,
            job_service=JobService(
                object_store,
                runtime_instance_id=settings.runtime_instance_id,
                job_lease_ttl_seconds=settings.job_lease_ttl_seconds,
            ),
        )
    except Exception:  # noqa: BLE001 - memory tools should still work when jobs are not configured.
        return MemoryService(object_store)
