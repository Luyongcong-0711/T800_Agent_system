from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.graph_pipeline.query import ObjectStoreGraphQueryService
from app.tools.builtin.rag_tools import build_rag_search_tool


class GraphScopeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)


class GraphEntitySearchArgs(GraphScopeArgs):
    query: str = Field(min_length=1)
    entity_types: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=20)
    include_aliases: bool = True


class GraphExpandEntityArgs(GraphScopeArgs):
    entity_id: str = Field(min_length=1)
    depth: int = Field(default=1, ge=1)
    relationship_allowlist: list[str] = Field(default_factory=list)
    limit: int = Field(default=30, ge=1, le=50)
    include_evidence: bool = True


class GraphFindRelationshipArgs(GraphScopeArgs):
    source_entity: str = Field(min_length=1)
    target_entity: str = Field(min_length=1)
    relationship_allowlist: list[str] = Field(default_factory=list)
    include_evidence: bool = True


class GraphFindPathsArgs(GraphScopeArgs):
    source_entity: str = Field(min_length=1)
    target_entity: str = Field(min_length=1)
    max_depth: int = Field(default=2, ge=1)
    relationship_allowlist: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=20)


class GraphGetEvidenceArgs(GraphScopeArgs):
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    include_chunk_text: bool = True
    max_chars_per_chunk: int = Field(default=1200, ge=1, le=4000)


class GraphTimelineQueryArgs(GraphScopeArgs):
    entity: str = Field(min_length=1)
    date_from: str | None = None
    date_to: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


class GraphReadonlyCypherArgs(GraphScopeArgs):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=50, ge=1, le=200)


class GraphRagSearchArgs(GraphScopeArgs):
    query: str = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=50, ge=1, le=100)
    final_top_k: int = Field(default=10, ge=1, le=20)
    graph_depth: int = Field(default=2, ge=1)
    relationship_allowlist: list[str] = Field(default_factory=list)
    include_sources: bool = True


def build_graph_schema_get_tool(
    *,
    graph_query: Any,
    audit_log: Any | None = None,
) -> StructuredTool:
    def graph_schema_get(workspace_id: str, knowledge_base_id: str) -> dict[str, Any]:
        schema = graph_query.get_schema_snapshot(workspace_id, knowledge_base_id)
        data = {
            **schema,
            "allowed_depth": 2,
            "readonly": True,
            "warnings": _graph_query_warnings(graph_query),
        }
        return _finish("graph_schema_get", {"schema": data, **data}, audit_log)

    return StructuredTool.from_function(
        func=graph_schema_get,
        name="graph_schema_get",
        description="Return graph labels, relationships, and property summary for scoped querying.",
        args_schema=GraphScopeArgs,
    )


def build_graph_entity_search_tool(
    *,
    graph_query: Any,
    audit_log: Any | None = None,
) -> StructuredTool:
    def graph_entity_search(
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 10,
        include_aliases: bool = True,
    ) -> dict[str, Any]:
        entities = graph_query.entity_search(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            query=query,
            entity_types=entity_types or [],
            limit=_clamp(limit, 1, 20),
            include_aliases=include_aliases,
        )
        return _finish(
            "graph_entity_search",
            {"entities": entities, "warnings": _graph_query_warnings(graph_query)},
            audit_log,
        )

    return StructuredTool.from_function(
        func=graph_entity_search,
        name="graph_entity_search",
        description="Search graph entities by name or alias within a knowledge base.",
        args_schema=GraphEntitySearchArgs,
    )


def build_graph_expand_entity_tool(
    *,
    graph_query: Any,
    audit_log: Any | None = None,
) -> StructuredTool:
    def graph_expand_entity(
        workspace_id: str,
        knowledge_base_id: str,
        entity_id: str,
        depth: int = 1,
        relationship_allowlist: list[str] | None = None,
        limit: int = 30,
        include_evidence: bool = True,
    ) -> dict[str, Any]:
        safe_depth = _clamp(depth, 1, 2)
        entity = _resolve_entity(graph_query, workspace_id, knowledge_base_id, entity_id)
        paths = graph_query.expand_entity(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            entity_id=getattr(entity, "entity_id", entity_id),
            depth=safe_depth,
            relationship_allowlist=relationship_allowlist or [],
            limit=_clamp(limit, 1, 50),
            include_evidence=include_evidence,
        )
        data = {
            "start_entity": _summary(entity),
            "paths": _limit_paths(paths, 50),
            "warnings": _graph_query_warnings(graph_query),
        }
        return _finish("graph_expand_entity", data, audit_log)

    return StructuredTool.from_function(
        func=graph_expand_entity,
        name="graph_expand_entity",
        description="Expand one graph entity up to two hops with relationship direction preserved.",
        args_schema=GraphExpandEntityArgs,
    )


def build_graph_find_relationship_tool(
    *,
    graph_query: Any,
    audit_log: Any | None = None,
) -> StructuredTool:
    def graph_find_relationship(
        workspace_id: str,
        knowledge_base_id: str,
        source_entity: str,
        target_entity: str,
        relationship_allowlist: list[str] | None = None,
        include_evidence: bool = True,
    ) -> dict[str, Any]:
        source = _resolve_entity(graph_query, workspace_id, knowledge_base_id, source_entity)
        target = _resolve_entity(graph_query, workspace_id, knowledge_base_id, target_entity)
        relationships = graph_query.find_direct_relationships(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            source_entity_id=getattr(source, "entity_id", source_entity),
            target_entity_id=getattr(target, "entity_id", target_entity),
            relationship_allowlist=relationship_allowlist or [],
            limit=20,
            include_evidence=include_evidence,
        )
        data = {
            "source": _summary(source),
            "target": _summary(target),
            "relationships": relationships,
            "ok": True,
            "empty": len(relationships) == 0,
            "warnings": _graph_query_warnings(graph_query),
        }
        return _finish("graph_find_relationship", data, audit_log)

    return StructuredTool.from_function(
        func=graph_find_relationship,
        name="graph_find_relationship",
        description="Find direct relationships between two scoped graph entities.",
        args_schema=GraphFindRelationshipArgs,
    )


def build_graph_find_paths_tool(
    *,
    graph_query: Any,
    audit_log: Any | None = None,
) -> StructuredTool:
    def graph_find_paths(
        workspace_id: str,
        knowledge_base_id: str,
        source_entity: str,
        target_entity: str,
        max_depth: int = 2,
        relationship_allowlist: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        safe_depth = _clamp(max_depth, 1, 2)
        source = _resolve_entity(graph_query, workspace_id, knowledge_base_id, source_entity)
        target = _resolve_entity(graph_query, workspace_id, knowledge_base_id, target_entity)
        paths = graph_query.find_paths(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            source_entity_id=getattr(source, "entity_id", source_entity),
            target_entity_id=getattr(target, "entity_id", target_entity),
            max_depth=safe_depth,
            relationship_allowlist=relationship_allowlist or [],
            limit=_clamp(limit, 1, 20),
        )
        data = {
            "paths": _limit_paths(paths, 20),
            "empty": len(paths) == 0,
            "warnings": _graph_query_warnings(graph_query),
        }
        return _finish("graph_find_paths", data, audit_log)

    return StructuredTool.from_function(
        func=graph_find_paths,
        name="graph_find_paths",
        description="Find scoped graph paths between two entities with a maximum depth of two.",
        args_schema=GraphFindPathsArgs,
    )


def build_graph_get_evidence_tool(
    *,
    graph_query: Any,
    object_store: Any | None = None,
    audit_log: Any | None = None,
) -> StructuredTool:
    def graph_get_evidence(
        workspace_id: str,
        knowledge_base_id: str,
        fact_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        include_chunk_text: bool = True,
        max_chars_per_chunk: int = 1200,
    ) -> dict[str, Any]:
        evidence = graph_query.get_evidence_refs(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            fact_ids=fact_ids or [],
            evidence_ids=evidence_ids or [],
            limit=50,
        )
        hydrated = [
            _hydrate_evidence(item, object_store, include_chunk_text, max_chars_per_chunk)
            for item in evidence
        ]
        return _finish(
            "graph_get_evidence",
            {"evidence": hydrated, "warnings": _graph_query_warnings(graph_query)},
            audit_log,
        )

    return StructuredTool.from_function(
        func=graph_get_evidence,
        name="graph_get_evidence",
        description="Return evidence records and optional source chunk text for graph facts.",
        args_schema=GraphGetEvidenceArgs,
    )


def build_graph_timeline_query_tool(
    *,
    graph_query: Any,
    audit_log: Any | None = None,
) -> StructuredTool:
    def graph_timeline_query(
        workspace_id: str,
        knowledge_base_id: str,
        entity: str,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        resolved = _resolve_entity(graph_query, workspace_id, knowledge_base_id, entity)
        events = graph_query.timeline_query(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            entity_id=getattr(resolved, "entity_id", entity),
            date_from=date_from,
            date_to=date_to,
            limit=_clamp(limit, 1, 50),
        )
        return _finish(
            "graph_timeline_query",
            {
                "entity": _summary(resolved),
                "events": events,
                "warnings": _graph_query_warnings(graph_query),
            },
            audit_log,
        )

    return StructuredTool.from_function(
        func=graph_timeline_query,
        name="graph_timeline_query",
        description="Return timeline-style graph facts for one scoped entity.",
        args_schema=GraphTimelineQueryArgs,
    )


def build_graph_readonly_cypher_tool(
    *,
    graph_query: Any,
    audit_log: Any | None = None,
) -> StructuredTool:
    def graph_readonly_cypher(
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        parameters: dict[str, Any] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if audit_log is not None and hasattr(audit_log, "write_tool_event"):
            audit_log.write_tool_event(
                "graph_readonly_cypher",
                result_summary={"has_execute_readonly": hasattr(graph_query, "execute_readonly")},
            )
        if not hasattr(graph_query, "execute_readonly"):
            return {
                "ok": False,
                "error_type": "graph_readonly_cypher_unavailable",
                "message_for_model": "Neo4j read-only Cypher is not configured for this workspace.",
                "retryable": False,
            }
        if "$workspace_id" not in query or "$knowledge_base_id" not in query:
            return {
                "ok": False,
                "error_type": "graph_readonly_scope_required",
                "message_for_model": (
                    "Read-only Cypher must explicitly filter with $workspace_id "
                    "and $knowledge_base_id."
                ),
                "retryable": False,
            }
        scoped_parameters = {
            **(parameters or {}),
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
        }
        return graph_query.execute_readonly(
            query,
            parameters=scoped_parameters,
            limit=_clamp(limit, 1, 200),
        )

    return StructuredTool.from_function(
        func=graph_readonly_cypher,
        name="graph_readonly_cypher",
        description=(
            "Execute one read-only Neo4j Cypher query. Only MATCH, OPTIONAL MATCH, "
            "WITH, UNWIND, and RETURN are allowed."
        ),
        args_schema=GraphReadonlyCypherArgs,
    )


def build_graphrag_search_tool(
    *,
    graph_query: Any,
    object_store: Any | None = None,
    embedding_client: Any | None = None,
    milvus: Any | None = None,
    vector_store: Any | None = None,
    kb_store: Any | None = None,
    knowledge_base_store: Any | None = None,
    audit_log: Any | None = None,
) -> StructuredTool:
    rag_tool = build_rag_search_tool(
        object_store=object_store,
        embedding_client=embedding_client,
        milvus=milvus,
        vector_store=vector_store,
        kb_store=kb_store,
        knowledge_base_store=knowledge_base_store,
    )

    def graphrag_search(
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        filters: dict[str, Any] | None = None,
        top_k: int = 50,
        final_top_k: int = 10,
        graph_depth: int = 2,
        relationship_allowlist: list[str] | None = None,
        include_sources: bool = True,
    ) -> dict[str, Any]:
        text_result = rag_tool.invoke(
            {
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "query": query,
                "top_k": top_k,
                "final_top_k": final_top_k,
                "filters": filters or {},
                "max_chars_per_chunk": 1200,
            }
        )
        text_data = text_result.get("data", text_result)
        text_evidence = list(text_data.get("text_evidence", []))
        warnings = [*list(text_data.get("warnings", [])), *_graph_query_warnings(graph_query)]
        graph_evidence = _graph_evidence_from_text_hits(
            graph_query=graph_query,
            object_store=object_store,
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            text_evidence=text_evidence,
            graph_depth=_clamp(graph_depth, 1, 2),
            relationship_allowlist=relationship_allowlist or [],
            include_sources=include_sources,
        )
        data = {
            "text_evidence": text_evidence,
            "graph_evidence": graph_evidence,
            "warnings": warnings,
        }
        return _finish("graphrag_search", data, audit_log)

    return StructuredTool.from_function(
        func=graphrag_search,
        name="graphrag_search",
        description="Combine document chunk retrieval with scoped graph evidence.",
        args_schema=GraphRagSearchArgs,
    )


def build_default_graph_tools(
    object_store: Any,
    *,
    embedding_client: Any | None = None,
    graph_query: Any | None = None,
    milvus: Any | None = None,
    vector_store: Any | None = None,
    kb_store: Any | None = None,
    knowledge_base_store: Any | None = None,
) -> list[StructuredTool]:
    graph_query = graph_query or ObjectStoreGraphQueryService(object_store)
    return [
        build_graph_schema_get_tool(graph_query=graph_query),
        build_graph_entity_search_tool(graph_query=graph_query),
        build_graph_expand_entity_tool(graph_query=graph_query),
        build_graph_find_relationship_tool(graph_query=graph_query),
        build_graph_find_paths_tool(graph_query=graph_query),
        build_graph_get_evidence_tool(graph_query=graph_query, object_store=object_store),
        build_graph_timeline_query_tool(graph_query=graph_query),
        build_graph_readonly_cypher_tool(graph_query=graph_query),
        build_graphrag_search_tool(
            graph_query=graph_query,
            object_store=object_store,
            embedding_client=embedding_client,
            milvus=milvus,
            vector_store=vector_store,
            kb_store=kb_store,
            knowledge_base_store=knowledge_base_store,
        ),
    ]


def _graph_evidence_from_text_hits(
    *,
    graph_query: Any,
    object_store: Any | None,
    workspace_id: str,
    knowledge_base_id: str,
    text_evidence: list[dict[str, Any]],
    graph_depth: int,
    relationship_allowlist: list[str],
    include_sources: bool,
) -> list[dict[str, Any]]:
    evidence_ids: set[str] = set()
    fact_ids: set[str] = set()
    chunk_ids: set[str] = set()
    graph_items: list[dict[str, Any]] = []
    for chunk in text_evidence:
        chunk_id = str(chunk.get("chunk_id") or "")
        if not chunk_id:
            continue
        chunk_ids.add(chunk_id)
        entities = graph_query.entities_by_chunk_id(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            chunk_id=chunk_id,
            limit=10,
        )
        for entity in entities:
            paths = graph_query.expand_entity(
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
                entity_id=entity["entity_id"],
                depth=graph_depth,
                relationship_allowlist=relationship_allowlist,
                limit=30,
                include_evidence=True,
            )
            for path in paths:
                graph_items.append(path)
                for relationship in path.get("relationships", []):
                    if relationship.get("fact_id"):
                        fact_ids.add(str(relationship["fact_id"]))
                    for evidence_id in relationship.get("evidence_ids", []):
                        evidence_ids.add(str(evidence_id))
    refs = graph_query.get_evidence_refs(
        workspace_id=workspace_id,
        knowledge_base_id=knowledge_base_id,
        fact_ids=sorted(fact_ids),
        evidence_ids=sorted(evidence_ids),
        limit=50,
    )
    chunk_refs = []
    if hasattr(graph_query, "evidence_by_chunk_ids") and chunk_ids:
        chunk_refs = graph_query.evidence_by_chunk_ids(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            chunk_ids=sorted(chunk_ids),
            limit=50,
            include_chunk_text=include_sources,
            max_chars_per_chunk=1200,
        )
    refs = _dedupe_evidence_refs([*chunk_refs, *refs])
    return [
        _hydrate_evidence(ref, object_store, include_sources, 1200)
        for ref in refs
    ] or graph_items


def _hydrate_evidence(
    item: dict[str, Any],
    object_store: Any | None,
    include_chunk_text: bool,
    max_chars: int,
) -> dict[str, Any]:
    hydrated = dict(item)
    object_key = item.get("chunk_object_key")
    if include_chunk_text and object_store is not None and object_key:
        try:
            chunk = _read_json(object_store, str(object_key))
        except Exception:  # noqa: BLE001 - evidence hydration is best effort.
            chunk = {}
        if chunk:
            hydrated["chunk_text"] = str(chunk.get("text") or "")[:max_chars]
            hydrated["source"] = chunk.get("source") or item.get("source") or {}
            hydrated["chunk_id"] = chunk.get("chunk_id") or item.get("chunk_id")
    return hydrated


def _resolve_entity(
    graph_query: Any,
    workspace_id: str,
    knowledge_base_id: str,
    value: str,
) -> Any:
    if hasattr(graph_query, "resolve_entity"):
        return graph_query.resolve_entity(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            name_or_id=value,
        )
    if hasattr(graph_query, "resolve_entity_id_or_fail"):
        return graph_query.resolve_entity_id_or_fail(value, workspace_id, knowledge_base_id)
    return {"entity_id": value, "name": value}


def _summary(entity: Any) -> dict[str, Any]:
    if hasattr(entity, "summary"):
        return entity.summary()
    if isinstance(entity, dict):
        return entity
    return {"entity_id": getattr(entity, "entity_id", str(entity)), "name": str(entity)}


def _read_json(object_store: Any, key: str) -> dict[str, Any]:
    if hasattr(object_store, "read_json"):
        return dict(object_store.read_json(key))
    import json

    return json.loads(object_store.read_text(key))


def _finish(tool_name: str, data: dict[str, Any], audit_log: Any | None = None) -> dict[str, Any]:
    if audit_log is not None and hasattr(audit_log, "write_tool_event"):
        audit_log.write_tool_event(tool_name, result_summary={"keys": sorted(data.keys())})
    return {"ok": True, "data": data}


def _limit_paths(paths: list[dict[str, Any]], max_limit: int) -> list[dict[str, Any]]:
    return [
        {**path, "depth": _clamp(int(path.get("depth") or 1), 1, 2)}
        for path in paths[:max_limit]
    ]


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


def _dedupe_evidence_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped = []
    for item in items:
        key = str(
            item.get("evidence_id")
            or item.get("fact_id")
            or item.get("source_chunk_id")
            or item.get("chunk_id")
            or item
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _graph_query_warnings(graph_query: Any) -> list[str]:
    if isinstance(graph_query, ObjectStoreGraphQueryService):
        return ["using_object_store_graph_fallback"]
    return []
