from __future__ import annotations

from collections import deque
from typing import Any

from app.graph_pipeline.paths import graph_index_key
from app.rag_pipeline.ingestion import DocumentIngestionService
from app.storage.object_store import JsonObjectStore, ObjectStore

SCHEMA_SNAPSHOT = {
    "labels": [
        "Document",
        "DocumentVersion",
        "Chunk",
        "Mention",
        "Entity",
        "RelationFact",
        "Evidence",
    ],
    "relationships": [
        "CHUNK_HAS_MENTION",
        "MENTION_REFERS_TO_ENTITY",
        "RELATION_SUBJECT",
        "RELATION_OBJECT",
        "RELATION_SUPPORTED_BY",
        "EVIDENCE_FROM_CHUNK",
    ],
    "properties": {
        "Entity": ["entity_id", "name", "aliases", "entity_type"],
        "RelationFact": ["fact_id", "predicate", "confidence", "relation_strength"],
        "Evidence": ["evidence_id", "source_chunk_id", "evidence_text"],
    },
    "allowed_depth": 2,
    "readonly": True,
}


class ResolvedEntity:
    def __init__(
        self,
        entity: dict[str, Any],
        candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        self.entity = entity
        self.entity_id = str(entity.get("entity_id") or "")
        self.is_ambiguous = False
        self.candidates = candidates or []

    def summary(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.entity.get("name") or self.entity_id,
            "entity_type": self.entity.get("entity_type"),
            "evidence_count": self.entity.get("evidence_count", 0),
        }


class ObjectStoreGraphQueryService:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)

    def get_schema_snapshot(self, workspace_id: str, knowledge_base_id: str) -> dict[str, Any]:
        _ = workspace_id, knowledge_base_id
        return dict(SCHEMA_SNAPSHOT)

    def entity_search(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 10,
        include_aliases: bool = True,
    ) -> list[dict[str, Any]]:
        index = self._index(workspace_id, knowledge_base_id)
        normalized = query.casefold()
        allowed = {item.casefold() for item in entity_types or []}
        hits: list[tuple[float, dict[str, Any]]] = []
        for entity in index.get("entities", []):
            if allowed and str(entity.get("entity_type", "")).casefold() not in allowed:
                continue
            name = str(entity.get("name") or "")
            aliases = [str(item) for item in entity.get("aliases", [])]
            haystack = [name, *(aliases if include_aliases else [])]
            score = _entity_score(normalized, haystack)
            if score <= 0:
                continue
            hits.append((score, {**entity, "score": score, "match_type": "name_or_alias"}))
        hits.sort(key=lambda item: (-item[0], str(item[1].get("entity_id"))))
        return [item[1] for item in hits[: max(1, min(limit, 20))]]

    def resolve_entity(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        name_or_id: str,
        entity_types: list[str] | None = None,
    ) -> ResolvedEntity:
        index = self._index(workspace_id, knowledge_base_id)
        for entity in index.get("entities", []):
            if entity.get("entity_id") == name_or_id:
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
        index = self._index(workspace_id, knowledge_base_id)
        entity_ids = {
            mention.get("entity_id")
            for mention in index.get("mentions", [])
            if mention.get("chunk_id") == chunk_id
        }
        return [
            entity
            for entity in index.get("entities", [])
            if entity.get("entity_id") in entity_ids
        ][: max(1, min(limit, 20))]

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
        resolved = self.resolve_entity(workspace_id, knowledge_base_id, entity_id)
        index = self._index(workspace_id, knowledge_base_id)
        safe_depth = max(1, min(depth, 2))
        paths = _find_paths_from_index(
            index=index,
            source_entity_id=resolved.entity_id,
            target_entity_id=None,
            max_depth=safe_depth,
            relationship_allowlist=relationship_allowlist or [],
            limit=limit,
            include_evidence=include_evidence,
        )
        return paths[: max(1, min(limit, 50))]

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
        index = self._index(workspace_id, knowledge_base_id)
        allowed = set(relationship_allowlist or [])
        relationships: list[dict[str, Any]] = []
        for fact in index.get("relation_facts", []):
            if allowed and fact.get("predicate") not in allowed:
                continue
            if (
                fact.get("subject_entity_id") == source_entity_id
                and fact.get("object_entity_id") == target_entity_id
            ):
                relationships.append(_relationship_record(fact, include_evidence))
        return relationships[: max(1, min(limit, 20))]

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
        index = self._index(workspace_id, knowledge_base_id)
        return _find_paths_from_index(
            index=index,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            max_depth=max(1, min(max_depth, 2)),
            relationship_allowlist=relationship_allowlist or [],
            limit=limit,
        )

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
        index = self._index(workspace_id, knowledge_base_id)
        fact_set = set(fact_ids or [])
        evidence_set = set(evidence_ids or [])
        evidence = []
        for item in index.get("evidence", []):
            if fact_set and item.get("fact_id") not in fact_set:
                continue
            if evidence_set and item.get("evidence_id") not in evidence_set:
                continue
            if not fact_set and not evidence_set:
                continue
            evidence.append(
                self._hydrate_evidence(
                    workspace_id,
                    knowledge_base_id,
                    item,
                    include_chunk_text=include_chunk_text,
                    max_chars_per_chunk=max_chars_per_chunk,
                )
            )
        return evidence[: max(1, min(limit, 50))]

    def evidence_by_chunk_ids(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        chunk_ids: list[str],
        limit: int = 50,
        include_chunk_text: bool = False,
        max_chars_per_chunk: int = 1200,
    ) -> list[dict[str, Any]]:
        index = self._index(workspace_id, knowledge_base_id)
        chunk_set = {str(chunk_id) for chunk_id in chunk_ids if chunk_id}
        evidence = []
        for item in index.get("evidence", []):
            if not isinstance(item, dict):
                continue
            item_chunk_id = item.get("chunk_id") or item.get("source_chunk_id")
            if str(item_chunk_id or "") not in chunk_set:
                continue
            evidence.append(
                self._hydrate_evidence(
                    workspace_id,
                    knowledge_base_id,
                    item,
                    include_chunk_text=include_chunk_text,
                    max_chars_per_chunk=max_chars_per_chunk,
                )
            )
        return evidence[: max(1, min(limit, 50))]

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

    def _index(self, workspace_id: str, knowledge_base_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            graph_index_key(workspace_id, knowledge_base_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "entities": [],
                "mentions": [],
                "relation_facts": [],
                "evidence": [],
            },
        )

    def _hydrate_evidence(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        item: dict[str, Any],
        *,
        include_chunk_text: bool,
        max_chars_per_chunk: int,
    ) -> dict[str, Any]:
        hydrated = dict(item)
        if not include_chunk_text:
            return hydrated

        chunk = self._read_evidence_chunk(workspace_id, knowledge_base_id, item)
        if not chunk:
            return hydrated

        hydrated["chunk_text"] = str(chunk.get("text") or "")[:max(1, max_chars_per_chunk)]
        hydrated["source"] = chunk.get("source") or item.get("source") or {}
        hydrated["chunk_id"] = (
            chunk.get("chunk_id") or item.get("chunk_id") or item.get("source_chunk_id")
        )
        hydrated["doc_id"] = chunk.get("doc_id") or item.get("doc_id")
        hydrated["doc_version_id"] = chunk.get("doc_version_id") or item.get("doc_version_id")
        hydrated["chunk_object_key"] = (
            chunk.get("object_key") or item.get("chunk_object_key") or item.get("object_key")
        )
        return hydrated

    def _read_evidence_chunk(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        object_key = item.get("chunk_object_key") or item.get("object_key")
        if object_key:
            try:
                return self.json_store.read_json(str(object_key))
            except Exception:  # noqa: BLE001 - evidence hydration is best effort.
                return {}

        chunk_id = item.get("chunk_id") or item.get("source_chunk_id")
        if not chunk_id:
            return {}
        try:
            return DocumentIngestionService(self.object_store).get_chunk(
                workspace_id,
                knowledge_base_id,
                chunk_id=str(chunk_id),
            )
        except Exception:  # noqa: BLE001 - evidence hydration is best effort.
            return {}


def _entity_score(query: str, values: list[str]) -> float:
    for value in values:
        normalized = value.casefold()
        if normalized == query:
            return 1.0
        if query and query in normalized:
            return 0.7
    return 0


def _relationship_record(fact: dict[str, Any], include_evidence: bool = True) -> dict[str, Any]:
    record = {
        "type": fact.get("predicate"),
        "direction": fact.get("direction", "outgoing"),
        "fact_id": fact.get("fact_id"),
        "source_entity_id": fact.get("subject_entity_id"),
        "target_entity_id": fact.get("object_entity_id"),
        "confidence": fact.get("confidence"),
        "relation_strength": fact.get("relation_strength"),
    }
    if include_evidence:
        record["evidence_ids"] = fact.get("evidence_ids", [])
    return record


def _find_paths_from_index(
    *,
    index: dict[str, Any],
    source_entity_id: str,
    target_entity_id: str | None,
    max_depth: int,
    relationship_allowlist: list[str],
    limit: int,
    include_evidence: bool = True,
) -> list[dict[str, Any]]:
    allowed = set(relationship_allowlist or [])
    adjacency: dict[str, list[dict[str, Any]]] = {}
    entity_lookup = {
        str(entity.get("entity_id")): entity for entity in index.get("entities", [])
    }
    for fact in index.get("relation_facts", []):
        if allowed and fact.get("predicate") not in allowed:
            continue
        adjacency.setdefault(str(fact.get("subject_entity_id")), []).append(fact)

    queue: deque[tuple[str, list[dict[str, Any]]]] = deque([(source_entity_id, [])])
    found: list[dict[str, Any]] = []
    while queue and len(found) < max(1, min(limit, 50)):
        current_entity_id, facts = queue.popleft()
        if len(facts) >= max_depth:
            continue
        for fact in adjacency.get(current_entity_id, []):
            next_entity_id = str(fact.get("object_entity_id"))
            if any(item.get("subject_entity_id") == next_entity_id for item in facts):
                continue
            next_facts = [*facts, fact]
            if target_entity_id is None or next_entity_id == target_entity_id:
                found.append(
                    _path_record(
                        source_entity_id,
                        next_entity_id,
                        next_facts,
                        entity_lookup,
                        include_evidence=include_evidence,
                    )
                )
            queue.append((next_entity_id, next_facts))
    return found


def _path_record(
    source_entity_id: str,
    target_entity_id: str,
    facts: list[dict[str, Any]],
    entity_lookup: dict[str, dict[str, Any]],
    *,
    include_evidence: bool,
) -> dict[str, Any]:
    nodes = [entity_lookup.get(source_entity_id, {"entity_id": source_entity_id})]
    relationships = []
    for fact in facts:
        relationships.append(_relationship_record(fact, include_evidence))
        nodes.append(
            entity_lookup.get(
                str(fact.get("object_entity_id")),
                {"entity_id": fact.get("object_entity_id")},
            )
        )
    return {
        "path_id": "path_" + "_".join(str(item.get("fact_id")) for item in facts),
        "depth": len(facts),
        "source_entity_id": source_entity_id,
        "target_entity_id": target_entity_id,
        "nodes": nodes,
        "relationships": relationships,
        "direction_preserved": True,
    }
