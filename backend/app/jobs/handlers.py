from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.settings import Settings, get_settings
from app.database.service import DatabaseConfigService
from app.graph_pipeline.builder import GraphBuildJobService
from app.jobs.worker import JobContext, JobHandler, JobHandlerResult
from app.mcp.service import McpCapabilityProvider, McpService
from app.mcp_client.invocation import McpInvocationError
from app.observability.service import ObservabilityService
from app.rag_pipeline.ingestion import DocumentIngestionService
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import (
    database_health_snapshot_key,
    memory_sync_state_key,
    subagent_task_result_key,
)


def build_document_ingestion_handler(
    object_store: ObjectStore,
    embedding_client: Any | None = None,
    embedding_client_factory: Callable[[JobContext], Any | None] | None = None,
    vector_store: Any | None = None,
    vector_store_factory: Callable[[JobContext], Any | None] | None = None,
) -> JobHandler:
    document_service = DocumentIngestionService(object_store)

    def handle(context: JobContext) -> JobHandlerResult:
        target_scope = context.target_scope
        knowledge_base_id = str(target_scope["knowledge_base_id"])
        doc_id = str(target_scope["doc_id"])
        manifest = document_service.get_manifest(
            context.workspace_id,
            knowledge_base_id,
            doc_id,
        )
        original_key = str(manifest["object_keys"]["original"])
        content = object_store.read_bytes(original_key)
        context.mark_running(
            stage="parse_chunk_index",
            message="Parsing and chunking document.",
            percent=10,
        )
        try:
            active_embedding = document_service.get_active_embedding(
                context.workspace_id,
                knowledge_base_id,
            )
            should_index_vectors = _requires_vector_ingestion(active_embedding)
            active_document_service = DocumentIngestionService(
                object_store,
                embedding_client=(
                    _resolve_job_dependency(
                        context,
                        fixed=embedding_client,
                        factory=embedding_client_factory,
                    )
                    if should_index_vectors
                    else None
                ),
                vector_store=(
                    _resolve_job_dependency(
                        context,
                        fixed=vector_store,
                        factory=vector_store_factory,
                    )
                    if should_index_vectors
                    else None
                ),
            )
            indexed = active_document_service.ingest_document_content(
                workspace_id=context.workspace_id,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
                content=content,
                filename=str(manifest["source_file_name"]),
                content_type=manifest.get("mime_type"),
                metadata=_safe_metadata(manifest.get("metadata")),
                job_id=context.job_id,
            )
        except ValueError as exc:
            error_type = (
                "parser_backend_not_configured"
                if "Parser backend is not configured" in str(exc)
                else (
                    "document_embedding_index_failed"
                    if "Embedding" in str(exc) or "Vector" in str(exc)
                    else "document_parse_failed"
                )
            )
            document_service.mark_ingestion_failed(
                workspace_id=context.workspace_id,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
                stage="parse_chunk_index",
                error_type=error_type,
                message=str(exc) or error_type,
                retryable=error_type == "parser_backend_not_configured",
                job_id=context.job_id,
            )
            return JobHandlerResult.failed(
                stage="parse_chunk_index",
                message="Document parsing failed; manifest was updated.",
                error_type=error_type,
                retryable=error_type == "parser_backend_not_configured",
            )
        artifacts = [
            {
                "doc_id": doc_id,
                "manifest_object_key": indexed["object_keys"]["manifest"],
                "chunks_object_key": indexed["object_keys"]["chunks"],
                "chunk_total": indexed.get("chunk_total", 0),
                "chunk_embedded": indexed.get("chunk_embedded", 0),
                "chunk_failed": indexed.get("chunk_failed", 0),
            }
        ]
        if indexed.get("ingestion_status") == "partial_success":
            retryable = any(
                bool(warning.get("retryable")) for warning in indexed.get("warnings", [])
            )
            return JobHandlerResult.partial_success(
                stage="partial_success",
                message="Document ingestion completed with failed chunks.",
                artifacts=artifacts,
                error_type="document_chunk_partial_failure",
                retryable=retryable,
            )
        if indexed.get("ingestion_status") == "failed":
            retryable = any(
                bool(warning.get("retryable")) for warning in indexed.get("warnings", [])
            )
            return JobHandlerResult.failed(
                stage="chunk_index",
                message="Document ingestion failed for all chunks.",
                error_type="document_chunk_index_failed",
                retryable=retryable,
            )
        return JobHandlerResult.succeeded(
            stage=str(indexed.get("ingestion_status") or "indexed"),
            message=(
                "Document is searchable through Milvus."
                if indexed.get("search_available")
                else "Document was parsed and chunked; embedding reindex is required before semantic search."
            ),
            artifacts=artifacts,
        )

    return handle


def _requires_vector_ingestion(active_embedding: dict[str, Any]) -> bool:
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


def build_subagent_execution_handler(object_store: ObjectStore) -> JobHandler:
    from app.subagents.service import SubAgentService

    service = SubAgentService(object_store)

    def handle(context: JobContext) -> JobHandlerResult:
        task_id = str(context.input.get("task_id") or context.target_scope.get("task_id") or "")
        if not task_id:
            return JobHandlerResult.failed(
                stage="validate_input",
                message="SubAgent execution job requires task_id.",
                error_type="subagent_task_id_missing",
                retryable=False,
            )
        context.mark_running(
            stage="subagent_execute",
            message="Executing queued SubAgent task.",
            percent=20,
        )
        result = service.execute_task(context.workspace_id, task_id)
        artifact = {
            "artifact_type": "subagent_result",
            "task_id": task_id,
            "result_object_key": subagent_task_result_key(context.workspace_id, task_id),
            "status": result.get("status"),
        }
        if result.get("status") == "completed":
            return JobHandlerResult.succeeded(
                stage="subagent_completed",
                message="SubAgent task completed and awaits main Agent review.",
                artifacts=[artifact],
            )
        return JobHandlerResult.failed(
            stage="subagent_failed",
            message="SubAgent task failed; result was persisted for main Agent review.",
            error_type=str(result.get("error_type") or "subagent_executor_failed"),
            retryable=False,
        )

    return handle


def build_embedding_reindex_handler(
    object_store: ObjectStore,
    *,
    settings: Settings | None = None,
    embedding_client: Any | None = None,
    vector_store: Any | None = None,
    embedding_client_factory: Callable[[JobContext], Any | None] | None = None,
    vector_store_factory: Callable[[JobContext], Any | None] | None = None,
) -> JobHandler:
    current_settings = settings or get_settings()

    def handle(context: JobContext) -> JobHandlerResult:
        target_scope = context.target_scope
        job_input = context.input
        knowledge_base_id = str(target_scope["knowledge_base_id"])
        config_id = _optional_str(job_input.get("config_id")) or "embedding"
        embedding_config = _embedding_public_config(
            object_store,
            context.workspace_id,
            config_id=config_id,
            settings=current_settings,
        )
        provider = _normalize_embedding_provider(
            _optional_str(job_input.get("provider"))
            or _optional_str(embedding_config.get("provider"))
            or "openai_compatible"
        )
        model = (
            _optional_str(job_input.get("model"))
            or _optional_str(embedding_config.get("model"))
            or current_settings.default_embedding_model_name
        )
        dimension = _resolve_positive_int(
            job_input.get("dimension"),
            embedding_config.get("dimension"),
            current_settings.default_embedding_dimension,
        )
        if not model or dimension <= 0:
            return JobHandlerResult.failed(
                stage="embedding_config",
                message="Embedding reindex requires model and positive dimension.",
                error_type="embedding_config_incomplete",
                retryable=False,
            )
        context.mark_running(
            stage="create_embedding_version",
            message="Creating embedding version and collection.",
            percent=10,
        )
        context.mark_running(
            stage="embedding_insert",
            message="Reindexing chunks into the new embedding collection.",
            percent=40,
        )
        try:
            resolved_embedding_client = _resolve_job_dependency(
                context,
                fixed=embedding_client,
                factory=embedding_client_factory,
            )
            resolved_vector_store = _resolve_job_dependency(
                context,
                fixed=vector_store,
                factory=vector_store_factory,
            )
        except Exception as exc:  # noqa: BLE001 - config boundary must redact details.
            return JobHandlerResult.failed(
                stage="embedding_backend_config",
                message="Embedding backend is not configured.",
                error_type=exc.__class__.__name__,
                retryable=False,
            )
        if resolved_embedding_client is None or resolved_vector_store is None:
            return JobHandlerResult.failed(
                stage="embedding_backend_config",
                message="Embedding backend is not configured.",
                error_type="embedding_vector_backend_not_configured",
                retryable=False,
            )

        document_service = DocumentIngestionService(
            object_store,
            embedding_client=resolved_embedding_client,
            vector_store=resolved_vector_store,
        )
        try:
            result = document_service.reindex_embeddings(
                workspace_id=context.workspace_id,
                knowledge_base_id=knowledge_base_id,
                provider=provider,
                model=model,
                dimension=dimension,
                collection=_optional_str(job_input.get("collection")),
                job_id=context.job_id,
            )
        except Exception as exc:  # noqa: BLE001 - preserve active embedding on failure.
            return JobHandlerResult.failed(
                stage="embedding_insert",
                message="Embedding reindex failed before active collection switch.",
                error_type=_job_error_type(exc),
                retryable=_job_retryable(exc, default=True),
            )
        active_embedding = result["active_embedding"]
        return JobHandlerResult.succeeded(
            stage="switch_active_embedding",
            message="Active embedding collection switched.",
            artifacts=[
                {
                    "artifact_type": "embedding_collection",
                    "knowledge_base_id": knowledge_base_id,
                    "version_id": active_embedding["version_id"],
                    "collection": active_embedding["collection"],
                    "previous_collection": active_embedding.get("previous_collection"),
                    "chunk_count": result["chunk_count"],
                    "manifest_object_key": result["version_manifest_object_key"],
                }
            ],
        )

    return handle


def build_memory_sync_handler(
    object_store: ObjectStore,
    *,
    settings: Settings | None = None,
    embedding_client_factory: Callable[[JobContext], Any | None] | None = None,
    vector_store_factory: Callable[[JobContext], Any | None] | None = None,
    graph_writer_factory: Callable[[JobContext], Any | None] | None = None,
) -> JobHandler:
    current_settings = settings or get_settings()

    def handle(context: JobContext) -> JobHandlerResult:
        from app.memory.sync_service import MemorySyncService

        job_input = context.input
        if not current_settings.memory_external_sync_enabled:
            sync_state = JsonObjectStore(object_store).read_json_or_default(
                memory_sync_state_key(context.workspace_id),
                {"pending_targets": []},
            )
            return JobHandlerResult.succeeded(
                stage="memory_sync_disabled",
                message=(
                    "External memory index sync is disabled; pending targets are retained "
                    "in the local outbox."
                ),
                artifacts=[
                    {
                        "artifact_type": "memory_sync_result",
                        "processed_count": 0,
                        "succeeded_count": 0,
                        "failed_count": 0,
                        "pending_count": len(sync_state.get("pending_targets") or []),
                        "disabled": True,
                    }
                ],
            )
        embedding_config = _embedding_public_config(
            object_store,
            context.workspace_id,
            settings=current_settings,
        )
        context.mark_running(
            stage="memory_sync",
            message="Synchronizing long-term memory indexes.",
            percent=20,
        )
        embedding_client = _optional_job_dependency(context, embedding_client_factory)
        vector_store = _optional_job_dependency(context, vector_store_factory)
        graph_writer = _optional_job_dependency(context, graph_writer_factory)
        embedding_dimension = int(
            job_input.get("dimension") or current_settings.default_embedding_dimension
        )
        embedding_model = (
            _optional_str(job_input.get("model"))
            or _optional_str(embedding_config.get("model"))
            or current_settings.default_embedding_model_name
        )
        embedding_provider = _optional_str(job_input.get("provider")) or _optional_str(
            embedding_config.get("provider")
        )
        result = MemorySyncService(
            object_store,
            embedding_client=embedding_client,
            vector_store=vector_store,
            graph_writer=graph_writer,
        ).process_pending(
            context.workspace_id,
            limit=int(job_input.get("limit") or 50),
            collection=_optional_str(job_input.get("collection"))
            or f"{context.workspace_id}_memory_{embedding_dimension}",
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            embedding_provider=embedding_provider,
        )
        artifacts = [
            {
                "artifact_type": "memory_sync_result",
                "processed_count": result["processed_count"],
                "succeeded_count": result["succeeded_count"],
                "failed_count": result["failed_count"],
                "pending_count": result["pending_count"],
            }
        ]
        if result["failed_count"] and result["succeeded_count"]:
            return JobHandlerResult.partial_success(
                stage="memory_sync_partial",
                message="Memory sync completed with failed targets retained for retry.",
                artifacts=artifacts,
                error_type="memory_sync_partial_failure",
                retryable=True,
            )
        if result["failed_count"]:
            first_error = next(
                (item for item in result["results"] if not item["ok"]),
                {},
            )
            return JobHandlerResult.failed(
                stage="memory_sync_failed",
                message="Memory sync failed; pending targets were retained.",
                error_type=str(first_error.get("error_type") or "memory_sync_failed"),
                retryable=bool(first_error.get("retryable", True)),
            )
        return JobHandlerResult.succeeded(
            stage="memory_sync_complete",
            message="Memory sync pending targets were processed.",
            artifacts=artifacts,
        )

    return handle


def _embedding_public_config(
    object_store: ObjectStore,
    workspace_id: str,
    *,
    config_id: str = "embedding",
    settings: Settings,
) -> dict[str, Any]:
    try:
        from app.model_connector.config_service import ModelConfigService

        return ModelConfigService(object_store, settings).get_config(workspace_id, config_id)
    except Exception:  # noqa: BLE001 - memory sync records missing model config per target.
        return {}


def _optional_job_dependency(
    context: JobContext,
    factory: Callable[[JobContext], Any | None] | None,
) -> Any | None:
    if factory is None:
        return None
    try:
        return factory(context)
    except Exception:  # noqa: BLE001 - per-target sync records missing dependency as job failure.
        return None


def _resolve_job_dependency(
    context: JobContext,
    *,
    fixed: Any | None,
    factory: Callable[[JobContext], Any | None] | None,
) -> Any | None:
    if factory is not None:
        return factory(context)
    return fixed


def build_graph_build_handler(
    object_store: ObjectStore,
    graph_writer: Any | None = None,
    graph_writer_factory: Callable[[JobContext], Any | None] | None = None,
    graph_extractor: Any | None = None,
    graph_extractor_factory: Callable[[JobContext], Any | None] | None = None,
) -> JobHandler:
    def handle(context: JobContext) -> JobHandlerResult:
        target_scope = context.target_scope
        knowledge_base_id = str(target_scope["knowledge_base_id"])
        doc_id = str(target_scope["doc_id"])
        doc_version_id = target_scope.get("doc_version_id")
        context.mark_running(
            stage="graph_extract",
            message="Building graph artifacts.",
            percent=10,
        )
        try:
            resolved_graph_writer = _resolve_job_dependency(
                context,
                fixed=graph_writer,
                factory=graph_writer_factory,
            )
            resolved_graph_extractor = _resolve_job_dependency(
                context,
                fixed=graph_extractor,
                factory=graph_extractor_factory,
            )
            graph_service = GraphBuildJobService(
                object_store,
                graph_writer=resolved_graph_writer,
                graph_extractor=resolved_graph_extractor,
            )
            result = graph_service.build(
                workspace_id=context.workspace_id,
                knowledge_base_id=knowledge_base_id,
                doc_id=doc_id,
                doc_version_id=str(doc_version_id) if doc_version_id else None,
                job_id=context.job_id,
                operation_id=new_id("op_graph"),
            )
        except Exception as exc:  # noqa: BLE001 - graph backend boundary records failure.
            return JobHandlerResult.failed(
                stage="graph_index",
                message="Graph build failed before graph availability switch.",
                error_type=getattr(exc, "error_type", exc.__class__.__name__),
                retryable=True,
            )
        return JobHandlerResult.succeeded(
            stage="graph_indexed",
            message="Graph artifacts are available.",
            artifacts=[
                {
                    "artifact_type": "graph_build_result",
                    "artifacts": result.get("artifacts", {}),
                    "counts": result.get("counts", {}),
                    "extraction": result.get("extraction", {}),
                }
            ],
        )

    return handle


def build_mcp_capability_refresh_handler(
    object_store: ObjectStore,
    capability_provider: McpCapabilityProvider | None = None,
    builtin_tool_names: set[str] | None = None,
) -> JobHandler:
    def handle(context: JobContext) -> JobHandlerResult:
        server_name = str(context.target_scope["server_name"])
        service = McpService(
            object_store,
            job_service=context.job_service,
            capability_provider=capability_provider,
            builtin_tool_names=builtin_tool_names,
        )
        context.mark_running(
            stage="mcp_capability_refresh",
            message="Refreshing MCP capability snapshot.",
            percent=20,
        )
        try:
            refreshed = service.execute_refresh_job(
                context.workspace_id,
                server_name,
                before_snapshot_commit=lambda: context.mark_running(
                    stage="mcp_capability_refresh_commit",
                    message="Committing MCP capability snapshot.",
                    percent=80,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - connector boundary records failure.
            error_type = (
                exc.error_type if isinstance(exc, AgentSystemError) else exc.__class__.__name__
            )
            if isinstance(exc, McpInvocationError):
                error_type = exc.error_type
            retryable = bool(getattr(exc, "retryable", True))
            if error_type != "mcp_refresh_job_lease_lost":
                service.mark_refresh_failed(
                    context.workspace_id,
                    server_name,
                    error_type=error_type,
                    message=str(exc) or error_type,
                    retryable=retryable,
                )
            return JobHandlerResult.failed(
                stage="mcp_capability_refresh",
                message="MCP capability refresh failed; previous snapshot was preserved.",
                error_type=error_type,
                retryable=retryable,
            )

        snapshot = refreshed["snapshot"]
        return JobHandlerResult.succeeded(
            stage="mcp_capability_refresh",
            message="MCP capability snapshot refreshed.",
            artifacts=[
                {
                    "artifact_type": "mcp_capability_snapshot",
                    "server_name": server_name,
                    "snapshot_hash": snapshot["snapshot_hash"],
                }
            ],
        )

    return handle


def build_database_health_check_handler(
    object_store: ObjectStore,
    *,
    settings: Settings | None = None,
    secret_resolver: Any | None = None,
) -> JobHandler:
    service = DatabaseConfigService(
        object_store,
        settings or get_settings(),
        secret_resolver=secret_resolver,
    )

    def handle(context: JobContext) -> JobHandlerResult:
        context.mark_running(
            stage="database_health_check",
            message="Checking configured database services.",
            percent=20,
        )
        snapshot = service.run_health_check_sync(context.workspace_id)
        unhealthy = [
            item["target"]
            for item in snapshot.get("services", [])
            if item.get("status") != "healthy"
        ]
        artifacts = [
            {
                "artifact_type": "database_health_snapshot",
                "object_key": database_health_snapshot_key(context.workspace_id),
                "ok": snapshot["ok"],
                "source": snapshot["source"],
                "unhealthy_targets": unhealthy,
            }
        ]
        if not unhealthy:
            return JobHandlerResult.succeeded(
                stage="write_health_snapshot",
                message="Database health snapshot updated.",
                artifacts=artifacts,
            )
        services = snapshot.get("services") if isinstance(snapshot.get("services"), list) else []
        all_targets_unhealthy = bool(services) and len(unhealthy) >= len(services)
        if all_targets_unhealthy:
            return JobHandlerResult.failed(
                stage="database_unhealthy",
                message="Database health check found no healthy targets.",
                artifacts=artifacts,
                error_type="database_health_all_targets_unhealthy",
                retryable=True,
            )
        return JobHandlerResult.partial_success(
            stage="database_degraded",
            message="Database health snapshot updated with unhealthy targets.",
            artifacts=artifacts,
            error_type="database_health_degraded",
            retryable=True,
        )

    return handle


def build_diagnostic_bundle_handler(
    object_store: ObjectStore,
    *,
    runtime_instance_id: str = "rt_local",
    service_name: str = "agent-runtime",
    environment: str = "development",
) -> JobHandler:
    observability_service = ObservabilityService(
        object_store,
        runtime_instance_id=runtime_instance_id,
        service=service_name,
        environment=environment,
    )

    def handle(context: JobContext) -> JobHandlerResult:
        target_scope = context.target_scope
        job_input = context.input
        bundle_id = str(target_scope["bundle_id"])
        component = _optional_str(job_input.get("component"))
        context.mark_running(
            stage="collect_scope",
            message="Collecting redacted diagnostics.",
            percent=25,
        )
        manifest = observability_service.create_diagnostic_bundle(
            workspace_id=context.workspace_id,
            created_by=str(context.manifest.get("created_by") or "system"),
            bundle_id=bundle_id,
            trace_id=_optional_str(job_input.get("trace_id")),
            run_id=_optional_str(job_input.get("run_id")),
            component=component,
            components=_safe_string_list(job_input.get("components")),
            limit=_safe_limit(job_input.get("limit")),
            related_job_id=context.job_id,
            include_summary=_bool_with_default(job_input.get("include_summary"), True),
            include_errors=_bool_with_default(job_input.get("include_errors"), True),
            include_component_logs=_bool_with_default(
                job_input.get("include_component_logs"),
                True,
            ),
        )
        return JobHandlerResult.succeeded(
            stage="write_bundle_manifest",
            message="Diagnostic bundle generated.",
            artifacts=[
                {
                    "artifact_type": "diagnostic_bundle",
                    "bundle_id": bundle_id,
                    "object_key": manifest["object_key"],
                    "manifest_object_key": manifest["manifest_object_key"],
                    "package_object_key": manifest.get("package_object_key"),
                    "package_sha256": manifest.get("package_sha256"),
                    "package_bytes": manifest.get("package_bytes"),
                    "redacted": True,
                }
            ],
        )

    return handle


def build_log_archive_handler(
    object_store: ObjectStore,
    *,
    runtime_instance_id: str = "rt_local",
    service_name: str = "agent-runtime",
    environment: str = "development",
) -> JobHandler:
    observability_service = ObservabilityService(
        object_store,
        runtime_instance_id=runtime_instance_id,
        service=service_name,
        environment=environment,
    )

    def handle(context: JobContext) -> JobHandlerResult:
        target_scope = context.target_scope
        job_input = context.input
        date = _optional_str(job_input.get("date")) or _optional_str(target_scope.get("date"))
        runtime_id = (
            _optional_str(job_input.get("runtime_instance_id"))
            or _optional_str(target_scope.get("runtime_instance_id"))
            or runtime_instance_id
        )
        context.mark_running(
            stage="scan_rotated_logs",
            message="Scanning system logs for archive candidates.",
            percent=20,
        )
        context.mark_running(
            stage="redact_and_checksum",
            message="Redacting logs and computing archive checksums.",
            percent=45,
        )
        manifest = observability_service.archive_system_logs(
            date=date,
            runtime_instance_id=runtime_id,
            job_id=context.job_id,
            archive_id=f"arch_{context.job_id}",
        )
        return JobHandlerResult.succeeded(
            stage="write_shipper_manifest",
            message="System logs archived.",
            artifacts=[
                {
                    "artifact_type": "log_archive_manifest",
                    "runtime_instance_id": manifest["runtime_instance_id"],
                    "date": manifest["date"],
                    "manifest_object_key": log_archive_manifest_object_key(
                        manifest["date"],
                        manifest["runtime_instance_id"],
                    ),
                    "archived_count": manifest["last_archived_count"],
                    "file_count": manifest["file_count"],
                    "redacted": True,
                }
            ],
        )

    return handle


def _safe_metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _job_error_type(exc: Exception) -> str:
    return str(getattr(exc, "error_type", "") or exc.__class__.__name__)


def _job_retryable(exc: Exception, *, default: bool) -> bool:
    retryable = getattr(exc, "retryable", None)
    if retryable is not None:
        return bool(retryable)
    if isinstance(exc, ValueError):
        return False
    return default


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


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _bool_with_default(value: Any, default: bool) -> bool:
    return default if value is None else bool(value)


def _safe_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 100
    return max(1, min(parsed, 1000))


def log_archive_manifest_object_key(date: str, runtime_instance_id: str) -> str:
    from app.storage.path_builder import log_archive_manifest_key

    return log_archive_manifest_key(date, runtime_instance_id)
