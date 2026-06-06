from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any

from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.rag_pipeline.chunking import DeterministicChunker
from app.rag_pipeline.models import DocumentChunk
from app.rag_pipeline.parser import LocalDocumentParser
from app.rag_pipeline.paths import (
    active_embedding_key,
    document_chunk_errors_prefix,
    document_chunk_key,
    document_chunks_index_key,
    document_manifest_key,
    document_original_key,
    document_representation_key,
    document_versions_key,
    documents_index_key,
    embedding_version_manifest_key,
    embedding_versions_index_key,
    knowledge_base_manifest_key,
    knowledge_bases_index_key,
    legacy_document_chunks_index_key,
    parsed_text_key,
    safe_file_name,
    safe_path_id,
)
from app.rag_pipeline.search import ObjectStoreSearchIndex, SearchIndex
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectNotFoundError, ObjectStore

DEFAULT_ACTIVE_EMBEDDING = {
    "schema_version": 1,
    "provider": "local_fallback",
    "model": "object_store_lexical_fallback",
    "dimension": 0,
    "collection": "object_store_lexical_fallback",
    "status": "active",
}


class DocumentIngestionService:
    def __init__(
        self,
        object_store: ObjectStore,
        *,
        parser: LocalDocumentParser | None = None,
        chunker: DeterministicChunker | None = None,
        search_index: SearchIndex | None = None,
        embedding_client: Any | None = None,
        vector_store: Any | None = None,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.parser = parser or LocalDocumentParser()
        self.chunker = chunker or DeterministicChunker()
        self.search_index = search_index or ObjectStoreSearchIndex(object_store)
        self.embedding_client = embedding_client
        self.vector_store = vector_store

    def ensure_knowledge_base(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        workspace_id = safe_path_id("workspace_id", workspace_id)
        knowledge_base_id = safe_path_id("knowledge_base_id", knowledge_base_id)
        now = utc_now_iso()
        key = knowledge_base_manifest_key(workspace_id, knowledge_base_id)
        manifest = self.json_store.read_json_or_default(
            key,
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "name": name or knowledge_base_id,
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "revision": 0,
            },
        )
        if name and manifest.get("name") != name:
            manifest["name"] = name
        manifest["updated_at"] = now
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(key, manifest)
        active_key = active_embedding_key(workspace_id, knowledge_base_id)
        if not self.object_store.exists(active_key):
            self.json_store.write_json(
                active_key,
                {
                    **DEFAULT_ACTIVE_EMBEDDING,
                    "workspace_id": workspace_id,
                    "knowledge_base_id": knowledge_base_id,
                    "version_id": "embv_default",
                    "updated_at": now,
                    "revision": 1,
                },
            )
        self._upsert_knowledge_base_index(workspace_id, manifest)
        return manifest

    def ensure_active_embedding_defaults(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        *,
        provider: str,
        model: str,
        dimension: int,
        collection: str | None = None,
        config_id: str | None = None,
    ) -> dict[str, Any]:
        workspace_id = safe_path_id("workspace_id", workspace_id)
        knowledge_base_id = safe_path_id("knowledge_base_id", knowledge_base_id)
        self.ensure_knowledge_base(workspace_id, knowledge_base_id)
        active_key = active_embedding_key(workspace_id, knowledge_base_id)
        active = self.json_store.read_json(active_key)
        if not _is_local_fallback_embedding(active) or not model or int(dimension) <= 0:
            return active

        now = utc_now_iso()
        version_id = "embv_settings_default"
        next_collection = collection or (
            f"kb_{knowledge_base_id}_{_safe_collection_component(model)}_{int(dimension)}_settings"
        )
        version = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "version_id": version_id,
            "provider": provider,
            "model": model,
            "dimension": int(dimension),
            "collection": next_collection,
            "status": "active",
            "previous_version_id": active.get("version_id"),
            "previous_collection": active.get("collection"),
            "chunk_count": 0,
            "job_id": None,
            "config_id": config_id,
            "manifest_object_key": embedding_version_manifest_key(
                workspace_id,
                knowledge_base_id,
                version_id,
            ),
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        self.json_store.write_json(str(version["manifest_object_key"]), version)
        self._upsert_embedding_version(workspace_id, knowledge_base_id, version)
        next_active = {**version, "active": True}
        next_active["revision"] = int(active.get("revision") or 0) + 1
        self.json_store.write_json(active_key, next_active)
        return next_active

    def get_active_embedding(self, workspace_id: str, knowledge_base_id: str) -> dict[str, Any]:
        workspace_id = safe_path_id("workspace_id", workspace_id)
        knowledge_base_id = safe_path_id("knowledge_base_id", knowledge_base_id)
        self.ensure_knowledge_base(workspace_id, knowledge_base_id)
        return self.json_store.read_json(
            active_embedding_key(workspace_id, knowledge_base_id)
        )

    def reindex_embeddings(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        provider: str,
        model: str,
        dimension: int,
        collection: str | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        workspace_id = safe_path_id("workspace_id", workspace_id)
        knowledge_base_id = safe_path_id("knowledge_base_id", knowledge_base_id)
        self.ensure_knowledge_base(workspace_id, knowledge_base_id)
        previous = self.get_active_embedding(workspace_id, knowledge_base_id)
        version_id = new_id("embv")
        safe_model = _safe_collection_component(model)
        next_collection = collection or (
            f"kb_{knowledge_base_id}_{safe_model}_{int(dimension)}_{version_id[-8:]}"
        )
        now = utc_now_iso()
        documents = self.list_documents(workspace_id, knowledge_base_id)
        document_chunks: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        all_chunks: list[dict[str, Any]] = []
        for document in documents:
            doc_id = str(document["doc_id"])
            chunks = self.get_chunks(workspace_id, knowledge_base_id, doc_id)
            document_chunks.append((document, chunks))
            all_chunks.extend(chunks)
        self._write_external_vectors(
            collection=next_collection,
            dimension=int(dimension),
            provider=provider,
            model=model,
            chunks=all_chunks,
        )
        chunk_count = 0
        for document, chunks in document_chunks:
            doc_id = str(document["doc_id"])
            for chunk in chunks:
                chunk_count += 1
                _apply_embedding_to_chunk(
                    chunk,
                    provider=provider,
                    model=model,
                    dimension=dimension,
                    collection=next_collection,
                    version_id=version_id,
                )
                object_key = chunk.get("object_key")
                if object_key:
                    self.json_store.write_json(str(object_key), chunk)
            chunks_payload_key = document_chunks_index_key(workspace_id, knowledge_base_id, doc_id)
            chunks_payload = self.json_store.read_json(chunks_payload_key)
            updated_records = []
            by_chunk_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
            for record in chunks_payload.get("chunks", []):
                if isinstance(record, dict) and str(record.get("chunk_id")) in by_chunk_id:
                    updated_records.append(by_chunk_id[str(record["chunk_id"])])
                else:
                    updated_records.append(record)
            chunks_payload.update(
                {
                    "chunks": updated_records,
                    "embedding_provider": provider,
                    "embedding_model": model,
                    "embedding_dimension": int(dimension),
                    "embedding_collection": next_collection,
                    "embedding_version_id": version_id,
                    "updated_at": now,
                    "revision": int(chunks_payload.get("revision") or 0) + 1,
                }
            )
            self.json_store.write_json(chunks_payload_key, chunks_payload)
            manifest = self.get_manifest(workspace_id, knowledge_base_id, doc_id)
            manifest.update(
                {
                    "embedding_status": "indexed",
                    "embedding_provider": provider,
                    "embedding_model": model,
                    "embedding_dimension": int(dimension),
                    "embedding_collection": next_collection,
                    "embedding_version_id": version_id,
                    "chunk_embedded": len(chunks),
                    "chunk_failed": 0,
                    "search_available": bool(chunks),
                    "last_job_id": job_id or manifest.get("last_job_id"),
                    "updated_at": now,
                    "revision": int(manifest.get("revision") or 0) + 1,
                }
            )
            self.json_store.write_json(
                document_manifest_key(workspace_id, knowledge_base_id, doc_id),
                manifest,
            )
            self._upsert_documents_index(workspace_id, knowledge_base_id, manifest)

        version_manifest_key = embedding_version_manifest_key(
            workspace_id,
            knowledge_base_id,
            version_id,
        )
        version = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "version_id": version_id,
            "provider": provider,
            "model": model,
            "dimension": int(dimension),
            "collection": next_collection,
            "status": "active",
            "previous_version_id": previous.get("version_id"),
            "previous_collection": previous.get("collection"),
            "chunk_count": chunk_count,
            "job_id": job_id,
            "manifest_object_key": version_manifest_key,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        self.json_store.write_json(version_manifest_key, version)
        self._upsert_embedding_version(workspace_id, knowledge_base_id, version)
        active = {
            **version,
            "active": True,
        }
        previous_revision = int(previous.get("revision") or 0)
        active["revision"] = previous_revision + 1
        self.json_store.write_json(active_embedding_key(workspace_id, knowledge_base_id), active)
        return {
            "active_embedding": active,
            "embedding_version": version,
            "chunk_count": chunk_count,
            "version_manifest_object_key": version_manifest_key,
        }

    def _write_external_vectors(
        self,
        *,
        collection: str,
        dimension: int,
        provider: str,
        model: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        if not chunks:
            return
        if self.embedding_client is None or self.vector_store is None:
            raise ValueError(
                "Embedding reindex requires a configured embedding client and vector store."
            )
        if hasattr(self.vector_store, "ensure_collection"):
            self.vector_store.ensure_collection(collection=collection, dimension=dimension)
        vectors = _embed_documents(
            self.embedding_client,
            texts=[str(chunk.get("text") or "") for chunk in chunks],
            provider=provider,
            model=model,
            dimension=dimension,
        )
        if len(vectors) != len(chunks):
            raise ValueError("Embedding vector count must match chunk count.")
        for vector in vectors:
            if dimension > 0 and len(vector) != dimension:
                raise ValueError("Embedding vector dimension must match requested dimension.")
        records = [
            _vector_record(
                chunk,
                vector=vector,
                provider=provider,
                model=model,
                dimension=dimension,
                collection=collection,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        if hasattr(self.vector_store, "upsert"):
            _validate_upsert_result(
                self.vector_store.upsert(collection=collection, records=records),
                expected_count=len(records),
            )
            return
        if hasattr(self.vector_store, "upsert_vectors"):
            _validate_upsert_result(
                self.vector_store.upsert_vectors(collection=collection, records=records),
                expected_count=len(records),
            )
            return
        raise ValueError("Vector store must expose upsert() or upsert_vectors().")

    def list_knowledge_bases(self, workspace_id: str) -> list[dict[str, Any]]:
        index = self.json_store.read_json_or_default(
            knowledge_bases_index_key(workspace_id),
            {"knowledge_bases": []},
        )
        items = index.get("knowledge_bases", [])
        return items if isinstance(items, list) else []

    def create_uploaded_document(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        content: bytes | str,
        source_file_name: str,
        mime_type: str | None,
        doc_id: str | None = None,
        doc_version_id: str | None = None,
        last_job_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workspace_id = safe_path_id("workspace_id", workspace_id)
        knowledge_base_id = safe_path_id("knowledge_base_id", knowledge_base_id)
        doc_id = safe_path_id("doc_id", doc_id or new_id("doc"))
        doc_version_id = safe_path_id("doc_version_id", doc_version_id or new_id("docv"))
        source_file_name = safe_file_name(source_file_name)
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        now = utc_now_iso()

        self.ensure_knowledge_base(workspace_id, knowledge_base_id)
        original_key = document_original_key(
            workspace_id,
            knowledge_base_id,
            doc_id,
            source_file_name,
        )
        self.object_store.write_bytes(original_key, raw_bytes, content_type=mime_type)
        manifest = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "doc_id": doc_id,
            "doc_version_id": doc_version_id,
            "current_doc_version_id": doc_version_id,
            "source_file_name": source_file_name,
            "mime_type": mime_type or "text/plain",
            "size_bytes": len(raw_bytes),
            "file_sha256": file_sha256,
            "title": (metadata or {}).get("title") or source_file_name,
            "parser_name": None,
            "parser_quality": None,
            "ingestion_status": "uploaded",
            "parse_status": "uploaded",
            "chunk_status": "pending",
            "embedding_status": "pending",
            "graph_status": "pending",
            "chunk_total": 0,
            "chunk_embedded": 0,
            "chunk_failed": 0,
            "search_available": False,
            "graphrag_available": False,
            "retryable": False,
            "failure_strategy": None,
            "last_error": None,
            "last_job_id": last_job_id,
            "warnings": [],
            "metadata": metadata or {},
            "object_keys": {
                "manifest": document_manifest_key(workspace_id, knowledge_base_id, doc_id),
                "versions": document_versions_key(workspace_id, knowledge_base_id, doc_id),
                "original": original_key,
                "parsed_text": parsed_text_key(workspace_id, knowledge_base_id, doc_id),
                "document_representation": document_representation_key(
                    workspace_id,
                    knowledge_base_id,
                    doc_id,
                ),
                "chunks": document_chunks_index_key(workspace_id, knowledge_base_id, doc_id),
                "chunk_errors": document_chunk_errors_prefix(
                    workspace_id,
                    knowledge_base_id,
                    doc_id,
                ),
            },
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        self.json_store.write_json(
            document_manifest_key(workspace_id, knowledge_base_id, doc_id),
            manifest,
        )
        self.json_store.write_json(
            document_versions_key(workspace_id, knowledge_base_id, doc_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "doc_id": doc_id,
                "current_doc_version_id": doc_version_id,
                "versions": [
                    {
                        "doc_version_id": doc_version_id,
                        "version_no": 1,
                        "status": "current",
                        "manifest_object_key": manifest["object_keys"]["manifest"],
                        "created_at": now,
                    }
                ],
                "updated_at": now,
                "revision": 1,
            },
        )
        self._upsert_documents_index(workspace_id, knowledge_base_id, manifest)
        return manifest

    def ingest_document_content(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        content: bytes | str,
        filename: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        workspace_id = safe_path_id("workspace_id", workspace_id)
        knowledge_base_id = safe_path_id("knowledge_base_id", knowledge_base_id)
        doc_id = safe_path_id("doc_id", doc_id)
        manifest = self.get_manifest(workspace_id, knowledge_base_id, doc_id)
        doc_version_id = str(manifest["doc_version_id"])
        parsed = self.parser.parse(content, filename=filename, content_type=content_type)
        merged_metadata = {
            "source_file_name": filename or manifest["source_file_name"],
            "mime_type": content_type or manifest["mime_type"],
            **(metadata or {}),
        }
        chunks = self.chunker.chunk(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            doc_version_id=doc_version_id,
            parsed_document=parsed,
            metadata=merged_metadata,
        )
        chunks = [
            replace(
                chunk,
                object_key=document_chunk_key(
                    workspace_id,
                    knowledge_base_id,
                    doc_id,
                    chunk.chunk_id,
                ),
            )
            for chunk in chunks
        ]
        return self._persist_ingestion(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            parsed_text=parsed.text,
            parsed_document={
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "doc_id": doc_id,
                "doc_version_id": doc_version_id,
                "parser": self.parser.version,
                "parser_version": self.parser.version,
                "document_format": parsed.document_format,
                "metadata": parsed.metadata,
                "blocks": parsed.blocks,
            },
            chunks=chunks,
            job_id=job_id,
        )

    def ingest_bytes(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        content: bytes | str,
        filename: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        return self.ingest_document_content(
            workspace_id=workspace_id,
            knowledge_base_id=knowledge_base_id,
            doc_id=doc_id,
            content=content,
            filename=filename,
            content_type=content_type,
            metadata=metadata,
            job_id=job_id,
        )

    def mark_ingestion_failed(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        stage: str,
        error_type: str,
        message: str,
        retryable: bool,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        workspace_id = safe_path_id("workspace_id", workspace_id)
        knowledge_base_id = safe_path_id("knowledge_base_id", knowledge_base_id)
        doc_id = safe_path_id("doc_id", doc_id)
        manifest = self.get_manifest(workspace_id, knowledge_base_id, doc_id)
        now = utc_now_iso()
        warning = {
            "stage": stage,
            "error_type": error_type,
            "message": message,
            "retryable": retryable,
            "created_at": now,
        }
        JsonlSegmentStore(
            self.object_store,
            document_chunk_errors_prefix(workspace_id, knowledge_base_id, doc_id),
        ).append(
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "doc_id": doc_id,
                "doc_version_id": manifest.get("doc_version_id"),
                **warning,
            }
        )
        manifest.update(
            {
                "parser_name": self.parser.version,
                "parser_quality": "failed",
                "ingestion_status": "failed",
                "parse_status": (
                    "failed" if stage.startswith("parse") else manifest.get("parse_status")
                ),
                "chunk_status": "failed",
                "embedding_status": "pending",
                "graph_status": manifest.get("graph_status") or "pending",
                "chunk_total": 0,
                "chunk_embedded": 0,
                "chunk_failed": 1,
                "search_available": False,
                "graphrag_available": False,
                "retryable": bool(retryable),
                "failure_strategy": _failure_strategy(
                    ingestion_status="failed",
                    retryable=bool(retryable),
                ),
                "last_error": warning,
                "last_job_id": job_id or manifest.get("last_job_id"),
                "warnings": [*list(manifest.get("warnings") or []), warning],
                "updated_at": now,
                "revision": int(manifest.get("revision") or 0) + 1,
            }
        )
        self.json_store.write_json(
            document_manifest_key(workspace_id, knowledge_base_id, doc_id),
            manifest,
        )
        self._upsert_documents_index(workspace_id, knowledge_base_id, manifest)
        return manifest

    def get_manifest(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
    ) -> dict[str, Any]:
        return self.json_store.read_json(
            document_manifest_key(workspace_id, knowledge_base_id, doc_id)
        )

    def list_documents(self, workspace_id: str, knowledge_base_id: str) -> list[dict[str, Any]]:
        index = self.json_store.read_json_or_default(
            documents_index_key(workspace_id, knowledge_base_id),
            {"documents": []},
        )
        documents = index.get("documents", [])
        return documents if isinstance(documents, list) else []

    def get_chunks(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
    ) -> list[dict[str, Any]]:
        key = document_chunks_index_key(workspace_id, knowledge_base_id, doc_id)
        if not self.object_store.exists(key):
            key = legacy_document_chunks_index_key(workspace_id, knowledge_base_id, doc_id)
        payload = self.json_store.read_json(key)
        records = payload.get("chunks", [])
        if not isinstance(records, list):
            return []
        hydrated: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            object_key = record.get("object_key")
            if object_key and self.object_store.exists(str(object_key)):
                hydrated.append(self.json_store.read_json(str(object_key)))
            else:
                hydrated.append(record)
        return hydrated

    def get_chunk(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        *,
        chunk_id: str | None = None,
        doc_id: str | None = None,
        object_key: str | None = None,
    ) -> dict[str, Any]:
        if object_key:
            return self.json_store.read_json(object_key)
        if doc_id and chunk_id:
            key = document_chunk_key(workspace_id, knowledge_base_id, doc_id, chunk_id)
            if self.object_store.exists(key):
                return self.json_store.read_json(key)
        if chunk_id:
            for document in self.list_documents(workspace_id, knowledge_base_id):
                candidate_doc_id = str(document.get("doc_id") or "")
                if not candidate_doc_id:
                    continue
                for chunk in self.get_chunks(workspace_id, knowledge_base_id, candidate_doc_id):
                    if chunk.get("chunk_id") == chunk_id:
                        return chunk
        raise ObjectNotFoundError(chunk_id or object_key or "chunk")

    def search(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        query: str,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            result.to_record()
            for result in self.search_index.search(
                safe_path_id("workspace_id", workspace_id),
                safe_path_id("knowledge_base_id", knowledge_base_id),
                query,
                limit,
                filters=filters or {},
            )
        ]

    def _persist_ingestion(
        self,
        *,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        parsed_text: str,
        parsed_document: dict[str, Any],
        chunks: list[DocumentChunk],
        job_id: str | None = None,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(workspace_id, knowledge_base_id, doc_id)
        now = utc_now_iso()
        active_embedding = self.get_active_embedding(workspace_id, knowledge_base_id)
        should_index_vectors = _is_external_embedding_active(active_embedding)
        active_embedding_version_id = str(active_embedding.get("version_id") or "")
        self.json_store.write_json(
            parsed_text_key(workspace_id, knowledge_base_id, doc_id),
            {"schema_version": 1, "text": parsed_text, "updated_at": now},
        )
        self.json_store.write_json(
            document_representation_key(workspace_id, knowledge_base_id, doc_id),
            parsed_document,
        )

        previous_successful = self._successful_chunk_records(
            workspace_id,
            knowledge_base_id,
            doc_id,
            doc_version_id=str(manifest["doc_version_id"]),
        )
        chunk_records = []
        chunk_errors = []
        for chunk in chunks:
            if not chunk.object_key:
                raise ValueError("chunk object_key must be set before persisting.")
            existing = previous_successful.get(chunk.chunk_id)
            existing_embedding_version_id = str((existing or {}).get("embedding_version_id") or "")
            if (
                should_index_vectors
                and existing
                and existing.get("text_hash") == chunk.text_hash
                and existing_embedding_version_id == active_embedding_version_id
            ):
                skipped_record = _chunk_index_record(
                    chunk,
                    status="indexed",
                    retry_skipped=True,
                )
                _apply_embedding_to_chunk(
                    skipped_record,
                    provider=str(active_embedding["provider"]),
                    model=str(active_embedding["model"]),
                    dimension=int(active_embedding["dimension"]),
                    collection=str(active_embedding["collection"]),
                    version_id=active_embedding_version_id,
                )
                chunk_records.append(skipped_record)
                continue
            chunk_record = _chunk_record_with_status(chunk, status="persisting")
            try:
                self.json_store.write_json(chunk.object_key, chunk_record)
                if should_index_vectors:
                    indexed_record = _chunk_record_with_status(chunk, status="indexed")
                    _apply_embedding_to_chunk(
                        indexed_record,
                        provider=str(active_embedding["provider"]),
                        model=str(active_embedding["model"]),
                        dimension=int(active_embedding["dimension"]),
                        collection=str(active_embedding["collection"]),
                        version_id=active_embedding_version_id,
                    )
                    self._write_external_vectors(
                        collection=str(active_embedding["collection"]),
                        dimension=int(active_embedding["dimension"]),
                        provider=str(active_embedding["provider"]),
                        model=str(active_embedding["model"]),
                        chunks=[indexed_record],
                    )
                    self.json_store.write_json(chunk.object_key, indexed_record)
                    index_record = _chunk_index_record(chunk, status="indexed")
                    _apply_embedding_to_chunk(
                        index_record,
                        provider=str(active_embedding["provider"]),
                        model=str(active_embedding["model"]),
                        dimension=int(active_embedding["dimension"]),
                        collection=str(active_embedding["collection"]),
                        version_id=active_embedding_version_id,
                    )
                    chunk_records.append(index_record)
                else:
                    chunked_record = _chunk_record_with_status(chunk, status="chunked")
                    self.json_store.write_json(chunk.object_key, chunked_record)
                    chunk_records.append(_chunk_index_record(chunk, status="chunked"))
            except Exception as exc:  # noqa: BLE001 - chunk-level failure must be resumable.
                error = _chunk_error(
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    doc_id=doc_id,
                    doc_version_id=str(manifest["doc_version_id"]),
                    chunk_id=chunk.chunk_id,
                    chunk_index=chunk.chunk_index,
                    stage="chunk_index",
                    exc=exc,
                )
                failed_record = _chunk_record_with_status(chunk, status="failed", error=error)
                if self.object_store.exists(chunk.object_key):
                    self.json_store.write_json(chunk.object_key, failed_record)
                chunk_records.append(_chunk_index_record(chunk, status="failed", error=error))
                chunk_errors.append(error)
        if chunk_errors:
            error_store = JsonlSegmentStore(
                self.object_store,
                document_chunk_errors_prefix(workspace_id, knowledge_base_id, doc_id),
            )
            for error in chunk_errors:
                error_store.append({"schema_version": 1, **error})
        indexed_count = sum(
            1 for record in chunk_records if record.get("chunk_status") == "indexed"
        )
        persisted_count = sum(
            1
            for record in chunk_records
            if record.get("chunk_status") in {"chunked", "indexed"}
        )
        failed_count = sum(1 for record in chunk_records if record.get("chunk_status") == "failed")
        if should_index_vectors:
            if indexed_count and failed_count:
                ingestion_status = "partial_success"
                chunk_status = "partial_success"
                embedding_status = "partial_success"
                parser_quality = "partial"
            elif indexed_count:
                ingestion_status = "indexed"
                chunk_status = "chunked"
                embedding_status = "indexed"
                parser_quality = "full"
            else:
                ingestion_status = "failed"
                chunk_status = "failed"
                embedding_status = "pending"
                parser_quality = "failed" if chunks else "empty"
        elif persisted_count and failed_count:
            ingestion_status = "partial_success"
            chunk_status = "partial_success"
            embedding_status = "pending"
            parser_quality = "partial"
        elif persisted_count:
            ingestion_status = "chunked"
            chunk_status = "chunked"
            embedding_status = "pending"
            parser_quality = "full"
        else:
            ingestion_status = "failed"
            chunk_status = "failed"
            embedding_status = "pending"
            parser_quality = "failed" if chunks else "empty"
        failure_contract = _failure_contract(
            ingestion_status=ingestion_status,
            current_errors=chunk_errors,
        )
        chunks_payload = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "doc_id": doc_id,
            "doc_version_id": manifest["doc_version_id"],
            "chunker_version": self.chunker.version,
            "chunk_count": len(chunk_records),
            "chunks": chunk_records,
            "updated_at": now,
            "revision": int(manifest.get("revision") or 0) + 1,
        }
        self.json_store.write_json(
            document_chunks_index_key(workspace_id, knowledge_base_id, doc_id),
            chunks_payload,
        )
        warnings = [*list(manifest.get("warnings") or []), *chunk_errors]

        manifest.update(
            {
                "parser_name": self.parser.version,
                "parser_quality": parser_quality,
                "ingestion_status": ingestion_status,
                "parse_status": "parsed",
                "chunk_status": chunk_status,
                "embedding_status": embedding_status,
                "graph_status": manifest.get("graph_status") or "pending",
                "chunk_total": len(chunks),
                "chunk_embedded": indexed_count,
                "chunk_failed": failed_count,
                "search_available": bool(indexed_count),
                "graphrag_available": False,
                **failure_contract,
                "last_job_id": job_id or manifest.get("last_job_id"),
                "warnings": warnings,
                "updated_at": now,
                "revision": int(manifest.get("revision") or 0) + 1,
            }
        )
        self.json_store.write_json(
            document_manifest_key(workspace_id, knowledge_base_id, doc_id),
            manifest,
        )
        self._upsert_documents_index(workspace_id, knowledge_base_id, manifest)
        return manifest

    def _successful_chunk_records(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        doc_id: str,
        *,
        doc_version_id: str,
    ) -> dict[str, dict[str, Any]]:
        key = document_chunks_index_key(workspace_id, knowledge_base_id, doc_id)
        if not self.object_store.exists(key):
            return {}
        payload = self.json_store.read_json(key)
        successful: dict[str, dict[str, Any]] = {}
        for record in payload.get("chunks", []):
            if not isinstance(record, dict):
                continue
            chunk_id = str(record.get("chunk_id") or "")
            object_key = str(record.get("object_key") or "")
            if (
                not chunk_id
                or not object_key
                or record.get("doc_version_id") != doc_version_id
                or record.get("chunk_status") != "indexed"
                or record.get("search_index_status") != "indexed"
                or not self.object_store.exists(object_key)
            ):
                continue
            chunk = self.json_store.read_json(object_key)
            if (
                chunk.get("chunk_status") == "indexed"
                and chunk.get("search_index_status") == "indexed"
            ):
                successful[chunk_id] = chunk
        return successful

    def _upsert_knowledge_base_index(
        self,
        workspace_id: str,
        manifest: dict[str, Any],
    ) -> None:
        key = knowledge_bases_index_key(workspace_id)
        index = self.json_store.read_json_or_default(
            key,
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_bases": [],
                "revision": 0,
            },
        )
        summary = {
            "workspace_id": workspace_id,
            "knowledge_base_id": manifest["knowledge_base_id"],
            "name": manifest.get("name") or manifest["knowledge_base_id"],
            "status": manifest.get("status", "active"),
            "manifest_object_key": knowledge_base_manifest_key(
                workspace_id,
                manifest["knowledge_base_id"],
            ),
            "updated_at": manifest["updated_at"],
        }
        index["knowledge_bases"] = [
            item
            for item in index.get("knowledge_bases", [])
            if item.get("knowledge_base_id") != manifest["knowledge_base_id"]
        ]
        index["knowledge_bases"].append(summary)
        index["revision"] = int(index.get("revision") or 0) + 1
        index["updated_at"] = utc_now_iso()
        self.json_store.write_json(key, index)

    def _upsert_documents_index(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        manifest: dict[str, Any],
    ) -> None:
        key = documents_index_key(workspace_id, knowledge_base_id)
        index = self.json_store.read_json_or_default(
            key,
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "documents": [],
                "revision": 0,
            },
        )
        summary = self._document_summary(manifest)
        index["documents"] = [
            item for item in index.get("documents", []) if item.get("doc_id") != manifest["doc_id"]
        ]
        index["documents"].append(summary)
        index["documents"] = sorted(
            index["documents"],
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        index["updated_at"] = utc_now_iso()
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(key, index)

    def _upsert_embedding_version(
        self,
        workspace_id: str,
        knowledge_base_id: str,
        version: dict[str, Any],
    ) -> None:
        key = embedding_versions_index_key(workspace_id, knowledge_base_id)
        index = self.json_store.read_json_or_default(
            key,
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "knowledge_base_id": knowledge_base_id,
                "versions": [],
                "revision": 0,
            },
        )
        versions = [
            {
                **item,
                "status": "readonly_retained"
                if item.get("status") == "active"
                else item.get("status", "readonly_retained"),
            }
            for item in index.get("versions", [])
            if item.get("version_id") != version["version_id"]
        ]
        versions.append(
            {
                "version_id": version["version_id"],
                "provider": version["provider"],
                "model": version["model"],
                "dimension": version["dimension"],
                "collection": version["collection"],
                "status": version["status"],
                "chunk_count": version["chunk_count"],
                "manifest_object_key": version["manifest_object_key"],
                "updated_at": version["updated_at"],
            }
        )
        index["versions"] = versions
        index["updated_at"] = utc_now_iso()
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(key, index)

    @staticmethod
    def _document_summary(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "workspace_id": manifest["workspace_id"],
            "knowledge_base_id": manifest["knowledge_base_id"],
            "doc_id": manifest["doc_id"],
            "doc_version_id": manifest["doc_version_id"],
            "current_doc_version_id": manifest["current_doc_version_id"],
            "source_file_name": manifest["source_file_name"],
            "mime_type": manifest["mime_type"],
            "size_bytes": manifest["size_bytes"],
            "file_sha256": manifest["file_sha256"],
            "title": manifest.get("title"),
            "parser_quality": manifest.get("parser_quality"),
            "ingestion_status": manifest["ingestion_status"],
            "parse_status": manifest.get("parse_status"),
            "chunk_status": manifest.get("chunk_status"),
            "embedding_status": manifest.get("embedding_status"),
            "graph_status": manifest.get("graph_status"),
            "chunk_total": manifest.get("chunk_total", 0),
            "chunk_embedded": manifest.get("chunk_embedded", 0),
            "chunk_failed": manifest.get("chunk_failed", 0),
            "search_available": manifest.get("search_available", False),
            "graphrag_available": manifest.get("graphrag_available", False),
            "retryable": bool(manifest.get("retryable", False)),
            "failure_strategy": manifest.get("failure_strategy"),
            "last_error": manifest.get("last_error"),
            "last_job_id": manifest.get("last_job_id"),
            "warnings": manifest.get("warnings", []),
            "manifest_object_key": document_manifest_key(
                manifest["workspace_id"],
                manifest["knowledge_base_id"],
                manifest["doc_id"],
            ),
            "updated_at": manifest["updated_at"],
            "created_at": manifest["created_at"],
        }


def _safe_collection_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", str(value).strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized[:64] or "embedding"


def _is_external_embedding_active(active_embedding: dict[str, Any]) -> bool:
    try:
        dimension = int(active_embedding.get("dimension") or 0)
    except (TypeError, ValueError):
        dimension = 0
    return (
        str(active_embedding.get("provider") or "") != "local_fallback"
        and str(active_embedding.get("model") or "") != "object_store_lexical_fallback"
        and str(active_embedding.get("collection") or "") != "object_store_lexical_fallback"
        and dimension > 0
    )


def _is_local_fallback_embedding(active_embedding: dict[str, Any]) -> bool:
    return (
        str(active_embedding.get("provider") or "") == "local_fallback"
        or str(active_embedding.get("model") or "") == "object_store_lexical_fallback"
        or str(active_embedding.get("collection") or "") == "object_store_lexical_fallback"
    )


def _failure_contract(
    *,
    ingestion_status: str,
    current_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    if ingestion_status not in {"partial_success", "failed"}:
        return {
            "retryable": False,
            "failure_strategy": None,
            "last_error": None,
        }
    retryable = any(bool(error.get("retryable")) for error in current_errors)
    return {
        "retryable": retryable,
        "failure_strategy": _failure_strategy(
            ingestion_status=ingestion_status,
            retryable=retryable,
        ),
        "last_error": current_errors[-1] if current_errors else None,
    }


def _failure_strategy(*, ingestion_status: str, retryable: bool) -> str:
    if ingestion_status == "partial_success":
        return "retry_failed_chunks" if retryable else "inspect_failed_chunks"
    return "retry_document_ingestion" if retryable else "replace_or_skip_document"


def _chunk_record_with_status(
    chunk: DocumentChunk,
    *,
    status: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = chunk.to_record()
    status_fields = _chunk_status_fields(status=status, error=error)
    record["metadata_filter"] = _metadata_with_chunk_status(
        chunk.metadata_filter,
        status_fields,
    )
    record.update(status_fields)
    return record


def _chunk_index_record(
    chunk: DocumentChunk,
    *,
    status: str,
    error: dict[str, Any] | None = None,
    retry_skipped: bool = False,
) -> dict[str, Any]:
    record = {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "doc_version_id": chunk.doc_version_id,
        "chunk_index": chunk.chunk_index,
        "parent_chunk_id": chunk.parent_chunk_id,
        "section_path": chunk.section_path,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "token_count": chunk.token_count,
        "text_hash": chunk.text_hash,
        "object_key": chunk.object_key,
        "embedding_status": "indexed" if status == "indexed" else "pending",
        "graph_status": "pending",
        "text": chunk.text,
        "metadata_filter": _metadata_with_chunk_status(
            chunk.metadata_filter,
            _chunk_status_fields(status=status, error=error),
        ),
    }
    record.update(_chunk_status_fields(status=status, error=error))
    if retry_skipped:
        record["retry_skipped"] = True
    return record


def _chunk_status_fields(
    *,
    status: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status == "indexed":
        return {
            "chunk_status": "indexed",
            "search_index_status": "indexed",
            "retryable": False,
            "last_error": None,
        }
    if status == "persisting":
        return {
            "chunk_status": "persisting",
            "search_index_status": "pending",
            "retryable": True,
            "last_error": None,
        }
    if status == "chunked":
        return {
            "chunk_status": "chunked",
            "search_index_status": "pending",
            "retryable": False,
            "last_error": None,
        }
    return {
        "chunk_status": "failed",
        "search_index_status": "failed",
        "retryable": bool((error or {}).get("retryable", True)),
        "last_error": error,
    }


def _metadata_with_chunk_status(
    metadata: dict[str, Any],
    status_fields: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(metadata)
    enriched["chunk_status"] = status_fields["chunk_status"]
    enriched["search_index_status"] = status_fields["search_index_status"]
    enriched["retryable"] = bool(status_fields["retryable"])
    if status_fields.get("last_error") is not None:
        enriched["last_error"] = status_fields["last_error"]
    else:
        enriched.pop("last_error", None)
    return enriched


def _chunk_error(
    *,
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    doc_version_id: str,
    chunk_id: str,
    chunk_index: int,
    stage: str,
    exc: Exception,
) -> dict[str, Any]:
    error_type = str(getattr(exc, "error_type", "") or exc.__class__.__name__)
    retryable = getattr(exc, "retryable", None)
    if retryable is None:
        retryable = not isinstance(exc, ValueError)
    return {
        "workspace_id": workspace_id,
        "knowledge_base_id": knowledge_base_id,
        "doc_id": doc_id,
        "doc_version_id": doc_version_id,
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "stage": stage,
        "error_type": error_type,
        "message": str(exc) or error_type,
        "retryable": bool(retryable),
        "created_at": utc_now_iso(),
    }


def _apply_embedding_to_chunk(
    chunk: dict[str, Any],
    *,
    provider: str,
    model: str,
    dimension: int,
    collection: str,
    version_id: str,
) -> None:
    metadata_filter = dict(chunk.get("metadata_filter") or {})
    metadata_filter.update(
        {
            "embedding_provider": provider,
            "embedding_model": model,
            "embedding_dimension": int(dimension),
            "embedding_collection": collection,
            "embedding_version_id": version_id,
        }
    )
    chunk.update(
        {
            "embedding_status": "indexed",
            "embedding_provider": provider,
            "embedding_model": model,
            "embedding_dimension": int(dimension),
            "embedding_collection": collection,
            "embedding_version_id": version_id,
            "metadata_filter": metadata_filter,
        }
    )


def _embed_documents(
    embedding_client: Any,
    *,
    texts: list[str],
    provider: str,
    model: str,
    dimension: int,
) -> list[list[float]]:
    if hasattr(embedding_client, "embed_documents"):
        return _normalize_vectors(
            embedding_client.embed_documents(
                texts=texts,
                provider=provider,
                model=model,
                dimension=dimension,
            )
        )
    if hasattr(embedding_client, "embed_many"):
        return _normalize_vectors(
            embedding_client.embed_many(
                texts=texts,
                provider=provider,
                model=model,
                dimension=dimension,
            )
        )
    if hasattr(embedding_client, "embed_query"):
        return [
            _normalize_vector(
                embedding_client.embed_query(
                    text=text,
                    provider=provider,
                    model=model,
                    dimension=dimension,
                )
            )
            for text in texts
        ]
    raise ValueError(
        "Embedding client must expose embed_documents(), embed_many(), or embed_query()."
    )


def _vector_record(
    chunk: dict[str, Any],
    *,
    vector: list[float],
    provider: str,
    model: str,
    dimension: int,
    collection: str,
) -> dict[str, Any]:
    metadata = dict(chunk.get("metadata_filter") or {})
    metadata.update(
        {
            "embedding_collection": collection,
            "embedding_dimension": int(dimension),
            "embedding_model": model,
            "embedding_provider": provider,
        }
    )
    return {
        "chunk_id": str(chunk["chunk_id"]),
        "doc_id": str(chunk["doc_id"]),
        "doc_version_id": str(chunk["doc_version_id"]),
        "workspace_id": str(metadata.get("workspace_id") or chunk.get("workspace_id") or ""),
        "knowledge_base_id": str(
            metadata.get("knowledge_base_id") or chunk.get("knowledge_base_id") or ""
        ),
        "object_key": str(chunk.get("object_key") or ""),
        "text": str(chunk.get("text") or ""),
        "vector": vector,
        "metadata": metadata,
        "source": dict(chunk.get("source") or {}),
    }


def _normalize_vectors(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        raise ValueError("Embedding client must return a list of vectors.")
    return [_normalize_vector(item) for item in value]


def _normalize_vector(value: Any) -> list[float]:
    if not isinstance(value, list):
        raise ValueError("Embedding vector must be a list.")
    return [float(item) for item in value]


def _validate_upsert_result(value: Any, *, expected_count: int) -> None:
    if not isinstance(value, dict):
        return
    if value.get("ok") is False:
        raise VectorStoreUpsertError(
            str(value.get("error_type") or "vector_store_upsert_failed"),
            str(
                value.get("message_for_user")
                or value.get("message")
                or "Vector store upsert failed."
            ),
            retryable=bool(value.get("retryable", False)),
            details=dict(value),
        )
    failed_count = value.get("failed_count")
    if failed_count is not None and int(failed_count) > 0:
        raise VectorStoreUpsertError(
            str(value.get("error_type") or "vector_store_upsert_partial_failure"),
            str(
                value.get("message_for_user")
                or value.get("message")
                or "Vector store upsert completed with failures."
            ),
            retryable=bool(value.get("retryable", True)),
            details=dict(value),
        )
    raw_count = value.get("upserted_count", value.get("inserted_count"))
    if raw_count is None:
        return
    if int(raw_count) != expected_count:
        raise ValueError("Vector store upsert count must match chunk count.")


class VectorStoreUpsertError(ValueError):
    def __init__(
        self,
        error_type: str,
        message_for_user: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.error_type = error_type
        self.message_for_user = message_for_user
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message_for_user)
