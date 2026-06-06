from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.identity import RuntimeIdentity
from app.schemas.skill import (
    SkillActivateRequest,
    SkillCreateFromProposalRequest,
    SkillEntrypointSpec,
    SkillPermissions,
    SkillProposalRequest,
    SkillSource,
)
from app.skills.service import SkillService


class SkillScopeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class SkillSearchArgs(SkillScopeArgs):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=10)


class SkillViewArgs(SkillScopeArgs):
    skill_id: str = Field(min_length=1)
    version: str | None = None


class SkillActivateArgs(SkillViewArgs):
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SkillProposeArgs(SkillScopeArgs):
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    when_to_use: list[str] = Field(default_factory=list)
    workflow_steps: list[str] = Field(min_length=1)
    knowledge_notes: list[str] = Field(default_factory=list)
    entrypoints: list[dict[str, Any]] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)
    script_required: bool = False
    thread_id: str | None = None
    run_id: str | None = None
    source_message_ids: list[str] = Field(default_factory=list)


class SkillCreateFromProposalArgs(SkillScopeArgs):
    proposal_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    skill_id: str | None = None
    version: str = "0.1.0"


class SkillEntrypointCallArgs(SkillScopeArgs):
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    entrypoint_tool_name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str | None = None


def _identity(workspace_id: str, user_id: str) -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id=user_id,
        role="owner",
        workspace_id=workspace_id,
        workspace_role="owner",
    )


def build_skill_search_tool(*, skill_service: SkillService) -> StructuredTool:
    def skill_search(
        workspace_id: str,
        user_id: str,
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        _ = user_id
        return {
            "ok": True,
            "data": {"items": skill_service.search(workspace_id, query=query, top_k=top_k)},
        }

    return StructuredTool.from_function(
        func=skill_search,
        name="skill_search",
        description="Search compact user-created Skill summaries by task need.",
        args_schema=SkillSearchArgs,
    )


def build_skill_view_tool(*, skill_service: SkillService) -> StructuredTool:
    def skill_view(
        workspace_id: str,
        user_id: str,
        skill_id: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        return {"ok": True, "data": skill_service.view_skill(workspace_id, skill_id, version)}

    return StructuredTool.from_function(
        func=skill_view,
        name="skill_view",
        description="View one Skill compact detail without exposing raw script content.",
        args_schema=SkillViewArgs,
    )


def build_skill_activate_tool(*, skill_service: SkillService) -> StructuredTool:
    def skill_activate(
        workspace_id: str,
        user_id: str,
        skill_id: str,
        run_id: str,
        thread_id: str,
        reason: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        activation = skill_service.activate_skill(
            workspace_id,
            skill_id,
            SkillActivateRequest(
                run_id=run_id,
                thread_id=thread_id,
                reason=reason,
                version=version,
            ),
        )
        return {"ok": True, "data": activation}

    return StructuredTool.from_function(
        func=skill_activate,
        name="skill_activate",
        description="Activate a Skill for the current run and expose its entrypoint tool names.",
        args_schema=SkillActivateArgs,
    )


def build_skill_propose_tool(*, skill_service: SkillService) -> StructuredTool:
    def skill_propose(
        workspace_id: str,
        user_id: str,
        display_name: str,
        description: str,
        workflow_steps: list[str],
        when_to_use: list[str] | None = None,
        knowledge_notes: list[str] | None = None,
        entrypoints: list[dict[str, Any]] | None = None,
        permissions: dict[str, Any] | None = None,
        script_required: bool = False,
        thread_id: str | None = None,
        run_id: str | None = None,
        source_message_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        request = SkillProposalRequest(
            display_name=display_name,
            description=description,
            when_to_use=when_to_use or [],
            workflow_steps=workflow_steps,
            knowledge_notes=knowledge_notes or [],
            entrypoints=[
                SkillEntrypointSpec(**entrypoint) for entrypoint in (entrypoints or [])
            ],
            permissions=SkillPermissions(**(permissions or {})),
            script_required=script_required,
            source=SkillSource(
                thread_id=thread_id,
                run_id=run_id,
                message_ids=source_message_ids or [],
            ),
        )
        proposal = skill_service.create_proposal(
            workspace_id,
            _identity(workspace_id, user_id),
            request,
        )
        return {
            "ok": False,
            "error_type": "approval_required",
            "retryable": False,
            "message_for_model": (
                "Skill proposal created. User approval is required before materializing it."
            ),
            "data": proposal,
            "approval_id": proposal["approval_id"],
        }

    return StructuredTool.from_function(
        func=skill_propose,
        name="skill_propose",
        description="Create a Skill proposal from a reusable workflow; requires user approval.",
        args_schema=SkillProposeArgs,
    )


def build_skill_create_from_proposal_tool(*, skill_service: SkillService) -> StructuredTool:
    def skill_create_from_proposal(
        workspace_id: str,
        user_id: str,
        proposal_id: str,
        approval_id: str,
        skill_id: str | None = None,
        version: str = "0.1.0",
    ) -> dict[str, Any]:
        detail = skill_service.materialize_proposal(
            workspace_id,
            _identity(workspace_id, user_id),
            SkillCreateFromProposalRequest(
                proposal_id=proposal_id,
                approval_id=approval_id,
                skill_id=skill_id,
                version=version,
            ),
        )
        return {"ok": True, "data": detail}

    return StructuredTool.from_function(
        func=skill_create_from_proposal,
        name="skill_create_from_proposal",
        description="Materialize an approved Skill proposal into the Skill Registry.",
        args_schema=SkillCreateFromProposalArgs,
    )


def build_skill_entrypoint_call_tool(*, skill_service: SkillService) -> StructuredTool:
    def skill_entrypoint_call(
        workspace_id: str,
        user_id: str,
        run_id: str,
        thread_id: str,
        entrypoint_tool_name: str,
        args: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        return skill_service.execute_activated_entrypoint(
            workspace_id=workspace_id,
            run_id=run_id,
            thread_id=thread_id,
            entrypoint_tool_name=entrypoint_tool_name,
            args=args or {},
            tool_call_id=tool_call_id,
        )

    return StructuredTool.from_function(
        func=skill_entrypoint_call,
        name="skill_entrypoint_call",
        description=(
            "Call a previously activated Skill entrypoint by its activated entrypoint tool name."
        ),
        args_schema=SkillEntrypointCallArgs,
    )


def build_default_skill_tools(object_store: Any) -> list[StructuredTool]:
    service = SkillService(object_store)
    return [
        build_skill_search_tool(skill_service=service),
        build_skill_view_tool(skill_service=service),
        build_skill_activate_tool(skill_service=service),
        build_skill_propose_tool(skill_service=service),
        build_skill_create_from_proposal_tool(skill_service=service),
        build_skill_entrypoint_call_tool(skill_service=service),
    ]
