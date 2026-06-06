import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api.dependencies import get_object_store
from app.jobs.service import JobService
from app.main import app
from app.schemas.identity import RuntimeIdentity
from app.schemas.job import CreateJobRequest
from app.storage.local_object_store import LocalObjectStore
from app.storage.path_builder import (
    model_config_key,
    run_manifest_key,
    threads_index_key,
    workspace_runs_index_key,
)


FINAL_HANDOFF_FLAGS = [
    "--include-root-e2e",
    "--include-p0-contracts",
    "--include-runtime-http",
    "--include-model-smoke",
    "--include-docker",
    "--mcp-server-name",
    "--require-final-handoff",
]

FINAL_HANDOFF_CHECK_IDS = [
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
    "runtime.p0_readiness_after_report",
]

ACCEPTANCE_EXTERNAL_CHECK_IDS = [
    "external.main_chat_model_smoke",
    "external.graphrag_llm_model_smoke",
    "external.embedding_model_smoke",
    "external.docker_compose",
    "external.database_live_health",
    "external.job_worker_status",
    "external.mcp_live_smoke",
    "external.browser_smoke",
    "external.frontend_browser_smoke",
    "external.p0_readiness_after_report",
]

FINAL_HANDOFF_CONTRACT_ID = "p0-final-handoff-2026-06-01"
FINAL_HANDOFF_CONTRACT_VERSION = 3


def _acceptance_check(check_id: str, status: str = "pass") -> dict[str, str]:
    return {
        "check_id": check_id,
        "status": status,
        "summary": f"{check_id} {status}",
    }


def _acceptance_report(checks: list[dict[str, str]], **final_handoff_overrides):
    by_check_id = {check["check_id"]: check for check in checks}
    missing = [
        check_id
        for check_id in FINAL_HANDOFF_CHECK_IDS
        if check_id not in by_check_id
    ]
    non_passing = [
        check
        for check in checks
        if check.get("status") != "pass"
    ]
    final_handoff = {
        "ready": not missing and not non_passing,
        "schema_version": 1,
        "contract_id": FINAL_HANDOFF_CONTRACT_ID,
        "contract_version": FINAL_HANDOFF_CONTRACT_VERSION,
        "required_flags": FINAL_HANDOFF_FLAGS,
        "provided_flags": FINAL_HANDOFF_FLAGS,
        "missing_flags": [],
        "required_check_ids": FINAL_HANDOFF_CHECK_IDS,
        "missing_check_ids": missing,
        "non_passing_checks": non_passing,
        "non_passing_executed_checks": non_passing,
        "readiness_after_report_pending": False,
        "recommended_command": (
            "conda activate py313\n"
            "python scripts/p0_acceptance.py --include-root-e2e "
            "--include-p0-contracts --include-runtime-http "
            "--include-model-smoke --include-docker "
            "--mcp-server-name <configured-server-name> --require-final-handoff"
        ),
    }
    final_handoff.update(final_handoff_overrides)
    return {
        "schema_version": 2,
        "final_handoff_contract_id": FINAL_HANDOFF_CONTRACT_ID,
        "final_handoff_contract_version": FINAL_HANDOFF_CONTRACT_VERSION,
        "generated_at": "2026-05-31T19:30:00+0800",
        "provided_flags": FINAL_HANDOFF_FLAGS,
        "summary": {
            "pass": sum(1 for check in checks if check.get("status") == "pass"),
            "fail": sum(1 for check in checks if check.get("status") == "fail"),
            "skipped": sum(1 for check in checks if check.get("status") == "skipped"),
            "total": len(checks),
        },
        "final_handoff": final_handoff,
        "checks": checks,
    }


def test_health_endpoint_returns_process_health() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["service"] == "agent-server"


def test_p0_readiness_endpoint_returns_workspace_checks() -> None:
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == "default"
    assert body["status"] in {"pass", "warn", "fail", "blocked"}
    assert body["summary"]["total"] == len(body["checks"])
    assert body["categories"]
    assert any(check["check_id"] == "storage.object_store" for check in body["checks"])


def test_p0_readiness_reports_stale_running_runs(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    stale_run_id = "run_stale_readiness"
    object_store.write_text(
        threads_index_key("default"),
        json.dumps(
            {
                "schema_version": 1,
                "workspace_id": "default",
                "threads": [],
                "revision": 1,
            }
        ),
    )
    object_store.write_text(
        workspace_runs_index_key("default"),
        json.dumps(
            {
                "schema_version": 1,
                "workspace_id": "default",
                "runs": [
                    {
                        "run_id": stale_run_id,
                        "thread_id": "thread_stale_readiness",
                        "status": "running",
                    }
                ],
                "revision": 1,
            }
        ),
    )
    object_store.write_text(
        run_manifest_key("default", stale_run_id),
        json.dumps(
            {
                "schema_version": 1,
                "workspace_id": "default",
                "thread_id": "thread_stale_readiness",
                "run_id": stale_run_id,
                "status": "running",
                "updated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                "owner": None,
                "revision": 1,
            }
        ),
    )
    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        client = TestClient(app)
        response = client.get("/workspaces/default/readiness")
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    assert response.status_code == 200
    body = response.json()
    checks = {check["check_id"]: check for check in body["checks"]}
    stale_check = checks["conversation.stale_runs"]
    assert body["ok"] is False
    assert stale_check["status"] == "blocked"
    assert "stale_running_runs=1" in stale_check["evidence"]
    assert stale_check["details"]["stale_run_ids"] == [stale_run_id]
    assert any("conversation.stale_runs" in item for item in body["remaining_blockers"])


def test_p0_readiness_blocks_unknown_outcome_jobs(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    service = JobService(object_store, runtime_instance_id="rt_readiness")
    identity = RuntimeIdentity(
        user_id="default_user",
        workspace_id="default",
        workspace_role="owner",
    )
    created = service.create_job(
        "default",
        identity,
        CreateJobRequest(
            job_type="mcp_capability_refresh_job",
            title="Recoverable job",
            target_scope={"scope_type": "mcp_server", "server_name": "filesystem"},
            idempotency_key="job-readiness-recovery",
        ),
    )
    manifest = service.get_job("default", created["job_id"])["manifest"]
    manifest["status"] = "unknown_outcome"
    manifest["progress"] = {
        **manifest.get("progress", {}),
        "current_stage": "recovery",
        "message": "Worker outcome is unknown.",
    }
    service._write_manifest("default", created["job_id"], manifest)
    service._upsert_job_index("default", manifest)

    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        client = TestClient(app)
        response = client.get("/workspaces/default/readiness")
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    assert response.status_code == 200
    body = response.json()
    checks = {check["check_id"]: check for check in body["checks"]}
    job_check = checks["jobs.index"]
    assert body["ok"] is False
    assert job_check["status"] == "blocked"
    assert "recovery_sensitive=1" in job_check["evidence"]
    assert job_check["details"]["recovery_job_ids"] == [created["job_id"]]
    assert any("jobs.index" in item for item in body["remaining_blockers"])


def test_p0_readiness_blocks_anthropic_embedding_provider(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")

    def write_model_config(config_id: str, provider: str, model: str, api_key_ref: str) -> None:
        object_store.write_text(
            model_config_key("default", config_id),
            json.dumps(
                {
                    "schema_version": 1,
                    "workspace_id": "default",
                    "config_id": config_id,
                    "display_name": config_id,
                    "purpose": "embedding" if config_id == "embedding" else "chat",
                    "provider": provider,
                    "model": model,
                    "base_url": "https://example.invalid/v1",
                    "api_key_ref": api_key_ref,
                    "context_window_tokens": 200000,
                    "max_output_tokens": 8192,
                    "timeout_ms": 60000,
                    "supports_tool_calling": config_id != "embedding",
                    "enabled": True,
                    "updated_at": "2026-05-31T00:00:00+00:00",
                    "revision": 1,
                }
            ),
        )

    write_model_config("main_chat", "openai_compatible", "chat-model", "secret_chat")
    write_model_config("graphrag_llm", "anthropic", "graph-model", "secret_graph")
    write_model_config("embedding", "anthropic", "embedding-model", "secret_embedding")

    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        client = TestClient(app)
        response = client.get("/workspaces/default/readiness")
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    assert response.status_code == 200
    checks = {check["check_id"]: check for check in response.json()["checks"]}
    model_check = checks["models.config"]
    assert model_check["status"] == "blocked"
    assert any(
        item.startswith("embedding:missing_secret:anthropic")
        for item in model_check["evidence"]
    )
    assert "OpenAI-compatible provider" in model_check["next_actions"][0]


def test_p0_readiness_consumes_latest_acceptance_report(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-05-31T17:05:55+0800",
                "provided_flags": [
                    "--include-root-e2e",
                    "--include-p0-contracts",
                    "--include-runtime-http",
                    "--include-model-smoke",
                    "--include-docker",
                    "--mcp-server-name",
                    "--require-final-handoff",
                ],
                "summary": {"pass": 1, "fail": 1, "skipped": 1, "total": 3},
                "final_handoff": {
                    "ready": False,
                    "schema_version": 1,
                    "required_flags": [
                        "--include-root-e2e",
                        "--include-p0-contracts",
                        "--include-runtime-http",
                        "--include-model-smoke",
                        "--include-docker",
                        "--mcp-server-name",
                        "--require-final-handoff",
                    ],
                    "provided_flags": [
                        "--include-root-e2e",
                        "--include-p0-contracts",
                        "--include-runtime-http",
                        "--include-model-smoke",
                        "--include-docker",
                        "--mcp-server-name",
                        "--require-final-handoff",
                    ],
                    "missing_flags": [],
                    "required_check_ids": [
                        "code.root_e2e_contracts",
                        "code.backend_p0_contracts",
                        "code.frontend_p0_contracts",
                        "runtime.model_config.main_chat_smoke",
                        "runtime.model_config.graphrag_llm_smoke",
                        "runtime.docker_compose_ps",
                        "runtime.mcp_live_smoke",
                        "runtime.frontend_route_smoke",
                    ],
                    "missing_check_ids": [],
                    "non_passing_checks": [
                        {
                            "check_id": "runtime.mcp_live_smoke",
                            "status": "skipped",
                            "summary": "No MCP server name was provided.",
                            "next_action": "Rerun with --mcp-server-name.",
                        },
                        {
                            "check_id": "runtime.frontend_route_smoke",
                            "status": "fail",
                            "summary": "Browser smoke failed.",
                            "next_action": "Inspect browser logs.",
                        },
                    ],
                    "non_passing_executed_checks": [
                        {
                            "check_id": "runtime.mcp_live_smoke",
                            "status": "skipped",
                            "summary": "No MCP server name was provided.",
                            "next_action": "Rerun with --mcp-server-name.",
                        },
                        {
                            "check_id": "runtime.frontend_route_smoke",
                            "status": "fail",
                            "summary": "Browser smoke failed.",
                            "next_action": "Inspect browser logs.",
                        },
                    ],
                    "recommended_command": (
                        "conda activate py313\n"
                        "python scripts/p0_acceptance.py --include-root-e2e "
                        "--include-p0-contracts --include-runtime-http "
                        "--include-model-smoke --include-docker "
                        "--mcp-server-name <configured-server-name> --require-final-handoff"
                    ),
                },
                "checks": [
                    {
                        "check_id": "runtime.model_config.main_chat_smoke",
                        "status": "pass",
                        "summary": "Main chat model smoke passed.",
                    },
                    {
                        "check_id": "runtime.model_config.graphrag_llm_smoke",
                        "status": "pass",
                        "summary": "GraphRAG LLM smoke passed.",
                    },
                    {
                        "check_id": "runtime.docker_compose_ps",
                        "status": "pass",
                        "summary": "Docker compose services are running.",
                    },
                    {
                        "check_id": "runtime.mcp_live_smoke",
                        "status": "skipped",
                        "summary": "No MCP server name was provided.",
                        "next_action": "Rerun with --mcp-server-name.",
                    },
                    {
                        "check_id": "runtime.frontend_route_smoke",
                        "status": "fail",
                        "summary": "Browser smoke failed.",
                        "next_action": "Inspect browser logs.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    checks = {check["check_id"]: check for check in response.json()["checks"]}
    assert checks["external.final_handoff"]["status"] == "fail"
    assert checks["external.final_handoff"]["required"] is True
    assert (
        checks["external.final_handoff"]["details"]["final_handoff"]["ready"]
        is False
    )
    assert checks["external.main_chat_model_smoke"]["status"] == "pass"
    assert checks["external.graphrag_llm_model_smoke"]["status"] == "pass"
    assert checks["external.docker_compose"]["status"] == "pass"
    assert checks["external.mcp_live_smoke"]["status"] == "blocked"
    assert checks["external.browser_smoke"]["status"] == "fail"
    assert checks["external.main_chat_model_smoke"]["required"] is True
    assert checks["external.graphrag_llm_model_smoke"]["required"] is True
    assert checks["external.docker_compose"]["required"] is True
    assert checks["external.mcp_live_smoke"]["required"] is True
    assert checks["external.browser_smoke"]["required"] is True
    assert "external.mcp_live_smoke" in "\n".join(response.json()["remaining_blockers"])
    assert "external.browser_smoke" in "\n".join(response.json()["remaining_blockers"])
    assert "external.final_handoff" in "\n".join(response.json()["remaining_blockers"])
    assert (
        checks["external.browser_smoke"]["details"]["source_check"]["summary"]
        == "Browser smoke failed."
    )


def test_p0_readiness_blocks_when_acceptance_report_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(tmp_path / "missing.json"))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    body = response.json()
    checks = {check["check_id"]: check for check in body["checks"]}
    assert body["ok"] is False
    assert checks["external.main_chat_model_smoke"]["status"] == "blocked"
    assert checks["external.graphrag_llm_model_smoke"]["status"] == "blocked"
    assert checks["external.final_handoff"]["status"] == "blocked"
    assert checks["external.final_handoff"]["required"] is True
    assert checks["external.main_chat_model_smoke"]["required"] is True
    assert checks["external.graphrag_llm_model_smoke"]["required"] is True
    assert checks["external.docker_compose"]["status"] == "blocked"
    assert checks["external.docker_compose"]["required"] is True
    assert checks["external.mcp_live_smoke"]["required"] is True
    assert checks["external.browser_smoke"]["required"] is True
    assert checks["external.p0_readiness_after_report"]["status"] == "blocked"
    assert checks["external.p0_readiness_after_report"]["required"] is True
    assert any("external.docker_compose" in item for item in body["remaining_blockers"])


def test_p0_readiness_surfaces_all_final_acceptance_report_checks(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    report_path.write_text(
        json.dumps(
            _acceptance_report(
                [_acceptance_check(check_id) for check_id in FINAL_HANDOFF_CHECK_IDS]
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    checks = {check["check_id"]: check for check in response.json()["checks"]}
    assert checks["external.final_handoff"]["status"] == "pass"
    for check_id in ACCEPTANCE_EXTERNAL_CHECK_IDS:
        assert checks[check_id]["required"] is True
        assert checks[check_id]["status"] == "pass"


def test_p0_readiness_blocks_final_handoff_when_report_required_check_list_is_stale(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    stale_required_check_ids = [
        check_id
        for check_id in FINAL_HANDOFF_CHECK_IDS
        if check_id
        not in {
            "runtime.model_config.embedding_smoke",
            "runtime.frontend_browser_smoke",
        }
    ]
    report_path.write_text(
        json.dumps(
            _acceptance_report(
                [_acceptance_check(check_id) for check_id in FINAL_HANDOFF_CHECK_IDS],
                ready=True,
                required_check_ids=stale_required_check_ids,
                missing_check_ids=[],
                non_passing_checks=[],
                non_passing_executed_checks=[],
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    body = response.json()
    checks = {check["check_id"]: check for check in body["checks"]}
    final_handoff = checks["external.final_handoff"]
    assert body["ok"] is False
    assert final_handoff["status"] == "blocked"
    assert "final_handoff_declared_ready=True" in final_handoff["evidence"]
    assert "final_handoff_ready=False" in final_handoff["evidence"]
    assert "stale_required_check_count=2" in final_handoff["evidence"]
    assert final_handoff["details"]["stale_required_check_ids"] == [
        "runtime.model_config.embedding_smoke",
        "runtime.frontend_browser_smoke",
    ]
    assert "external.final_handoff" in "\n".join(body["remaining_blockers"])


def test_p0_readiness_blocks_final_handoff_when_contract_metadata_is_stale(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    payload = _acceptance_report(
        [_acceptance_check(check_id) for check_id in FINAL_HANDOFF_CHECK_IDS]
    )
    payload["final_handoff_contract_version"] = FINAL_HANDOFF_CONTRACT_VERSION - 1
    payload["final_handoff"]["contract_version"] = FINAL_HANDOFF_CONTRACT_VERSION - 1
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    body = response.json()
    checks = {check["check_id"]: check for check in body["checks"]}
    final_handoff = checks["external.final_handoff"]
    assert body["ok"] is False
    assert final_handoff["status"] == "blocked"
    assert "stale_final_handoff_contract=True" in final_handoff["evidence"]
    assert "stale_contract_count=2" in final_handoff["evidence"]
    assert final_handoff["details"]["stale_contract_reasons"] == [
        "top_contract_version",
        "final_handoff.contract_version",
    ]
    assert "external.final_handoff" in "\n".join(body["remaining_blockers"])


def test_p0_readiness_blocks_declared_ready_report_missing_readiness_self_check(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    checks_without_self = [
        _acceptance_check(check_id)
        for check_id in FINAL_HANDOFF_CHECK_IDS
        if check_id != "runtime.p0_readiness_after_report"
    ]
    report_path.write_text(
        json.dumps(
            _acceptance_report(
                checks_without_self,
                ready=True,
                missing_check_ids=[],
                non_passing_checks=[],
                non_passing_executed_checks=[],
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    body = response.json()
    checks = {check["check_id"]: check for check in body["checks"]}
    assert body["ok"] is False
    assert checks["external.p0_readiness_after_report"]["status"] == "blocked"
    assert checks["external.p0_readiness_after_report"]["required"] is True
    assert "source_check=missing" in checks["external.p0_readiness_after_report"]["evidence"]
    assert any(
        "external.p0_readiness_after_report" in item
        for item in body["remaining_blockers"]
    )


def test_p0_readiness_does_not_trust_declared_ready_with_non_passing_checks(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    checks = [
        _acceptance_check(check_id, "fail" if check_id == "runtime.mcp_live_smoke" else "pass")
        for check_id in FINAL_HANDOFF_CHECK_IDS
    ]
    non_passing = [
        check for check in checks if check["check_id"] == "runtime.mcp_live_smoke"
    ]
    report_path.write_text(
        json.dumps(
            _acceptance_report(
                checks,
                ready=True,
                missing_check_ids=[],
                non_passing_checks=non_passing,
                non_passing_executed_checks=non_passing,
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    checks_by_id = {check["check_id"]: check for check in response.json()["checks"]}
    final_handoff = checks_by_id["external.final_handoff"]
    assert final_handoff["status"] == "fail"
    assert "final_handoff_declared_ready=True" in final_handoff["evidence"]
    assert "final_handoff_ready=False" in final_handoff["evidence"]
    assert checks_by_id["external.mcp_live_smoke"]["status"] == "fail"


def test_p0_readiness_allows_only_marked_preliminary_readiness_self_check(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    checks_without_self = [
        _acceptance_check(check_id)
        for check_id in FINAL_HANDOFF_CHECK_IDS
        if check_id != "runtime.p0_readiness_after_report"
    ]
    report_path.write_text(
        json.dumps(
            _acceptance_report(
                checks_without_self,
                ready=False,
                missing_check_ids=["runtime.p0_readiness_after_report"],
                readiness_after_report_pending=True,
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    checks = {check["check_id"]: check for check in response.json()["checks"]}
    assert checks["external.final_handoff"]["status"] == "pass"
    assert checks["external.p0_readiness_after_report"]["status"] == "not_applicable"
    assert checks["external.p0_readiness_after_report"]["required"] is True
    assert "source_check=pending" in checks["external.p0_readiness_after_report"]["evidence"]


def test_p0_readiness_frontend_route_smoke_prefers_primary_id_over_legacy_alias(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-05-31T18:45:00+0800",
                "final_handoff": {
                    "ready": True,
                    "schema_version": 1,
                    "missing_flags": [],
                    "required_check_ids": [
                        "runtime.frontend_route_smoke",
                    ],
                    "missing_check_ids": [],
                    "non_passing_checks": [],
                    "non_passing_executed_checks": [],
                },
                "summary": {"pass": 2, "fail": 1, "skipped": 0, "total": 3},
                "checks": [
                    {
                        "check_id": "runtime.browser_e2e_smoke",
                        "status": "fail",
                        "summary": "Legacy browser id failed.",
                    },
                    {
                        "check_id": "runtime.frontend_route_smoke",
                        "status": "pass",
                        "summary": "Frontend route smoke passed.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    checks = {check["check_id"]: check for check in response.json()["checks"]}
    route_check = checks["external.browser_smoke"]
    assert route_check["status"] == "pass"
    assert (
        route_check["details"]["source_check"]["check_id"]
        == "runtime.frontend_route_smoke"
    )


def test_p0_readiness_frontend_route_smoke_accepts_legacy_alias(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-05-31T18:46:00+0800",
                "final_handoff": {
                    "ready": False,
                    "schema_version": 1,
                    "missing_flags": [],
                    "required_check_ids": [
                        "runtime.frontend_route_smoke",
                    ],
                    "missing_check_ids": [],
                    "non_passing_checks": [
                        {
                            "check_id": "runtime.browser_e2e_smoke",
                            "status": "fail",
                            "summary": "Legacy route smoke failed.",
                        },
                    ],
                    "non_passing_executed_checks": [
                        {
                            "check_id": "runtime.browser_e2e_smoke",
                            "status": "fail",
                            "summary": "Legacy route smoke failed.",
                        },
                    ],
                },
                "summary": {"pass": 0, "fail": 1, "skipped": 0, "total": 1},
                "checks": [
                    {
                        "check_id": "runtime.browser_e2e_smoke",
                        "status": "fail",
                        "summary": "Legacy route smoke failed.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("P0_ACCEPTANCE_REPORT_PATH", str(report_path))
    client = TestClient(app)

    response = client.get("/workspaces/default/readiness")

    assert response.status_code == 200
    checks = {check["check_id"]: check for check in response.json()["checks"]}
    route_check = checks["external.browser_smoke"]
    assert route_check["status"] == "fail"
    assert (
        route_check["details"]["source_check"]["check_id"]
        == "runtime.browser_e2e_smoke"
    )
