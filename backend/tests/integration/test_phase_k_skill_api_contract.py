from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_object_store
from app.main import app
from app.storage.local_object_store import LocalObjectStore

FORBIDDEN_TERMS = (
    "sk-test-secret",
    "api_key",
    "password",
    "plaintext",
    "ciphertext",
    "nonce",
    "tag",
    "authorization",
    "cookie",
    "RAW_SCRIPT_CONTENT_SHOULD_NOT_LEAK",
)


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


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _assert_no_secret_material(value: Any) -> None:
    serialized = json.dumps(_dump(value), ensure_ascii=False, default=str).lower()
    for term in FORBIDDEN_TERMS:
        assert term.lower() not in serialized


def _items(body: Any, key: str = "skills") -> list[dict[str, Any]]:
    if isinstance(body, list):
        return body
    assert isinstance(body, dict)
    for candidate in (key, "items", "results", "proposals"):
        value = body.get(candidate)
        if isinstance(value, list):
            return value
    pytest.fail(f"Expected list payload under {key}/items/results: {body}")


def _proposal_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
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


def _proposal_id(body: dict[str, Any]) -> str:
    for key in ("proposal_id", "skill_proposal_id", "id"):
        if body.get(key):
            return str(body[key])
    data = body.get("data")
    if isinstance(data, dict) and data.get("proposal_id"):
        return str(data["proposal_id"])
    pytest.fail(f"Proposal response must include proposal_id: {body}")


def _approval_id(body: dict[str, Any]) -> str:
    for key in ("approval_id", "approval_request_id"):
        if body.get(key):
            return str(body[key])
    data = body.get("data")
    if isinstance(data, dict) and data.get("approval_id"):
        return str(data["approval_id"])
    pytest.fail(f"Proposal response must include approval_id: {body}")


def _skill_id(body: dict[str, Any]) -> str:
    for key in ("skill_id", "id"):
        if body.get(key):
            return str(body[key])
    data = body.get("data")
    if isinstance(data, dict) and data.get("skill_id"):
        return str(data["skill_id"])
    pytest.fail(f"Skill response must include skill_id: {body}")


def _make_skill_service(object_store: LocalObjectStore) -> Any:
    for module_name, class_names in (
        ("app.skills.service", ("SkillService",)),
        ("app.skills.registry", ("SkillRegistry", "SkillRegistryService")),
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


def _approve_proposal(
    object_store: LocalObjectStore,
    proposal_id: str,
    approval_id: str,
) -> None:
    service = _make_skill_service(object_store)
    for name in ("approve_proposal", "mark_proposal_approved", "approve_skill_proposal"):
        func = getattr(service, name, None)
        if callable(func):
            func(
                workspace_id="default",
                proposal_id=proposal_id,
                approval_id=approval_id,
                approved_by="default_user",
            )
            return
    return


def test_skill_list_starts_empty_and_response_has_no_secrets(client: TestClient) -> None:
    response = client.get("/workspaces/default/skills")

    assert response.status_code == 200
    assert _items(response.json()) == []
    _assert_no_secret_material(response.json())


def test_skill_proposal_requires_approval_and_does_not_create_skill(
    client: TestClient,
) -> None:
    response = client.post("/workspaces/default/skill-proposals", json=_proposal_payload())
    skills_response = client.get("/workspaces/default/skills")

    assert response.status_code == 200
    body = response.json()
    assert _proposal_id(body)
    assert _approval_id(body)
    assert (body.get("requires_approval") or body.get("approval_required")) is True
    assert body.get("skill_id") is None
    assert skills_response.status_code == 200
    assert _items(skills_response.json()) == []
    _assert_no_secret_material(body)
    _assert_no_secret_material(skills_response.json())


def test_skill_create_from_proposal_list_detail_and_disable_endpoints(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    proposal_response = client.post(
        "/workspaces/default/skill-proposals",
        json=_proposal_payload(description="Normalize contract text and extract metadata."),
    )
    assert proposal_response.status_code == 200
    proposal = proposal_response.json()
    _approve_proposal(object_store, _proposal_id(proposal), _approval_id(proposal))

    create_response = client.post(
        "/workspaces/default/skills/from-proposal",
        json={
            "proposal_id": _proposal_id(proposal),
            "approval_id": _approval_id(proposal),
            "skill_id": "contract_cleaner",
            "version": "0.1.0",
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    skill_id = _skill_id(created)

    list_response = client.get("/workspaces/default/skills")
    detail_response = client.get(f"/workspaces/default/skills/{skill_id}")
    disable_response = client.post(
        f"/workspaces/default/skills/{skill_id}/disable",
        json={"reason": "contract test"},
    )

    assert list_response.status_code == 200
    listed = [item for item in _items(list_response.json()) if item["skill_id"] == skill_id]
    assert listed
    assert "workflow_steps" not in listed[0]
    assert "scripts" not in listed[0]

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["skill_id"] == skill_id
    assert detail["workflow_summary"]
    assert detail["entrypoints"]
    assert detail["permissions"]["network"] is False
    assert "scripts" not in detail
    assert "script_content" not in detail

    assert disable_response.status_code == 200
    disabled = disable_response.json()
    assert disabled["skill_id"] == skill_id
    assert disabled["enabled"] is False

    keys = set(object_store.list_keys("skills/default"))
    assert f"skills/default/{skill_id}/0.1.0/skill.yaml" in keys
    assert f"skills/default/{skill_id}/latest.json" in keys
    assert "skills/default/skill_index.json" in keys

    _assert_no_secret_material(create_response.json())
    _assert_no_secret_material(list_response.json())
    _assert_no_secret_material(detail_response.json())
    _assert_no_secret_material(disable_response.json())


def test_script_skill_validate_endpoint_enables_validated_skill(
    client: TestClient,
    object_store: LocalObjectStore,
) -> None:
    proposal_response = client.post(
        "/workspaces/default/skill-proposals",
        json=_proposal_payload(
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
                    "script_content": (
                        "def main(args):\n"
                        "    return {'document_id': args['document_id']}\n"
                    ),
                }
            ],
        ),
    )
    assert proposal_response.status_code == 200
    proposal = proposal_response.json()
    _approve_proposal(object_store, _proposal_id(proposal), _approval_id(proposal))

    create_response = client.post(
        "/workspaces/default/skills/from-proposal",
        json={
            "proposal_id": _proposal_id(proposal),
            "approval_id": _approval_id(proposal),
            "skill_id": "scripted_contract_cleaner",
            "version": "0.1.0",
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    skill_id = _skill_id(created)
    assert created["status"] == "disabled"
    assert created["validation_status"] == "pending_validation"

    validate_response = client.post(
        f"/workspaces/default/skills/{skill_id}/validate",
        json={"version": "0.1.0"},
    )

    assert validate_response.status_code == 200
    validated = validate_response.json()
    assert validated["status"] == "enabled"
    assert validated["enabled"] is True
    assert validated["validation_status"] == "validated"
    assert validated["entrypoints"][0]["sandbox_profile"] == "skill_script_readonly"
    assert validated["entrypoints"][0]["script_checksum"].startswith("sha256:")
    assert "script_content" not in validated["entrypoints"][0]
    _assert_no_secret_material(validated)
