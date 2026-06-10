from __future__ import annotations

from typing import Any

from app.api import dependencies
from app.api.dependencies import get_conversation_service
from app.rag_pipeline.ingestion import DocumentIngestionService
from app.rag_pipeline.paths import active_embedding_key
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def embed_query(self, **kwargs: Any) -> list[float]:
        self.calls.append(kwargs)
        return [0.1, 0.2, 0.3]


class _FakeVectorStore:
    def __init__(self, object_key: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self.object_key = object_key

    def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return [
            {
                "chunk_id": "chk_001",
                "doc_id": "doc_001",
                "doc_version_id": "docv_001",
                "object_key": self.object_key,
                "score": 0.97,
            }
        ]


def test_conversation_service_uses_default_model_config_and_secret_resolver(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("DEFAULT_MODEL_NAME", "mimo-v2.5-pro")
    monkeypatch.setenv("DEFAULT_MODEL_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
    monkeypatch.setenv("DEFAULT_MODEL_API_KEY_REF", "secret_default_model_api_key")
    monkeypatch.setenv("DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS", "200000")
    monkeypatch.setenv("DEFAULT_MODEL_MAX_OUTPUT_TOKENS", "8192")

    service = get_conversation_service(LocalObjectStore(tmp_path / "objects"))
    runner = service.runtime_runner

    assert runner.model_config.provider == "openai_compatible"
    assert runner.model_config.model == "mimo-v2.5-pro"
    assert runner.model_config.base_url == "https://token-plan-cn.xiaomimimo.com/v1"
    assert runner.model_config.api_key_ref == "secret_default_model_api_key"
    assert runner.model_config.context_window_tokens == 200000
    assert runner.model_config.max_output_tokens == 8192
    assert runner.llm_connector.secret_resolver is not None


def test_local_object_store_can_be_explicitly_enabled_in_production(
    monkeypatch,
    tmp_path,
) -> None:
    dependencies.get_object_store.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_OBJECT_STORE_ALLOW_PRODUCTION", "true")
    monkeypatch.setenv("LOCAL_OBJECT_STORE_DIR", str(tmp_path / "objects"))

    try:
        store = dependencies.get_object_store()
    finally:
        dependencies.get_object_store.cache_clear()

    store.write_text("probe.txt", "ok")
    assert store.read_text("probe.txt") == "ok"


def test_conversation_service_default_rag_tool_uses_active_embedding_collection(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DEFAULT_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("DEFAULT_MODEL_NAME", "mimo-v2.5-pro")
    monkeypatch.setenv("DEFAULT_MODEL_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
    monkeypatch.setenv("DEFAULT_MODEL_API_KEY_REF", "secret_default_model_api_key")
    object_store = LocalObjectStore(tmp_path / "objects")
    json_store = JsonObjectStore(object_store)
    DocumentIngestionService(object_store).ensure_knowledge_base("default", "kb_default")
    json_store.write_json(
        active_embedding_key("default", "kb_default"),
        {
            "schema_version": 1,
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "version_id": "embv_real",
            "provider": "openai_compatible",
            "model": "mimo-embedding-test",
            "dimension": 3,
            "collection": "kb_default_real_v1",
            "status": "active",
            "revision": 2,
        },
    )
    chunk_key = (
        "workspaces/default/knowledge_bases/kb_default/documents/"
        "doc_001/chunks/chk_001.json"
    )
    json_store.write_json(
        chunk_key,
        {
            "chunk_id": "chk_001",
            "doc_id": "doc_001",
            "doc_version_id": "docv_001",
            "text": "Active Milvus evidence.",
            "source": {"source_file_name": "active.md"},
            "metadata_filter": {
                "workspace_id": "default",
                "knowledge_base_id": "kb_default",
            },
        },
    )
    embedding_client = _FakeEmbeddingClient()
    vector_store = _FakeVectorStore(chunk_key)
    monkeypatch.setattr(
        dependencies,
        "build_embedding_client_for_workspace",
        lambda *args, **kwargs: embedding_client,
    )
    monkeypatch.setattr(
        dependencies,
        "build_milvus_vector_store_for_workspace",
        lambda *args, **kwargs: vector_store,
    )

    service = get_conversation_service(object_store, workspace_id="default")
    result = service.runtime_runner.tool_registry.invoke(
        "rag_search",
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "query": "active evidence",
            "top_k": 5,
            "final_top_k": 1,
        },
    )

    assert result["data"]["text_evidence"][0]["text"] == "Active Milvus evidence."
    assert embedding_client.calls[0]["model"] == "mimo-embedding-test"
    assert vector_store.calls[0]["collection"] == "kb_default_real_v1"
    assert vector_store.calls[0]["filters"]["workspace_id"] == "default"
