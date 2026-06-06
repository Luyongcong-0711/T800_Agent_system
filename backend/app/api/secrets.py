from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_secret_service, require_workspace_role
from app.schemas.identity import RuntimeIdentity
from app.schemas.secret import (
    CreateSecretRequest,
    CreateSecretResponse,
    ListSecretsResponse,
    RotateSecretRequest,
    SecretReferencesResponse,
    SecretSummary,
    UpdateSecretRequest,
)
from app.secret_store.master_key import SecretStoreUnavailableError
from app.secret_store.secret_service import SecretNotFoundError, SecretService
from app.storage.object_store import RevisionConflictError

router = APIRouter(prefix="/workspaces/{workspace_id}/secrets", tags=["secrets"])


@router.get("", response_model=ListSecretsResponse)
async def list_secrets(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SecretService, Depends(get_secret_service)],
) -> ListSecretsResponse:
    return service.list_secrets(workspace_id)


@router.post("", response_model=CreateSecretResponse)
async def create_secret(
    workspace_id: str,
    request: CreateSecretRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("admin"))],
    service: Annotated[SecretService, Depends(get_secret_service)],
) -> CreateSecretResponse:
    try:
        return service.create_secret(workspace_id, request, created_by=identity.user_id)
    except SecretStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail="secret_store_unavailable") from exc
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail="secret_revision_conflict") from exc


@router.get("/{secret_id}", response_model=SecretSummary)
async def get_secret(
    workspace_id: str,
    secret_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SecretService, Depends(get_secret_service)],
) -> SecretSummary:
    try:
        return service.get_secret_summary(workspace_id, secret_id)
    except SecretNotFoundError as exc:
        raise HTTPException(status_code=404, detail="secret_not_found") from exc


@router.patch("/{secret_id}", response_model=SecretSummary)
async def update_secret(
    workspace_id: str,
    secret_id: str,
    request: UpdateSecretRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("admin"))],
    service: Annotated[SecretService, Depends(get_secret_service)],
) -> SecretSummary:
    try:
        return service.update_secret(workspace_id, secret_id, request)
    except SecretNotFoundError as exc:
        raise HTTPException(status_code=404, detail="secret_not_found") from exc
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail="secret_revision_conflict") from exc


@router.post("/{secret_id}/disable", response_model=SecretSummary)
async def disable_secret(
    workspace_id: str,
    secret_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("admin"))],
    service: Annotated[SecretService, Depends(get_secret_service)],
) -> SecretSummary:
    try:
        return service.disable_secret(workspace_id, secret_id)
    except SecretNotFoundError as exc:
        raise HTTPException(status_code=404, detail="secret_not_found") from exc
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail="secret_revision_conflict") from exc


@router.post("/{secret_id}/rotate", response_model=SecretSummary)
async def rotate_secret(
    workspace_id: str,
    secret_id: str,
    request: RotateSecretRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("admin"))],
    service: Annotated[SecretService, Depends(get_secret_service)],
) -> SecretSummary:
    try:
        return service.rotate_secret(workspace_id, secret_id, request)
    except SecretNotFoundError as exc:
        raise HTTPException(status_code=404, detail="secret_not_found") from exc
    except SecretStoreUnavailableError as exc:
        raise HTTPException(status_code=503, detail="secret_store_unavailable") from exc
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail="secret_revision_conflict") from exc


@router.delete("/{secret_id}", response_model=SecretSummary)
async def delete_secret(
    workspace_id: str,
    secret_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("admin"))],
    service: Annotated[SecretService, Depends(get_secret_service)],
) -> SecretSummary:
    try:
        return service.delete_secret(workspace_id, secret_id)
    except SecretNotFoundError as exc:
        raise HTTPException(status_code=404, detail="secret_not_found") from exc
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail="secret_revision_conflict") from exc


@router.get("/{secret_id}/references", response_model=SecretReferencesResponse)
async def get_secret_references(
    workspace_id: str,
    secret_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[SecretService, Depends(get_secret_service)],
) -> SecretReferencesResponse:
    return SecretReferencesResponse(
        secret_id=secret_id,
        references=service.list_references(workspace_id, secret_id),
    )
