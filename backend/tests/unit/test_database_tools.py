from __future__ import annotations

import json

from app.database import service as database_service
from app.runtime.tools import build_default_tool_registry
from app.schemas.health import ServiceHealth
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore
from app.storage.path_builder import database_health_snapshot_key


def test_default_tool_registry_exposes_database_health_tools(tmp_path) -> None:
    registry = build_default_tool_registry(LocalObjectStore(tmp_path / "objects"))
    names = {spec["name"] for spec in registry.model_safe_specs()}

    assert {"database_health_check", "database_health_diagnose"} <= names


def test_database_health_tools_return_sanitized_diagnosis_and_persist_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")

    def fake_check_database_services_sync(_settings):
        return [
            ServiceHealth(
                target="minio",
                status="healthy",
                latency_ms=1.0,
                message="HTTP 200",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="milvus",
                status="healthy",
                latency_ms=2.0,
                message="HTTP 200",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="neo4j",
                status="unhealthy",
                latency_ms=3.0,
                message="ConnectionError",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
            ServiceHealth(
                target="redis",
                status="healthy",
                latency_ms=1.0,
                message="tcp_connect_ok",
                checked_at="2026-05-31T00:00:00+00:00",
            ),
        ]

    monkeypatch.setattr(
        database_service,
        "check_database_services_sync",
        fake_check_database_services_sync,
    )
    registry = build_default_tool_registry(object_store)

    check_result = registry.invoke("database_health_check", {"workspace_id": "default"})
    diagnose_result = registry.invoke("database_health_diagnose", {"workspace_id": "default"})

    assert check_result["ok"] is False
    assert check_result["data"]["summary"]["unhealthy_targets"] == ["neo4j"]
    neo4j_report = next(
        item for item in check_result["data"]["targets"] if item["target"] == "neo4j"
    )
    assert neo4j_report["status"] == "unhealthy"
    assert neo4j_report["recommended_actions"]
    assert "确认 Neo4j HTTP 或 Bolt 入口可达。" in neo4j_report["recommended_actions"]
    assert "为 Neo4j 补充可解析的连接凭据配置。" in neo4j_report[
        "recommended_actions"
    ]
    assert "secret_ref" not in json.dumps(check_result, ensure_ascii=False).lower()
    assert "纭" not in json.dumps(check_result, ensure_ascii=False)
    assert "�" not in json.dumps(check_result, ensure_ascii=False)

    assert diagnose_result["ok"] is False
    assert diagnose_result["data"]["summary"]["unhealthy_targets"] == ["neo4j"]
    assert diagnose_result["data"]["targets"][2]["status"] == "unhealthy"

    snapshot = JsonObjectStore(object_store).read_json(
        database_health_snapshot_key("default"),
    )
    assert snapshot["ok"] is False
    assert snapshot["services"][2]["target"] == "neo4j"
