from __future__ import annotations

import hashlib
import json

import pytest

from app.core.errors import AgentSystemError
from app.skills.runner import SkillScriptRunner
from app.storage.local_object_store import LocalObjectStore


def _entrypoint(
    object_store: LocalObjectStore,
    script: str,
    *,
    checksum_override: str | None = None,
    timeout_ms: int = 30000,
) -> dict[str, object]:
    script_key = "skills/default/scripted/0.1.0/scripts/main.py"
    object_store.write_text(script_key, script)
    checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
    return {
        "name": "main",
        "type": "script",
        "runtime": "python",
        "script_object_key": script_key,
        "script_checksum": checksum_override or f"sha256:{checksum}",
        "sandbox_profile": "skill_script_readonly",
        "timeout_ms": timeout_ms,
        "write_mode": "none",
    }


def _artifacts() -> dict[str, str]:
    return {
        "stdout_object_key": "runs/run_001/skill_runs/run/stdout.txt",
        "stderr_object_key": "runs/run_001/skill_runs/run/stderr.txt",
    }


def test_skill_script_runner_executes_readonly_script_and_writes_redacted_streams(
    tmp_path,
) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    runner = SkillScriptRunner(object_store)
    entrypoint = _entrypoint(
        object_store,
        """
import math

def main(args):
    print("processed " + args["document_id"])
    print(args["note"])
    return {"document_id": args["document_id"], "score": math.sqrt(81), "note": args["note"]}
""",
    )
    artifacts = _artifacts()

    result = runner.run_readonly(
        entrypoint=entrypoint,
        args={
            "document_id": "doc_001",
            "note": "Authorization: Bearer sk-test-secret",
        },
        artifacts=artifacts,
    )

    assert result["ok"] is True
    assert result["error_type"] is None
    assert result["data"]["document_id"] == "doc_001"
    assert result["data"]["score"] == 9.0
    serialized = json.dumps(result, ensure_ascii=False)
    assert "sk-test-secret" not in serialized
    assert "Authorization: Bearer ***" in serialized
    assert object_store.read_text(artifacts["stdout_object_key"])
    assert object_store.read_text(artifacts["stderr_object_key"]) == ""


def test_skill_script_runner_rejects_forbidden_import(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    runner = SkillScriptRunner(object_store)
    entrypoint = _entrypoint(
        object_store,
        """
import os

def main(args):
    return {"cwd": os.getcwd()}
""",
    )

    with pytest.raises(AgentSystemError) as exc:
        runner.validate_entrypoint(entrypoint)

    assert exc.value.error_type == "skill_script_forbidden_import"


def test_skill_script_runner_rejects_open_builtin(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    runner = SkillScriptRunner(object_store)
    entrypoint = _entrypoint(
        object_store,
        """
def main(args):
    return open("secret.txt").read()
""",
    )

    with pytest.raises(AgentSystemError) as exc:
        runner.validate_entrypoint(entrypoint)

    assert exc.value.error_type == "skill_script_forbidden_name"


def test_skill_script_runner_validate_requires_main_function(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    runner = SkillScriptRunner(object_store)
    entrypoint = _entrypoint(object_store, "NORMALIZED_VALUE = 1\n")

    with pytest.raises(AgentSystemError) as exc:
        runner.validate_entrypoint(entrypoint)

    assert exc.value.error_type == "skill_script_missing_main"


def test_skill_script_runner_rejects_checksum_mismatch(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    runner = SkillScriptRunner(object_store)
    entrypoint = _entrypoint(
        object_store,
        "def main(args):\n    return {'ok': True}\n",
        checksum_override="sha256:bad",
    )

    with pytest.raises(AgentSystemError) as exc:
        runner.run_readonly(entrypoint=entrypoint, args={}, artifacts=_artifacts())

    assert exc.value.error_type == "skill_script_checksum_mismatch"


def test_skill_script_runner_timeout_returns_failed_result_and_stream_artifacts(
    tmp_path,
) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    runner = SkillScriptRunner(object_store)
    entrypoint = _entrypoint(
        object_store,
        """
def main(args):
    while True:
        pass
""",
        timeout_ms=1000,
    )
    artifacts = _artifacts()

    result = runner.run_readonly(entrypoint=entrypoint, args={}, artifacts=artifacts)

    assert result["ok"] is False
    assert result["error_type"] == "skill_script_timeout"
    assert object_store.exists(artifacts["stdout_object_key"])
    assert object_store.exists(artifacts["stderr_object_key"])
