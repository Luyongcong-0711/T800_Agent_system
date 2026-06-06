from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

from scripts import p0_acceptance


class _JsonResponse:
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_http_check_fails_when_expected_boolean_values_do_not_match(monkeypatch) -> None:
    monkeypatch.setattr(
        p0_acceptance,
        "urlopen",
        lambda request, timeout: _JsonResponse(
            {
                "server_name": "filesystem",
                "status": "configured",
                "transport": "stdio",
                "runtime_configured": False,
                "connected": False,
                "tool_count": 0,
            }
        ),
    )

    result = p0_acceptance.http_check(
        "runtime.mcp_live_smoke",
        "http://localhost:8000/workspaces/default/mcp/servers/filesystem/health",
        1,
        required_keys=("server_name", "status", "transport", "runtime_configured"),
        expected_values={"runtime_configured": True, "connected": True},
        minimum_values={"tool_count": 1},
    )

    assert result.status == p0_acceptance.FAIL
    assert "runtime_configured=False" in result.summary


def test_http_check_fails_when_numeric_values_are_below_minimum(monkeypatch) -> None:
    monkeypatch.setattr(
        p0_acceptance,
        "urlopen",
        lambda request, timeout: _JsonResponse(
            {
                "server_name": "filesystem",
                "status": "connected",
                "transport": "stdio",
                "runtime_configured": True,
                "connected": True,
                "tool_count": 0,
            }
        ),
    )

    result = p0_acceptance.http_check(
        "runtime.mcp_live_smoke",
        "http://localhost:8000/workspaces/default/mcp/servers/filesystem/health",
        1,
        required_keys=("server_name", "status", "transport", "runtime_configured"),
        expected_values={"runtime_configured": True, "connected": True},
        minimum_values={"tool_count": 1},
    )

    assert result.status == p0_acceptance.FAIL
    assert "tool_count=0 < 1" in result.summary


def test_browser_smoke_marker_detection_requires_route_content() -> None:
    rendered_dom = """
    <html>
      <body>
        <h1>Agent System</h1>
        <main>P0 readiness</main>
      </body>
    </html>
    """

    assert p0_acceptance.missing_render_markers(
        rendered_dom,
        "/readiness",
        "P0 Readiness",
    ) == []
    assert p0_acceptance.missing_render_markers(
        rendered_dom,
        "/settings?tab=secrets",
        "Settings Secrets",
    ) == ["Settings", "Secrets"]


def test_backend_python_does_not_fallback_to_backend_venv(monkeypatch, tmp_path) -> None:
    root = tmp_path / "agent-system"
    fake_venv_python = root / "backend" / ".venv" / "Scripts" / "python.exe"
    fake_venv_python.parent.mkdir(parents=True)
    fake_venv_python.write_text("", encoding="utf-8")

    monkeypatch.delenv("AGENT_BACKEND_PYTHON", raising=False)
    monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    monkeypatch.setattr(p0_acceptance.platform, "system", lambda: "Windows")
    monkeypatch.setattr(p0_acceptance.Path, "home", lambda: tmp_path / "home")

    assert p0_acceptance.backend_python(root) == sys.executable


def test_backend_python_prefers_configured_py313(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "miniconda3" / "envs" / "py313" / "python.exe"
    configured.parent.mkdir(parents=True)
    configured.write_text("", encoding="utf-8")

    monkeypatch.setenv("AGENT_BACKEND_PYTHON", str(configured))

    assert p0_acceptance.backend_python(tmp_path / "agent-system") == str(configured)


def test_final_handoff_summary_marks_only_preliminary_readiness_self_check_pending() -> None:
    results = [
        p0_acceptance.CheckResult(
            check_id,
            p0_acceptance.PASS,
            "ok",
        )
        for check_id in p0_acceptance.FINAL_HANDOFF_CHECK_IDS
        if check_id != "runtime.p0_readiness_after_report"
    ]
    flags = list(p0_acceptance.FINAL_HANDOFF_REQUIRED_FLAGS)

    preliminary = p0_acceptance.final_handoff_summary(
        results,
        flags,
        readiness_after_report_pending=True,
    )
    final = p0_acceptance.final_handoff_summary(results, flags)

    assert preliminary["ready"] is False
    assert preliminary["readiness_after_report_pending"] is True
    assert preliminary["missing_check_ids"] == ["runtime.p0_readiness_after_report"]
    assert final["ready"] is False
    assert final["readiness_after_report_pending"] is False
    assert final["missing_check_ids"] == ["runtime.p0_readiness_after_report"]


def test_main_writes_preliminary_report_without_provisional_readiness_pass(
    monkeypatch,
    tmp_path,
) -> None:
    report_path = tmp_path / "p0_acceptance_report.json"
    required_flags = Namespace(
        backend_url="http://localhost:8000",
        frontend_url="http://localhost:3000",
        include_docker=True,
        include_model_smoke=True,
        include_p0_contracts=True,
        include_root_e2e=True,
        include_runtime_http=True,
        list_final_checks=False,
        mcp_server_name="filesystem",
        report_path=str(report_path),
        require_final_handoff=True,
        timeout_seconds=1,
        workspace_id="default",
    )
    monkeypatch.setattr(p0_acceptance, "parse_args", lambda: required_flags)
    monkeypatch.setattr(p0_acceptance, "project_root", lambda: Path.cwd())
    monkeypatch.setattr(
        p0_acceptance,
        "code_checks",
        lambda root, timeout: [
            p0_acceptance.CheckResult(
                "code.backend_python_env",
                p0_acceptance.PASS,
                "py313 ok",
            )
        ],
    )
    monkeypatch.setattr(
        p0_acceptance,
        "root_e2e_checks",
        lambda root, timeout: [
            p0_acceptance.CheckResult("code.root_e2e_contracts", p0_acceptance.PASS, "ok")
        ],
    )
    monkeypatch.setattr(
        p0_acceptance,
        "p0_contract_checks",
        lambda root, timeout: [
            p0_acceptance.CheckResult("code.backend_p0_contracts", p0_acceptance.PASS, "ok"),
            p0_acceptance.CheckResult("code.frontend_p0_contracts", p0_acceptance.PASS, "ok"),
        ],
    )
    monkeypatch.setattr(
        p0_acceptance,
        "runtime_http_checks",
        lambda *args, **kwargs: [
            p0_acceptance.CheckResult("runtime.database_live_health", p0_acceptance.PASS, "ok"),
            p0_acceptance.CheckResult("runtime.job_worker_status", p0_acceptance.PASS, "ok"),
            p0_acceptance.CheckResult("runtime.mcp_live_smoke", p0_acceptance.PASS, "ok"),
            p0_acceptance.CheckResult("runtime.frontend_route_smoke", p0_acceptance.PASS, "ok"),
            p0_acceptance.CheckResult("runtime.frontend_browser_smoke", p0_acceptance.PASS, "ok"),
        ],
    )
    monkeypatch.setattr(
        p0_acceptance,
        "runtime_model_smoke_checks",
        lambda *args, **kwargs: [
            p0_acceptance.CheckResult(
                "runtime.model_config.main_chat_smoke",
                p0_acceptance.PASS,
                "ok",
            ),
            p0_acceptance.CheckResult(
                "runtime.model_config.graphrag_llm_smoke",
                p0_acceptance.PASS,
                "ok",
            ),
            p0_acceptance.CheckResult(
                "runtime.model_config.embedding_smoke",
                p0_acceptance.PASS,
                "ok",
            ),
        ],
    )
    monkeypatch.setattr(
        p0_acceptance,
        "docker_checks",
        lambda root, timeout: [
            p0_acceptance.CheckResult("runtime.docker_compose_ps", p0_acceptance.PASS, "ok")
        ],
    )

    def fake_readiness_after_report_check(
        *args: object,
        **kwargs: object,
    ) -> p0_acceptance.CheckResult:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["final_handoff"]["ready"] is False
        assert payload["final_handoff"]["readiness_after_report_pending"] is True
        assert all(
            check["check_id"] != "runtime.p0_readiness_after_report"
            for check in payload["checks"]
        )
        assert "runtime.p0_readiness_after_report" in payload["final_handoff"][
            "required_check_ids"
        ]
        assert payload["final_handoff"]["missing_check_ids"] == [
            "runtime.p0_readiness_after_report",
        ]
        return p0_acceptance.CheckResult(
            "runtime.p0_readiness_after_report",
            p0_acceptance.PASS,
            "readiness ok",
        )

    monkeypatch.setattr(
        p0_acceptance,
        "readiness_after_report_check",
        fake_readiness_after_report_check,
    )

    assert p0_acceptance.main() == 0
    final_payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert final_payload["final_handoff"]["ready"] is True
    assert final_payload["final_handoff"]["readiness_after_report_pending"] is False
