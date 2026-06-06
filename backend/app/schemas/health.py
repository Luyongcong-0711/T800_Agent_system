from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ServiceStatus = Literal["healthy", "unhealthy", "unknown"]


class ServiceHealth(BaseModel):
    target: str
    status: ServiceStatus
    latency_ms: float | None = None
    message: str | None = None
    checked_at: str
    details: dict[str, str] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    ok: bool
    service: str
    version: str
    environment: str


class DatabaseHealthResponse(BaseModel):
    ok: bool
    workspace_id: str
    services: list[ServiceHealth]


ReadinessStatus = Literal["pass", "warn", "fail", "blocked", "not_applicable"]


class ReadinessCheck(BaseModel):
    check_id: str
    category: str
    title: str
    status: ReadinessStatus
    summary: str
    required: bool = True
    evidence: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ReadinessCategory(BaseModel):
    category: str
    status: ReadinessStatus
    pass_count: int = 0
    warn_count: int = 0
    fail_count: int = 0
    blocked_count: int = 0
    checks: list[ReadinessCheck] = Field(default_factory=list)


class P0ReadinessResponse(BaseModel):
    workspace_id: str
    ok: bool
    status: ReadinessStatus
    generated_at: str
    environment: str
    runtime_instance_id: str
    summary: dict[str, int]
    categories: list[ReadinessCategory]
    checks: list[ReadinessCheck]
    remaining_blockers: list[str] = Field(default_factory=list)
