from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

from app.schemas.identity import RuntimeIdentity
from app.schemas.subagent import (
    SubAgentCompleteRequest,
    SubAgentReviewRequest,
    SubAgentTaskRequest,
)
from app.storage.local_object_store import LocalObjectStore

SECRET_TEXT = "sk-test-secret api_key password plaintext ciphertext nonce tag authorization cookie"
RAW_CONTEXT = "RAW_PARENT_CONTEXT_SHOULD_NOT_BE_RETURNED_TO_MODEL"
FINAL_CLAIMS = ("final_answer", "final response", "mark run final", "run_final")
REQUIRED_AGENT_TYPES = ("code_reviewer", "researcher", "log_analyst", "database_checker")


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_dump(item) for item in value)
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _require_module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Phase L must expose {name}: {exc}")


def _call_first(target: Any, names: tuple[str, ...], *args: Any, **kwargs: Any) -> Any:
    errors: list[str] = []
    for name in names:
        func = getattr(target, name, None)
        if not callable(func):
            continue
        try:
            return func(*args, **kwargs)
        except TypeError as exc:
            errors.append(f"{name}: {exc}")
            if kwargs and not args:
                try:
                    return func(kwargs)
                except TypeError as dict_exc:
                    errors.append(f"{name}(dict): {dict_exc}")
    detail = f" Signature errors: {'; '.join(errors)}" if errors else ""
    pytest.fail(f"{target!r} must expose one of: {', '.join(names)}.{detail}")


def _make_service(tmp_path: Any) -> Any:
    object_store = LocalObjectStore(tmp_path / "objects")
    for module_name, class_names in (
        ("app.subagents.service", ("SubAgentService", "SubAgentScheduler")),
        ("app.subagents.scheduler", ("SubAgentScheduler", "SubAgentService")),
    ):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        for class_name in class_names:
            service_cls = getattr(module, class_name, None)
            if service_cls is None:
                continue
            try:
                return service_cls(object_store=object_store)
            except TypeError:
                return service_cls(object_store)
    pytest.fail("Phase L must expose app.subagents service/scheduler backed by ObjectStore.")


def _task_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "default",
        "parent_run_id": "run_parent_001",
        "thread_id": "thread_001",
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


def _create_task(service: Any, **overrides: Any) -> dict[str, Any]:
    payload = _task_payload(**overrides)
    if hasattr(service, "create_task"):
        request = SubAgentTaskRequest(
            parent_run_id=payload["parent_run_id"],
            parent_thread_id=payload.get("thread_id"),
            agent_type=payload["agent_type"],
            objective=payload["objective"],
            mode=payload["mode"],
            read_scope=payload["read_scope"],
            write_scope=payload["write_scope"],
            allowed_tools=payload["allowed_tools"],
            forbidden_tools=payload["forbidden_tools"],
            timeout_ms=payload["timeout_ms"],
            token_budget=payload["token_budget"],
            expected_output=payload["expected_output"],
        )
        return _dump(service.create_task("default", _identity(), request))
    return _dump(
        _call_first(
            service,
            ("create_task", "schedule_task", "spawn_subagent", "call_subagent"),
            **payload,
        )
    )


def _complete_task(service: Any, task_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "default",
        "parent_run_id": "run_parent_001",
        "task_id": task_id,
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
        "evidence": ["unit contract"],
        "risks": [],
        "open_questions": [],
    }
    payload.update(overrides)
    if hasattr(service, "complete_task"):
        request = SubAgentCompleteRequest(
            status=payload["status"],
            summary=payload["summary"],
            findings=payload["findings"],
            changed_files=payload["changed_files"],
            risks=payload["risks"],
            open_questions=payload["open_questions"],
            created_job_id=payload.get("created_job_id"),
            error_type=payload.get("error_type"),
        )
        return _dump(service.complete_task("default", task_id, request))
    return _dump(
        _call_first(
            service,
            ("complete_task", "record_result", "complete_subagent_task", "submit_result"),
            **payload,
        )
    )


def _review_result(service: Any, task_id: str) -> dict[str, Any]:
    if hasattr(service, "review_result"):
        request = SubAgentReviewRequest(
            decision="accepted",
            reviewer_notes="Accepted as supporting evidence, not final answer.",
        )
        return _dump(service.review_result("default", task_id, _identity(), request))
    return _dump(
        _call_first(
            service,
            ("review_result", "mark_result_reviewed", "main_review_result"),
            workspace_id="default",
            parent_run_id="run_parent_001",
            task_id=task_id,
            reviewer="main_agent",
            accepted=True,
            review_notes="Accepted as supporting evidence, not final answer.",
        )
    )


def _task_id(value: dict[str, Any]) -> str:
    for key in ("task_id", "subagent_task_id", "id"):
        if value.get(key):
            return str(value[key])
    pytest.fail(f"SubAgent task must include task_id: {value}")


def _assert_model_safe(value: Any) -> None:
    serialized = json.dumps(_dump(value), ensure_ascii=False, default=str).lower()
    for term in SECRET_TEXT.split():
        assert term.lower() not in serialized
    assert RAW_CONTEXT.lower() not in serialized
    for claim in FINAL_CLAIMS:
        assert claim not in serialized


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id="default_user",
        role="owner",
        workspace_id="default",
        workspace_role="owner",
    )


def test_subagent_task_requires_full_boundary_and_budget(tmp_path) -> None:
    service = _make_service(tmp_path)

    task = _create_task(service)

    assert task["objective"]
    assert task["agent_type"] == "code_reviewer"
    assert task["mode"] == "readonly"
    assert task["read_scope"] == ["backend/app/runtime", "backend/app/tools"]
    assert task["write_scope"] == []
    assert task["allowed_tools"] == ["read_file", "search_files"]
    assert "exec" in task["forbidden_tools"]
    assert task["timeout_ms"] == 300000
    assert task["token_budget"] == 12000
    assert task.get("requires_main_review", task.get("needs_main_review")) is True
    assert task.get("output_schema", "SubAgentResult") == "SubAgentResult"


def test_readonly_allows_empty_write_scope_but_write_mode_requires_scope(tmp_path) -> None:
    service = _make_service(tmp_path)

    readonly = _create_task(service, mode="readonly", write_scope=[])
    assert readonly["write_scope"] == []

    with pytest.raises(Exception) as exc_info:
        _create_task(service, mode="write", write_scope=[])

    serialized = str(exc_info.value).lower()
    assert "write_scope" in serialized
    assert "required" in serialized or "empty" in serialized or "declare" in serialized


def test_overlapping_active_write_scopes_are_rejected_with_conflict_evidence(tmp_path) -> None:
    service = _make_service(tmp_path)
    first = _create_task(
        service,
        agent_type="code_reviewer",
        mode="write",
        write_scope=["backend/app/runtime/tools.py"],
        allowed_tools=["read_file", "apply_patch"],
        forbidden_tools=["exec"],
    )

    with pytest.raises(Exception) as exc_info:
        _create_task(
            service,
            agent_type="researcher",
            mode="write",
            write_scope=["backend/app/runtime"],
            allowed_tools=["read_file", "apply_patch"],
            forbidden_tools=["exec"],
        )

    serialized = str(exc_info.value).lower()
    details = getattr(exc_info.value, "details", {})
    evidence = json.dumps(details, ensure_ascii=False).lower()
    assert _task_id(first).lower() in evidence
    assert "write_scope" in serialized or "write_scope" in evidence
    assert "conflict" in serialized or "overlap" in serialized or "overlap" in evidence


def test_completed_result_needs_main_review_and_cannot_mark_run_final(tmp_path) -> None:
    service = _make_service(tmp_path)
    task = _create_task(service)

    result = _complete_task(
        service,
        _task_id(task),
    )

    assert result["task_id"] == _task_id(task)
    assert result["status"] == "completed"
    assert result["needs_main_review"] is True
    assert result.get("parent_run_final") is not True
    assert result.get("can_directly_finalize") is False
    assert result.get("final_answer") is None
    _assert_model_safe(result)


def test_call_subagent_tools_exist_for_required_agent_types_and_return_controlled_result(
    tmp_path,
) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    object_store.write_text("backend/app/runtime/tools.py", "runtime tool registry evidence")
    module = _require_module("app.tools.builtin.subagent_tools")
    build_tools = getattr(module, "build_default_subagent_tools", None)
    if build_tools is None:
        pytest.fail("app.tools.builtin.subagent_tools must expose build_default_subagent_tools.")
    try:
        tools = build_tools(object_store=object_store)
    except TypeError:
        tools = build_tools(object_store)
    by_name = {tool.name: tool for tool in tools}

    for agent_type in REQUIRED_AGENT_TYPES:
        tool_name = f"call_subagent_{agent_type}"
        assert tool_name in by_name
        result = _dump(
            by_name[tool_name].invoke(
                {
                    "objective": f"Run controlled {agent_type} check.",
                    "mode": "readonly",
                    "read_scope": ["backend/app"],
                    "write_scope": [],
                    "allowed_tools": ["read_file", "search_files"],
                    "forbidden_tools": ["write_file", "apply_patch", "exec"],
                    "timeout_ms": 300000,
                    "token_budget": 12000,
                    "expected_output": "Return evidence and risks only.",
                    "parent_run_id": "run_parent_001",
                    "thread_id": "thread_001",
                    "inherited_context": RAW_CONTEXT + " " + SECRET_TEXT,
                }
            )
        )
        assert result["ok"] is True
        data = result.get("data", result)
        assert data["agent_type"] == agent_type
        assert data["needs_main_review"] is True
        assert data.get("parent_run_final") is not True
        assert data["execution"]["executor"] == "langgraph_local_subagent_executor"
        assert data["evidence"]["scoped_object_key_count"] >= 1
        _assert_model_safe(result)


def test_review_moves_result_to_reviewed_collection_and_leaf_state(tmp_path) -> None:
    service = _make_service(tmp_path)
    task = _create_task(service)
    result = _complete_task(service, _task_id(task))

    reviewed = _review_result(service, _task_id(task))

    assert reviewed["task_id"] == _task_id(task)
    assert reviewed.get("review_status", reviewed.get("decision")) in {"accepted", "reviewed"}
    leaf_state = _dump(
        service.run_leaf_state("default", "run_parent_001")
        if hasattr(service, "run_leaf_state")
        else _call_first(
            service,
            ("get_run_leaf_state", "load_leaf_state", "get_leaf_state"),
            workspace_id="default",
            parent_run_id="run_parent_001",
        )
    )
    assert any(item["task_id"] == _task_id(task) for item in leaf_state["subagent_tasks"])
    assert any(item["task_id"] == _task_id(task) for item in leaf_state["subagent_results"])
    assert any(
        item["task_id"] == _task_id(task)
        for item in leaf_state["reviewed_subagent_results"]
    )
    assert result["needs_main_review"] is True
