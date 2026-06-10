from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return _env(name, fallback).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str = field(default_factory=lambda: _env("APP_ENV", "development"))
    server_host: str = field(default_factory=lambda: _env("AGENT_SERVER_HOST", "0.0.0.0"))
    server_port: int = field(default_factory=lambda: int(_env("AGENT_SERVER_PORT", "8000")))
    frontend_url: str = field(default_factory=lambda: _env("FRONTEND_URL", "http://localhost:3000"))
    runtime_instance_id: str = field(
        default_factory=lambda: _env("RUNTIME_INSTANCE_ID", "rt_local")
    )
    run_lease_ttl_seconds: int = field(
        default_factory=lambda: int(_env("RUN_LEASE_TTL_SECONDS", "300"))
    )
    job_lease_ttl_seconds: int = field(
        default_factory=lambda: int(_env("JOB_LEASE_TTL_SECONDS", "300"))
    )
    job_worker_autostart: bool = field(
        default_factory=lambda: _env_bool("JOB_WORKER_AUTOSTART", False)
    )
    job_worker_poll_interval_seconds: float = field(
        default_factory=lambda: float(_env("JOB_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    )
    job_worker_max_jobs_per_tick: int = field(
        default_factory=lambda: int(_env("JOB_WORKER_MAX_JOBS_PER_TICK", "5"))
    )

    default_user_id: str = field(default_factory=lambda: _env("DEFAULT_USER_ID", "default_user"))
    default_user_role: str = field(default_factory=lambda: _env("DEFAULT_USER_ROLE", "owner"))
    default_workspace_id: str = field(
        default_factory=lambda: _env("DEFAULT_WORKSPACE_ID", "default")
    )
    default_workspace_role: str = field(
        default_factory=lambda: _env("DEFAULT_WORKSPACE_ROLE", "owner")
    )
    login_enabled: bool = field(default_factory=lambda: _env_bool("LOGIN_ENABLED", False))
    workspace_switch_enabled: bool = field(
        default_factory=lambda: _env_bool("WORKSPACE_SWITCH_ENABLED", False)
    )

    minio_endpoint: str = field(default_factory=lambda: _env("MINIO_ENDPOINT", "http://localhost:9000"))
    minio_bucket: str = field(default_factory=lambda: _env("MINIO_BUCKET", "agent-system"))
    minio_access_key: str | None = field(default_factory=lambda: os.getenv("MINIO_ACCESS_KEY"))
    minio_secret_key: str | None = field(default_factory=lambda: os.getenv("MINIO_SECRET_KEY"))
    minio_secure: bool = field(
        default_factory=lambda: _env("MINIO_SECURE", "false").lower() in {"1", "true", "yes"}
    )
    milvus_uri: str = field(default_factory=lambda: _env("MILVUS_URI", "http://localhost:19530"))
    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_http_url: str = field(default_factory=lambda: _env("NEO4J_HTTP_URL", "http://localhost:7474"))
    neo4j_auth: str | None = field(default_factory=lambda: os.getenv("NEO4J_AUTH"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    object_store_backend: str = field(default_factory=lambda: _env("OBJECT_STORE_BACKEND", "local"))
    local_object_store_allow_production: bool = field(
        default_factory=lambda: _env_bool("LOCAL_OBJECT_STORE_ALLOW_PRODUCTION", False)
    )
    local_object_store_dir: str = field(
        default_factory=lambda: _env(
            "LOCAL_OBJECT_STORE_DIR",
            str(Path(__file__).resolve().parents[3] / ".agent_state"),
        )
    )
    external_database_targets_enabled: bool = field(
        default_factory=lambda: _env_bool("EXTERNAL_DATABASE_TARGETS_ENABLED", True)
    )
    memory_external_sync_enabled: bool = field(
        default_factory=lambda: _env_bool("MEMORY_EXTERNAL_SYNC_ENABLED", True)
    )
    static_secret_seed_enabled: bool = field(
        default_factory=lambda: _env_bool("STATIC_SECRET_SEED_ENABLED", False)
    )
    p0_acceptance_report_path: str = field(
        default_factory=lambda: _env(
            "P0_ACCEPTANCE_REPORT_PATH",
            str(Path(__file__).resolve().parents[3] / "logs" / "p0_acceptance_report.json"),
        )
    )
    agent_master_key: str | None = field(default_factory=lambda: os.getenv("AGENT_MASTER_KEY"))
    default_model_provider: str = field(
        default_factory=lambda: _env("DEFAULT_MODEL_PROVIDER", "fake")
    )
    default_model_name: str = field(
        default_factory=lambda: _env("DEFAULT_MODEL_NAME", "fake-runtime-smoke")
    )
    default_model_base_url: str | None = field(
        default_factory=lambda: os.getenv("DEFAULT_MODEL_BASE_URL")
    )
    default_model_api_key_ref: str | None = field(
        default_factory=lambda: os.getenv("DEFAULT_MODEL_API_KEY_REF")
    )
    default_model_api_key: str | None = field(
        default_factory=lambda: os.getenv("DEFAULT_MODEL_API_KEY")
    )
    default_embedding_model_name: str = field(
        default_factory=lambda: _env("DEFAULT_EMBEDDING_MODEL_NAME", "text-embedding-v4")
    )
    default_embedding_base_url: str | None = field(
        default_factory=lambda: _env(
            "DEFAULT_EMBEDDING_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    )
    default_embedding_api_key_ref: str | None = field(
        default_factory=lambda: os.getenv("DEFAULT_EMBEDDING_API_KEY_REF")
    )
    default_embedding_api_key: str | None = field(
        default_factory=lambda: os.getenv("DEFAULT_EMBEDDING_API_KEY")
    )
    default_embedding_dimension: int = field(
        default_factory=lambda: int(_env("DEFAULT_EMBEDDING_DIMENSION", "1024"))
    )
    default_model_context_window_tokens: int = field(
        default_factory=lambda: int(_env("DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS", "200000"))
    )
    default_model_max_output_tokens: int = field(
        default_factory=lambda: int(_env("DEFAULT_MODEL_MAX_OUTPUT_TOKENS", "8192"))
    )
    default_model_timeout_ms: int = field(
        default_factory=lambda: int(_env("DEFAULT_MODEL_TIMEOUT_MS", "60000"))
    )
    local_file_tools_enabled: bool = field(
        default_factory=lambda: _env_bool("LOCAL_FILE_TOOLS_ENABLED", True)
    )
    local_file_tools_root: str = field(
        default_factory=lambda: _env(
            "LOCAL_FILE_TOOLS_ROOT",
            str(Path(__file__).resolve().parents[3] / "local_workspace"),
        )
    )
    local_file_host_root: str | None = field(
        default_factory=lambda: os.getenv("LOCAL_FILE_HOST_ROOT")
    )

    @property
    def is_development_like(self) -> bool:
        return self.app_env in {"development", "test", "testing"}


def get_settings() -> Settings:
    return Settings()
