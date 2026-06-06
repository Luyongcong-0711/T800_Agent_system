from __future__ import annotations

import re
from typing import Any

from app.graph_pipeline.query import SCHEMA_SNAPSHOT, ResolvedEntity

READONLY_ERROR_TYPE = "neo4j_readonly_query_rejected"
QUERY_ERROR_TYPE = "neo4j_readonly_query_failed"

_FORBIDDEN_CLAUSE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD|CALL|IMPORT|EXPORT)\b",
    re.IGNORECASE,
)
_ALLOWED_START_PATTERN = re.compile(
    r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN)\b",
    re.IGNORECASE,
)
_PARAMETER_PATTERN = re.compile(r"(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)")


class Neo4jReadOnlyQueryAdapter:
    def __init__(
        self,
        *,
        driver: Any,
        database: str | None = None,
        default_limit: int = 50,
        max_limit: int = 200,
    ) -> None:
        self.driver = driver
        self.database = database
        self.default_limit = default_limit
        self.max_limit = max_limit

    @classmethod
    def from_uri(
        cls,
        *,
        uri: str,
        username: str,
        password: str,
        database: str | None = None,
        default_limit: int = 50,
        max_limit: int = 200,
    ) -> Neo4jReadOnlyQueryAdapter:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(username, password))
        return cls(
            driver=driver,
            database=database,
            default_limit=default_limit,
            max_limit=max_limit,
        )

    def execute_readonly(
        self,
        query: str,
        *,
        parameters: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        safe_limit = _clamp_limit(
            self.default_limit if limit is None else limit,
            max_limit=self.max_limit,
        )
        if parameters is None:
            safe_parameters: dict[str, Any] = {}
        elif isinstance(parameters, dict):
            safe_parameters = parameters
        else:
            return _error_result(READONLY_ERROR_TYPE, "Cypher parameters must be an object.")
        execute_parameters = {**safe_parameters, "limit": safe_limit}
        validation_error = validate_readonly_cypher(query, execute_parameters)
        if validation_error is not None:
            return validation_error

        execute_kwargs: dict[str, Any] = {
            "parameters_": execute_parameters,
            "routing_": "r",
        }
        if self.database:
            execute_kwargs["database_"] = self.database
        try:
            records, summary, keys = self.driver.execute_query(
                query,
                **execute_kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - adapter boundary returns ToolResult-style errors.
            return _error_result(
                QUERY_ERROR_TYPE,
                "Neo4j read-only query failed.",
                retryable=True,
                detail=exc.__class__.__name__,
            )

        return {
            "ok": True,
            "data": {
                "records": [_record_to_dict(record) for record in records],
                "keys": list(keys or []),
                "limit": safe_limit,
                "counters": _summary_counters(summary),
            },
        }

    def get_schema_snapshot(self, workspace_id: str, knowledge_base_id: str) -> dict[str, Any]:
        _ = workspace_id, knowledge_base_id
        return {
            **SCHEMA_SNAPSHOT,
            "backend": "neo4j_readonly",
            "readonly": True,
        }

    def entity_search(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 10,
        include_aliases: bool = True,
    ) -> list[dict[str, Any]]:
        result = self.execute_readonly(
            """
            MATCH (e:GraphEntity)
            WHERE e.workspace_id = $workspace_id
              AND e.knowledge_base_id = $knowledge_base_id
              AND ($entity_types = [] OR e.entity_type IN $entity_types)
              AND (
                toLower(coalesce(e.name, '')) CONTAINS toLower($query)
                OR (
                  $include_aliases = true
                  AND any(alias IN coalesce(e.aliases, [])
                    WHERE toLower(toString(alias)) CONTAINS toLower($query))
                )
              )
            RETURN properties(e) AS entity,
              CASE
                WHEN toLower(coalesce(e.name, '')) = toLower($query) THEN 1.0
                ELSE 0.7
              END AS score
            ORDER BY score DESC, e.entity_id ASC
            LIMIT $limit
            """,
            parameters={
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "query": query,
                "entity_types": entity_types or [],
                "include_aliases": include_aliases,
            },
            limit=limit,
        )
        entities = []
        for record in _records(result):
            entity = _record_value(record, "entity")
            if not isinstance(entity, dict):
                continue
            score = record.get("score")
            entities.append(
                {
                    **entity,
                    "score": score if isinstance(score, int | float) else 0.7,
                    "match_type": "name_or_alias",
                }
            )
        return entities

    def resolve_entity(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        name_or_id: str,
        entity_types: list[str] | None = None,
    ) -> ResolvedEntity:
        by_id = self.execute_readonly(
            """
            MATCH (e:GraphEntity {entity_id: $entity_id})
            WHERE e.workspace_id = $workspace_id
              AND e.knowledge_base_id = $knowledge_base_id
            RETURN properties(e) AS entity
            LIMIT $limit
            """,
            parameters={
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "entity_id": name_or_id,
            },
            limit=1,
        )
        first = _first_record(by_id)
        if first:
            entity = _record_value(first, "entity")
            if isinstance(entity, dict):
                return ResolvedEntity(entity)

        hits = self.entity_search(
            workspace_id,
            knowledge_base_id,
            name_or_id,
            entity_types=entity_types,
            limit=5,
        )
        if not hits:
            return ResolvedEntity({"entity_id": name_or_id, "name": name_or_id}, [])
        resolved = ResolvedEntity(hits[0], candidates=hits)
        resolved.is_ambiguous = len(hits) > 1 and hits[0].get("score") == hits[1].get("score")
        return resolved

    def resolve_entity_id_or_fail(
        self,
        entity_id: str,
        workspace_id: str,
        knowledge_base_id: str,
    ) -> ResolvedEntity:
        return self.resolve_entity(workspace_id, knowledge_base_id, entity_id)

    def entities_by_chunk_id(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        chunk_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        result = self.execute_readonly(
            """
            MATCH (e:GraphEntity)
            WHERE e.workspace_id = $workspace_id
              AND e.knowledge_base_id = $knowledge_base_id
              AND (
                $chunk_id IN coalesce(e.source_chunk_ids, [])
                OR EXISTS {
                  MATCH (m:GraphMention)-[:MENTIONS_ENTITY]->(e)
                  WHERE m.chunk_id = $chunk_id
                    AND m.workspace_id = $workspace_id
                    AND m.knowledge_base_id = $knowledge_base_id
                }
              )
            RETURN properties(e) AS entity
            ORDER BY e.entity_id ASC
            LIMIT $limit
            """,
            parameters={
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "chunk_id": chunk_id,
            },
            limit=limit,
        )
        return [
            entity
            for record in _records(result)
            if isinstance((entity := _record_value(record, "entity")), dict)
        ]

    def expand_entity(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        entity_id: str,
        depth: int = 1,
        relationship_allowlist: list[str] | None = None,
        limit: int = 30,
        include_evidence: bool = True,
    ) -> list[dict[str, Any]]:
        _ = include_evidence
        safe_depth = _clamp_limit(depth, max_limit=2)
        result = self.execute_readonly(
            f"""
            MATCH path =
              (start:GraphEntity {{entity_id: $entity_id}})
              -[rels:GRAPH_RELATION*1..{safe_depth}]->
              (target:GraphEntity)
            WHERE start.workspace_id = $workspace_id
              AND start.knowledge_base_id = $knowledge_base_id
              AND all(rel IN rels WHERE $relationship_allowlist = []
                OR rel.predicate IN $relationship_allowlist)
            RETURN
              [node IN nodes(path) | properties(node)] AS nodes,
              [rel IN relationships(path) | properties(rel)] AS relationships,
              length(path) AS depth
            ORDER BY depth ASC
            LIMIT $limit
            """,
            parameters={
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "entity_id": entity_id,
                "relationship_allowlist": relationship_allowlist or [],
            },
            limit=limit,
        )
        return [
            _path_from_record(record, source_entity_id=entity_id)
            for record in _records(result)
        ]

    def find_direct_relationships(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        source_entity_id: str,
        target_entity_id: str,
        relationship_allowlist: list[str] | None = None,
        limit: int = 20,
        include_evidence: bool = True,
    ) -> list[dict[str, Any]]:
        result = self.execute_readonly(
            """
            MATCH
              (source:GraphEntity {entity_id: $source_entity_id})
              -[rel:GRAPH_RELATION]->
              (target:GraphEntity {entity_id: $target_entity_id})
            WHERE source.workspace_id = $workspace_id
              AND source.knowledge_base_id = $knowledge_base_id
              AND ($relationship_allowlist = [] OR rel.predicate IN $relationship_allowlist)
            RETURN properties(rel) AS relationship
            ORDER BY rel.fact_id ASC
            LIMIT $limit
            """,
            parameters={
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "relationship_allowlist": relationship_allowlist or [],
            },
            limit=limit,
        )
        relationships = []
        for record in _records(result):
            relationship = _record_value(record, "relationship")
            if isinstance(relationship, dict):
                relationships.append(
                    _relationship_record(
                        relationship,
                        source_entity_id=source_entity_id,
                        target_entity_id=target_entity_id,
                        include_evidence=include_evidence,
                    )
                )
        return relationships

    def find_paths(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        source_entity_id: str,
        target_entity_id: str,
        max_depth: int = 2,
        relationship_allowlist: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        safe_depth = _clamp_limit(max_depth, max_limit=2)
        result = self.execute_readonly(
            f"""
            MATCH path =
              (source:GraphEntity {{entity_id: $source_entity_id}})
              -[rels:GRAPH_RELATION*1..{safe_depth}]->
              (target:GraphEntity {{entity_id: $target_entity_id}})
            WHERE source.workspace_id = $workspace_id
              AND source.knowledge_base_id = $knowledge_base_id
              AND (
                $relationship_allowlist = []
                OR all(rel IN rels WHERE rel.predicate IN $relationship_allowlist)
              )
            RETURN
              [node IN nodes(path) | properties(node)] AS nodes,
              [rel IN relationships(path) | properties(rel)] AS relationships,
              length(path) AS depth
            ORDER BY depth ASC
            LIMIT $limit
            """,
            parameters={
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "relationship_allowlist": relationship_allowlist or [],
            },
            limit=limit,
        )
        return [
            _path_from_record(
                record,
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
            )
            for record in _records(result)
        ]

    def get_evidence_refs(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        fact_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        limit: int = 50,
        include_chunk_text: bool = False,
        max_chars_per_chunk: int = 1200,
    ) -> list[dict[str, Any]]:
        _ = include_chunk_text, max_chars_per_chunk
        fact_ids = fact_ids or []
        evidence_ids = evidence_ids or []
        if not fact_ids and not evidence_ids:
            return []
        result = self.execute_readonly(
            """
            MATCH (ev:GraphEvidence)
            WHERE ev.workspace_id = $workspace_id
              AND ev.knowledge_base_id = $knowledge_base_id
              AND (
                ($fact_ids <> [] AND ev.fact_id IN $fact_ids)
                OR ($evidence_ids <> [] AND ev.evidence_id IN $evidence_ids)
              )
            RETURN properties(ev) AS evidence
            ORDER BY ev.evidence_id ASC
            LIMIT $limit
            """,
            parameters={
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "fact_ids": fact_ids,
                "evidence_ids": evidence_ids,
            },
            limit=limit,
        )
        return [
            evidence
            for record in _records(result)
            if isinstance((evidence := _record_value(record, "evidence")), dict)
        ]

    def evidence_by_chunk_ids(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        chunk_ids: list[str],
        limit: int = 50,
        include_chunk_text: bool = False,
        max_chars_per_chunk: int = 1200,
    ) -> list[dict[str, Any]]:
        _ = include_chunk_text, max_chars_per_chunk
        result = self.execute_readonly(
            """
            MATCH (ev:GraphEvidence)
            WHERE ev.workspace_id = $workspace_id
              AND ev.knowledge_base_id = $knowledge_base_id
              AND (ev.chunk_id IN $chunk_ids OR ev.source_chunk_id IN $chunk_ids)
            RETURN properties(ev) AS evidence
            ORDER BY ev.evidence_id ASC
            LIMIT $limit
            """,
            parameters={
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "chunk_ids": chunk_ids,
            },
            limit=limit,
        )
        return [
            evidence
            for record in _records(result)
            if isinstance((evidence := _record_value(record, "evidence")), dict)
        ]

    def timeline_query(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        entity_id: str,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        _ = date_from, date_to
        paths = self.expand_entity(
            workspace_id,
            knowledge_base_id,
            entity_id,
            depth=1,
            limit=limit,
        )
        return [{"event_id": path["path_id"], "summary": path} for path in paths]


def validate_readonly_cypher(
    query: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(query, str) or not query.strip():
        return _error_result(READONLY_ERROR_TYPE, "Cypher query is required.")
    if parameters is not None and not isinstance(parameters, dict):
        return _error_result(READONLY_ERROR_TYPE, "Cypher parameters must be an object.")

    stripped = _strip_cypher_comments_and_literals(query)
    if ";" in stripped:
        return _error_result(READONLY_ERROR_TYPE, "Multiple Cypher statements are not allowed.")
    if not _ALLOWED_START_PATTERN.search(stripped):
        return _error_result(
            READONLY_ERROR_TYPE,
            "Only read-only MATCH, OPTIONAL MATCH, WITH, UNWIND, or RETURN queries are allowed.",
        )
    forbidden = _FORBIDDEN_CLAUSE_PATTERN.search(stripped)
    if forbidden is not None:
        return _error_result(
            READONLY_ERROR_TYPE,
            "Cypher clause is not allowed in read-only graph queries: "
            f"{forbidden.group(1).upper()}.",
        )

    missing_parameters = sorted(
        name for name in set(_PARAMETER_PATTERN.findall(stripped)) if name not in (parameters or {})
    )
    if missing_parameters:
        return _error_result(
            READONLY_ERROR_TYPE,
            "Cypher query parameters must be supplied separately.",
            detail={"missing_parameters": missing_parameters},
        )
    return None


def _strip_cypher_comments_and_literals(query: str) -> str:
    result: list[str] = []
    index = 0
    length = len(query)
    while index < length:
        char = query[index]
        next_char = query[index + 1] if index + 1 < length else ""
        if char == "/" and next_char == "/":
            index += 2
            while index < length and query[index] not in "\r\n":
                index += 1
            result.append(" ")
            continue
        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < length and not (query[index] == "*" and query[index + 1] == "/"):
                index += 1
            index = min(length, index + 2)
            result.append(" ")
            continue
        if char in {"'", '"', "`"}:
            index = _skip_quoted(query, index, char)
            result.append(" ")
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _skip_quoted(query: str, start: int, quote: str) -> int:
    index = start + 1
    while index < len(query):
        char = query[index]
        if char == "\\" and quote != "`":
            index += 2
            continue
        if char == quote:
            if index + 1 < len(query) and query[index + 1] == quote:
                index += 2
                continue
            return index + 1
        index += 1
    return len(query)


def _record_to_dict(record: Any) -> dict[str, Any]:
    if hasattr(record, "data"):
        data = record.data()
        if isinstance(data, dict):
            return data
    if isinstance(record, dict):
        return dict(record)
    try:
        return dict(record)
    except (TypeError, ValueError):
        return {"value": record}


def _summary_counters(summary: Any) -> dict[str, Any]:
    counters = getattr(summary, "counters", None)
    if counters is None:
        return {}
    if hasattr(counters, "_asdict"):
        return dict(counters._asdict())
    if isinstance(counters, dict):
        return dict(counters)
    return {}


def _clamp_limit(value: int, *, max_limit: int) -> int:
    return max(1, min(int(value), max_limit))


def _records(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not bool(result.get("ok")):
        return []
    records = result.get("data", {}).get("records", [])
    return [record for record in records if isinstance(record, dict)]


def _first_record(result: dict[str, Any]) -> dict[str, Any] | None:
    records = _records(result)
    return records[0] if records else None


def _record_value(record: dict[str, Any], key: str) -> Any:
    value = record.get(key)
    if hasattr(value, "items"):
        return dict(value)
    return value


def _relationship_record(
    rel: dict[str, Any],
    *,
    source_entity_id: str | None = None,
    target_entity_id: str | None = None,
    include_evidence: bool = True,
) -> dict[str, Any]:
    record = {
        "type": rel.get("predicate"),
        "direction": rel.get("direction", "outgoing"),
        "fact_id": rel.get("fact_id"),
        "source_entity_id": (
            rel.get("source_entity_id") or rel.get("subject_entity_id") or source_entity_id
        ),
        "target_entity_id": (
            rel.get("target_entity_id") or rel.get("object_entity_id") or target_entity_id
        ),
        "confidence": rel.get("confidence"),
        "relation_strength": rel.get("relation_strength"),
    }
    if include_evidence:
        record["evidence_ids"] = rel.get("evidence_ids", [])
    return record


def _path_from_record(
    record: dict[str, Any],
    *,
    source_entity_id: str,
    target_entity_id: str | None = None,
) -> dict[str, Any]:
    nodes = record.get("nodes")
    relationships = record.get("relationships")
    if not isinstance(nodes, list):
        nodes = []
    if not isinstance(relationships, list):
        relationships = []
    normalized_relationships = []
    for index, relationship in enumerate(relationships):
        rel = dict(relationship) if isinstance(relationship, dict) else {}
        source = (
            nodes[index].get("entity_id")
            if index < len(nodes) and isinstance(nodes[index], dict)
            else None
        )
        target = (
            nodes[index + 1].get("entity_id")
            if index + 1 < len(nodes) and isinstance(nodes[index + 1], dict)
            else None
        )
        normalized_relationships.append(
            _relationship_record(rel, source_entity_id=source, target_entity_id=target)
        )
    relationship_ids = [
        str(item.get("fact_id")) for item in normalized_relationships if item.get("fact_id")
    ]
    return {
        "path_id": "path_" + "_".join(relationship_ids or [source_entity_id]),
        "depth": _clamp_limit(
            int(record.get("depth") or len(normalized_relationships) or 1),
            max_limit=2,
        ),
        "source_entity_id": source_entity_id,
        "target_entity_id": target_entity_id or _last_entity_id(nodes),
        "nodes": [dict(node) for node in nodes if isinstance(node, dict)],
        "relationships": normalized_relationships,
        "direction_preserved": True,
    }


def _last_entity_id(nodes: list[Any]) -> str | None:
    for node in reversed(nodes):
        if isinstance(node, dict) and node.get("entity_id"):
            return str(node["entity_id"])
    return None


def _error_result(
    error_type: str,
    message: str,
    *,
    retryable: bool = False,
    detail: Any | None = None,
) -> dict[str, Any]:
    error = {
        "ok": False,
        "error_type": error_type,
        "message_for_model": message,
        "retryable": retryable,
    }
    if detail is not None:
        error["detail"] = detail
    return error
