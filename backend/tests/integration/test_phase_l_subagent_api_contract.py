from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_object_store
from app.conversation.service import ConversationService
from app.main import app
from app.runtime.runner import RuntimeRunner
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore
from app.storage.path_builder import run_manifest_key

SECRET_TEXT = "sk-test-secret api_key password plaintext ciphertext nonce tag authorization cookie"
RAW_CONTEXT = "RAW_PARENT_CONTEXT_SHOULD_NOT_BE_RETURNED_TO_MODEL"


@pytest.fixture()
def object_store(tmp_path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture()
def client(object_store: LocalObjectStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_object_store] = lambda: object_store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()


def _assert_model_safe(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, default=str).lower()
    for term in SECRET_TEXT.split():
        assert term.lower() not in serialized
    assert RAW_CONTEXT.lower() not in serialized
    assert "final_answer" not in serialized
    assert "mark run final" not in serialized


def _items(body: Any, key: str = "tasks") -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    assert isinstance(body, dict)
    for candidate in (key, "items", "results"):
        value = body.get(candidate)
        if isinstance(value, list):
            return value
    pytest.fail(f"Expected list payload under {key}/items/results: {body}")


def _task_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_type": "code_reviewer",
        "objective": "Review runtime tool policy for permission bypass risk.",
        "mode": "readonly",
        "read_scope": ["backend/app/runtime", "backend/app/tools"],
        "write_scope": [],
        "allowed_tools": ["read_file", "search_files"],
        "forbidden_tools": ["write_file", "apply_patch", "exec"],
        "timeout_ms": 300000,
        "token_budget": 12000,
        "expected_output": "Findings with evidence, risk, and recommendation.",
    }
    payload.update(overrides)
    return payload


def _result_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "completed",
        "summary": "Reviewed runtime tool policy; no direct writes performed.",
        "findings": [
            {
                "severity": "P2",
                "title": "Tool policy should keep model-visible surfaces compact.",
                "evidence": "Tool inventory exposes only enabled tools.",
                "recommendation": "Keep disabled tools out of model-safe specs.",
            }
        ],
        "changed_files": [],
        "evidence": ["integration contract"],
        "risks": [],
        "open_questions": [],
        "final_answer": "This field must be ignored by the SubAgent boundary.",
        "raw_inherited_context": RAW_CONTEXT + " " + SECRET_TEXT,
    }
    payload.update(overrides)
    return payload


def _create_task(
    client: TestClient,
    run_id: str = "run_parent_001",
    **overrides: Any,
) -> dict[str, Any]:
    response = client.post(
        f"/workspaces/default/runs/{run_id}/subagent-tasks",
        json=_task_payload(**overrides),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"]
    _assert_model_safe(body)
    return body


def test_subagent_api_create_list_detail_and_object_store_persistence(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    task = _create_task(client)

    list_response = client.get("/workspaces/default/runs/run_parent_001/subagent-tasks")
    detail_response = client.get(
        f"/workspaces/default/runs/run_parent_001/subagent-tasks/{task['task_id']}"
    )

    assert list_response.status_code == 200
    assert any(item["task_id"] == task["task_id"] for item in _items(list_response.json()))
    assert detail_response.status_code == 200
    assert detail_response.json()["task_id"] == task["task_id"]
    assert detail_response.json()["requires_main_review"] is True

    keys = object_store.list_keys("workspaces/default/runs/run_parent_001")
    assert any(task["task_id"] in key and "subagent" in key for key in keys)
    assert any(key.endswith("leaf_state.json") for key in keys)
    _assert_model_safe(list_response.json())
    _assert_model_safe(detail_response.json())


def test_subagent_api_rejects_overlapping_active_write_scopes_with_evidence(
    client: TestClient,
) -> None:
    first = _create_task(
        client,
        agent_type="code_reviewer",
        mode="write",
        write_scope=["backend/app/runtime/tools.py"],
        allowed_tools=["read_file", "apply_patch"],
        forbidden_tools=["exec"],
    )
    conflict = client.post(
        "/workspaces/default/runs/run_parent_001/subagent-tasks",
        json=_task_payload(
            agent_type="researcher",
            mode="write",
            write_scope=["backend/app/runtime"],
            allowed_tools=["read_file", "apply_patch"],
            forbidden_tools=["exec"],
        ),
    )

    assert conflict.status_code == 409
    body = conflict.json()
    assert body["error_type"] in {"subagent_write_scope_conflict", "write_scope_conflict"}
    assert first["task_id"] in json.dumps(body, ensure_ascii=False)
    assert "backend/app/runtime" in json.dumps(body, ensure_ascii=False)
    _assert_model_safe(body)


def test_subagent_api_complete_and_review_updates_leaf_state(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    JsonObjectStore(object_store).write_json(
        run_manifest_key("default", "run_parent_001"),
        {
            "schema_version": 1,
            "workspace_id": "default",
            "thread_id": "thread_parent_001",
            "run_id": "run_parent_001",
            "status": "running",
            "last_event_id": None,
            "last_event_seq": 0,
            "created_at": "2026-05-31T00:00:00+00:00",
            "updated_at": "2026-05-31T00:00:00+00:00",
            "revision": 1,
        },
    )
    task = _create_task(client)

    complete_response = client.post(
        f"/workspaces/default/runs/run_parent_001/subagent-tasks/{task['task_id']}/complete",
        json=_result_payload(),
    )
    assert complete_response.status_code == 200
    result = complete_response.json()
    assert result["task_id"] == task["task_id"]
    assert result["status"] == "completed"
    assert result["needs_main_review"] is True
    assert result.get("parent_run_final") is not True
    _assert_model_safe(result)

    review_response = client.post(
        f"/workspaces/default/runs/run_parent_001/subagent-results/{task['task_id']}/review",
        json={
            "accepted": True,
            "reviewer": "main_agent",
            "review_notes": "Use as supporting evidence only.",
        },
    )
    assert review_response.status_code == 200
    reviewed = review_response.json()
    assert reviewed["task_id"] == task["task_id"]
    assert reviewed["review_status"] in {"accepted", "reviewed"}
    _assert_model_safe(reviewed)

    leaf_key = "workspaces/default/runs/run_parent_001/leaf_state.json"
    leaf_state = json.loads(object_store.read_text(leaf_key))
    assert any(item["task_id"] == task["task_id"] for item in leaf_state["subagent_tasks"])
    assert any(item["task_id"] == task["task_id"] for item in leaf_state["subagent_results"])
    assert any(
        item["task_id"] == task["task_id"]
        for item in leaf_state["reviewed_subagent_results"]
    )
    events, _ = ConversationService(
        object_store,
        RuntimeRunner(object_store=object_store),
    ).list_run_events("default", "run_parent_001")
    assert any(
        event["type"] == "subagent_result_reviewed"
        and event["payload"]["task_id"] == task["task_id"]
        for event in events
    )
