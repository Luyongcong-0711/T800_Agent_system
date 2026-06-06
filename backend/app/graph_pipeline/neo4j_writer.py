from __future__ import annotations

from typing import Any


class Neo4jGraphWriter:
    def __init__(self, *, driver: Any) -> None:
        self.driver = driver

    @classmethod
    def from_uri(
        cls,
        *,
        uri: str,
        username: str,
        password: str,
        database: str | None = None,
    ) -> Neo4jGraphWriter:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(username, password))
        return cls(driver=_DatabaseDriver(driver, database=database))

    def write_graph_batch_internal(
        self,
        batch: dict[str, Any],
        *,
        operation_id: str | None = None,
        caller_type: str = "graph_build_job",
        job_id: str | None = None,
    ) -> dict[str, Any]:
        audit = {
            "operation_id": operation_id,
            "caller_type": caller_type,
            "job_id": job_id,
        }
        entities = [
            _with_audit(item, batch, **audit)
            for item in batch.get("entities", [])
            if isinstance(item, dict)
        ]
        mentions = [
            _with_audit(item, batch, **audit)
            for item in batch.get("mentions", [])
            if isinstance(item, dict)
        ]
        relation_facts = [
            _with_audit(item, batch, **audit)
            for item in batch.get("relation_facts", [])
            if isinstance(item, dict)
        ]
        evidence = [
            _with_audit(item, batch, **audit)
            for item in batch.get("evidence", [])
            if isinstance(item, dict)
        ]
        self.driver.execute_query(_MERGE_ENTITIES, entities=entities)
        self.driver.execute_query(_MERGE_MENTIONS, mentions=mentions)
        self.driver.execute_query(_MERGE_RELATION_FACTS, relation_facts=relation_facts)
        self.driver.execute_query(_MERGE_EVIDENCE, evidence=evidence)
        return {
            "ok": True,
            "backend": "neo4j",
            "entity_count": len(entities),
            "mention_count": len(mentions),
            "relation_fact_count": len(relation_facts),
            "evidence_count": len(evidence),
        }

    def write_graph_batch(
        self,
        *,
        batch: dict[str, Any],
        operation_id: str | None = None,
        caller_type: str = "graph_build_job",
        job_id: str | None = None,
    ) -> dict[str, Any]:
        return self.write_graph_batch_internal(
            batch,
            operation_id=operation_id,
            caller_type=caller_type,
            job_id=job_id,
        )

    def upsert_memory(
        self,
        *,
        record: dict[str, Any],
        operation_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        memory = _memory_node(record, operation_id=operation_id, job_id=job_id)
        self.driver.execute_query(_MERGE_MEMORY, memory=memory)
        if memory.get("user_id"):
            self.driver.execute_query(
                _MERGE_USER_MEMORY_LINK,
                user_id=memory["user_id"],
                memory_id=memory["memory_id"],
            )
        return {
            "ok": True,
            "backend": "neo4j",
            "memory_id": memory["memory_id"],
            "action": "upsert",
        }

    def delete_memory(
        self,
        *,
        memory_id: str,
        operation_id: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        self.driver.execute_query(
            _DELETE_MEMORY,
            memory_id=memory_id,
            operation_id=operation_id,
            job_id=job_id,
        )
        return {
            "ok": True,
            "backend": "neo4j",
            "memory_id": memory_id,
            "action": "delete",
        }


class _DatabaseDriver:
    def __init__(self, driver: Any, *, database: str | None = None) -> None:
        self.driver = driver
        self.database = database

    def execute_query(self, query: str, **params: Any) -> Any:
        if self.database:
            return self.driver.execute_query(query, database_=self.database, **params)
        return self.driver.execute_query(query, **params)


def _with_audit(
    item: dict[str, Any],
    batch: dict[str, Any],
    *,
    operation_id: str | None,
    caller_type: str,
    job_id: str | None,
) -> dict[str, Any]:
    normalized = {
        **item,
        "caller_type": caller_type,
        "doc_id": item.get("doc_id") or batch.get("doc_id"),
        "doc_version_id": item.get("doc_version_id") or batch.get("doc_version_id"),
        "job_id": job_id,
        "knowledge_base_id": item.get("knowledge_base_id") or batch.get("knowledge_base_id"),
        "operation_id": operation_id,
        "workspace_id": item.get("workspace_id") or batch.get("workspace_id"),
    }
    if (
        normalized.get("label") == "RelationFact"
        or normalized.get("subject_entity_id")
        or normalized.get("object_entity_id")
    ):
        normalized["source_entity_id"] = (
            normalized.get("source_entity_id") or normalized.get("subject_entity_id")
        )
        normalized["target_entity_id"] = (
            normalized.get("target_entity_id") or normalized.get("object_entity_id")
        )
    return normalized


def _memory_node(
    record: dict[str, Any],
    *,
    operation_id: str | None,
    job_id: str | None,
) -> dict[str, Any]:
    return {
        "memory_id": record["memory_id"],
        "workspace_id": record.get("workspace_id"),
        "user_id": record.get("user_id"),
        "scope": record.get("scope"),
        "type": record.get("type"),
        "field": record.get("field"),
        "value": record.get("value"),
        "summary": record.get("summary"),
        "content_object_key": record.get("content_object_key"),
        "status": record.get("status"),
        "enabled_for_model_context": bool(record.get("enabled_for_model_context")),
        "operation_id": operation_id,
        "job_id": job_id,
    }


_MERGE_ENTITIES = """
UNWIND $entities AS entity
MERGE (e:GraphEntity {entity_id: entity.entity_id})
SET e += entity
"""

_MERGE_MENTIONS = """
UNWIND $mentions AS mention
MERGE (m:GraphMention {mention_id: mention.mention_id})
SET m += mention
WITH mention, m
MERGE (e:GraphEntity {entity_id: mention.entity_id})
MERGE (m)-[:MENTIONS_ENTITY]->(e)
"""

_MERGE_RELATION_FACTS = """
UNWIND $relation_facts AS fact
MERGE (source:GraphEntity {entity_id: fact.source_entity_id})
MERGE (target:GraphEntity {entity_id: fact.target_entity_id})
MERGE (source)-[rel:GRAPH_RELATION {fact_id: fact.fact_id}]->(target)
SET rel += fact
"""

_MERGE_EVIDENCE = """
UNWIND $evidence AS evidence
MERGE (ev:GraphEvidence {evidence_id: evidence.evidence_id})
SET ev += evidence
"""

_MERGE_MEMORY = """
MERGE (m:Memory {memory_id: $memory.memory_id})
SET m += $memory,
    m.deleted = false
"""

_MERGE_USER_MEMORY_LINK = """
MERGE (u:User {user_id: $user_id})
MERGE (m:Memory {memory_id: $memory_id})
MERGE (u)-[:HAS_MEMORY]->(m)
"""

_DELETE_MEMORY = """
MATCH (m:Memory {memory_id: $memory_id})
SET m.deleted = true,
    m.status = 'deleted',
    m.enabled_for_model_context = false,
    m.deleted_operation_id = $operation_id,
    m.deleted_job_id = $job_id
"""
