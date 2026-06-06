from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.dependencies import get_object_store, require_workspace_role
from app.core.settings import Settings, get_settings
from app.core.time import utc_now_iso
from app.database.service import DatabaseConfigService
from app.jobs.service import JobService
from app.mcp.service import McpService
from app.memory.service import MemoryService
from app.model_connector.config_service import ModelConfigService
from app.rag_pipeline.ingestion import DocumentIngestionService
from app.schemas.health import (
    HealthResponse,
    P0ReadinessResponse,
    ReadinessCategory,
    ReadinessCheck,
    ReadinessStatus,
)
from app.schemas.identity import RuntimeIdentity
from app.skills.service import SkillService
from app.storage.object_store import ObjectStore
from app.storage.path_builder import (
    threads_index_key,
    workspace_jobs_index_key,
    workspace_prefix,
    workspace_runs_index_key,
)
from app.subagents.service import SubAgentService

router = APIRouter(tags=["health"])

TERMINAL_JOB_STATUSES = {"succeeded", "partial_success", "failed", "cancelled"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
JOB_RECOVERY_STATUSES = {"unknown_outcome", "recovering"}
MIN_ACCEPTANCE_REPORT_SCHEMA_VERSION = 2
FINAL_HANDOFF_CONTRACT_ID = "p0-final-handoff-2026-06-01"
FINAL_HANDOFF_CONTRACT_VERSION = 3
READINESS_AFTER_REPORT_CHECK_ID = "runtime.p0_readiness_after_report"
FINAL_HANDOFF_REQUIRED_CHECK_IDS = (
    "code.backend_python_env",
    "code.root_e2e_contracts",
    "code.backend_p0_contracts",
    "code.frontend_p0_contracts",
    "runtime.model_config.main_chat_smoke",
    "runtime.model_config.graphrag_llm_smoke",
    "runtime.model_config.embedding_smoke",
    "runtime.docker_compose_ps",
    "runtime.database_live_health",
    "runtime.job_worker_status",
    "runtime.mcp_live_smoke",
    "runtime.frontend_route_smoke",
    "runtime.frontend_browser_smoke",
    READINESS_AFTER_REPORT_CHECK_ID,
)
FINAL_HANDOFF_REQUIRED_FLAGS = (
    "--include-root-e2e",
    "--include-p0-contracts",
    "--include-runtime-http",
    "--include-model-smoke",
    "--include-docker",
    "--mcp-server-name",
    "--require-final-handoff",
)


@router.get("/health", response_model=HealthResponse)
async def health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    return HealthResponse(
        ok=True,
        service="agent-server",
        version="0.1.0",
        environment=settings.app_env,
    )


@router.get("/workspaces/{workspace_id}/readiness", response_model=P0ReadinessResponse)
@router.get("/workspaces/{workspace_id}/readiness/p0", response_model=P0ReadinessResponse)
async def p0_readiness(
    workspace_id: str,
    _: Annotated[RuntimeIdentity, Depends(require_workspace_role("viewer"))],
    object_store: Annotated[ObjectStore, Depends(get_object_store)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> P0ReadinessResponse:
    checks = build_p0_readiness_checks(workspace_id, object_store, settings)
    categories = _group_readiness_checks(checks)
    summary = Counter(check.status for check in checks)
    hard_failures = [
        check for check in checks if check.required and check.status in {"fail", "blocked"}
    ]
    status = _overall_status(checks)
    return P0ReadinessResponse(
        workspace_id=workspace_id,
        ok=not hard_failures,
        status=status,
        generated_at=utc_now_iso(),
        environment=settings.app_env,
        runtime_instance_id=settings.runtime_instance_id,
        summary={
            "pass": summary["pass"],
            "warn": summary["warn"],
            "fail": summary["fail"],
            "blocked": summary["blocked"],
            "not_applicable": summary["not_applicable"],
            "total": len(checks),
        },
        categories=categories,
        checks=checks,
        remaining_blockers=[
            f"{check.check_id}: {check.summary}"
            for check in hard_failures
        ],
    )


def build_p0_readiness_checks(
    workspace_id: str,
    object_store: ObjectStore,
    settings: Settings,
) -> list[ReadinessCheck]:
    return [
        _identity_check(workspace_id, settings),
        _object_store_check(workspace_id, object_store, settings),
        _secret_store_check(settings),
        *_model_config_checks(workspace_id, object_store, settings),
        *_database_checks(workspace_id, object_store, settings),
        *_job_checks(workspace_id, object_store, settings),
        *_conversation_checks(workspace_id, object_store, settings),
        *_knowledge_checks(workspace_id, object_store),
        *_runtime_capability_checks(workspace_id, object_store, settings),
        _observability_check(object_store, settings),
        *_external_acceptance_checks(settings),
    ]


def _identity_check(workspace_id: str, settings: Settings) -> ReadinessCheck:
    status: ReadinessStatus = "pass" if workspace_id == settings.default_workspace_id else "warn"
    return ReadinessCheck(
        check_id="core.identity",
        category="core",
        title="Default user and workspace",
        status=status,
        summary="Default P0 identity is available.",
        evidence=[
            f"user_id={settings.default_user_id}",
            f"user_role={settings.default_user_role}",
            f"workspace_id={settings.default_workspace_id}",
            f"requested_workspace={workspace_id}",
        ],
        next_actions=[] if status == "pass" else ["Confirm this workspace is intentional."],
    )


def _object_store_check(
    workspace_id: str,
    object_store: ObjectStore,
    settings: Settings,
) -> ReadinessCheck:
    try:
        keys = object_store.list_keys(workspace_prefix(workspace_id))
    except Exception as exc:  # noqa: BLE001 - readiness must report object-store failures.
        return ReadinessCheck(
            check_id="storage.object_store",
            category="storage",
            title="ObjectStore readable",
            status="fail",
            summary="ObjectStore cannot be listed.",
            evidence=[exc.__class__.__name__],
            next_actions=["Fix OBJECT_STORE_BACKEND and MinIO/local object-store configuration."],
        ),
    status: ReadinessStatus = "pass" if settings.object_store_backend == "minio" else "warn"
    return ReadinessCheck(
        check_id="storage.object_store",
        category="storage",
        title="ObjectStore readable",
        status=status,
        summary="ObjectStore can be listed.",
        evidence=[
            f"backend={settings.object_store_backend}",
            f"workspace_key_count={len(keys)}",
        ],
        next_actions=[]
        if status == "pass"
        else ["P0 local development may use local ObjectStore; final deployment should use MinIO."],
    )


def _secret_store_check(settings: Settings) -> ReadinessCheck:
    has_master_key = bool(settings.agent_master_key)
    status: ReadinessStatus = "pass" if has_master_key else "warn"
    return ReadinessCheck(
        check_id="security.secret_store",
        category="security",
        title="Secret Store master key",
        status=status,
        summary=(
            "Secret Store master key is configured."
            if has_master_key
            else (
                "Development can start, but Secret Store cannot encrypt new secrets "
                "without a master key."
            )
        ),
        evidence=[f"app_env={settings.app_env}", f"master_key_configured={has_master_key}"],
        next_actions=[]
        if has_master_key
        else ["Set AGENT_MASTER_KEY before testing secret writes."],
    )


def _model_config_checks(
    workspace_id: str,
    object_store: ObjectStore,
    settings: Settings,
) -> list[ReadinessCheck]:
    try:
        configs = ModelConfigService(object_store, settings).list_configs(workspace_id)["configs"]
    except Exception as exc:  # noqa: BLE001
        return [
            ReadinessCheck(
                check_id="models.config",
                category="models",
                title="Model configuration",
                status="fail",
                summary="Model configs cannot be loaded.",
                evidence=[exc.__class__.__name__],
                next_actions=["Open Settings -> Models and save the required configs."],
            )
        ]
    by_id = {str(config.get("config_id")): config for config in configs}
    required_ids = ["main_chat", "graphrag_llm", "embedding"]
    missing = [config_id for config_id in required_ids if config_id not in by_id]
    not_configured = [
        config_id
        for config_id in required_ids
        if by_id.get(config_id, {}).get("status") != "configured"
    ]
    invalid_providers = [
        config_id
        for config_id in required_ids
        if not _is_supported_model_provider(
            config_id,
            str(by_id.get(config_id, {}).get("provider") or ""),
        )
    ]
    missing_runtime_fields = [
        config_id
        for config_id in required_ids
        if not by_id.get(config_id, {}).get("model")
        or not by_id.get(config_id, {}).get("base_url")
        or not by_id.get(config_id, {}).get("api_key_ref")
    ]
    status: ReadinessStatus = (
        "fail"
        if missing
        else (
            "blocked"
            if not_configured or invalid_providers or missing_runtime_fields
            else "pass"
        )
    )
    return [
        ReadinessCheck(
            check_id="models.config",
            category="models",
            title="Required model configs",
            status=status,
            summary=(
                "Required model config slots are ready."
                if status == "pass"
                else "Required model config slots are not ready for final P0 acceptance."
            ),
            evidence=[
                (
                    f"{config_id}:"
                    f"{by_id.get(config_id, {}).get('status', 'missing')}:"
                    f"{by_id.get(config_id, {}).get('provider', 'unknown')}:"
                    f"{by_id.get(config_id, {}).get('source', 'unknown')}:"
                    f"model={'set' if by_id.get(config_id, {}).get('model') else 'missing'}:"
                    f"base_url={'set' if by_id.get(config_id, {}).get('base_url') else 'missing'}:"
                    f"api_key_ref="
                    f"{'set' if by_id.get(config_id, {}).get('api_key_ref') else 'missing'}"
                )
                for config_id in required_ids
            ],
            next_actions=[]
            if status == "pass"
            else [
                (
                    "Configure main_chat and graphrag_llm with OpenAI-compatible or "
                    "Anthropic providers, and configure embedding with an "
                    "OpenAI-compatible provider and active secret refs."
                )
            ],
        )
    ]


def _is_supported_model_provider(config_id: str, provider: str) -> bool:
    if config_id == "embedding":
        return provider == "openai_compatible"
    return provider in {"openai_compatible", "anthropic"}


def _database_checks(
    workspace_id: str,
    object_store: ObjectStore,
    settings: Settings,
) -> list[ReadinessCheck]:
    service = DatabaseConfigService(object_store, settings)
    try:
        config = service.get_config(workspace_id)
    except Exception as exc:  # noqa: BLE001
        return [
            ReadinessCheck(
                check_id="database.config",
                category="database",
                title="Database config",
                status="fail",
                summary="Database config cannot be loaded.",
                evidence=[exc.__class__.__name__],
                next_actions=[
                    "Open Settings -> Databases and save MinIO/Milvus/Neo4j/Redis config."
                ],
            )
        ]
    targets = {str(target.get("target")): target for target in config.get("targets", [])}
    required_targets = ["minio", "milvus", "neo4j", "redis"]
    missing_targets = [target for target in required_targets if target not in targets]
    redis_role = str((targets.get("redis", {}).get("options") or {}).get("role") or "")
    snapshot = service.get_health_snapshot(workspace_id)
    health_source = str(snapshot.get("source") or "unknown")
    unhealthy = [
        str(item.get("target"))
        for item in snapshot.get("services", [])
        if item.get("status") not in {"healthy", "unknown"}
    ]
    return [
        ReadinessCheck(
            check_id="database.config",
            category="database",
            title="MinIO, Milvus, Neo4j, Redis config",
            status="fail"
            if missing_targets or redis_role != "cache_only"
            else "pass",
            summary="Database target config matches P0 topology.",
            evidence=[
                f"targets={','.join(sorted(targets))}",
                f"redis_role={redis_role or 'missing'}",
            ],
            next_actions=[]
            if not missing_targets and redis_role == "cache_only"
            else ["Keep only MinIO + Milvus + Neo4j as data stores and Redis as cache_only."],
        ),
        ReadinessCheck(
            check_id="database.health_snapshot",
            category="database",
            title="Database health snapshot",
            status="pass"
            if snapshot.get("ok")
            else ("warn" if health_source == "unknown" else "blocked"),
            summary="Latest database health snapshot is available."
            if health_source != "unknown"
            else "No database health snapshot has been created yet.",
            evidence=[
                f"source={health_source}",
                f"ok={bool(snapshot.get('ok'))}",
                f"unhealthy={','.join(unhealthy) if unhealthy else 'none'}",
            ],
            next_actions=[]
            if snapshot.get("ok")
            else ["Run database health check from Settings or Jobs before final acceptance."],
        ),
    ]


def _job_checks(
    workspace_id: str,
    object_store: ObjectStore,
    settings: Settings,
) -> list[ReadinessCheck]:
    service = JobService(
        object_store,
        runtime_instance_id=settings.runtime_instance_id,
        job_lease_ttl_seconds=settings.job_lease_ttl_seconds,
    )
    try:
        jobs = service.list_jobs(workspace_id, limit=1000)
    except Exception as exc:  # noqa: BLE001
        return [
            ReadinessCheck(
                check_id="jobs.index",
                category="jobs",
                title="Jobs index",
                status="fail",
                summary="Jobs index cannot be read.",
                evidence=[exc.__class__.__name__],
                next_actions=["Run jobs index rebuild."],
            )
        ]
    non_terminal = [job for job in jobs if job.get("status") not in TERMINAL_JOB_STATUSES]
    recovery_jobs = [job for job in jobs if job.get("status") in JOB_RECOVERY_STATUSES]
    index_exists = object_store.exists(workspace_jobs_index_key(workspace_id))
    status: ReadinessStatus = "blocked" if recovery_jobs else ("pass" if index_exists else "warn")
    return [
        ReadinessCheck(
            check_id="jobs.index",
            category="jobs",
            title="Jobs source of truth",
            status=status,
            summary=(
                "Jobs index has recovery-sensitive jobs that need operator action."
                if recovery_jobs
                else "Jobs index is readable."
            ),
            evidence=[
                f"job_count={len(jobs)}",
                f"non_terminal={len(non_terminal)}",
                f"recovery_sensitive={len(recovery_jobs)}",
            ],
            next_actions=[]
            if not recovery_jobs and not non_terminal
            else [
                (
                    "Recover unknown_outcome/recovering jobs or retry them from Jobs "
                    "before final acceptance."
                )
                if recovery_jobs
                else "Review running jobs in Jobs page."
            ],
            details={
                "recovery_job_ids": [
                    str(job.get("job_id") or "unknown") for job in recovery_jobs[:20]
                ],
                "recovery_statuses": [
                    str(job.get("status") or "unknown") for job in recovery_jobs[:20]
                ],
            },
        )
    ]


def _conversation_checks(
    workspace_id: str,
    object_store: ObjectStore,
    settings: Settings,
) -> list[ReadinessCheck]:
    try:
        thread_index_exists = object_store.exists(threads_index_key(workspace_id))
        runs_index_exists = object_store.exists(workspace_runs_index_key(workspace_id))
        run_manifests, unreadable_run_keys = _load_run_manifests(workspace_id, object_store)
    except Exception as exc:  # noqa: BLE001 - readiness must surface corrupted state.
        return [
            ReadinessCheck(
                check_id="conversation.state_indexes",
                category="runtime",
                title="Thread and Run state indexes",
                status="fail",
                summary="Conversation state cannot be inspected.",
                evidence=[exc.__class__.__name__],
                next_actions=["Inspect MinIO conversation indexes and run manifests."],
            )
        ]
    non_terminal_runs = [
        manifest
        for manifest in run_manifests
        if str(manifest.get("status") or "") not in TERMINAL_RUN_STATUSES
    ]
    running_runs = [
        manifest
        for manifest in run_manifests
        if str(manifest.get("status") or "") == "running"
    ]
    stale_after_seconds = max(3600, int(settings.run_lease_ttl_seconds))
    stale_running_runs = [
        manifest
        for manifest in running_runs
        if _is_stale_running_run(manifest, stale_after_seconds=stale_after_seconds)
    ]
    stale_run_ids = [str(run.get("run_id") or "unknown") for run in stale_running_runs]
    unreadable_count = len(unreadable_run_keys)
    stale_status: ReadinessStatus = (
        "fail"
        if unreadable_count
        else ("blocked" if stale_running_runs else ("pass" if runs_index_exists else "warn"))
    )
    return [
        ReadinessCheck(
            check_id="conversation.state_indexes",
            category="runtime",
            title="Thread and Run state indexes",
            status="pass" if thread_index_exists and runs_index_exists else "warn",
            summary="Thread and Run indexes are available."
            if thread_index_exists and runs_index_exists
            else "No conversation state has been created yet.",
            evidence=[
                f"threads_index={thread_index_exists}",
                f"runs_index={runs_index_exists}",
                f"run_manifest_count={len(run_manifests)}",
                f"non_terminal_runs={len(non_terminal_runs)}",
            ],
            next_actions=[]
            if thread_index_exists
            else ["Create a first conversation during smoke."],
        ),
        ReadinessCheck(
            check_id="conversation.stale_runs",
            category="runtime",
            title="Stale running Run recovery",
            status=stale_status,
            summary=(
                "No stale running runs were found."
                if stale_status == "pass"
                else (
                    "Stale running runs need recovery."
                    if stale_running_runs
                    else (
                        "Some run manifests cannot be read."
                        if unreadable_count
                        else "No run index exists yet."
                    )
                )
            ),
            evidence=[
                f"running_runs={len(running_runs)}",
                f"non_terminal_runs={len(non_terminal_runs)}",
                f"stale_running_runs={len(stale_running_runs)}",
                f"stale_after_seconds={stale_after_seconds}",
                f"unreadable_run_manifests={unreadable_count}",
            ],
            next_actions=[]
            if stale_status == "pass"
            else (
                [
                    (
                        "Call POST /workspaces/{workspace_id}/runs/recover-stale "
                        "or use the Chat recovery action."
                    )
                ]
                if stale_running_runs
                else (
                    ["Inspect corrupted run manifest objects in ObjectStore."]
                    if unreadable_count
                    else ["Create a first conversation during smoke."]
                )
            ),
            details={
                "stale_run_ids": stale_run_ids[:20],
                "unreadable_run_manifest_keys": unreadable_run_keys[:20],
            },
        )
    ]


def _load_run_manifests(
    workspace_id: str,
    object_store: ObjectStore,
) -> tuple[list[dict[str, Any]], list[str]]:
    manifests: list[dict[str, Any]] = []
    unreadable_keys: list[str] = []
    for key in object_store.list_keys(f"{workspace_prefix(workspace_id)}/runs"):
        if not key.endswith("/manifest.json"):
            continue
        try:
            value = json.loads(object_store.read_text(key))
        except Exception:  # noqa: BLE001 - readiness reports bad objects without crashing.
            unreadable_keys.append(key)
            continue
        if isinstance(value, dict):
            manifests.append(value)
        else:
            unreadable_keys.append(key)
    return manifests, unreadable_keys


def _is_stale_running_run(
    manifest: dict[str, Any],
    *,
    stale_after_seconds: int,
) -> bool:
    if str(manifest.get("status") or "") != "running":
        return False
    owner = manifest.get("owner")
    if isinstance(owner, dict):
        owner_expires_at = _parse_iso_datetime(owner.get("expires_at"))
        if owner_expires_at and owner_expires_at > datetime.now(timezone.utc):
            return False
    updated_at = _parse_iso_datetime(manifest.get("updated_at"))
    if updated_at is None:
        return True
    return datetime.now(timezone.utc) - updated_at >= timedelta(seconds=stale_after_seconds)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _knowledge_checks(workspace_id: str, object_store: ObjectStore) -> list[ReadinessCheck]:
    service = DocumentIngestionService(object_store)
    try:
        knowledge_bases = service.list_knowledge_bases(workspace_id)
        document_count = 0
        partial_count = 0
        retryable_count = 0
        for kb in knowledge_bases:
            kb_id = str(kb.get("knowledge_base_id") or "")
            if not kb_id:
                continue
            documents = service.list_documents(workspace_id, kb_id)
            document_count += len(documents)
            partial_count += sum(
                1 for doc in documents if doc.get("ingestion_status") == "partial_success"
            )
            retryable_count += sum(1 for doc in documents if doc.get("retryable"))
    except Exception as exc:  # noqa: BLE001
        return [
            ReadinessCheck(
                check_id="knowledge.indexes",
                category="knowledge",
                title="Knowledge base indexes",
                status="fail",
                summary="Knowledge base metadata cannot be read.",
                evidence=[exc.__class__.__name__],
                next_actions=["Rebuild knowledge indexes or inspect MinIO object records."],
            )
        ]
    return [
        ReadinessCheck(
            check_id="knowledge.indexes",
            category="knowledge",
            title="Knowledge base indexes",
            status="pass" if knowledge_bases else "warn",
            summary="Knowledge base metadata is readable.",
            evidence=[
                f"knowledge_base_count={len(knowledge_bases)}",
                f"document_count={document_count}",
                f"partial_success_documents={partial_count}",
                f"retryable_documents={retryable_count}",
            ],
            next_actions=[]
            if knowledge_bases
            else ["Create or upload a first knowledge base document."],
        )
    ]


def _runtime_capability_checks(
    workspace_id: str,
    object_store: ObjectStore,
    settings: Settings,
) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    try:
        mcp_service = McpService(object_store)
        mcp_servers = mcp_service.list_servers(workspace_id)
        model_visible_mcp_tools = mcp_service.build_mcp_tool_specs(workspace_id)
    except Exception as exc:  # noqa: BLE001
        checks.append(
            ReadinessCheck(
                check_id="mcp.inventory",
                category="runtime",
                title="MCP server inventory",
                status="fail",
                summary="MCP inventory cannot be read.",
                evidence=[exc.__class__.__name__],
                next_actions=[
                    "Inspect MCP registry objects and run capability refresh after fixing storage."
                ],
            )
        )
    else:
        runtime_configured_count = sum(
            1 for server in mcp_servers if McpService._has_runtime_config(server)
        )
        fallback_server_count = len(mcp_servers) - runtime_configured_count
        status: ReadinessStatus = (
            "pass"
            if mcp_servers and runtime_configured_count > 0
            else ("warn" if mcp_servers else "warn")
        )
        checks.append(
            ReadinessCheck(
                check_id="mcp.inventory",
                category="runtime",
                title="MCP server inventory",
                status=status,
                summary="MCP inventory is readable.",
                evidence=[
                    f"server_count={len(mcp_servers)}",
                    f"runtime_configured_server_count={runtime_configured_count}",
                    f"fallback_unconfigured_server_count={fallback_server_count}",
                    f"model_visible_mcp_tool_count={len(model_visible_mcp_tools)}",
                ],
                next_actions=[]
                if mcp_servers
                else ["Configure a real MCP transport before live smoke."],
            )
        )
    try:
        memories = MemoryService(object_store).list_memories(
            workspace_id,
            user_id=settings.default_user_id,
        )
        skills = SkillService(object_store).list_skills(workspace_id)
        subagent_tasks = SubAgentService(object_store).list_tasks(workspace_id)
    except Exception as exc:  # noqa: BLE001
        checks.append(
            ReadinessCheck(
                check_id="memory.skills.subagents",
                category="runtime",
                title="Memory, Skill, SubAgent registries",
                status="fail",
                summary="Runtime registries cannot be read.",
                evidence=[exc.__class__.__name__],
                next_actions=[
                    "Inspect memory, Skill and SubAgent registry objects in ObjectStore."
                ],
            )
        ),
    else:
        checks.append(
            ReadinessCheck(
                check_id="memory.skills.subagents",
                category="runtime",
                title="Memory, Skill, SubAgent registries",
                status="pass",
                summary="Runtime registries are readable.",
                evidence=[
                    f"memory_count={len(memories)}",
                    f"skill_count={len(skills)}",
                    f"subagent_task_count={len(subagent_tasks)}",
                ],
            )
        )
    return checks


def _observability_check(object_store: ObjectStore, settings: Settings) -> ReadinessCheck:
    prefix = "system/logs"
    try:
        keys = object_store.list_keys(prefix)
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck(
            check_id="observability.logs",
            category="observability",
            title="System logs",
            status="fail",
            summary="System logs cannot be listed.",
            evidence=[exc.__class__.__name__],
            next_actions=["Fix ObjectStore access before relying on diagnostics."],
        )
    today_keys = [
        key for key in keys if f"/{settings.runtime_instance_id}/" in key
    ]
    return ReadinessCheck(
        check_id="observability.logs",
        category="observability",
        title="System logs",
        status="pass" if keys else "warn",
        summary="System logs are present." if keys else "No system logs have been written yet.",
        evidence=[
            f"log_object_count={len(keys)}",
            f"runtime_log_object_count={len(today_keys)}",
        ],
        next_actions=[] if keys else ["Call any API endpoint or run a smoke test to create logs."],
    )


def _external_acceptance_checks(settings: Settings) -> list[ReadinessCheck]:
    report = _load_p0_acceptance_report(settings)
    return [
        _final_handoff_acceptance_check(report),
        _external_acceptance_check(
            check_id="external.main_chat_model_smoke",
            title="Main chat model smoke",
            summary="The configured main chat model must pass a real runtime smoke test.",
            source_check_id="runtime.model_config.main_chat_smoke",
            report=report,
        ),
        _external_acceptance_check(
            check_id="external.graphrag_llm_model_smoke",
            title="GraphRAG LLM smoke",
            summary="The configured GraphRAG LLM must pass a real runtime smoke test.",
            source_check_id="runtime.model_config.graphrag_llm_smoke",
            report=report,
        ),
        _external_acceptance_check(
            check_id="external.embedding_model_smoke",
            title="Embedding model smoke",
            summary="The configured embedding model must pass a real embeddings smoke test.",
            source_check_id="runtime.model_config.embedding_smoke",
            report=report,
        ),
        _external_acceptance_check(
            check_id="external.docker_compose",
            title="Docker Compose full health",
            summary=(
                "Docker Desktop / container runtime health must be verified "
                "in the final acceptance pass."
            ),
            source_check_id="runtime.docker_compose_ps",
            report=report,
        ),
        _external_acceptance_check(
            check_id="external.database_live_health",
            title="Database live health",
            summary="MinIO, Milvus, Neo4j, and Redis live health must pass before handoff.",
            source_check_id="runtime.database_live_health",
            report=report,
        ),
        _external_acceptance_check(
            check_id="external.job_worker_status",
            title="Job worker status",
            summary="The P0 Job worker must be running before handoff.",
            source_check_id="runtime.job_worker_status",
            report=report,
        ),
        _external_acceptance_check(
            check_id="external.mcp_live_smoke",
            title="External MCP live smoke",
            summary="Real external MCP server smoke remains a final acceptance item.",
            source_check_id="runtime.mcp_live_smoke",
            report=report,
        ),
        _external_acceptance_check(
            check_id="external.browser_smoke",
            title="Frontend route smoke",
            summary="Frontend workspace route smoke remains a final acceptance item.",
            source_check_id="runtime.frontend_route_smoke",
            report=report,
            source_check_aliases=("runtime.browser_e2e_smoke",),
        ),
        _external_acceptance_check(
            check_id="external.frontend_browser_smoke",
            title="Frontend browser smoke",
            summary=(
                "A real headless browser must render the workspace routes before handoff."
            ),
            source_check_id="runtime.frontend_browser_smoke",
            report=report,
        ),
        _readiness_after_report_acceptance_check(report),
    ]


def _final_handoff_acceptance_check(report: dict[str, Any] | None) -> ReadinessCheck:
    if not report:
        return ReadinessCheck(
            check_id="external.final_handoff",
            category="acceptance",
            title="Final handoff completeness",
            status="blocked",
            summary="No P0 acceptance report has been generated for final handoff.",
            required=True,
            evidence=["p0_acceptance_report=missing"],
            next_actions=["Run scripts/p0_acceptance.py with the required final acceptance flags."],
        )
    report_path = str(report.get("path") or "")
    if report.get("invalid"):
        return ReadinessCheck(
            check_id="external.final_handoff",
            category="acceptance",
            title="Final handoff completeness",
            status="fail",
            summary="Latest P0 acceptance report is invalid.",
            required=True,
            evidence=[f"report_path={report_path}", "report_invalid=true"],
            next_actions=["Regenerate the P0 acceptance report."],
        )
    schema_version = _safe_int(report.get("schema_version"))
    if schema_version < MIN_ACCEPTANCE_REPORT_SCHEMA_VERSION:
        return ReadinessCheck(
            check_id="external.final_handoff",
            category="acceptance",
            title="Final handoff completeness",
            status="blocked",
            summary="Latest P0 acceptance report was generated by an outdated helper.",
            required=True,
            evidence=[
                f"report_path={report_path}",
                f"report_schema_version={schema_version}",
                f"required_schema_version={MIN_ACCEPTANCE_REPORT_SCHEMA_VERSION}",
            ],
            next_actions=["Regenerate the P0 acceptance report with the current helper script."],
            details={
                "report_summary": report.get("summary")
                if isinstance(report.get("summary"), dict)
                else {},
            },
        )
    final_handoff = report.get("final_handoff")
    if not isinstance(final_handoff, dict):
        return ReadinessCheck(
            check_id="external.final_handoff",
            category="acceptance",
            title="Final handoff completeness",
            status="blocked",
            summary="Latest P0 acceptance report does not include final_handoff.",
            required=True,
            evidence=[
                f"report_path={report_path}",
                f"report_generated_at={report.get('generated_at')}",
                "final_handoff=missing",
            ],
            next_actions=["Regenerate the P0 acceptance report with the current helper script."],
            details={
                "report_summary": report.get("summary")
                if isinstance(report.get("summary"), dict)
                else {},
            },
        )
    top_contract_id = str(report.get("final_handoff_contract_id") or "")
    top_contract_version = _safe_int(report.get("final_handoff_contract_version"))
    nested_contract_id = str(final_handoff.get("contract_id") or "")
    nested_contract_version = _safe_int(final_handoff.get("contract_version"))
    stale_contract_reasons = []
    if top_contract_id != FINAL_HANDOFF_CONTRACT_ID:
        stale_contract_reasons.append("top_contract_id")
    if top_contract_version != FINAL_HANDOFF_CONTRACT_VERSION:
        stale_contract_reasons.append("top_contract_version")
    if nested_contract_id != FINAL_HANDOFF_CONTRACT_ID:
        stale_contract_reasons.append("final_handoff.contract_id")
    if nested_contract_version != FINAL_HANDOFF_CONTRACT_VERSION:
        stale_contract_reasons.append("final_handoff.contract_version")
    readiness_after_report_pending = (
        final_handoff.get("readiness_after_report_pending") is True
    )
    declared_required_check_ids = _list_or_schema_error(
        final_handoff.get("required_check_ids"),
        "required_check_ids",
    )
    declared_required_flags = _list_or_schema_error(
        final_handoff.get("required_flags")
        or report.get("required_final_handoff_flags"),
        "required_flags",
    )
    stale_required_check_ids = [
        check_id
        for check_id in FINAL_HANDOFF_REQUIRED_CHECK_IDS
        if check_id not in declared_required_check_ids
    ]
    stale_required_flags = [
        flag for flag in FINAL_HANDOFF_REQUIRED_FLAGS if flag not in declared_required_flags
    ]
    missing = _list_or_schema_error(
        final_handoff.get("missing_check_ids"),
        "missing_check_ids",
    )
    missing_flags = _list_or_schema_error(final_handoff.get("missing_flags"), "missing_flags")
    non_passing = _list_or_schema_error(
        final_handoff.get("non_passing_checks"),
        "non_passing_checks",
    )
    non_passing_executed = _list_or_schema_error(
        final_handoff.get("non_passing_executed_checks"),
        "non_passing_executed_checks",
    )
    effective_missing = (
        _without_readiness_self_check(missing)
        if readiness_after_report_pending
        else missing
    )
    effective_non_passing = (
        _without_readiness_self_check(non_passing)
        if readiness_after_report_pending
        else non_passing
    )
    effective_non_passing_executed = (
        _without_readiness_self_check(non_passing_executed)
        if readiness_after_report_pending
        else non_passing_executed
    )
    missing_count = len(effective_missing)
    missing_flag_count = len(missing_flags)
    non_passing_count = len(effective_non_passing)
    non_passing_executed_count = len(effective_non_passing_executed)
    stale_required_count = len(stale_required_check_ids) + len(stale_required_flags)
    stale_contract_count = len(stale_contract_reasons)
    has_failed_check = (
        any(
            isinstance(item, dict) and item.get("status") == "fail"
            for item in effective_non_passing_executed
        )
        or any(
            isinstance(item, dict) and item.get("status") == "fail"
            for item in effective_non_passing
        )
    )
    declared_ready = final_handoff.get("ready") is True
    computed_ready = (
        missing_flag_count == 0
        and missing_count == 0
        and non_passing_count == 0
        and non_passing_executed_count == 0
        and stale_required_count == 0
        and stale_contract_count == 0
    )
    ready = computed_ready and (declared_ready or readiness_after_report_pending)
    status: ReadinessStatus = "pass" if ready else ("fail" if has_failed_check else "blocked")
    recommended_command = str(
        final_handoff.get("recommended_command")
        or (
            "conda activate py313\n"
            "python scripts/p0_acceptance.py --include-root-e2e "
            "--include-p0-contracts --include-runtime-http "
            "--include-model-smoke --include-docker "
            "--mcp-server-name <configured-server-name> --require-final-handoff"
        )
    )
    return ReadinessCheck(
        check_id="external.final_handoff",
        category="acceptance",
        title="Final handoff completeness",
        status=status,
        summary=(
            "Final P0 handoff checks are complete."
            if ready
            else "Final P0 handoff checks are incomplete."
        ),
        required=True,
        evidence=[
            f"report_path={report_path}",
            f"report_generated_at={report.get('generated_at')}",
            f"report_schema_version={schema_version}",
            f"final_handoff_contract_id={top_contract_id or 'missing'}",
            f"final_handoff_contract_version={top_contract_version}",
            f"final_handoff_nested_contract_id={nested_contract_id or 'missing'}",
            f"final_handoff_nested_contract_version={nested_contract_version}",
            f"required_final_handoff_contract_id={FINAL_HANDOFF_CONTRACT_ID}",
            f"required_final_handoff_contract_version={FINAL_HANDOFF_CONTRACT_VERSION}",
            f"stale_final_handoff_contract={bool(stale_contract_reasons)}",
            f"final_handoff_declared_ready={declared_ready}",
            f"final_handoff_ready={ready}",
            f"readiness_after_report_pending={readiness_after_report_pending}",
            f"missing_required_flag_count={missing_flag_count}",
            f"missing_check_count={missing_count}",
            f"non_passing_check_count={non_passing_count}",
            f"non_passing_executed_check_count={non_passing_executed_count}",
            f"stale_required_check_count={len(stale_required_check_ids)}",
            f"stale_required_flag_count={len(stale_required_flags)}",
            f"stale_contract_count={stale_contract_count}",
            f"{READINESS_AFTER_REPORT_CHECK_ID}=self_check_evaluated_by_acceptance_helper",
        ],
        next_actions=[] if ready else [recommended_command],
        details={
            "final_handoff": final_handoff,
            "report_summary": report.get("summary")
            if isinstance(report.get("summary"), dict)
            else {},
            "stale_required_check_ids": stale_required_check_ids,
            "stale_required_flags": stale_required_flags,
            "stale_contract_reasons": stale_contract_reasons,
        },
    )


def _list_or_schema_error(value: Any, field_name: str) -> list[Any]:
    if isinstance(value, list):
        return value
    return [f"{field_name}=missing_or_invalid"]


def _without_readiness_self_check(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    retained: list[Any] = []
    for item in value:
        if item == READINESS_AFTER_REPORT_CHECK_ID:
            continue
        if isinstance(item, dict) and item.get("check_id") == READINESS_AFTER_REPORT_CHECK_ID:
            continue
        retained.append(item)
    return retained


def _readiness_after_report_acceptance_check(
    report: dict[str, Any] | None,
) -> ReadinessCheck:
    check_id = "external.p0_readiness_after_report"
    source_check_id = READINESS_AFTER_REPORT_CHECK_ID
    if _has_pending_readiness_after_report_check(report):
        report_path = str((report or {}).get("path") or "")
        final_handoff = (report or {}).get("final_handoff")
        return ReadinessCheck(
            check_id=check_id,
            category="acceptance",
            title="P0 readiness after report",
            status="not_applicable",
            summary=(
                "The acceptance helper is evaluating the post-report readiness "
                "self-check for this preliminary report."
            ),
            required=True,
            evidence=[
                f"report_path={report_path}",
                f"source_check_id={source_check_id}",
                "source_check=pending",
            ],
            details={
                "source_check_id": source_check_id,
                "final_handoff": final_handoff if isinstance(final_handoff, dict) else {},
            },
        )
    return _external_acceptance_check(
        check_id=check_id,
        title="P0 readiness after report",
        summary=(
            "The backend P0 readiness endpoint must be clean after the final "
            "acceptance report is written."
        ),
        source_check_id=source_check_id,
        report=report,
    )


def _has_pending_readiness_after_report_check(report: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict) or report.get("invalid"):
        return False
    if _safe_int(report.get("schema_version")) < MIN_ACCEPTANCE_REPORT_SCHEMA_VERSION:
        return False
    final_handoff = report.get("final_handoff")
    if not isinstance(final_handoff, dict):
        return False
    if final_handoff.get("readiness_after_report_pending") is not True:
        return False
    return _find_acceptance_report_check(report, (READINESS_AFTER_REPORT_CHECK_ID,)) is None


def _load_p0_acceptance_report(settings: Settings) -> dict[str, Any] | None:
    path = Path(settings.p0_acceptance_report_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {
            "invalid": True,
            "path": str(path),
        }
    if isinstance(payload, dict):
        payload["path"] = str(path)
        return payload
    return {
        "invalid": True,
        "path": str(path),
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _external_acceptance_check(
    check_id: str,
    title: str,
    summary: str,
    source_check_id: str,
    report: dict[str, Any] | None,
    source_check_aliases: Sequence[str] = (),
) -> ReadinessCheck:
    if not report:
        return ReadinessCheck(
            check_id=check_id,
            category="acceptance",
            title=title,
            status="blocked",
            summary=summary,
            required=True,
            evidence=["p0_acceptance_report=missing"],
            next_actions=["Run scripts/p0_acceptance.py with the required final acceptance flags."],
            details={"source_check_id": source_check_id},
        )
    report_path = str(report.get("path") or "")
    if report.get("invalid"):
        return ReadinessCheck(
            check_id=check_id,
            category="acceptance",
            title=title,
            status="fail",
            summary="Latest P0 acceptance report is invalid.",
            required=True,
            evidence=[f"report_path={report_path}", "report_invalid=true"],
            next_actions=["Regenerate the P0 acceptance report."],
            details={"source_check_id": source_check_id},
        )
    schema_version = _safe_int(report.get("schema_version"))
    if schema_version < MIN_ACCEPTANCE_REPORT_SCHEMA_VERSION:
        return ReadinessCheck(
            check_id=check_id,
            category="acceptance",
            title=title,
            status="blocked",
            summary="Latest P0 acceptance report was generated by an outdated helper.",
            required=True,
            evidence=[
                f"report_path={report_path}",
                f"report_schema_version={schema_version}",
                f"required_schema_version={MIN_ACCEPTANCE_REPORT_SCHEMA_VERSION}",
                f"source_check_id={source_check_id}",
            ],
            next_actions=["Regenerate the P0 acceptance report with the current helper script."],
            details={"source_check_id": source_check_id},
        )
    source = _find_acceptance_report_check(
        report,
        (source_check_id, *source_check_aliases),
    )
    if source is None:
        return ReadinessCheck(
            check_id=check_id,
            category="acceptance",
            title=title,
            status="blocked",
            summary=summary,
            required=True,
            evidence=[
                f"report_path={report_path}",
                f"source_check_id={source_check_id}",
                "source_check_aliases="
                f"{','.join(source_check_aliases) if source_check_aliases else 'none'}",
                "source_check=missing",
            ],
            next_actions=[
                "Rerun scripts/p0_acceptance.py with runtime flags that include this check."
            ],
            details={
                "source_check_id": source_check_id,
                "source_check_aliases": list(source_check_aliases),
                "report_generated_at": report.get("generated_at"),
            },
        )
    source_status = str(source.get("status") or "")
    status = _map_acceptance_status(source_status)
    source_summary = str(source.get("summary") or summary)
    next_action = str(source.get("next_action") or "")
    return ReadinessCheck(
        check_id=check_id,
        category="acceptance",
        title=title,
        status=status,
        summary=source_summary,
        required=True,
        evidence=[
            f"report_path={report_path}",
            f"report_generated_at={report.get('generated_at')}",
            f"source_check_id={source_check_id}",
            "source_check_aliases="
            f"{','.join(source_check_aliases) if source_check_aliases else 'none'}",
            f"source_status={source_status or 'unknown'}",
        ],
        next_actions=[]
        if status == "pass"
        else [next_action or "Rerun final P0 acceptance after fixing this check."],
        details={
            "source_check": source,
            "report_summary": report.get("summary")
            if isinstance(report.get("summary"), dict)
            else {},
        },
    )


def _find_acceptance_report_check(
    report: dict[str, Any],
    source_check_ids: Sequence[str],
) -> dict[str, Any] | None:
    checks = report.get("checks")
    if not isinstance(checks, list):
        return None
    typed_checks = [check for check in checks if isinstance(check, dict)]
    for source_check_id in source_check_ids:
        for check in typed_checks:
            if check.get("check_id") == source_check_id:
                return check
    return None


def _map_acceptance_status(status: str) -> ReadinessStatus:
    if status == "pass":
        return "pass"
    if status == "fail":
        return "fail"
    return "blocked"


def _group_readiness_checks(checks: list[ReadinessCheck]) -> list[ReadinessCategory]:
    categories: dict[str, list[ReadinessCheck]] = {}
    for check in checks:
        categories.setdefault(check.category, []).append(check)
    return [
        ReadinessCategory(
            category=category,
            status=_overall_status(items),
            pass_count=sum(1 for item in items if item.status == "pass"),
            warn_count=sum(1 for item in items if item.status == "warn"),
            fail_count=sum(1 for item in items if item.status == "fail"),
            blocked_count=sum(1 for item in items if item.status == "blocked"),
            checks=items,
        )
        for category, items in sorted(categories.items())
    ]


def _overall_status(checks: list[ReadinessCheck]) -> ReadinessStatus:
    required = [check for check in checks if check.required]
    if any(check.status == "fail" for check in required):
        return "fail"
    if any(check.status == "blocked" for check in required):
        return "blocked"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "pass"
