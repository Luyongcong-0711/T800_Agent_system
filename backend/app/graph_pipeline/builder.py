from __future__ import annotations

from typing import Any

from app.core.time import utc_now_iso
from app.graph_pipeline.extraction import GraphExtractionError, extract_graph_records
from app.graph_pipeline.paths import (
    graph_decisions_key,
    graph_entities_key,
    graph_evidence_key,
    graph_index_key,
    graph_mentions_key,
    graph_operation_key,
    graph_relation_facts_key,
)
from app.rag_pipeline.paths import document_chunks_index_key, document_manifest_key
from app.storage.object_store import JsonObjectStore, ObjectStore


class GraphWriterError(RuntimeError):
    def __init__(
        self,
        message: str = "Graph writer failed.",
        *,
        error_type: str = "graph_writer_failed",
    ) -> None:
        self.error_type = error_type
        super().__init__(message)


class GraphBuildJobService:
    def __init__(
        self,
        object_store: ObjectStore,
        graph_writer: Any | None = None,
        graph_extractor: Any | None = None,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.graph_writer = graph_writer
        self.graph_extractor = graph_extractor

    def run(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        doc_version_id: str | None = None,
        job_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        return self.build(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            doc_version_id=doc_version_id,
            job_id=job_id,
            operation_id=operation_id,
        )

    def build(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        doc_version_id: str | None = None,
        job_id: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        chunks_payload = self.json_store.read_json(
            document_chunks_index_key(workspace_id, knowledge_base_id, doc_id)
        )
        chunks = [
            self._hydrate_chunk(record)
            for record in chunks_payload.get("chunks", [])
            if isinstance(record, dict)
        ]
        current_doc_version_id = doc_version_id or str(
            chunks_payload.get("doc_version_id") or "unknown_doc_version"
        )
        records, extraction = self._extract_records(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            doc_version_id=current_doc_version_id,
            chunks=chunks,
        )
        now = utc_now_iso()
        artifacts = {
            "entities": graph_entities_key(workspace_id, knowledge_base_id, doc_id),
            "mentions": graph_mentions_key(workspace_id, knowledge_base_id, doc_id),
            "relation_facts": graph_relation_facts_key(workspace_id, knowledge_base_id, doc_id),
            "evidence": graph_evidence_key(workspace_id, knowledge_base_id, doc_id),
            "decisions": graph_decisions_key(workspace_id, knowledge_base_id, doc_id),
        }
        self.json_store.write_json(
            artifacts["entities"],
            {"schema_version": 1, "entities": records["entities"], "updated_at": now},
        )
        self.json_store.write_json(
            artifacts["mentions"],
            {"schema_version": 1, "mentions": records["mentions"], "updated_at": now},
        )
        self.json_store.write_json(
            artifacts["relation_facts"],
            {
                "schema_version": 1,
                "relation_facts": records["relation_facts"],
                "updated_at": now,
            },
        )
        self.json_store.write_json(
            artifacts["evidence"],
            {"schema_version": 1, "evidence": records["evidence"], "updated_at": now},
        )
        self.json_store.write_json(
            artifacts["decisions"],
            {"schema_version": 1, "decisions": records["decisions"], "updated_at": now},
        )
        self._merge_graph_index(
            workspace_id,
            knowledge_base_id,
            doc_id,
            records,
            updated_at=now,
        )
        batch = {
            "schema_version": 1,
            "caller_type": "graph_build_job",
            "job_id": job_id,
            "operation_id": operation_id,
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "doc_id": doc_id,
            "doc_version_id": current_doc_version_id,
            "extraction": extraction,
            "entities": records["entities"],
            "mentions": records["mentions"],
            "relation_facts": records["relation_facts"],
            "evidence": records["evidence"],
        }
        writer_result = self._write_graph_batch(batch, operation_id=operation_id, job_id=job_id)
        if not bool(writer_result.get("ok", False)):
            if operation_id:
                self.json_store.write_json(
                    graph_operation_key(workspace_id, knowledge_base_id, operation_id),
                    {
                        "schema_version": 1,
                        "status": "failed",
                        "batch": batch,
                        "writer_result": writer_result,
                        "updated_at": now,
                    },
                )
            raise GraphWriterError(
                error_type=str(writer_result.get("error_type") or "graph_writer_failed")
            )
        if operation_id:
            self.json_store.write_json(
                graph_operation_key(workspace_id, knowledge_base_id, operation_id),
                {
                    "schema_version": 1,
                    "status": "committed",
                    "batch": batch,
                    "writer_result": writer_result,
                    "updated_at": now,
                },
            )
        self._update_document_manifest(workspace_id, knowledge_base_id, doc_id, now)
        return {
            "ok": True,
            "doc_id": doc_id,
            "doc_version_id": current_doc_version_id,
            "artifacts": artifacts,
            "counts": {key: len(value) for key, value in records.items()},
            "extraction": extraction,
            "writer_result": writer_result,
        }

    def handle(self, **kwargs: Any) -> dict[str, Any]:
        return self.build(**kwargs)

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        return self.build(**kwargs)

    def _hydrate_chunk(self, record: dict[str, Any]) -> dict[str, Any]:
        object_key = record.get("object_key")
        if object_key and self.object_store.exists(str(object_key)):
            return self.json_store.read_json(str(object_key))
        return record

    def _extract_records(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        doc_version_id: str,
        chunks: list[dict[str, Any]],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        if self.graph_extractor is None:
            return (
                extract_graph_records(
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    doc_id=doc_id,
                    doc_version_id=doc_version_id,
                    chunks=chunks,
                ),
                {
                    "source": "rule_based",
                    "fallback_used": False,
                    "warnings": [],
                },
            )
        try:
            records = self.graph_extractor.extract(
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
                doc_version_id=doc_version_id,
                chunks=chunks,
            )
            return (
                self._normalize_records(records),
                {
                    "source": "graphrag_llm",
                    "fallback_used": False,
                    "warnings": [],
                },
            )
        except GraphExtractionError as exc:
            fallback = extract_graph_records(
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
                doc_version_id=doc_version_id,
                chunks=chunks,
            )
            fallback["decisions"].append(
                {
                    "schema_version": 1,
                    "decision_id": f"dec_graph_llm_fallback_{doc_version_id}",
                    "type": "graph_extraction_fallback",
                    "source": "rule_based",
                    "failed_source": "graphrag_llm",
                    "error_type": exc.error_type,
                    "message": str(exc)[:300],
                }
            )
            return (
                fallback,
                {
                    "source": "rule_based",
                    "fallback_used": True,
                    "failed_source": "graphrag_llm",
                    "error_type": exc.error_type,
                    "warnings": [
                        {
                            "type": "graph_extraction_fallback",
                            "error_type": exc.error_type,
                            "message": str(exc)[:300],
                        }
                    ],
                },
            )

    @staticmethod
    def _normalize_records(records: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        normalized: dict[str, list[dict[str, Any]]] = {}
        for key in ("entities", "mentions", "relation_facts", "evidence", "decisions"):
            value = records.get(key, [])
            normalized[key] = [item for item in value if isinstance(item, dict)]
        return normalized

    def _merge_graph_index(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        records: dict[str, list[dict[str, Any]]],
        *,
        updated_at: str,
    ) -> None:
        key = graph_index_key(workspace_id, knowledge_base_id)
        index = self.json_store.read_json_or_default(
            key,
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "entities": [],
                "mentions": [],
                "relation_facts": [],
                "evidence": [],
                "revision": 0,
            },
        )
        for section in ("entities", "mentions", "relation_facts", "evidence"):
            id_field = {
                "entities": "entity_id",
                "mentions": "mention_id",
                "relation_facts": "fact_id",
                "evidence": "evidence_id",
            }[section]
            retained = [
                item
                for item in index.get(section, [])
                if item.get("doc_id") != doc_id and doc_id not in item.get("source_chunk_ids", [])
            ]
            merged = {str(item[id_field]): item for item in retained if item.get(id_field)}
            for item in records[section]:
                merged[str(item[id_field])] = item
            index[section] = sorted(merged.values(), key=lambda item: str(item.get(id_field)))
        index["updated_at"] = updated_at
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(key, index)

    def _write_graph_batch(
        self,
        batch: dict[str, Any],
        *,
        operation_id: str | None,
        job_id: str | None,
    ) -> dict[str, Any]:
        if self.graph_writer is None:
            return {
                "ok": False,
                "backend": "neo4j",
                "error_type": "neo4j_writer_not_configured",
                "message_for_user": "Neo4j graph writer is required before GraphRAG can be marked available.",
                "retryable": True,
            }
        if hasattr(self.graph_writer, "write_graph_batch_internal"):
            return self.graph_writer.write_graph_batch_internal(
                batch,
                operation_id=operation_id,
                caller_type="graph_build_job",
                job_id=job_id,
            )
        if hasattr(self.graph_writer, "write_graph_batch"):
            return self.graph_writer.write_graph_batch(
                batch=batch,
                operation_id=operation_id,
                caller_type="graph_build_job",
                job_id=job_id,
            )
        return {"ok": False, "error_type": "graph_writer_not_supported"}

    def _update_document_manifest(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        updated_at: str,
    ) -> None:
        key = document_manifest_key(workspace_id, knowledge_base_id, doc_id)
        if not self.object_store.exists(key):
            return
        manifest = self.json_store.read_json(key)
        manifest["graph_status"] = "indexed"
        manifest["graphrag_available"] = True
        manifest["updated_at"] = updated_at
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(key, manifest)


def build_graph_build_service(
    object_store: ObjectStore,
    graph_writer: Any | None = None,
    neo4j: Any | None = None,
    graph_extractor: Any | None = None,
) -> GraphBuildJobService:
    return GraphBuildJobService(
        object_store,
        graph_writer=graph_writer or neo4j,
        graph_extractor=graph_extractor,
    )


def build_graph_build_job_handler(
    object_store: ObjectStore,
    graph_writer: Any | None = None,
    neo4j: Any | None = None,
    graph_extractor: Any | None = None,
) -> GraphBuildJobService:
    return build_graph_build_service(
        object_store,
        graph_writer=graph_writer,
        neo4j=neo4j,
        graph_extractor=graph_extractor,
    )
