from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException

from app.core.identity import get_default_identity
from app.core.settings import Settings, get_settings
from app.model_connector.connector import LLMConnector
from app.schemas.identity import RuntimeIdentity
from app.schemas.model import ModelConfig
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_resolver import SecretResolver
from app.secret_store.secret_service import SecretService
from app.storage.local_object_store import LocalObjectStore
from app.storage.minio_store import MinioObjectStore
from app.storage.object_store import ObjectStore

if TYPE_CHECKING:
    from app.conversation.service import ConversationService

ROLE_RANK = {
    "viewer": 0,
    "editor": 1,
    "admin": 2,
    "owner": 3,
}


@lru_cache(maxsize=1)
def get_object_store() -> ObjectStore:
    settings = get_settings()
    if settings.object_store_backend == "local":
        if not settings.is_development_like and not settings.local_object_store_allow_production:
            raise RuntimeError(
                "Local object store in production requires "
                "LOCAL_OBJECT_STORE_ALLOW_PRODUCTION=true."
            )
        return LocalObjectStore(settings.local_object_store_dir)
    if settings.object_store_backend == "minio":
        if not settings.minio_access_key or not settings.minio_secret_key:
            raise RuntimeError("MinIO object store requires MINIO_ACCESS_KEY and MINIO_SECRET_KEY.")
        store = MinioObjectStore(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
        )
        store.ensure_bucket()
        return store
    raise RuntimeError(f"Unsupported object store backend: {settings.object_store_backend}")


def get_master_key_provider(settings: Settings | None = None) -> MasterKeyProvider:
    return MasterKeyProvider(settings or get_settings())


def build_secret_service(
    object_store: ObjectStore,
    settings: Settings | None = None,
) -> SecretService:
    return SecretService(object_store, get_master_key_provider(settings))


def get_secret_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> SecretService:
    return build_secret_service(object_store)


def build_secret_resolver(
    object_store: ObjectStore,
    settings: Settings | None = None,
) -> SecretResolver:
    master_key_provider = get_master_key_provider(settings)
    return SecretResolver(build_secret_service(object_store, settings), master_key_provider)


def get_secret_resolver(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> SecretResolver:
    return build_secret_resolver(object_store)


def get_default_model_config(settings: Settings | None = None) -> ModelConfig:
    current = settings or get_settings()
    provider = (
        current.default_model_provider
        if current.default_model_provider in {"openai_compatible", "anthropic"}
        else "openai_compatible"
    )
    model = (
        current.default_model_name
        if current.default_model_provider in {"openai_compatible", "anthropic"}
        else "mimo-v2.5-pro"
    )
    return ModelConfig(
        config_id="default",
        provider=provider,
        model=model,
        base_url=current.default_model_base_url,
        api_key_ref=current.default_model_api_key_ref,
        context_window_tokens=current.default_model_context_window_tokens,
        max_output_tokens=current.default_model_max_output_tokens,
        timeout_ms=current.default_model_timeout_ms,
    )


def get_smoke_model_config() -> ModelConfig:
    return ModelConfig(
        config_id="runtime_smoke",
        provider="fake",
        model="fake-runtime-smoke",
        context_window_tokens=200000,
        max_output_tokens=8192,
    )


def _get_conversation_model_fallback(settings: Settings) -> ModelConfig:
    if settings.default_model_provider == "fake":
        return get_smoke_model_config()
    return get_default_model_config(settings)


def build_llm_connector(object_store: ObjectStore) -> LLMConnector:
    return LLMConnector(secret_resolver=build_secret_resolver(object_store))


def get_llm_connector(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
) -> LLMConnector:
    return build_llm_connector(object_store)


@lru_cache(maxsize=1)
def get_runtime_runner():
    from app.runtime.runner import RuntimeRunner

    return RuntimeRunner(
        llm_connector=LLMConnector(secret_resolver=None),
        model_config=get_smoke_model_config(),
    )


def get_conversation_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
    workspace_id: str = "default",
) -> ConversationService:
    from app.conversation.service import ConversationService
    from app.model_connector.config_service import ModelConfigService
    from app.observability.service import ObservabilityService
    from app.rag_pipeline.ingestion import DocumentIngestionService
    from app.runtime.runner import RuntimeRunner
    from app.runtime.tools import build_default_tool_registry
    from app.tools.builtin.rag_tools import WorkspaceKnowledgeBaseStore

    settings = get_settings()
    model_config = ModelConfigService(object_store, settings).get_runtime_model_config(
        workspace_id,
        "main_chat",
        fallback=_get_conversation_model_fallback(settings),
    )
    if model_config.provider == "fake":
        if not model_config.model:
            raise RuntimeError("Fake runtime model config must include model.")
    elif (
        model_config.provider not in {"openai_compatible", "anthropic"}
        or not model_config.model
        or not model_config.base_url
        or not model_config.api_key_ref
    ):
        raise RuntimeError(
            "Main chat model config must use a public provider and include model, "
            "base_url, and api_key_ref."
        )
    embedding_client = _optional_runtime_dependency(
        lambda: build_embedding_client_for_workspace(
            object_store,
            workspace_id,
            config_id="embedding",
            settings=settings,
        )
    )
    vector_store = _optional_runtime_dependency(
        lambda: build_milvus_vector_store_for_workspace(
            object_store,
            workspace_id,
            settings=settings,
        )
    )
    knowledge_base_store = WorkspaceKnowledgeBaseStore(
        DocumentIngestionService(object_store),
        workspace_id,
    )
    graph_query = build_graph_query_service_for_workspace(
        object_store,
        workspace_id,
        settings=settings,
    )
    runtime_runner = RuntimeRunner(
        llm_connector=build_llm_connector(object_store),
        model_config=model_config,
        tool_registry=build_default_tool_registry(
            object_store,
            embedding_client=embedding_client,
            graph_query=graph_query,
            knowledge_base_store=knowledge_base_store,
            vector_store=vector_store,
            workspace_id=workspace_id,
        ),
        object_store=object_store,
        observability_service=ObservabilityService(
            object_store,
            runtime_instance_id=settings.runtime_instance_id,
            environment=settings.app_env,
        ),
    )
    return ConversationService(
        object_store,
        runtime_runner,
        runtime_instance_id=settings.runtime_instance_id,
        run_lease_ttl_seconds=settings.run_lease_ttl_seconds,
    )


def _optional_runtime_dependency(factory):
    try:
        return factory()
    except Exception:  # noqa: BLE001 - runtime tools keep lexical fallback when backend config is absent.
        return None


def get_job_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.jobs.service import JobService

    settings = get_settings()
    return JobService(
        object_store,
        runtime_instance_id=settings.runtime_instance_id,
        job_lease_ttl_seconds=settings.job_lease_ttl_seconds,
    )


def get_job_worker(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    return build_job_worker(object_store)


def build_job_worker(object_store: ObjectStore, settings: Settings | None = None):
    from app.jobs.handlers import (
        build_database_health_check_handler,
        build_diagnostic_bundle_handler,
        build_document_ingestion_handler,
        build_embedding_reindex_handler,
        build_graph_build_handler,
        build_log_archive_handler,
        build_mcp_capability_refresh_handler,
        build_memory_sync_handler,
        build_subagent_execution_handler,
    )
    from app.jobs.service import JobService
    from app.jobs.worker import JobWorker
    from app.mcp.configured_provider import build_configured_mcp_capability_provider
    from app.runtime.tools import build_default_reserved_tool_names

    current_settings = settings or get_settings()
    job_service = JobService(
        object_store,
        runtime_instance_id=current_settings.runtime_instance_id,
        job_lease_ttl_seconds=current_settings.job_lease_ttl_seconds,
    )
    return JobWorker(
        job_service,
        handlers={
            "document_ingestion_job": build_document_ingestion_handler(
                object_store,
                embedding_client_factory=lambda context: build_embedding_client_for_workspace(
                    object_store,
                    context.workspace_id,
                    config_id=str(context.input.get("config_id") or "embedding"),
                    settings=current_settings,
                ),
                vector_store_factory=lambda context: build_milvus_vector_store_for_workspace(
                    object_store,
                    context.workspace_id,
                    settings=current_settings,
                ),
            ),
            "embedding_reindex_job": build_embedding_reindex_handler(
                object_store,
                settings=current_settings,
                embedding_client_factory=lambda context: build_embedding_client_for_workspace(
                    object_store,
                    context.workspace_id,
                    config_id=str(context.input.get("config_id") or "embedding"),
                    settings=current_settings,
                ),
                vector_store_factory=lambda context: build_milvus_vector_store_for_workspace(
                    object_store,
                    context.workspace_id,
                    settings=current_settings,
                ),
            ),
            "graph_build_job": build_graph_build_handler(
                object_store,
                graph_writer_factory=lambda context: build_neo4j_graph_writer_for_workspace(
                    object_store,
                    context.workspace_id,
                    settings=current_settings,
                ),
                graph_extractor_factory=lambda context: build_graphrag_extractor_for_workspace(
                    object_store,
                    context.workspace_id,
                    settings=current_settings,
                ),
            ),
            "memory_sync_job": build_memory_sync_handler(
                object_store,
                settings=current_settings,
                embedding_client_factory=lambda context: build_embedding_client_for_workspace(
                    object_store,
                    context.workspace_id,
                    config_id=str(context.input.get("config_id") or "embedding"),
                    settings=current_settings,
                ),
                vector_store_factory=lambda context: build_milvus_vector_store_for_workspace(
                    object_store,
                    context.workspace_id,
                    settings=current_settings,
                ),
                graph_writer_factory=lambda context: build_neo4j_graph_writer_for_workspace(
                    object_store,
                    context.workspace_id,
                    settings=current_settings,
                ),
            ),
            "mcp_capability_refresh_job": build_mcp_capability_refresh_handler(
                object_store,
                capability_provider=build_configured_mcp_capability_provider(
                    object_store,
                    current_settings,
                ),
                builtin_tool_names=build_default_reserved_tool_names(object_store),
            ),
            "database_health_check_job": build_database_health_check_handler(
                object_store,
                settings=current_settings,
                secret_resolver=build_secret_resolver(object_store, current_settings),
            ),
            "subagent_execution_job": build_subagent_execution_handler(object_store),
            "diagnostic_bundle_job": build_diagnostic_bundle_handler(
                object_store,
                runtime_instance_id=current_settings.runtime_instance_id,
                environment=current_settings.app_env,
            ),
            "log_archive_job": build_log_archive_handler(
                object_store,
                runtime_instance_id=current_settings.runtime_instance_id,
                environment=current_settings.app_env,
            ),
            "log_shipper_job": build_log_archive_handler(
                object_store,
                runtime_instance_id=current_settings.runtime_instance_id,
                environment=current_settings.app_env,
            ),
        },
    )


def build_embedding_client_for_workspace(
    object_store: ObjectStore,
    workspace_id: str,
    *,
    config_id: str = "embedding",
    settings: Settings | None = None,
) -> Any:
    from app.embedding.client import OpenAICompatibleEmbeddingClient
    from app.model_connector.config_service import ModelConfigService

    current_settings = settings or get_settings()
    config_service = ModelConfigService(object_store, current_settings)
    public_config = config_service.get_config(workspace_id, config_id)
    if not public_config.get("enabled", True):
        raise ValueError("Embedding config is disabled.")
    config = config_service.get_runtime_model_config(workspace_id, config_id)
    if config.provider != "openai_compatible":
        raise ValueError("Embedding config must use openai_compatible provider.")
    if not config.model or not config.base_url or not config.api_key_ref:
        raise ValueError("Embedding config must include model, base_url, and api_key_ref.")
    resolved = build_secret_resolver(object_store, current_settings).resolve(
        workspace_id=workspace_id,
        secret_ref=_normalize_secret_ref(config.api_key_ref),
        purpose="embedding_call",
        caller="embedding_connector",
    )
    return OpenAICompatibleEmbeddingClient(
        base_url=config.base_url,
        api_key=resolved.plaintext,
        timeout_ms=config.timeout_ms,
    )


def build_milvus_vector_store_for_workspace(
    object_store: ObjectStore,
    workspace_id: str,
    *,
    settings: Settings | None = None,
) -> Any:
    from app.database.service import DatabaseConfigService
    from app.vector_store.milvus_http import MilvusHttpVectorStore

    current_settings = settings or get_settings()
    if not current_settings.external_database_targets_enabled:
        raise ValueError("External database targets are disabled.")
    target = _database_target(
        DatabaseConfigService(object_store, current_settings).get_config(workspace_id),
        "milvus",
    )
    if not target.get("enabled", True):
        raise ValueError("Milvus config is disabled.")
    endpoint = str(target.get("endpoint") or current_settings.milvus_uri)
    if not endpoint:
        raise ValueError("Milvus endpoint is required.")
    token_ref = _first_secret_ref(target.get("credential_refs"), ("token", "primary"))
    token = None
    if token_ref:
        token = build_secret_resolver(object_store, current_settings).resolve(
            workspace_id=workspace_id,
            secret_ref=_normalize_secret_ref(token_ref),
            purpose="milvus_connect",
            caller="milvus_connector",
        ).plaintext
    return MilvusHttpVectorStore(
        uri=endpoint,
        token=token,
        timeout_ms=current_settings.default_model_timeout_ms,
    )


def build_neo4j_graph_writer_for_workspace(
    object_store: ObjectStore,
    workspace_id: str,
    *,
    settings: Settings | None = None,
) -> Any | None:
    from app.database.service import DatabaseConfigService
    from app.graph_pipeline.neo4j_writer import Neo4jGraphWriter

    current_settings = settings or get_settings()
    if not current_settings.external_database_targets_enabled:
        return None
    target = _database_target(
        DatabaseConfigService(object_store, current_settings).get_config(workspace_id),
        "neo4j",
    )
    if not target.get("enabled", True):
        return None
    credential_ref = _first_secret_ref(
        target.get("credential_refs"),
        ("username_password", "primary"),
    )
    options = target.get("options") if isinstance(target.get("options"), dict) else {}
    if credential_ref:
        resolved = build_secret_resolver(object_store, current_settings).resolve(
            workspace_id=workspace_id,
            secret_ref=_normalize_secret_ref(credential_ref),
            purpose="neo4j_connect",
            caller="neo4j_connector",
        )
        credential = _parse_username_password_secret(
            resolved.plaintext,
            default_username=str(options.get("username") or "neo4j"),
        )
    else:
        credential = _parse_username_password_secret(
            getattr(current_settings, "neo4j_auth", None) or "",
            default_username=str(options.get("username") or "neo4j"),
        )
        if not credential.get("password"):
            return None
    return Neo4jGraphWriter.from_uri(
        uri=str(target.get("endpoint") or current_settings.neo4j_uri),
        username=credential["username"],
        password=credential["password"],
        database=str(options["database"]) if options.get("database") else None,
    )


def build_graphrag_extractor_for_workspace(
    object_store: ObjectStore,
    workspace_id: str,
    *,
    settings: Settings | None = None,
) -> Any | None:
    from app.graph_pipeline.extraction import LLMGraphExtractor
    from app.model_connector.config_service import ModelConfigService

    current_settings = settings or get_settings()
    config_service = ModelConfigService(object_store, current_settings)
    public_config = config_service.get_config(workspace_id, "graphrag_llm")
    if not public_config.get("enabled", True):
        return None
    config = config_service.get_runtime_model_config(workspace_id, "graphrag_llm")
    if config.provider not in {"openai_compatible", "anthropic"}:
        return None
    if not config.model or not config.base_url or not config.api_key_ref:
        return None
    return LLMGraphExtractor(
        llm_connector=build_llm_connector(object_store),
        model_config=config,
    )


def build_neo4j_readonly_query_for_workspace(
    object_store: ObjectStore,
    workspace_id: str,
    *,
    settings: Settings | None = None,
) -> Any | None:
    from app.database.service import DatabaseConfigService
    from app.graph_pipeline.neo4j_readonly import Neo4jReadOnlyQueryAdapter

    current_settings = settings or get_settings()
    if not current_settings.external_database_targets_enabled:
        return None
    target = _database_target(
        DatabaseConfigService(object_store, current_settings).get_config(workspace_id),
        "neo4j",
    )
    if not target.get("enabled", True):
        return None
    credential_ref = _first_secret_ref(
        target.get("credential_refs"),
        ("username_password", "primary"),
    )
    options = target.get("options") if isinstance(target.get("options"), dict) else {}
    if credential_ref:
        resolved = build_secret_resolver(object_store, current_settings).resolve(
            workspace_id=workspace_id,
            secret_ref=_normalize_secret_ref(credential_ref),
            purpose="neo4j_connect",
            caller="neo4j_readonly_query_adapter",
        )
        credential = _parse_username_password_secret(
            resolved.plaintext,
            default_username=str(options.get("username") or "neo4j"),
        )
    else:
        credential = _parse_username_password_secret(
            getattr(current_settings, "neo4j_auth", None) or "",
            default_username=str(options.get("username") or "neo4j"),
        )
        if not credential.get("password"):
            return None
    return Neo4jReadOnlyQueryAdapter.from_uri(
        uri=str(target.get("endpoint") or current_settings.neo4j_uri),
        username=credential["username"],
        password=credential["password"],
        database=str(options["database"]) if options.get("database") else None,
    )


def _database_target(config: dict[str, Any], target_name: str) -> dict[str, Any]:
    for target in config.get("targets", []):
        if target.get("target") == target_name:
            return target
    raise ValueError(f"Database target is not configured: {target_name}")


def _first_secret_ref(value: Any, names: tuple[str, ...]) -> str | None:
    if not isinstance(value, dict):
        return None
    for name in names:
        secret_ref = value.get(name)
        if secret_ref:
            return str(secret_ref)
    return None


def _normalize_secret_ref(secret_ref: str) -> str:
    return secret_ref.removeprefix("secret_ref://")


def _parse_username_password_secret(
    plaintext: str,
    *,
    default_username: str,
) -> dict[str, str]:
    import json

    text = plaintext.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        username = str(parsed.get("username") or parsed.get("user") or default_username)
        password = str(parsed.get("password") or "")
        if password:
            return {"username": username, "password": password}
    separator = ":" if ":" in text else ("/" if "/" in text else "")
    if separator:
        username, password = text.split(separator, 1)
        if password:
            return {"username": username or default_username, "password": password}
    if text:
        return {"username": default_username, "password": text}
    raise ValueError("Username/password secret is empty.")


def get_database_config_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.database.service import DatabaseConfigService

    settings = get_settings()
    return DatabaseConfigService(
        object_store,
        settings,
        secret_service=build_secret_service(object_store, settings),
        secret_resolver=build_secret_resolver(object_store, settings),
    )


def get_model_config_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.model_connector.config_service import ModelConfigService

    settings = get_settings()
    return ModelConfigService(
        object_store,
        settings,
        secret_service=build_secret_service(object_store, settings),
    )


def get_document_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.rag_pipeline.ingestion import DocumentIngestionService

    return DocumentIngestionService(object_store)


def get_graph_query_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
    workspace_id: str = "default",
):
    return build_graph_query_service_for_workspace(object_store, workspace_id)


def build_graph_query_service_for_workspace(
    object_store: ObjectStore,
    workspace_id: str,
    *,
    settings: Settings | None = None,
):
    from app.graph_pipeline.query import ObjectStoreGraphQueryService

    fallback = ObjectStoreGraphQueryService(object_store)
    try:
        neo4j_query = build_neo4j_readonly_query_for_workspace(
            object_store,
            workspace_id,
            settings=settings,
        )
    except Exception:  # noqa: BLE001 - missing/broken Neo4j config falls back to MinIO graph index.
        return fallback
    return neo4j_query or fallback


def get_graph_build_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.graph_pipeline.builder import GraphBuildJobService

    return GraphBuildJobService(object_store)


def get_mcp_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.mcp.configured_provider import build_configured_mcp_capability_provider
    from app.mcp.service import McpService
    from app.runtime.tools import build_default_reserved_tool_names

    return McpService(
        object_store,
        capability_provider=build_configured_mcp_capability_provider(object_store),
        builtin_tool_names=build_default_reserved_tool_names(object_store),
        secret_service=build_secret_service(object_store, get_settings()),
    )


def get_memory_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
    job_service: Annotated[Any, Depends(get_job_service)],
):
    from app.memory.service import MemoryService

    settings = get_settings()
    return MemoryService(
        object_store,
        job_service=job_service,
        external_sync_enabled=settings.memory_external_sync_enabled,
    )


def get_skill_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.skills.service import SkillService

    return SkillService(object_store)


def get_subagent_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.subagents.service import SubAgentService

    return SubAgentService(object_store)


def get_observability_service(
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
):
    from app.observability.service import ObservabilityService

    settings = get_settings()
    return ObservabilityService(
        object_store,
        runtime_instance_id=settings.runtime_instance_id,
        environment=settings.app_env,
    )


def get_identity() -> RuntimeIdentity:
    settings = get_settings()
    if settings.login_enabled and not settings.is_development_like:
        raise HTTPException(status_code=401, detail="authentication_required")
    return get_default_identity(settings)


def require_workspace_role(min_role: str):
    def dependency(
        workspace_id: str,
        identity: Annotated[RuntimeIdentity, Depends(get_identity)],
    ) -> RuntimeIdentity:
        if identity.workspace_id != workspace_id:
            raise HTTPException(status_code=403, detail="workspace_access_denied")
        current_rank = ROLE_RANK.get(identity.workspace_role, -1)
        required_rank = ROLE_RANK[min_role]
        if current_rank < required_rank:
            raise HTTPException(status_code=403, detail="insufficient_workspace_role")
        return identity

    return dependency
