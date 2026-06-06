from __future__ import annotations

import re
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request

from app.api.dependencies import get_document_service, get_job_service, require_workspace_role
from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.jobs.service import JobService
from app.rag_pipeline.ingestion import DocumentIngestionService
from app.schemas.document import (
    ActiveEmbeddingResponse,
    ChunkResponse,
    CreateKnowledgeBaseRequest,
    DocumentResponse,
    EmbeddingReindexRequest,
    EmbeddingReindexResponse,
    KnowledgeBaseResponse,
    ListChunksResponse,
    ListDocumentsResponse,
    ListKnowledgeBasesResponse,
    UploadDocumentJsonRequest,
)
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest
from app.storage.object_store import ObjectNotFoundError

router = APIRouter(tags=["knowledge"])

ALLOWED_UPLOAD_EXTENSIONS = {
    ".csv",
    ".docx",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".markdown",
    ".md",
    ".mdown",
    ".mkd",
    ".pdf",
    ".png",
    ".py",
    ".text",
    ".tif",
    ".tiff",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".webp",
    ".xls",
    ".xlsx",
    ".yaml",
    ".yml",
}
ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/csv",
    "application/json",
    "application/markdown",
    "application/pdf",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/x-markdown",
}


@router.get(
    "/workspaces/{workspace_id}/knowledge-bases",
    response_model=ListKnowledgeBasesResponse,
)
async def list_knowledge_bases(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
) -> ListKnowledgeBasesResponse:
    return ListKnowledgeBasesResponse(
        workspace_id=workspace_id,
        knowledge_bases=[
            KnowledgeBaseResponse(**item)
            for item in service.list_knowledge_bases(workspace_id)
        ],
    )


@router.post(
    "/workspaces/{workspace_id}/knowledge-bases",
    response_model=KnowledgeBaseResponse,
)
async def create_knowledge_base(
    workspace_id: str,
    request: CreateKnowledgeBaseRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
) -> dict[str, Any]:
    manifest = service.ensure_knowledge_base(
        workspace_id,
        request.knowledge_base_id,
        name=request.name,
    )
    return {
        "workspace_id": workspace_id,
        "knowledge_base_id": manifest["knowledge_base_id"],
        "name": manifest.get("name") or manifest["knowledge_base_id"],
        "status": manifest.get("status", "active"),
        "updated_at": manifest["updated_at"],
    }


@router.get(
    "/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
)
async def get_knowledge_base(
    workspace_id: str,
    knowledge_base_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
) -> dict[str, Any]:
    try:
        manifest = service.ensure_knowledge_base(workspace_id, knowledge_base_id)
    except ObjectNotFoundError as exc:
        raise AgentSystemError(
            "knowledge_base_not_found",
            "Knowledge base was not found.",
            404,
        ) from exc
    return {
        "workspace_id": workspace_id,
        "knowledge_base_id": manifest["knowledge_base_id"],
        "name": manifest.get("name") or manifest["knowledge_base_id"],
        "status": manifest.get("status", "active"),
        "updated_at": manifest["updated_at"],
    }


@router.get(
    "/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/active-embedding",
    response_model=ActiveEmbeddingResponse,
)
async def get_active_embedding(
    workspace_id: str,
    knowledge_base_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
) -> dict[str, Any]:
    return service.get_active_embedding(workspace_id, knowledge_base_id)


@router.post(
    "/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/embedding/reindex",
    response_model=EmbeddingReindexResponse,
)
async def create_embedding_reindex_job(
    workspace_id: str,
    knowledge_base_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
    job_service: Annotated[JobService, Depends(get_job_service)],
    request: Annotated[EmbeddingReindexRequest | None, Body()] = None,
) -> dict[str, Any]:
    payload = request.model_dump(exclude_none=True) if request else {}
    embedding_input = _resolve_embedding_job_input(
        workspace_id=workspace_id,
        payload=payload,
        service=service,
    )
    active_embedding = service.get_active_embedding(workspace_id, knowledge_base_id)
    job = job_service.create_job(
        workspace_id,
        identity,
        CreateJobRequest(
            job_type="embedding_reindex_job",
            title=f"Reindex embeddings for {knowledge_base_id}",
            target_scope={
                "scope_type": "knowledge_base",
                "knowledge_base_id": knowledge_base_id,
            },
            input=embedding_input,
            idempotency_key=_optional_str(payload.get("idempotency_key")),
        ),
    )
    return {
        "workspace_id": workspace_id,
        "knowledge_base_id": knowledge_base_id,
        "job_id": job["job_id"],
        "job_type": "embedding_reindex_job",
        "job_status": job["status"],
        "active_embedding": active_embedding,
    }


@router.post(
    "/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentResponse,
)
async def upload_document_to_knowledge_base(
    workspace_id: str,
    knowledge_base_id: str,
    request: Request,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
    job_service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, Any]:
    payload = await _read_upload_payload(request)
    return _create_document_and_ingest(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        content=payload["content"],
        source_file_name=payload["source_file_name"],
        mime_type=payload["mime_type"],
        metadata=payload.get("metadata", {}),
        idempotency_key=payload.get("idempotency_key"),
        identity=identity,
        service=service,
        job_service=job_service,
    )


@router.post(
    "/workspaces/{workspace_id}/documents/upload",
    response_model=DocumentResponse,
)
async def upload_document(
    workspace_id: str,
    request: UploadDocumentJsonRequest,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
    job_service: Annotated[JobService, Depends(get_job_service)],
) -> dict[str, Any]:
    return _create_document_and_ingest(
        workspace_id=workspace_id,
        knowledge_base_id=request.knowledge_base_id,
        content=request.content,
        source_file_name=request.source_file_name,
        mime_type=request.mime_type,
        metadata=request.metadata,
        idempotency_key=request.idempotency_key,
        identity=identity,
        service=service,
        job_service=job_service,
    )


@router.get(
    "/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents",
    response_model=ListDocumentsResponse,
)
async def list_documents(
    workspace_id: str,
    knowledge_base_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
) -> ListDocumentsResponse:
    return ListDocumentsResponse(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        documents=[
            DocumentResponse(**item)
            for item in service.list_documents(workspace_id, knowledge_base_id)
        ],
    )


@router.get(
    "/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents/{doc_id}",
    response_model=DocumentResponse,
)
async def get_document(
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
) -> dict[str, Any]:
    try:
        return service.get_manifest(workspace_id, knowledge_base_id, doc_id)
    except ObjectNotFoundError as exc:
        raise AgentSystemError("document_not_found", "Document was not found.", 404) from exc


@router.get(
    "/workspaces/{workspace_id}/knowledge-bases/{knowledge_base_id}/documents/{doc_id}/chunks",
    response_model=ListChunksResponse,
)
async def list_document_chunks(
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
) -> ListChunksResponse:
    try:
        chunks = service.get_chunks(workspace_id, knowledge_base_id, doc_id)
    except ObjectNotFoundError as exc:
        raise AgentSystemError("document_not_found", "Document was not found.", 404) from exc
    return ListChunksResponse(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        doc_id=doc_id,
        chunks=[ChunkResponse(**_normalize_chunk(chunk)) for chunk in chunks],
    )


@router.get(
    "/workspaces/{workspace_id}/chunks/{chunk_id}",
    response_model=ChunkResponse,
)
async def get_chunk(
    workspace_id: str,
    chunk_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[DocumentIngestionService, Depends(get_document_service)],
    knowledge_base_id: str = Query(default="kb_default"),
    doc_id: str | None = None,
    max_chars: Annotated[int, Query(ge=200, le=4000)] = 1200,
) -> ChunkResponse:
    try:
        chunk = service.get_chunk(
            workspace_id,
            knowledge_base_id,
            chunk_id=chunk_id,
            doc_id=doc_id,
        )
    except ObjectNotFoundError as exc:
        raise AgentSystemError("chunk_not_found", "Chunk was not found.", 404) from exc
    normalized = _normalize_chunk(chunk)
    normalized["text"] = normalized["text"][:max_chars]
    return ChunkResponse(**normalized)


def _create_document_and_ingest(
    *,
    workspace_id: str,
    knowledge_base_id: str,
    content: bytes | str,
    source_file_name: str,
    mime_type: str | None,
    metadata: dict[str, Any],
    idempotency_key: str | None,
    identity: RuntimeIdentity,
    service: DocumentIngestionService,
    job_service: JobService,
) -> dict[str, Any]:
    if not _is_allowed_upload_type(source_file_name, mime_type):
        raise AgentSystemError(
            "unsupported_document_type",
            "Unsupported document type.",
            status_code=415,
            retryable=False,
        )

    embedding_defaults = _resolve_workspace_embedding_defaults(
        workspace_id=workspace_id,
        service=service,
        config_id="embedding",
    )
    if embedding_defaults is not None:
        service.ensure_active_embedding_defaults(
            workspace_id,
            knowledge_base_id,
            **embedding_defaults,
        )

    doc_id = new_id("doc")
    doc_version_id = new_id("docv")
    job = job_service.create_job(
        workspace_id,
        identity,
        CreateJobRequest(
            job_type="document_ingestion_job",
            target_scope={
                "scope_type": "document_version",
                "knowledge_base_id": knowledge_base_id,
                "doc_id": doc_id,
                "doc_version_id": doc_version_id,
            },
            input={"source_file_name": source_file_name, "mime_type": mime_type},
            idempotency_key=idempotency_key,
            title=f"Ingest {source_file_name}",
        ),
    )
    uploaded = service.create_uploaded_document(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        doc_id=doc_id,
        doc_version_id=doc_version_id,
        content=content,
        source_file_name=source_file_name,
        mime_type=mime_type,
        last_job_id=job["job_id"],
        metadata=metadata,
    )
    return {
        **uploaded,
        "job_id": job["job_id"],
        "job_type": "document_ingestion_job",
    }


def _resolve_embedding_job_input(
    *,
    workspace_id: str,
    payload: dict[str, Any],
    service: DocumentIngestionService,
) -> dict[str, Any]:
    config_id = _optional_str(payload.get("config_id")) or "embedding"
    defaults = _resolve_workspace_embedding_defaults(
        workspace_id=workspace_id,
        service=service,
        config_id=config_id,
        require_backend=False,
    )
    provider = _normalize_embedding_provider(
        _optional_str(payload.get("provider"))
        or (defaults or {}).get("provider")
        or "openai_compatible"
    )
    model = _optional_str(payload.get("model")) or (defaults or {}).get("model") or ""
    dimension = _resolve_positive_int(
        payload.get("dimension"),
        (defaults or {}).get("dimension"),
    )
    return {
        "provider": provider,
        "model": model,
        "dimension": dimension,
        "collection": _optional_str(payload.get("collection")),
        "config_id": config_id,
    }


def _resolve_workspace_embedding_defaults(
    *,
    workspace_id: str,
    service: DocumentIngestionService,
    config_id: str,
    require_backend: bool = True,
) -> dict[str, Any] | None:
    from app.core.settings import get_settings
    from app.model_connector.config_service import ModelConfigService

    settings = get_settings()
    try:
        config = ModelConfigService(service.object_store, settings).get_config(
            workspace_id,
            config_id,
        )
    except Exception:  # noqa: BLE001 - keep lexical fallback when settings config is unavailable.
        config = {}
    if require_backend and (
        not config.get("enabled", True)
        or not _optional_str(config.get("base_url"))
        or not _optional_str(config.get("api_key_ref"))
    ):
        return None
    model = _optional_str(config.get("model")) or settings.default_embedding_model_name
    dimension = _resolve_positive_int(
        config.get("dimension"),
        settings.default_embedding_dimension,
    )
    if not model or dimension <= 0:
        return None
    return {
        "provider": _normalize_embedding_provider(
            _optional_str(config.get("provider")) or "openai_compatible"
        ),
        "model": model,
        "dimension": dimension,
        "config_id": config_id,
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _normalize_embedding_provider(value: str) -> str:
    if value == "openai-compatible":
        return "openai_compatible"
    return value


def _resolve_positive_int(*values: Any) -> int:
    for value in values:
        if value is None or value == "":
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0


def _is_allowed_upload_type(source_file_name: str, mime_type: str | None) -> bool:
    suffix = PurePath(source_file_name or "").suffix.lower()
    if suffix in ALLOWED_UPLOAD_EXTENSIONS:
        return True
    normalized_mime_type = (mime_type or "").split(";")[0].strip().lower()
    if normalized_mime_type in ALLOWED_UPLOAD_CONTENT_TYPES:
        return True
    return normalized_mime_type.startswith("text/") and suffix in {"", ".txt", ".text"}


async def _read_upload_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        return await _read_multipart_upload(request, content_type)
    data = await request.json()
    if not isinstance(data, dict):
        raise AgentSystemError("invalid_upload_payload", "Invalid upload payload.", 400)
    return {
        "content": str(data.get("content") or ""),
        "source_file_name": str(data.get("source_file_name") or "document.txt"),
        "mime_type": data.get("mime_type") or "text/plain",
        "metadata": data.get("metadata") or {},
        "idempotency_key": data.get("idempotency_key"),
    }


async def _read_multipart_upload(request: Request, content_type: str) -> dict[str, Any]:
    boundary_match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type)
    if not boundary_match:
        raise AgentSystemError("invalid_multipart", "Multipart boundary is missing.", 400)
    boundary = boundary_match.group("boundary").strip('"').encode("utf-8")
    body = await request.body()
    file_content: bytes | None = None
    file_name = "document.txt"
    file_mime_type = "text/plain"
    fields: dict[str, str] = {}
    for raw_part in body.split(b"--" + boundary):
        part = raw_part
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.startswith(b"--"):
            continue
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part:
            continue
        header_blob, separator, content = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers = header_blob.decode("utf-8", errors="replace")
        disposition = _header_value(headers, "content-disposition")
        name = _disposition_param(disposition, "name")
        filename = _disposition_param(disposition, "filename")
        part_content_type = _header_value(headers, "content-type")
        if filename is not None:
            file_name = filename
            file_mime_type = part_content_type or "text/plain"
            file_content = content
        elif name:
            fields[name] = content.decode("utf-8", errors="replace").strip()
    if file_content is None:
        raise AgentSystemError("file_missing", "Upload file is missing.", 400)
    return {
        "content": file_content,
        "source_file_name": fields.get("source_file_name") or file_name,
        "mime_type": file_mime_type,
        "metadata": {},
        "idempotency_key": fields.get("idempotency_key"),
    }


def _header_value(headers: str, name: str) -> str | None:
    lowered = name.lower()
    for line in headers.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == lowered:
            return value.strip()
    return None


def _disposition_param(disposition: str | None, name: str) -> str | None:
    if not disposition:
        return None
    match = re.search(rf'{name}="([^"]*)"', disposition)
    return match.group(1) if match else None


def _normalize_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    if "doc_id" not in chunk and "document_id" in chunk:
        chunk = {**chunk, "doc_id": chunk["document_id"]}
    if "doc_version_id" not in chunk:
        chunk = {**chunk, "doc_version_id": chunk.get("metadata_filter", {}).get("doc_version_id")}
    if "workspace_id" not in chunk:
        chunk = {**chunk, "workspace_id": chunk.get("metadata_filter", {}).get("workspace_id")}
    if "knowledge_base_id" not in chunk:
        chunk = {
            **chunk,
            "knowledge_base_id": chunk.get("metadata_filter", {}).get("knowledge_base_id"),
        }
    return chunk
