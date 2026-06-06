from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.conversation.history import SessionHistoryService


class SessionSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    thread_status: list[str] = Field(default_factory=lambda: ["active", "archived"])
    limit: int = Field(default=10, ge=1, le=20)


class SessionMessageGetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    include_neighbor: bool = True
    max_chars: int = Field(default=2000, ge=200, le=6000)


def build_session_search_tool(*, history_service: SessionHistoryService) -> StructuredTool:
    def session_search(
        workspace_id: str,
        user_id: str,
        query: str,
        thread_status: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        hits = history_service.search(
            workspace_id=workspace_id,
            user_id=user_id,
            query=query,
            thread_status=thread_status or ["active", "archived"],
            limit=limit,
        )
        return {
            "ok": True,
            "data": {
                "hits": hits,
                "warning": (
                    "session_search is for historical conversation lookup only; "
                    "use memory_upsert for durable long-term memory."
                ),
            },
        }

    return StructuredTool.from_function(
        func=session_search,
        name="session_search",
        description=(
            "Search historical conversation snippets by query and return traceable "
            "thread_id and message_id references."
        ),
        args_schema=SessionSearchArgs,
    )


def build_session_message_get_tool(*, history_service: SessionHistoryService) -> StructuredTool:
    def session_message_get(
        workspace_id: str,
        user_id: str,
        thread_id: str,
        message_id: str,
        include_neighbor: bool = True,
        max_chars: int = 2000,
    ) -> dict[str, Any]:
        window = history_service.get_message_window(
            workspace_id=workspace_id,
            user_id=user_id,
            thread_id=thread_id,
            message_id=message_id,
            include_neighbor=include_neighbor,
            max_chars=max_chars,
        )
        return {"ok": True, "data": window}

    return StructuredTool.from_function(
        func=session_message_get,
        name="session_message_get",
        description=(
            "Read a bounded historical message window by thread_id and message_id; "
            "does not load an entire old conversation."
        ),
        args_schema=SessionMessageGetArgs,
    )


def build_default_session_tools(object_store: Any) -> list[StructuredTool]:
    service = SessionHistoryService(object_store)
    return [
        build_session_search_tool(history_service=service),
        build_session_message_get_tool(history_service=service),
    ]
