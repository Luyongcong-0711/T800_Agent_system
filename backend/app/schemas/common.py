from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error_type: str
    message_for_user: str
    retryable: bool = False
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PageRequest(BaseModel):
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class PageResponse(BaseModel):
    items: list[Any]
    next_cursor: str | None = None

