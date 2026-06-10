from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import get_settings
from app.database.service import TARGET_ORDER, DatabaseConfigService
from app.secret_store.master_key import MasterKeyProvider
from app.secret_store.secret_resolver import SecretResolver
from app.secret_store.secret_service import SecretService


class DatabaseScopeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)


def build_database_health_check_tool(
    *,
    service: DatabaseConfigService,
) -> StructuredTool:
    def database_health_check(workspace_id: str) -> dict[str, Any]:
        snapshot = service.run_health_check_sync(workspace_id)
        report = _build_health_report(service, workspace_id, snapshot)
        return {"ok": bool(snapshot.get("ok")), "data": report}

    return StructuredTool.from_function(
        func=database_health_check,
        name="database_health_check",
        description=(
            "Run a live health check for MinIO, Milvus, Neo4j, and Redis "
            "and return a sanitized status report."
        ),
        args_schema=DatabaseScopeArgs,
    )


def build_database_health_diagnose_tool(
    *,
    service: DatabaseConfigService,
) -> StructuredTool:
    def database_health_diagnose(workspace_id: str) -> dict[str, Any]:
        snapshot = service.get_health_snapshot(workspace_id)
        report = _build_health_report(service, workspace_id, snapshot)
        return {"ok": bool(snapshot.get("ok")), "data": report}

    return StructuredTool.from_function(
        func=database_health_diagnose,
        name="database_health_diagnose",
        description=(
            "Read the latest stored database health snapshot and return "
            "a sanitized diagnosis report."
        ),
        args_schema=DatabaseScopeArgs,
    )


def build_default_database_tools(object_store: Any) -> list[StructuredTool]:
    settings = get_settings()
    master_key_provider = MasterKeyProvider(settings)
    secret_service = SecretService(object_store, master_key_provider)
    service = DatabaseConfigService(
        object_store,
        settings,
        secret_service=secret_service,
        secret_resolver=SecretResolver(secret_service, master_key_provider),
    )
    return [
        build_database_health_check_tool(service=service),
        build_database_health_diagnose_tool(service=service),
    ]


def _build_health_report(
    service: DatabaseConfigService,
    workspace_id: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    config = service.get_config(workspace_id)
    target_configs = {
        str(target.get("target")): _public_target(target)
        for target in config.get("targets", [])
        if target.get("target")
    }
    service_by_target = {
        str(item.get("target")): dict(item)
        for item in snapshot.get("services", [])
        if item.get("target")
    }

    target_reports = []
    unhealthy_targets: list[str] = []
    unknown_targets: list[str] = []
    recommended_actions: list[str] = []

    for target_name in TARGET_ORDER:
        config_item = target_configs.get(target_name, {})
        service_item = service_by_target.get(target_name, {})
        status = str(service_item.get("status") or "unknown")
        report_item = _target_report(target_name, config_item, service_item)
        target_reports.append(report_item)
        if status == "disabled" or report_item["enabled"] is False:
            continue
        if status != "healthy":
            if status == "unknown":
                unknown_targets.append(target_name)
            else:
                unhealthy_targets.append(target_name)
            recommended_actions.extend(report_item["recommended_actions"])

    summary = {
        "healthy_targets": [
            item["target"] for item in target_reports if item["status"] == "healthy"
        ],
        "unhealthy_targets": unhealthy_targets,
        "unknown_targets": unknown_targets,
        "source": snapshot.get("source") or "unknown",
        "checked_at": snapshot.get("checked_at"),
        "snapshot_ok": bool(snapshot.get("ok")),
    }

    return {
        "workspace_id": workspace_id,
        "snapshot": snapshot,
        "summary": summary,
        "targets": target_reports,
        "recommended_actions": _dedupe_text(recommended_actions),
    }


def _target_report(
    target_name: str,
    config_item: dict[str, Any],
    service_item: dict[str, Any],
) -> dict[str, Any]:
    status = str(service_item.get("status") or "unknown")
    enabled = bool(config_item.get("enabled", True))
    has_credential_refs = bool(config_item.get("credential_refs"))
    endpoint = str(config_item.get("endpoint") or "")
    return {
        "target": target_name,
        "enabled": enabled,
        "mode": str(config_item.get("mode") or "local"),
        "endpoint": endpoint,
        "tls": bool(config_item.get("tls", False)),
        "bucket": config_item.get("bucket"),
        "has_credential_refs": has_credential_refs,
        "status": status,
        "latency_ms": service_item.get("latency_ms"),
        "message": service_item.get("message"),
        "checked_at": service_item.get("checked_at"),
        "details": service_item.get("details") or {},
        "recommended_actions": _target_recommendations(
            target_name=target_name,
            status=status,
            enabled=enabled,
            endpoint=endpoint,
            has_credential_refs=has_credential_refs,
        ),
    }


def _target_recommendations(
    *,
    target_name: str,
    status: str,
    enabled: bool,
    endpoint: str,
    has_credential_refs: bool,
) -> list[str]:
    if not enabled:
        return [f"{target_name} 当前未启用，启用后再执行健康检查。"]
    if status == "healthy":
        return []

    recommendations: list[str] = []
    if target_name == "minio":
        recommendations.extend(
            [
                "确认 MinIO endpoint 可达，并且 health/live 返回正常状态。",
                "确认 bucket 已创建，并且对象存储访问配置可用。",
            ]
        )
    elif target_name == "milvus":
        recommendations.extend(
            [
                "确认 Milvus 服务地址与健康检查端口可达。",
                "确认当前 active embedding 版本对应的 collection 已创建并可查询。",
            ]
        )
    elif target_name == "neo4j":
        recommendations.extend(
            [
                "确认 Neo4j HTTP 或 Bolt 入口可达。",
                "确认数据库账号配置已准备好，并可用于图谱读写。",
            ]
        )
        if not has_credential_refs:
            recommendations.append("为 Neo4j 补充可解析的连接凭据配置。")
    elif target_name == "redis":
        recommendations.extend(
            [
                "确认 Redis 缓存地址可达。",
                "Redis 只承担缓存职责，不承担权威状态存储。",
            ]
        )
    else:
        recommendations.append(f"检查 {target_name} 的 endpoint 和运行时配置。")

    if endpoint:
        recommendations.append(f"复核 {target_name} endpoint: {endpoint}")
    return recommendations


def _public_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": target.get("target"),
        "mode": target.get("mode") or "local",
        "enabled": bool(target.get("enabled", True)),
        "endpoint": target.get("endpoint") or "",
        "tls": bool(target.get("tls", False)),
        "bucket": target.get("bucket"),
        "credential_refs": dict(target.get("credential_refs") or {}),
        "options": dict(target.get("options") or {}),
    }


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped
