from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from langchain_core.output_parsers import JsonOutputParser

from app.schemas.model import ModelConfig, ModelMessage, ModelRequest

TITLE_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3}\b")
PARTY_RE = re.compile(r"\bParty\s+[A-Z]\b")
CJK_ENTITY_RE = re.compile(r"[\u4e00-\u9fff]{2,24}(?:公司|大学|系统|合同|项目|知识库)?")


class GraphExtractionError(RuntimeError):
    def __init__(self, message: str, *, error_type: str = "graph_extraction_failed") -> None:
        self.error_type = error_type
        super().__init__(message)


class LLMGraphExtractor:
    def __init__(
        self,
        *,
        llm_connector: Any,
        model_config: ModelConfig,
        max_chunks_per_call: int = 24,
        max_chars_per_chunk: int = 1600,
    ) -> None:
        self.llm_connector = llm_connector
        self.model_config = model_config
        self.max_chunks_per_call = max(1, max_chunks_per_call)
        self.max_chars_per_chunk = max(200, max_chars_per_chunk)
        self.output_parser = JsonOutputParser()

    def extract(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        doc_version_id: str,
        chunks: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        if not chunks:
            return _empty_records(
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
                doc_version_id=doc_version_id,
                source="graphrag_llm",
            )
        records = _empty_llm_records(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            doc_version_id=doc_version_id,
        )
        batch_usage: list[dict[str, Any]] = []
        for batch_index, batch_chunks in enumerate(_chunk_batches(chunks, self.max_chunks_per_call)):
            request = ModelRequest(
                request_id=_stable_id(
                    "graph_extract",
                    workspace_id,
                    knowledge_base_id,
                    doc_id,
                    str(batch_index),
                ),
                messages=[
                    ModelMessage(role="system", content=_llm_graph_system_prompt()),
                    ModelMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "workspace_id": workspace_id,
                                "knowledge_base_id": knowledge_base_id,
                                "doc_id": doc_id,
                                "doc_version_id": doc_version_id,
                                "batch_index": batch_index,
                                "batch_size": len(batch_chunks),
                                "chunks": [
                                    _chunk_for_llm(chunk, max_chars=self.max_chars_per_chunk)
                                    for chunk in batch_chunks
                                ],
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                ],
                tools=[],
                max_output_tokens=min(4096, self.model_config.max_output_tokens),
            )
            try:
                result = self.llm_connector.call(
                    workspace_id=workspace_id,
                    config=self.model_config,
                    request=request,
                )
                parsed = self._parse_result(result.content)
                batch_records = _records_from_llm_payload(
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    doc_id=doc_id,
                    doc_version_id=doc_version_id,
                    chunks=batch_chunks,
                    payload=parsed,
                )
            except GraphExtractionError:
                raise
            except Exception as exc:  # noqa: BLE001 - model boundary is converted to fallback metadata.
                raise GraphExtractionError(
                    "GraphRAG LLM extraction failed.",
                    error_type=getattr(exc, "error_type", exc.__class__.__name__),
                ) from None

            batch_usage.append(result.usage.model_dump())
            records = _merge_llm_records(records, batch_records)
            records["decisions"].append(
                {
                    "schema_version": 1,
                    "decision_id": _stable_id(
                        "dec",
                        workspace_id,
                        knowledge_base_id,
                        doc_id,
                        doc_version_id,
                        "graphrag_llm",
                        str(batch_index),
                    ),
                    "type": "graph_extraction_source",
                    "source": "graphrag_llm",
                    "provider": self.model_config.provider,
                    "model": self.model_config.model,
                    "batch_index": batch_index,
                    "batch_size": len(batch_chunks),
                    "chunk_count": len(batch_chunks),
                    "usage": result.usage.model_dump(),
                }
            )
        records["decisions"].append(
            {
                "schema_version": 1,
                "decision_id": _stable_id(
                    "dec",
                    workspace_id,
                    knowledge_base_id,
                    doc_id,
                    doc_version_id,
                    "graphrag_llm",
                    "summary",
                ),
                "type": "graph_extraction_summary",
                "source": "graphrag_llm",
                "provider": self.model_config.provider,
                "model": self.model_config.model,
                "chunk_count": len(chunks),
                "batch_count": len(batch_usage),
                "usage": _sum_usage(batch_usage),
            }
        )
        return records

    def _parse_result(self, content: str) -> dict[str, Any]:
        try:
            parsed = self.output_parser.parse(content)
        except Exception:
            parsed = _extract_json_object(content)
        if not isinstance(parsed, dict):
            raise GraphExtractionError(
                "GraphRAG LLM returned a non-object payload.",
                error_type="graph_llm_invalid_json",
            )
        return parsed


def extract_graph_records(
    *,
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    doc_version_id: str,
    chunks: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    entities_by_name: dict[str, dict[str, Any]] = {}
    mentions: list[dict[str, Any]] = []
    relation_facts: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    for chunk in chunks:
        chunk_id = str(chunk["chunk_id"])
        text = str(chunk.get("text") or "")
        names = _extract_entity_names(text)
        chunk_entity_ids: list[str] = []
        for index, name in enumerate(names):
            entity_id = _entity_id(workspace_id, knowledge_base_id, name)
            chunk_entity_ids.append(entity_id)
            entities_by_name.setdefault(
                name,
                {
                    "schema_version": 1,
                    "label": "Entity",
                    "entity_id": entity_id,
                    "workspace_id": workspace_id,
                    "knowledge_base_id": knowledge_base_id,
                    "name": name,
                    "entity_type": _entity_type(name),
                    "aliases": [],
                    "source_chunk_ids": [],
                    "evidence_count": 0,
                },
            )
            entity = entities_by_name[name]
            if chunk_id not in entity["source_chunk_ids"]:
                entity["source_chunk_ids"].append(chunk_id)
                entity["evidence_count"] = int(entity["evidence_count"]) + 1
            mentions.append(
                {
                    "schema_version": 1,
                    "label": "Mention",
                    "mention_id": _stable_id("men", chunk_id, name, str(index)),
                    "workspace_id": workspace_id,
                    "knowledge_base_id": knowledge_base_id,
                    "doc_id": doc_id,
                    "doc_version_id": doc_version_id,
                    "chunk_id": chunk_id,
                    "surface": name,
                    "entity_id": entity_id,
                    "confidence": 0.75,
                }
            )

        if len(chunk_entity_ids) >= 2:
            subject_id = chunk_entity_ids[0]
            object_id = chunk_entity_ids[1]
            predicate = _predicate(text)
            fact_id = _stable_id("fact", doc_version_id, chunk_id, subject_id, predicate, object_id)
            evidence_id = _stable_id("ev", fact_id, chunk_id)
            relation_facts.append(
                {
                    "schema_version": 1,
                    "label": "RelationFact",
                    "fact_id": fact_id,
                    "workspace_id": workspace_id,
                    "knowledge_base_id": knowledge_base_id,
                    "doc_id": doc_id,
                    "doc_version_id": doc_version_id,
                    "subject_entity_id": subject_id,
                    "object_entity_id": object_id,
                    "predicate": predicate,
                    "direction": "outgoing",
                    "confidence": 0.72,
                    "relation_strength": "strong" if predicate != "RELATED_TO" else "weak",
                    "source_chunk_ids": [chunk_id],
                    "evidence_ids": [evidence_id],
                    "status": "active",
                }
            )
            evidence.append(
                {
                    "schema_version": 1,
                    "label": "Evidence",
                    "evidence_id": evidence_id,
                    "fact_id": fact_id,
                    "workspace_id": workspace_id,
                    "knowledge_base_id": knowledge_base_id,
                    "doc_id": doc_id,
                    "doc_version_id": doc_version_id,
                    "source_chunk_id": chunk_id,
                    "chunk_id": chunk_id,
                    "chunk_object_key": chunk.get("object_key"),
                    "evidence_text": text[:1200],
                    "source": chunk.get("source") or {},
                    "confidence": 0.72,
                }
            )

    return {
        "entities": sorted(entities_by_name.values(), key=lambda item: item["entity_id"]),
        "mentions": mentions,
        "relation_facts": relation_facts,
        "evidence": evidence,
        "decisions": [],
    }


def _records_from_llm_payload(
    *,
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    doc_version_id: str,
    chunks: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    chunk_lookup = {str(chunk.get("chunk_id")): chunk for chunk in chunks if chunk.get("chunk_id")}
    chunk_ids = list(chunk_lookup.keys())
    entities_by_name: dict[str, dict[str, Any]] = {}
    mentions_by_id: dict[str, dict[str, Any]] = {}
    relation_facts_by_id: dict[str, dict[str, Any]] = {}
    evidence_by_id: dict[str, dict[str, Any]] = {}

    for item in _safe_list(payload.get("entities")):
        name = _clean_name(item.get("name") or item.get("surface") or item.get("entity"))
        if not name:
            continue
        _upsert_llm_entity(
            entities_by_name,
            mentions_by_id,
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            doc_version_id=doc_version_id,
            name=name,
            entity_type=_optional_text(item.get("entity_type")) or _entity_type(name),
            aliases=_safe_text_list(item.get("aliases")),
            chunk_ids=_resolve_chunk_ids(item, chunk_ids, chunk_lookup, name=name),
            confidence=_safe_confidence(item.get("confidence"), default=0.82),
        )

    for item in _safe_list(payload.get("relations") or payload.get("relation_facts")):
        subject = _clean_name(
            item.get("subject") or item.get("source") or item.get("subject_name")
        )
        obj = _clean_name(item.get("object") or item.get("target") or item.get("object_name"))
        if not subject or not obj:
            continue
        predicate = _normalize_predicate(item.get("predicate") or item.get("type"))
        source_chunk_ids = _resolve_chunk_ids(item, chunk_ids, chunk_lookup)
        if not source_chunk_ids:
            source_chunk_ids = _entity_chunk_intersection(entities_by_name, subject, obj)
        if not source_chunk_ids and chunk_ids:
            source_chunk_ids = [chunk_ids[0]]
        subject_entity = _upsert_llm_entity(
            entities_by_name,
            mentions_by_id,
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            doc_version_id=doc_version_id,
            name=subject,
            entity_type=_optional_text(item.get("subject_type")) or _entity_type(subject),
            aliases=[],
            chunk_ids=source_chunk_ids,
            confidence=_safe_confidence(item.get("confidence"), default=0.82),
        )
        object_entity = _upsert_llm_entity(
            entities_by_name,
            mentions_by_id,
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            doc_version_id=doc_version_id,
            name=obj,
            entity_type=_optional_text(item.get("object_type")) or _entity_type(obj),
            aliases=[],
            chunk_ids=source_chunk_ids,
            confidence=_safe_confidence(item.get("confidence"), default=0.82),
        )
        fact_id = _stable_id(
            "fact",
            doc_version_id,
            "|".join(source_chunk_ids),
            subject_entity["entity_id"],
            predicate,
            object_entity["entity_id"],
        )
        evidence_ids: list[str] = []
        for chunk_id in source_chunk_ids:
            evidence_id = _stable_id("ev", fact_id, chunk_id)
            evidence_ids.append(evidence_id)
            chunk = chunk_lookup.get(chunk_id, {})
            evidence_by_id[evidence_id] = {
                "schema_version": 1,
                "label": "Evidence",
                "evidence_id": evidence_id,
                "fact_id": fact_id,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "doc_id": doc_id,
                "doc_version_id": doc_version_id,
                "source_chunk_id": chunk_id,
                "chunk_id": chunk_id,
                "chunk_object_key": chunk.get("object_key"),
                "evidence_text": _evidence_text(item, chunk),
                "source": chunk.get("source") or {},
                "confidence": _safe_confidence(item.get("confidence"), default=0.82),
                "extraction_source": "graphrag_llm",
            }
        relation_facts_by_id[fact_id] = {
            "schema_version": 1,
            "label": "RelationFact",
            "fact_id": fact_id,
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "doc_id": doc_id,
            "doc_version_id": doc_version_id,
            "subject_entity_id": subject_entity["entity_id"],
            "object_entity_id": object_entity["entity_id"],
            "predicate": predicate,
            "direction": "outgoing",
            "confidence": _safe_confidence(item.get("confidence"), default=0.82),
            "relation_strength": _relation_strength(item),
            "source_chunk_ids": source_chunk_ids,
            "evidence_ids": evidence_ids,
            "status": "active",
            "extraction_source": "graphrag_llm",
        }

    return {
        "entities": sorted(entities_by_name.values(), key=lambda item: item["entity_id"]),
        "mentions": sorted(mentions_by_id.values(), key=lambda item: item["mention_id"]),
        "relation_facts": sorted(
            relation_facts_by_id.values(),
            key=lambda item: item["fact_id"],
        ),
        "evidence": sorted(evidence_by_id.values(), key=lambda item: item["evidence_id"]),
        "decisions": _normalize_llm_decisions(payload),
    }


def _extract_entity_names(text: str) -> list[str]:
    names: list[str] = []
    for pattern in (PARTY_RE, TITLE_ENTITY_RE, CJK_ENTITY_RE):
        for match in pattern.finditer(text):
            name = match.group(0).strip(" .,;:()[]{}")
            if len(name) < 2 or name.lower() in {"the", "and"}:
                continue
            if name not in names:
                names.append(name)
    return names[:12]


def _entity_type(name: str) -> str:
    if "Party" in name:
        return "Role"
    if name.endswith(("公司", "大学")) or "Society" in name:
        return "Organization"
    return "Concept"


def _predicate(text: str) -> str:
    lowered = text.lower()
    if "sign" in lowered:
        return "SIGNS_WITH"
    if "deliver" in lowered:
        return "DELIVERS"
    if "participat" in lowered or "join" in lowered:
        return "PARTICIPATED_IN"
    return "RELATED_TO"


def _entity_id(workspace_id: str, knowledge_base_id: str, name: str) -> str:
    return _stable_id("ent", workspace_id, knowledge_base_id, name.casefold())


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _chunk_batches(chunks: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    safe_size = max(1, batch_size)
    return [chunks[index : index + safe_size] for index in range(0, len(chunks), safe_size)]


def _empty_llm_records(
    *,
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    doc_version_id: str,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "entities": [],
        "mentions": [],
        "relation_facts": [],
        "evidence": [],
        "decisions": [
            {
                "schema_version": 1,
                "decision_id": _stable_id(
                    "dec",
                    workspace_id,
                    knowledge_base_id,
                    doc_id,
                    doc_version_id,
                    "graphrag_llm",
                    "start",
                ),
                "type": "graph_extraction_started",
                "source": "graphrag_llm",
            }
        ],
    }


def _merge_llm_records(
    left: dict[str, list[dict[str, Any]]],
    right: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    merged = {
        "entities": {item["entity_id"]: dict(item) for item in left.get("entities", [])},
        "mentions": {item["mention_id"]: dict(item) for item in left.get("mentions", [])},
        "relation_facts": {
            item["fact_id"]: dict(item) for item in left.get("relation_facts", [])
        },
        "evidence": {item["evidence_id"]: dict(item) for item in left.get("evidence", [])},
        "decisions": {item["decision_id"]: dict(item) for item in left.get("decisions", [])},
    }
    for entity in right.get("entities", []):
        current = merged["entities"].get(entity["entity_id"])
        if current is None:
            merged["entities"][entity["entity_id"]] = dict(entity)
            continue
        current["aliases"] = _dedupe(
            [*current.get("aliases", []), *entity.get("aliases", [])]
        )
        current["source_chunk_ids"] = _dedupe(
            [*current.get("source_chunk_ids", []), *entity.get("source_chunk_ids", [])]
        )
        current["evidence_count"] = len(current["source_chunk_ids"])
        current["confidence"] = max(
            float(current.get("confidence") or 0),
            float(entity.get("confidence") or 0),
        )
    for section in ("mentions", "relation_facts", "evidence", "decisions"):
        id_field = {
            "mentions": "mention_id",
            "relation_facts": "fact_id",
            "evidence": "evidence_id",
            "decisions": "decision_id",
        }[section]
        for item in right.get(section, []):
            merged[section][item[id_field]] = dict(item)
    return {
        "entities": sorted(merged["entities"].values(), key=lambda item: item["entity_id"]),
        "mentions": sorted(merged["mentions"].values(), key=lambda item: item["mention_id"]),
        "relation_facts": sorted(
            merged["relation_facts"].values(),
            key=lambda item: item["fact_id"],
        ),
        "evidence": sorted(merged["evidence"].values(), key=lambda item: item["evidence_id"]),
        "decisions": sorted(merged["decisions"].values(), key=lambda item: item["decision_id"]),
    }


def _sum_usage(items: list[dict[str, Any]]) -> dict[str, Any]:
    input_tokens = sum(int(item.get("input_tokens") or 0) for item in items)
    output_tokens = sum(int(item.get("output_tokens") or 0) for item in items)
    total_tokens = sum(int(item.get("total_tokens") or 0) for item in items)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_estimated": any(bool(item.get("usage_estimated")) for item in items),
    }


def _empty_records(
    *,
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    doc_version_id: str,
    source: str,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "entities": [],
        "mentions": [],
        "relation_facts": [],
        "evidence": [],
        "decisions": [
            {
                "schema_version": 1,
                "decision_id": _stable_id(
                    "dec",
                    workspace_id,
                    knowledge_base_id,
                    doc_id,
                    doc_version_id,
                    source,
                    "empty",
                ),
                "type": "graph_extraction_empty_document",
                "source": source,
            }
        ],
    }


def _llm_graph_system_prompt() -> str:
    return (
        "Extract a compact knowledge graph from document chunks. "
        "Return only JSON. Do not include markdown. Schema: "
        "{\"entities\":[{\"name\":\"string\",\"entity_type\":\"Person|Organization|"
        "Role|Concept|System|Project\",\"aliases\":[\"string\"],"
        "\"chunk_ids\":[\"chunk_id\"],\"confidence\":0.0}],"
        "\"relations\":[{\"subject\":\"entity name\",\"predicate\":\"UPPER_SNAKE_CASE\","
        "\"object\":\"entity name\",\"chunk_ids\":[\"chunk_id\"],"
        "\"evidence_text\":\"short quote or paraphrase\",\"confidence\":0.0}],"
        "\"decisions\":[{\"type\":\"string\",\"reason\":\"string\"}]}. "
        "Use only entities and relations supported by the supplied chunks."
    )


def _chunk_for_llm(chunk: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "text": str(chunk.get("text") or "")[:max_chars],
        "source": chunk.get("source") or {},
    }


def _extract_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise GraphExtractionError(
            "GraphRAG LLM did not return a JSON object.",
            error_type="graph_llm_invalid_json",
        )
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise GraphExtractionError(
            "GraphRAG LLM returned invalid JSON.",
            error_type="graph_llm_invalid_json",
        ) from exc
    if not isinstance(parsed, dict):
        raise GraphExtractionError(
            "GraphRAG LLM returned a non-object JSON value.",
            error_type="graph_llm_invalid_json",
        )
    return parsed


def _upsert_llm_entity(
    entities_by_name: dict[str, dict[str, Any]],
    mentions_by_id: dict[str, dict[str, Any]],
    *,
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    doc_version_id: str,
    name: str,
    entity_type: str,
    aliases: list[str],
    chunk_ids: list[str],
    confidence: float,
) -> dict[str, Any]:
    entity_id = _entity_id(workspace_id, knowledge_base_id, name)
    entity = entities_by_name.setdefault(
        name,
        {
            "schema_version": 1,
            "label": "Entity",
            "entity_id": entity_id,
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "name": name,
            "entity_type": entity_type,
            "aliases": [],
            "source_chunk_ids": [],
            "evidence_count": 0,
            "confidence": confidence,
            "extraction_source": "graphrag_llm",
        },
    )
    entity["entity_type"] = entity.get("entity_type") or entity_type
    entity["confidence"] = max(float(entity.get("confidence") or 0), confidence)
    for alias in aliases:
        if alias and alias not in entity["aliases"]:
            entity["aliases"].append(alias)
    for index, chunk_id in enumerate(chunk_ids):
        if chunk_id not in entity["source_chunk_ids"]:
            entity["source_chunk_ids"].append(chunk_id)
            entity["evidence_count"] = int(entity["evidence_count"]) + 1
        mention_id = _stable_id("men", chunk_id, name, str(index))
        mentions_by_id.setdefault(
            mention_id,
            {
                "schema_version": 1,
                "label": "Mention",
                "mention_id": mention_id,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "doc_id": doc_id,
                "doc_version_id": doc_version_id,
                "chunk_id": chunk_id,
                "surface": name,
                "entity_id": entity_id,
                "confidence": confidence,
                "extraction_source": "graphrag_llm",
            },
        )
    return entity


def _resolve_chunk_ids(
    item: dict[str, Any],
    known_chunk_ids: list[str],
    chunk_lookup: dict[str, dict[str, Any]],
    *,
    name: str | None = None,
) -> list[str]:
    raw = (
        item.get("chunk_ids")
        or item.get("source_chunk_ids")
        or item.get("source_chunks")
        or item.get("evidence_chunk_ids")
    )
    chunk_ids = [chunk_id for chunk_id in _safe_text_list(raw) if chunk_id in chunk_lookup]
    if chunk_ids:
        return _dedupe(chunk_ids)
    if name:
        normalized = name.casefold()
        matches = [
            chunk_id
            for chunk_id, chunk in chunk_lookup.items()
            if normalized and normalized in str(chunk.get("text") or "").casefold()
        ]
        if matches:
            return matches[:5]
    return [known_chunk_ids[0]] if known_chunk_ids else []


def _entity_chunk_intersection(
    entities_by_name: dict[str, dict[str, Any]],
    subject: str,
    obj: str,
) -> list[str]:
    subject_chunks = set(entities_by_name.get(subject, {}).get("source_chunk_ids", []))
    object_chunks = set(entities_by_name.get(obj, {}).get("source_chunk_ids", []))
    return sorted(subject_chunks & object_chunks)


def _normalize_llm_decisions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for index, item in enumerate(_safe_list(payload.get("decisions"))):
        if isinstance(item, dict):
            reason = _optional_text(item.get("reason")) or _optional_text(item.get("summary"))
            decision_type = _optional_text(item.get("type")) or "graph_extraction_decision"
        else:
            reason = str(item)
            decision_type = "graph_extraction_decision"
        decisions.append(
            {
                "schema_version": 1,
                "decision_id": _stable_id("dec", "llm", str(index), reason or ""),
                "type": decision_type,
                "reason": reason or "",
                "source": "graphrag_llm",
            }
        )
    return decisions


def _evidence_text(item: dict[str, Any], chunk: dict[str, Any]) -> str:
    return (
        _optional_text(item.get("evidence_text"))
        or _optional_text(item.get("evidence"))
        or str(chunk.get("text") or "")
    )[:1200]


def _relation_strength(item: dict[str, Any]) -> str:
    raw = _optional_text(item.get("relation_strength"))
    if raw in {"strong", "weak"}:
        return raw
    return "strong" if _safe_confidence(item.get("confidence"), default=0.82) >= 0.75 else "weak"


def _normalize_predicate(value: Any) -> str:
    text = str(value or "RELATED_TO").strip()
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    return text or "RELATED_TO"


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _optional_text(item))]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_name(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    return text.strip(" .,;:()[]{}")[:160] or None


def _safe_confidence(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(parsed, 1.0))


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
