from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_skill_service, require_workspace_role
from app.schemas.identity import RuntimeIdentity
from app.schemas.skill import (
    ListSkillsResponse,
    SkillActivateRequest,
    SkillActivationResponse,
    SkillCreateFromProposalRequest,
    SkillDetailResponse,
    SkillDisableRequest,
    SkillProposalRequest,
    SkillProposalResponse,
    SkillSearchResponse,
    SkillSummary,
    SkillValidateRequest,
)
from app.skills.service import SkillService

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["skills"])


@router.get("/skills", response_model=ListSkillsResponse)
async def list_skills(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> ListSkillsResponse:
    return ListSkillsResponse(
        workspace_id=workspace_id,
        skills=[SkillSummary(**item) for item in service.list_skills(workspace_id)],
    )


@router.get("/skills/search", response_model=SkillSearchResponse)
async def search_skills(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
    query: Annotated[str, Query(min_length=1)],
    top_k: Annotated[int, Query(ge=1, le=10)] = 5,
) -> SkillSearchResponse:
    items = service.search(workspace_id, query=query, top_k=top_k)
    return SkillSearchResponse(
        workspace_id=workspace_id,
        items=[SkillSummary(**item) for item in items],
    )


@router.post("/skill-proposals", response_model=SkillProposalResponse)
async def create_skill_proposal(
    workspace_id: str,
    request: SkillProposalRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> dict[str, Any]:
    return service.create_proposal(workspace_id, identity, request)


@router.get("/skill-proposals/{proposal_id}", response_model=SkillProposalResponse)
async def get_skill_proposal(
    workspace_id: str,
    proposal_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> dict[str, Any]:
    return service.get_proposal(workspace_id, proposal_id)


@router.post("/skills/from-proposal", response_model=SkillDetailResponse)
async def create_skill_from_proposal(
    workspace_id: str,
    request: SkillCreateFromProposalRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> dict[str, Any]:
    return service.materialize_proposal(workspace_id, identity, request)


@router.get("/skills/{skill_id}", response_model=SkillDetailResponse)
async def get_skill(
    workspace_id: str,
    skill_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> dict[str, Any]:
    return service.view_skill(workspace_id, skill_id)


@router.get("/skills/{skill_id}/versions/{version}", response_model=SkillDetailResponse)
async def get_skill_version(
    workspace_id: str,
    skill_id: str,
    version: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> dict[str, Any]:
    return service.view_skill(workspace_id, skill_id, version)


@router.post("/skills/{skill_id}/activate", response_model=SkillActivationResponse)
async def activate_skill(
    workspace_id: str,
    skill_id: str,
    request: SkillActivateRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> dict[str, Any]:
    return service.activate_skill(workspace_id, skill_id, request)


@router.post("/skills/{skill_id}/disable", response_model=SkillDetailResponse)
async def disable_skill(
    workspace_id: str,
    skill_id: str,
    request: SkillDisableRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> dict[str, Any]:
    return service.disable_skill(workspace_id, skill_id, request)


@router.post("/skills/{skill_id}/validate", response_model=SkillDetailResponse)
async def validate_skill(
    workspace_id: str,
    skill_id: str,
    request: SkillValidateRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[SkillService, Depends(get_skill_service)],
) -> dict[str, Any]:
    return service.validate_skill_scripts(workspace_id, skill_id, request)
