from __future__ import annotations

import importlib
import json
from typing import Any

import pytest

from app.core.errors import AgentSystemError
from app.schemas.identity import RuntimeIdentity
from app.schemas.skill import (
    SkillActivateRequest,
    SkillCreateFromProposalRequest,
    SkillEntrypointSpec,
    SkillPermissions,
    SkillProposalRequest,
    SkillSource,
    SkillValidateRequest,
)
from app.storage.local_object_store import LocalObjectStore
from app.storage.object_store import JsonObjectStore
from app.storage.path_builder import (
    run_manifest_key,
    thread_manifest_key,
    workspace_file_object_key,
)

RAW_SCRIPT = "RAW_CONTRACT_SKILL_SCRIPT_CONTENT_SHOULD_NOT_BE_MODEL_VISIBLE"
FORBIDDEN_TERMS = (
    RAW_SCRIPT,
    "sk-test-secret",
    "api_key",
    "password",
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "authorization",
    "cookie",
)


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
        pytest.fail(f"Phase K must expose {name}: {exc}")


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


def _make_skill_service(tmp_path: Any) -> Any:
    object_store = LocalObjectStore(tmp_path / "objects")
    for module_name, class_names in (
        ("app.skills.service", ("SkillService",)),
        ("app.skills.registry", ("SkillRegistry", "SkillRegistryService")),
        ("app.skills.authoring", ("SkillAuthoringService",)),
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
    pytest.fail("Phase K must expose app.skills service/registry backed by ObjectStore.")


def _items(value: Any, key: str = "skills") -> list[dict[str, Any]]:
    dumped = _dump(value)
    if isinstance(dumped, list):
        return dumped
    assert isinstance(dumped, dict)
    for candidate in (key, "items", "results", "proposals"):
        items = dumped.get(candidate)
        if isinstance(items, list):
            return items
    pytest.fail(f"Expected list payload under {key}/items/results: {dumped}")


def _proposal_id(value: Any) -> str:
    dumped = _dump(value)
    for key in ("proposal_id", "skill_proposal_id", "id"):
        if isinstance(dumped, dict) and dumped.get(key):
            return str(dumped[key])
    data = dumped.get("data") if isinstance(dumped, dict) else None
    if isinstance(data, dict) and data.get("proposal_id"):
        return str(data["proposal_id"])
    pytest.fail(f"Proposal result must include proposal_id: {dumped}")


def _skill_id(value: Any) -> str:
    dumped = _dump(value)
    for key in ("skill_id", "id"):
        if isinstance(dumped, dict) and dumped.get(key):
            return str(dumped[key])
    data = dumped.get("data") if isinstance(dumped, dict) else None
    if isinstance(data, dict) and data.get("skill_id"):
        return str(data["skill_id"])
    pytest.fail(f"Skill result must include skill_id: {dumped}")


def _approval_id(value: Any) -> str:
    dumped = _dump(value)
    for key in ("approval_id", "approval_request_id"):
        if isinstance(dumped, dict) and dumped.get(key):
            return str(dumped[key])
    data = dumped.get("data") if isinstance(dumped, dict) else None
    if isinstance(data, dict) and data.get("approval_id"):
        return str(data["approval_id"])
    pytest.fail(f"Proposal result must include approval_id: {dumped}")


def _assert_no_forbidden_material(value: Any) -> None:
    serialized = json.dumps(_dump(value), ensure_ascii=False, default=str).lower()
    for term in FORBIDDEN_TERMS:
        assert term.lower() not in serialized


def _base_proposal(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": "default",
        "thread_id": "thread_001",
        "run_id": "run_001",
        "proposed_by": "model",
        "display_name": "Contract cleanup workflow",
        "description": "Normalize contract text and extract reusable metadata.",
        "when_to_use": ["Uploaded contracts need cleanup", "Contract sections are inconsistent"],
        "workflow_steps": [
            "Read parsed document representation.",
            "Normalize headings and clause numbering.",
            "Extract parties, dates, amounts, and obligations.",
            "Return cleaned blocks and metadata.",
        ],
        "knowledge_notes": ["Party aliases may include buyer, supplier, Party A, and Party B."],
        "entrypoints": [
            {
                "name": "normalize_contract",
                "type": "prompt_workflow",
                "args_schema": {
                    "type": "object",
                    "required": ["document_id"],
                    "properties": {"document_id": {"type": "string"}},
                },
                "risk_level": "low",
                "script_required": False,
            }
        ],
        "permissions": {
            "file_read": ["workspace"],
            "file_write": [],
            "database_read": ["minio"],
            "database_write": [],
            "network": False,
        },
        "source": {"thread_id": "thread_001", "message_ids": ["msg_010", "msg_018"]},
    }
    payload.update(overrides)
    return payload


def _script_proposal(**overrides: Any) -> dict[str, Any]:
    payload = _base_proposal(
        display_name="Scripted contract cleanup",
        script_required=True,
        entrypoints=[
            {
                "name": "normalize_contract",
                "type": "script",
                "runtime": "python",
                "args_schema": {
                    "type": "object",
                    "required": ["document_id"],
                    "properties": {"document_id": {"type": "string"}},
                },
                "risk_level": "medium",
                "script_required": True,
                "write_mode": "none",
                "script_content": RAW_SCRIPT,
            }
        ],
    )
    payload.update(overrides)
    return payload


def _script_proposal_with_script(
    script_content: str,
    *,
    file_write: list[str] | None = None,
    write_mode: str = "none",
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    return _script_proposal(
        entrypoints=[
            {
                "name": "normalize_contract",
                "type": "script",
                "runtime": "python",
                "args_schema": {
                    "type": "object",
                    "required": ["document_id"],
                    "properties": {"document_id": {"type": "string"}},
                },
                "risk_level": "medium",
                "script_required": True,
                "write_mode": write_mode,
                "file_write": file_write or [],
                "timeout_ms": timeout_ms,
                "script_content": script_content,
            }
        ],
    )


def _identity() -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id="default_user",
        role="owner",
        workspace_id="default",
        workspace_role="owner",
    )


def _proposal_request(payload: dict[str, Any]) -> SkillProposalRequest:
    return SkillProposalRequest(
        display_name=payload["display_name"],
        description=payload["description"],
        when_to_use=payload.get("when_to_use", []),
        workflow_steps=payload["workflow_steps"],
        knowledge_notes=payload.get("knowledge_notes", []),
        entrypoints=[SkillEntrypointSpec(**item) for item in payload.get("entrypoints", [])],
        permissions=SkillPermissions(**payload.get("permissions", {})),
        script_required=bool(payload.get("script_required", False)),
        source=SkillSource(**payload.get("source", {})),
    )


def _list_skills(service: Any) -> list[dict[str, Any]]:
    return _items(
        _call_first(
            service,
            ("list_skills", "list", "get_skill_index", "search"),
            workspace_id="default",
        )
    )


def _propose(service: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    current_payload = payload or _base_proposal()
    if hasattr(service, "create_proposal"):
        return _dump(
            service.create_proposal(
                "default",
                _identity(),
                _proposal_request(current_payload),
            )
        )
    return _dump(
        _call_first(
            service,
            ("skill_propose", "create_proposal", "propose_skill", "propose"),
            **current_payload,
        )
    )


def _approve(service: Any, proposal_id: str, approval_id: str) -> dict[str, Any]:
    # Some P0 implementations model approval outside the Skill service. In that case the
    # proposal approval_id itself is the approved capability token used by create-from-proposal.
    if not any(
        callable(getattr(service, name, None))
        for name in ("approve_proposal", "mark_proposal_approved", "approve_skill_proposal")
    ):
        return {
            "proposal_id": proposal_id,
            "approval_id": approval_id,
            "status": "approved",
        }
    return _dump(
        _call_first(
            service,
            ("approve_proposal", "mark_proposal_approved", "approve_skill_proposal"),
            workspace_id="default",
            proposal_id=proposal_id,
            approval_id=approval_id,
            approved_by="default_user",
        )
    )


def _create_from_proposal(
    service: Any,
    proposal_id: str,
    approval_id: str,
    *,
    skill_id: str = "contract_cleaner",
) -> dict[str, Any]:
    if hasattr(service, "materialize_proposal"):
        request = SkillCreateFromProposalRequest(
            proposal_id=proposal_id,
            approval_id=approval_id,
            skill_id=skill_id,
            version="0.1.0",
        )
        return _dump(service.materialize_proposal("default", _identity(), request))
    return _dump(
        _call_first(
            service,
            (
                "skill_create_from_proposal",
                "create_from_proposal",
                "materialize_proposal",
                "create_skill_from_proposal",
            ),
            workspace_id="default",
            proposal_id=proposal_id,
            approval_id=approval_id,
            skill_id=skill_id,
            version="0.1.0",
        )
    )


def _create_skill(service: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    proposal = _propose(service, payload)
    _approve(service, _proposal_id(proposal), _approval_id(proposal))
    return _create_from_proposal(service, _proposal_id(proposal), _approval_id(proposal))


def _search(service: Any, query: str = "contract cleanup") -> list[dict[str, Any]]:
    return _items(
        _call_first(
            service,
            ("skill_search", "search", "search_skills"),
            workspace_id="default",
            query=query,
            top_k=5,
        ),
        key="items",
    )


def _view(service: Any, skill_id: str) -> dict[str, Any]:
    if hasattr(service, "view_skill"):
        return _dump(service.view_skill("default", skill_id, None))
    return _dump(
        _call_first(
            service,
            ("skill_view", "view", "view_compact", "get_skill_detail"),
            workspace_id="default",
            skill_id=skill_id,
            version=None,
        )
    )


def _activate(service: Any, skill_id: str) -> dict[str, Any]:
    _write_runtime_context(service)
    if hasattr(service, "activate_skill"):
        request = SkillActivateRequest(
            run_id="run_001",
            thread_id="thread_001",
            reason="Contract text needs reusable cleanup workflow.",
        )
        return _dump(service.activate_skill("default", skill_id, request))
    return _dump(
        _call_first(
            service,
            ("skill_activate", "activate", "activate_skill"),
            workspace_id="default",
            run_id="run_001",
            thread_id="thread_001",
            skill_id=skill_id,
            version=None,
            reason="Contract text needs reusable cleanup workflow.",
        )
    )


def _write_runtime_context(
    service: Any,
    *,
    run_id: str = "run_001",
    thread_id: str = "thread_001",
    status: str = "running",
) -> None:
    store = JsonObjectStore(service.object_store)
    now = "2026-05-31T00:00:00.000Z"
    store.write_json(
        thread_manifest_key("default", thread_id),
        {
            "workspace_id": "default",
            "thread_id": thread_id,
            "user_id": "default_user",
            "title": "Contract cleanup",
            "status": "active",
            "current_run_id": run_id,
            "current_run_status": status,
            "created_at": now,
            "updated_at": now,
        },
    )
    store.write_json(
        run_manifest_key("default", run_id),
        {
            "workspace_id": "default",
            "thread_id": thread_id,
            "run_id": run_id,
            "status": status,
            "idempotency_key": "test-run",
            "created_at": now,
            "updated_at": now,
        },
    )


def test_initial_skill_index_can_be_empty(tmp_path) -> None:
    service = _make_skill_service(tmp_path)

    skills = _list_skills(service)

    assert skills == []


def test_skill_propose_requires_approval_and_does_not_create_active_skill(tmp_path) -> None:
    service = _make_skill_service(tmp_path)

    proposal = _propose(service)

    assert _proposal_id(proposal)
    assert _approval_id(proposal)
    assert (proposal.get("requires_approval") or proposal.get("approval_required")) is True
    assert proposal.get("skill_id") is None
    assert _list_skills(service) == []
    _assert_no_forbidden_material(proposal)


def test_skill_create_from_approved_proposal_writes_registry_paths(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    proposal = _propose(service)
    _approve(service, _proposal_id(proposal), _approval_id(proposal))

    skill = _create_from_proposal(service, _proposal_id(proposal), _approval_id(proposal))

    skill_id = _skill_id(skill)
    object_store = service.object_store
    keys = set(object_store.list_keys("skills/default"))
    assert f"skills/default/{skill_id}/0.1.0/skill.yaml" in keys
    assert f"skills/default/{skill_id}/0.1.0/README.md" in keys
    assert f"skills/default/{skill_id}/0.1.0/workflows/workflow.yaml" in keys
    assert f"skills/default/{skill_id}/latest.json" in keys
    assert "skills/default/skill_index.json" in keys
    assert any(item["skill_id"] == skill_id for item in _list_skills(service))


def test_script_skill_defaults_disabled_until_validation_checksum_and_sandbox_exist(
    tmp_path,
) -> None:
    service = _make_skill_service(tmp_path)

    skill = _create_skill(service, _script_proposal())

    assert skill["status"] == "disabled"
    assert skill["requires_validation"] is True
    assert skill["validation_status"] == "pending_validation"
    entrypoint = skill["entrypoints"][0]
    assert entrypoint["script_checksum"].startswith("sha256:")
    assert entrypoint.get("sandbox_profile") in {None, "skill_script_readonly"}


def test_script_skill_cannot_activate_until_validated(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(
        service,
        _script_proposal_with_script(
            "def main(args):\n    return {'document_id': args['document_id']}\n"
        ),
    )

    _write_runtime_context(service)
    with pytest.raises(AgentSystemError) as exc:
        service.activate_skill(
            "default",
            _skill_id(skill),
            SkillActivateRequest(
                run_id="run_001",
                thread_id="thread_001",
                reason="Script should stay disabled before validation.",
            ),
        )

    assert exc.value.error_type == "skill_disabled"


def test_validated_script_entrypoint_executes_and_logs_artifacts(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(
        service,
        _script_proposal_with_script(
            """
def main(args):
    print("normalized " + args["document_id"])
    return {"document_id": args["document_id"], "note": args["note"]}
"""
        ),
    )

    validated = service.validate_skill_scripts(
        "default",
        _skill_id(skill),
        SkillValidateRequest(version="0.1.0"),
    )
    activation = _activate(service, _skill_id(skill))
    result = service.execute_activated_entrypoint(
        workspace_id="default",
        run_id="run_001",
        thread_id="thread_001",
        entrypoint_tool_name=activation["activated_entrypoint_tools"][0],
        args={"document_id": "doc_001", "note": "sk-test-secret"},
        tool_call_id="call_script",
    )

    assert validated["status"] == "enabled"
    assert validated["validation_status"] == "validated"
    assert result["ok"] is True
    assert result["error_type"] is None
    assert result["data"]["result"]["document_id"] == "doc_001"
    assert result["data"]["result"]["note"] == "sk-***"
    artifacts = result["artifacts"]
    for key in (
        "manifest_object_key",
        "args_object_key",
        "stdout_object_key",
        "stderr_object_key",
        "result_object_key",
    ):
        assert service.object_store.exists(artifacts[key])
    manifest = json.loads(service.object_store.read_text(artifacts["manifest_object_key"]))
    assert manifest["status"] == "completed"
    assert manifest["entrypoint_type"] == "script"
    assert manifest["runtime"] == "python"
    assert manifest["sandbox_profile"] == "skill_script_readonly"
    assert manifest["script_checksum"].startswith("sha256:")
    assert manifest["write_mode"] == "none"
    stdout = service.object_store.read_text(artifacts["stdout_object_key"])
    assert "normalized doc_001" in stdout
    _assert_no_forbidden_material(result)


def test_script_validation_rejects_forbidden_import_and_keeps_skill_disabled(
    tmp_path,
) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(
        service,
        _script_proposal_with_script("import os\n\ndef main(args):\n    return os.environ\n"),
    )

    with pytest.raises(AgentSystemError) as exc:
        service.validate_skill_scripts(
            "default",
            _skill_id(skill),
            SkillValidateRequest(version="0.1.0"),
        )

    assert exc.value.error_type == "skill_script_forbidden_import"
    detail = _view(service, _skill_id(skill))
    assert detail["status"] == "disabled"
    assert detail["validation_status"] == "failed"


def test_script_checksum_mismatch_returns_failed_tool_result(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(
        service,
        _script_proposal_with_script(
            "def main(args):\n    return {'document_id': args['document_id']}\n"
        ),
    )
    service.validate_skill_scripts(
        "default",
        _skill_id(skill),
        SkillValidateRequest(version="0.1.0"),
    )
    manifest = service.get_manifest("default", _skill_id(skill), "0.1.0")
    script_key = manifest["entrypoints"][0]["script_object_key"]
    service.object_store.write_text(script_key, "def main(args):\n    return {'tampered': True}\n")
    activation = _activate(service, _skill_id(skill))

    result = service.execute_activated_entrypoint(
        workspace_id="default",
        run_id="run_001",
        thread_id="thread_001",
        entrypoint_tool_name=activation["activated_entrypoint_tools"][0],
        args={"document_id": "doc_001"},
        tool_call_id="call_script",
    )

    assert result["ok"] is False
    assert result["error_type"] == "skill_script_checksum_mismatch"
    artifacts = result["artifacts"]
    manifest = json.loads(service.object_store.read_text(artifacts["manifest_object_key"]))
    assert manifest["status"] == "failed"
    assert service.object_store.exists(artifacts["stdout_object_key"])
    assert service.object_store.exists(artifacts["stderr_object_key"])


def test_staged_patch_script_entrypoint_requires_approval_artifacts_without_execution(
    tmp_path,
    monkeypatch,
) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(
        service,
        _script_proposal_with_script(
            "def main(args):\n    print('should not execute')\n    return {'executed': True}\n",
            file_write=["workspace/reports/**"],
            write_mode="staged_patch",
        ),
    )
    service.validate_skill_scripts(
        "default",
        _skill_id(skill),
        SkillValidateRequest(version="0.1.0"),
    )
    activation = _activate(service, _skill_id(skill))

    def fail_if_runner_called(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("staged_patch must not execute the script before approval")

    monkeypatch.setattr(service.script_runner, "run_readonly", fail_if_runner_called)

    result = service.execute_activated_entrypoint(
        workspace_id="default",
        run_id="run_001",
        thread_id="thread_001",
        entrypoint_tool_name=activation["activated_entrypoint_tools"][0],
        args={"document_id": "doc_001"},
        tool_call_id="call_script",
    )

    assert result["ok"] is False
    assert result["error_type"] == "approval_required"
    assert result["data"]["approval_id"].startswith("approval_")
    assert result["data"]["diff_summary"]["files_changed"] == 0
    assert result["data"]["operation_plan_status"] == "waiting_approval"
    assert result["data"]["write_mode"] == "staged_patch"
    assert "stdout_preview" not in result["data"]
    assert "stderr_preview" not in result["data"]
    artifacts = result["artifacts"]
    assert artifacts["diff_object_key"].endswith("/diff.patch")
    assert artifacts["operation_plan_object_key"].endswith("/operation_plan.json")
    assert service.object_store.exists(artifacts["diff_object_key"])
    assert service.object_store.exists(artifacts["operation_plan_object_key"])
    operation_plan = json.loads(
        service.object_store.read_text(artifacts["operation_plan_object_key"])
    )
    assert operation_plan["approval_id"] == result["data"]["approval_id"]
    assert operation_plan["approval_kind"] == "skill_script_staged_patch"
    assert operation_plan["approval_ready"] is True
    assert operation_plan["changed_files"] == []
    assert operation_plan["diff_summary"]["files_changed"] == 0
    assert operation_plan["entrypoint"] == "normalize_contract"
    assert operation_plan["entrypoint_tool_name"] == activation["activated_entrypoint_tools"][0]
    assert operation_plan["overlay_diff_status"] == "not_generated"
    assert operation_plan["placeholder"] is True
    assert operation_plan["script_execution"] == "not_executed"
    assert operation_plan["skill_id"] == _skill_id(skill)
    assert operation_plan["skill_run_id"] == result["skill_run_id"]
    assert operation_plan["skill_version"] == "0.1.0"
    assert operation_plan["status"] == "waiting_approval"
    assert operation_plan["write_mode"] == "staged_patch"
    assert operation_plan["write_scope"]["accepted"] == ["workspace/reports/**"]
    assert operation_plan["write_scope"]["rejected"] == []
    diff_text = service.object_store.read_text(artifacts["diff_object_key"])
    assert "diff_generated: false" in diff_text
    assert "should not execute" not in diff_text
    assert "executed" not in diff_text
    manifest = json.loads(service.object_store.read_text(artifacts["manifest_object_key"]))
    assert manifest["status"] == "waiting_approval"
    assert manifest["artifacts"]["diff_object_key"] == artifacts["diff_object_key"]
    assert (
        manifest["artifacts"]["operation_plan_object_key"]
        == artifacts["operation_plan_object_key"]
    )
    assert not service.object_store.exists(artifacts["stdout_object_key"])
    assert not service.object_store.exists(artifacts["stderr_object_key"])
    events_text = service.object_store.read_text(
        "workspaces/default/runs/run_001/events/part-000001.jsonl"
    )
    assert '"type":"skill_entrypoint_approval_required"' in events_text
    assert artifacts["diff_object_key"] in events_text
    assert artifacts["operation_plan_object_key"] in events_text
    assert "should not execute" not in events_text
    assert "executed" not in json.dumps(result, ensure_ascii=False)


def test_staged_patch_rejects_unsafe_write_scope_and_redacts_args(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(
        service,
        _script_proposal_with_script(
            "def main(args):\n    return {'executed': True}\n",
            file_write=["../../.env", "/etc/passwd", "C:\\Users\\x\\.env", "\\\\server\\share"],
            write_mode="staged_patch",
        ),
    )
    service.validate_skill_scripts(
        "default",
        _skill_id(skill),
        SkillValidateRequest(version="0.1.0"),
    )
    activation = _activate(service, _skill_id(skill))

    result = service.execute_activated_entrypoint(
        workspace_id="default",
        run_id="run_001",
        thread_id="thread_001",
        entrypoint_tool_name=activation["activated_entrypoint_tools"][0],
        args={
            "approved": True,
            "approval_id": "approval_fake",
            "note": "Authorization: Bearer sk-test-secret",
            "write_mode": "none",
        },
        tool_call_id="call_script",
    )

    assert result["ok"] is False
    assert result["error_type"] == "approval_required"
    artifacts = result["artifacts"]
    operation_plan = json.loads(
        service.object_store.read_text(artifacts["operation_plan_object_key"])
    )
    assert operation_plan["approval_ready"] is False
    assert operation_plan["changed_files"] == []
    assert operation_plan["write_scope"]["accepted"] == []
    assert {item["reason"] for item in operation_plan["write_scope"]["rejected"]} == {
        "absolute_path",
        "path_traversal",
        "windows_drive_path",
    }
    serialized = json.dumps(
        {
            "diff": service.object_store.read_text(artifacts["diff_object_key"]),
            "events": service.object_store.read_text(
                "workspaces/default/runs/run_001/events/part-000001.jsonl"
            ),
            "operation_plan": operation_plan,
            "result": result,
        },
        ensure_ascii=False,
    )
    assert "sk-test-secret" not in serialized
    assert "Authorization: Bearer ***" not in serialized
    assert "approval_fake" not in serialized


def test_staged_patch_approval_artifacts_return_through_entrypoint_tool(tmp_path) -> None:
    from app.tools.builtin.skill_tools import build_skill_entrypoint_call_tool

    service = _make_skill_service(tmp_path)
    skill = _create_skill(
        service,
        _script_proposal_with_script(
            "def main(args):\n    return {'executed': True}\n",
            file_write=["workspace/reports/**"],
            write_mode="staged_patch",
        ),
    )
    service.validate_skill_scripts(
        "default",
        _skill_id(skill),
        SkillValidateRequest(version="0.1.0"),
    )
    activation = _activate(service, _skill_id(skill))
    tool = build_skill_entrypoint_call_tool(skill_service=service)

    result = tool.invoke(
        {
            "args": {"document_id": "doc_001"},
            "entrypoint_tool_name": activation["activated_entrypoint_tools"][0],
            "run_id": "run_001",
            "thread_id": "thread_001",
            "tool_call_id": "call_script",
            "user_id": "default_user",
            "workspace_id": "default",
        }
    )

    assert result["ok"] is False
    assert result["error_type"] == "approval_required"
    artifacts = result["artifacts"]
    assert service.object_store.exists(artifacts["diff_object_key"])
    assert service.object_store.exists(artifacts["operation_plan_object_key"])
    manifest = json.loads(service.object_store.read_text(artifacts["manifest_object_key"]))
    assert manifest["status"] == "waiting_approval"
    assert not service.object_store.exists(artifacts["stdout_object_key"])
    assert not service.object_store.exists(artifacts["stderr_object_key"])


def test_staged_patch_diff_text_is_applied_to_workspace_files(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    file_path = "workspace/reports/summary.md"
    workspace_key = workspace_file_object_key("default", file_path)
    service.object_store.write_text(workspace_key, "old\n")

    result = service._build_approved_staged_patch_result(  # noqa: SLF001
        workspace_id="default",
        script_data={
            "diff": (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
            )
        },
        accepted_scopes=["workspace/reports/**"],
    )

    assert result["ok"] is True
    assert result["changed_files"] == [file_path]
    assert result["staged_files"][0]["old_content"] == "old\n"
    assert result["staged_files"][0]["new_content"] == "new\n"


def test_skill_search_returns_compact_summaries_only(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(service)

    hits = _search(service)

    assert hits
    hit = [item for item in hits if item["skill_id"] == _skill_id(skill)][0]
    assert set(hit) >= {
        "skill_id",
        "display_name",
        "version",
        "description",
        "when_to_use",
        "entrypoint_count",
        "risk_level",
        "requires_activation",
    }
    assert "workflow_steps" not in hit
    assert "scripts" not in hit
    assert "script_content" not in hit
    _assert_no_forbidden_material(hit)


def test_skill_view_returns_compact_detail_without_raw_script_content(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(service, _script_proposal())

    detail = _view(service, _skill_id(skill))

    assert detail["skill_id"] == _skill_id(skill)
    assert detail["workflow_summary"]
    assert detail["entrypoints"]
    assert detail["permissions"]["network"] is False
    assert "scripts" not in detail
    assert "script_content" not in detail
    _assert_no_forbidden_material(detail)


def test_skill_activate_creates_run_scoped_context_and_entrypoint_tools(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(service)

    activation = _activate(service, _skill_id(skill))
    context_block = json.loads(
        service.object_store.read_text(activation["context_block_object_key"])
    )

    assert activation["skill_id"] == _skill_id(skill)
    assert activation["run_id"] == "run_001"
    assert context_block["summary"]
    assert activation["context_block_object_key"].endswith(
        f"/runs/run_001/skills/{_skill_id(skill)}/context_block.json"
    )
    assert activation["activated_entrypoint_tools"] == [
        f"skill_{_skill_id(skill)}_normalize_contract"
    ]
    assert service.object_store.exists(activation["context_block_object_key"])
    events_text = service.object_store.read_text(
        "workspaces/default/runs/run_001/events/part-000001.jsonl"
    )
    assert '"type":"skill_activated"' in events_text
    assert f'"skill_id":"{_skill_id(skill)}"' in events_text
    _assert_no_forbidden_material(activation)


def test_skill_activate_rejects_missing_or_finished_run_context(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(service)

    with pytest.raises(AgentSystemError) as missing:
        service.activate_skill(
            "default",
            _skill_id(skill),
            SkillActivateRequest(
                run_id="run_missing",
                thread_id="thread_001",
                reason="Should not activate without a real run.",
            ),
        )

    assert missing.value.error_type == "skill_activation_context_not_found"

    _write_runtime_context(service, status="completed")
    with pytest.raises(AgentSystemError) as finished:
        service.activate_skill(
            "default",
            _skill_id(skill),
            SkillActivateRequest(
                run_id="run_001",
                thread_id="thread_001",
                reason="Should not activate after run finished.",
            ),
        )

    assert finished.value.error_type == "skill_activation_run_not_running"


def test_activated_prompt_workflow_entrypoint_call_returns_guidance_and_artifacts(
    tmp_path,
) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(service)
    activation = _activate(service, _skill_id(skill))
    entrypoint_tool = activation["activated_entrypoint_tools"][0]

    result = service.execute_activated_entrypoint(
        workspace_id="default",
        run_id="run_001",
        thread_id="thread_001",
        entrypoint_tool_name=entrypoint_tool,
        args={"document_id": "doc_001", "note": "sk-test-secret"},
        tool_call_id="call_001",
    )

    assert result["ok"] is True
    assert result["error_type"] is None
    assert result["data"]["workflow_summary"]
    assert result["data"]["knowledge_notes"]
    assert result["data"]["args"]["note"] == "sk-***"
    artifacts = result["artifacts"]
    assert service.object_store.exists(artifacts["manifest_object_key"])
    assert service.object_store.exists(artifacts["args_object_key"])
    assert service.object_store.exists(artifacts["result_object_key"])
    manifest = json.loads(service.object_store.read_text(artifacts["manifest_object_key"]))
    assert manifest["status"] == "completed"
    assert manifest["entrypoint_tool_name"] == entrypoint_tool
    assert manifest["write_mode"] == "none"
    events_text = service.object_store.read_text(
        "workspaces/default/runs/run_001/events/part-000001.jsonl"
    )
    assert '"type":"skill_entrypoint_completed"' in events_text
    _assert_no_forbidden_material(result)


def test_unactivated_skill_entrypoint_call_is_rejected(tmp_path) -> None:
    service = _make_skill_service(tmp_path)
    skill = _create_skill(service)
    _write_runtime_context(service)

    with pytest.raises(AgentSystemError) as exc:
        service.execute_activated_entrypoint(
            workspace_id="default",
            run_id="run_001",
            thread_id="thread_001",
            entrypoint_tool_name=f"skill_{_skill_id(skill)}_normalize_contract",
            args={"document_id": "doc_001"},
        )

    assert exc.value.error_type == "skill_entrypoint_not_active"


def test_skill_entrypoint_dispatcher_tool_is_static_and_model_visible(tmp_path) -> None:
    from app.tools.builtin.skill_tools import build_default_skill_tools

    object_store = LocalObjectStore(tmp_path / "objects")
    tools = build_default_skill_tools(object_store)
    names = {tool.name for tool in tools}

    assert "skill_entrypoint_call" in names
    assert not any(name.startswith("skill_contract_cleaner_") for name in names)
