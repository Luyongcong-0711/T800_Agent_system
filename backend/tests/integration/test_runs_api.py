from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("app.api.runs", reason="Runs API implementation has not landed yet.")
pytest.importorskip(
    "langchain_core.messages",
    reason="LangChain runtime dependencies are not installed.",
)
pytest.importorskip("langgraph.graph", reason="LangGraph runtime dependencies are not installed.")

from app.main import app


def test_runs_smoke_endpoint_returns_success_with_default_identity() -> None:
    client = TestClient(app)

    response = client.post(
        "/workspaces/default/runs/smoke",
        json={"user_message": "Run the offline runtime smoke test."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"]
    assert body["thread_id"]
    assert body["workspace_id"] == "default"
    assert body["status"] == "completed"
    assert body["requires_approval"] is False
    assert body["context_usage"]["usage_estimated"] is True
    assert body["messages"]
    assert body["tool_results"]
    assert all(result["ok"] is True for result in body["tool_results"])
    assert {result["name"] for result in body["tool_results"]} == {"echo_runtime_context"}
    assert body["tool_specs"]
    serialized = json.dumps(body, ensure_ascii=False).lower()
    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "plaintext" not in serialized
    assert "ciphertext" not in serialized
    assert "nonce" not in serialized
    assert "tag" not in serialized
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
