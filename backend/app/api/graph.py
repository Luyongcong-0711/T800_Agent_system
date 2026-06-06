from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import (
    build_embedding_client_for_workspace,
    build_milvus_vector_store_for_workspace,
    get_graph_query_service,
    get_job_service,
    get_object_store,
    require_workspace_role,
)
from app.graph_pipeline.query import ObjectStoreGraphQueryService
from app.jobs.service import JobService
from app.rag_pipeline.ingestion import DocumentIngestionService
from app.schemas.graph import (
    GraphBuildResponse,
    GraphEntitySearchRequest,
    GraphExpandEntityRequest,
    GraphFindPathsRequest,
    GraphFindRelationshipRequest,
    GraphGetEvidenceRequest,
    GraphRagSearchRequest,
    GraphReadonlyCypherRequest,
)
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest
from app.storage.object_store import ObjectStore
from app.tools.builtin.graph_tools import build_graphrag_search_tool
from app.tools.builtin.rag_tools import WorkspaceKnowledgeBaseStore

router = APIRouter(prefix="/workspaces/{workspace_id}/graph", tags=["graph"])


@router.get("/schema")
async def get_graph_schema(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObjectStoreGraphQueryService, Depends(get_graph_query_service)],
    knowledge_base_id: str = Query(default="kb_default"),
) -> dict[str, Any]:
    return {
        **service.get_schema_snapshot(workspace_id, knowledge_base_id),
        "warnings": _graph_query_warnings(service),
    }


@router.post("/entities/search")
async def search_graph_entities(
    workspace_id: str,
    request: GraphEntitySearchRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObjectStoreGraphQueryService, Depends(get_graph_query_service)],
) -> dict[str, Any]:
    entities = service.entity_search(
        workspace_id,
        request.knowledge_base_id,
        request.query,
        entity_types=request.entity_types,
        limit=request.limit,
        include_aliases=request.include_aliases,
    )
    return {"entities": entities, "warnings": _graph_query_warnings(service)}


@router.post("/entities/{entity_id}/expand")
async def expand_graph_entity(
    workspace_id: str,
    entity_id: str,
    request: GraphExpandEntityRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObjectStoreGraphQueryService, Depends(get_graph_query_service)],
) -> dict[str, Any]:
    paths = service.expand_entity(
        workspace_id,
        request.knowledge_base_id,
        entity_id,
        depth=min(request.depth, 2),
        relationship_allowlist=request.relationship_allowlist,
        limit=request.limit,
        include_evidence=request.include_evidence,
    )
    return {"entity_id": entity_id, "paths": paths, "warnings": _graph_query_warnings(service)}


@router.post("/relationships/find")
async def find_graph_relationship(
    workspace_id: str,
    request: GraphFindRelationshipRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObjectStoreGraphQueryService, Depends(get_graph_query_service)],
) -> dict[str, Any]:
    source = service.resolve_entity(workspace_id, request.knowledge_base_id, request.source_entity)
    target = service.resolve_entity(workspace_id, request.knowledge_base_id, request.target_entity)
    relationships = service.find_direct_relationships(
        workspace_id,
        request.knowledge_base_id,
        source.entity_id,
        target.entity_id,
        relationship_allowlist=request.relationship_allowlist,
        include_evidence=request.include_evidence,
    )
    return {
        "source": source.summary(),
        "target": target.summary(),
        "relationships": relationships,
        "empty": len(relationships) == 0,
        "warnings": _graph_query_warnings(service),
    }


@router.post("/paths/find")
async def find_graph_paths(
    workspace_id: str,
    request: GraphFindPathsRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObjectStoreGraphQueryService, Depends(get_graph_query_service)],
) -> dict[str, Any]:
    source = service.resolve_entity(workspace_id, request.knowledge_base_id, request.source_entity)
    target = service.resolve_entity(workspace_id, request.knowledge_base_id, request.target_entity)
    paths = service.find_paths(
        workspace_id,
        request.knowledge_base_id,
        source.entity_id,
        target.entity_id,
        max_depth=min(request.max_depth, 2),
        relationship_allowlist=request.relationship_allowlist,
        limit=request.limit,
    )
    return {"paths": paths, "empty": len(paths) == 0, "warnings": _graph_query_warnings(service)}


@router.post("/evidence")
async def get_graph_evidence(
    workspace_id: str,
    request: GraphGetEvidenceRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObjectStoreGraphQueryService, Depends(get_graph_query_service)],
) -> dict[str, Any]:
    evidence = service.get_evidence_refs(
        workspace_id,
        request.knowledge_base_id,
        request.fact_ids,
        request.evidence_ids,
        limit=50,
        include_chunk_text=request.include_chunk_text,
        max_chars_per_chunk=request.max_chars_per_chunk,
    )
    return {"evidence": evidence, "warnings": _graph_query_warnings(service)}


@router.post("/cypher/read")
async def execute_readonly_cypher(
    workspace_id: str,
    request: GraphReadonlyCypherRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("admin"))],
    service: Annotated[ObjectStoreGraphQueryService, Depends(get_graph_query_service)],
) -> dict[str, Any]:
    if not hasattr(service, "execute_readonly"):
        return {
            "ok": False,
            "error_type": "graph_readonly_cypher_unavailable",
            "message_for_model": "Neo4j read-only Cypher is not configured for this workspace.",
            "retryable": False,
        }
    return service.execute_readonly(
        request.query,
        parameters=request.parameters,
        limit=request.limit,
    )


@router.post("/search")
async def search_graphrag(
    workspace_id: str,
    request: GraphRagSearchRequest,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    service: Annotated[ObjectStoreGraphQueryService, Depends(get_graph_query_service)],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> dict[str, Any]:
    embedding_client = _optional_embedding_client(object_store, workspace_id)
    vector_store = _optional_vector_store(object_store, workspace_id)
    tool = build_graphrag_search_tool(
        graph_query=service,
        object_store=object_store,
        embedding_client=embedding_client,
        vector_store=vector_store,
        knowledge_base_store=WorkspaceKnowledgeBaseStore(
            DocumentIngestionService(object_store),
            workspace_id,
        ),
    )
    result = tool.invoke(
        {
            "workspace_id": workspace_id,
            "knowledge_base_id": request.knowledge_base_id,
            "query": request.query,
            "filters": request.filters,
            "top_k": request.top_k,
            "final_top_k": request.final_top_k,
            "graph_depth": min(request.graph_depth, 2),
            "relationship_allowlist": request.relationship_allowlist,
            "include_sources": request.include_sources,
        }
    )
    data = result.get("data", result) if isinstance(result, dict) else {}
    return {
        "text_evidence": list(data.get("text_evidence", [])),
        "graph_evidence": list(data.get("graph_evidence", [])),
        "warnings": [*list(data.get("warnings", [])), *_graph_query_warnings(service)],
    }


@router.post(
    "/build/{knowledge_base_id}/documents/{doc_id}",
    response_model=GraphBuildResponse,
)
async def build_document_graph(
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    identity: Annotated[RuntimeIdentity, Depends(require_workspace_role("editor"))],
    job_service: Annotated[JobService, Depends(get_job_service)],
) -> GraphBuildResponse:
    job = job_service.create_job(
        workspace_id,
        identity,
        CreateJobRequest(
            job_type="graph_build_job",
            target_scope={
                "scope_type": "document_graph",
                "knowledge_base_id": knowledge_base_id,
                "doc_id": doc_id,
            },
            input={},
            idempotency_key=f"graph-build:{knowledge_base_id}:{doc_id}",
            title=f"Build graph for {doc_id}",
        ),
    )
    return GraphBuildResponse(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        doc_id=doc_id,
        job_id=job["job_id"],
        result={
            "status": job["status"],
            "job_type": "graph_build_job",
            "message": "Graph build job queued.",
        },
    )


def _optional_embedding_client(object_store: ObjectStore, workspace_id: str) -> Any | None:
    try:
        return build_embedding_client_for_workspace(object_store, workspace_id)
    except Exception:  # noqa: BLE001 - GraphRAG REST falls back to object-store lexical search.
        return None


def _optional_vector_store(object_store: ObjectStore, workspace_id: str) -> Any | None:
    try:
        return build_milvus_vector_store_for_workspace(object_store, workspace_id)
    except Exception:  # noqa: BLE001 - GraphRAG REST falls back to object-store lexical search.
        return None


def _graph_query_warnings(service: Any) -> list[str]:
    if isinstance(service, ObjectStoreGraphQueryService):
        return ["using_object_store_graph_fallback"]
    return []
