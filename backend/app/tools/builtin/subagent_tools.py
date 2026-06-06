from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.jobs.service import JobService
from app.schemas.identity import RuntimeIdentity
from app.schemas.subagent import SubAgentTaskRequest
from app.subagents.service import DEFAULT_AGENT_TYPES, SubAgentService


class CallSubAgentArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_id: str = Field(default="default", min_length=1)
    user_id: str = Field(default="default_user", min_length=1)
    parent_run_id: str = Field(min_length=1)
    parent_thread_id: str | None = None
    objective: str = Field(min_length=1)
    mode: str = "readonly"
    read_scope: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=300000, ge=1000, le=3600000)
    token_budget: int = Field(default=12000, ge=1000, le=200000)
    expected_output: str = ""
    execution_mode: str = Field(default="sync", pattern="^(sync|job|auto)$")


def _identity(workspace_id: str, user_id: str) -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id=user_id,
        role="owner",
        workspace_id=workspace_id,
        workspace_role="owner",
    )


def make_subagent_tool(
    agent_type: str,
    *,
    subagent_service: SubAgentService,
    job_service: JobService | None = None,
) -> StructuredTool:
    normalized_agent_type = agent_type.strip().lower().replace("-", "_").replace(" ", "_")

    def call_subagent(
        workspace_id: str,
        user_id: str,
        parent_run_id: str,
        objective: str,
        parent_thread_id: str | None = None,
        mode: str = "readonly",
        read_scope: list[str] | None = None,
        write_scope: list[str] | None = None,
        allowed_tools: list[str] | None = None,
        forbidden_tools: list[str] | None = None,
        timeout_ms: int = 300000,
        token_budget: int = 12000,
        expected_output: str = "",
        execution_mode: str = "sync",
    ) -> dict[str, Any]:
        task_request = SubAgentTaskRequest(
            parent_run_id=parent_run_id,
            parent_thread_id=parent_thread_id,
            agent_type=normalized_agent_type,
            objective=objective,
            mode=mode,  # type: ignore[arg-type]
            read_scope=read_scope or [],
            write_scope=write_scope or [],
            allowed_tools=allowed_tools or [],
            forbidden_tools=forbidden_tools or [],
            timeout_ms=timeout_ms,
            token_budget=token_budget,
            expected_output=expected_output,
        )
        normalized_execution_mode = execution_mode.strip().lower()
        should_queue = normalized_execution_mode == "job" or (
            normalized_execution_mode == "auto" and timeout_ms >= 900000
        )
        if should_queue:
            result = subagent_service.enqueue_subagent_job(
                workspace_id,
                _identity(workspace_id, user_id),
                task_request,
                job_service=job_service or JobService(subagent_service.object_store),
            )
        else:
            result = subagent_service.run_subagent(
                workspace_id,
                _identity(workspace_id, user_id),
                task_request,
            )
        return {
            "ok": True,
            "data": result,
            "needs_main_review": True,
            "can_directly_finalize": False,
        }

    return StructuredTool.from_function(
        func=call_subagent,
        name=f"call_subagent_{normalized_agent_type}",
        description=(
            f"Call a controlled {normalized_agent_type} SubAgent. "
            "The result returns to the main Agent for review before final use."
        ),
        args_schema=CallSubAgentArgs,
    )


def build_default_subagent_tools(object_store: Any) -> list[StructuredTool]:
    service = SubAgentService(object_store)
    job_service = JobService(object_store)
    return [
        make_subagent_tool(agent_type, subagent_service=service, job_service=job_service)
        for agent_type in DEFAULT_AGENT_TYPES
    ]
