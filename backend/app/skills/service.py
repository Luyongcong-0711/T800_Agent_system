from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import re
from typing import Any

from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.runtime.tools import redact_runtime_value
from app.schemas.identity import RuntimeIdentity
from app.schemas.skill import (
    SkillActivateRequest,
    SkillCreateFromProposalRequest,
    SkillDisableRequest,
    SkillEntrypointSpec,
    SkillProposalRequest,
    SkillValidateRequest,
)
from app.skills.runner import SkillScriptRunner
from app.storage.jsonl_store import JsonlSegmentStore
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import (
    run_event_index_key,
    run_events_prefix,
    run_manifest_key,
    run_operations_prefix,
    run_prefix,
    run_skill_context_key,
    run_skill_run_artifact_key,
    skill_index_key,
    skill_latest_key,
    skill_manifest_key,
    skill_prefix,
    skill_proposal_key,
    skill_script_key,
    thread_manifest_key,
    workspace_file_object_key,
)
from app.workspace_files.service import WorkspaceFileService

SCRIPT_RISK_TYPES = {"script"}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class SkillService:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.script_runner = SkillScriptRunner(object_store)

    def list_skills(self, workspace_id: str) -> list[dict[str, Any]]:
        return list(self._skill_index(workspace_id).get("skills", []))

    def search(self, workspace_id: str, *, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        query_terms = [term for term in query.lower().split() if term]
        hits: list[dict[str, Any]] = []
        for skill in self.list_skills(workspace_id):
            haystack = " ".join(
                [
                    str(skill.get("display_name") or ""),
                    str(skill.get("description") or ""),
                    " ".join(skill.get("when_to_use") or []),
                ]
            ).lower()
            if query_terms and not any(term in haystack for term in query_terms):
                continue
            hits.append(skill)
        return hits[: max(1, min(top_k, 10))]

    def create_proposal(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: SkillProposalRequest,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        proposal_id = new_id("skillprop")
        risk_level = self._risk_level(request.entrypoints, request.script_required)
        proposal = {
            "schema_version": 1,
            "proposal_id": proposal_id,
            "workspace_id": workspace_id,
            "user_id": identity.user_id,
            "display_name": redact_runtime_value(request.display_name),
            "description": redact_runtime_value(request.description),
            "when_to_use": redact_runtime_value(request.when_to_use),
            "workflow_steps": redact_runtime_value(request.workflow_steps),
            "knowledge_notes": redact_runtime_value(request.knowledge_notes),
            "entrypoints": [
                self._proposal_entrypoint(entrypoint) for entrypoint in request.entrypoints
            ],
            "permissions": request.permissions.model_dump(),
            "source": request.source.model_dump(),
            "scripts": redact_runtime_value(request.scripts),
            "script_required": request.script_required
            or any(self._is_script_entrypoint(item) for item in request.entrypoints),
            "risk_level": risk_level,
            "approval_required": True,
            "approval_id": new_id("approval"),
            "status": "pending_approval",
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        self.json_store.write_json(skill_proposal_key(workspace_id, proposal_id), proposal)
        return proposal

    def get_proposal(self, workspace_id: str, proposal_id: str) -> dict[str, Any]:
        key = skill_proposal_key(workspace_id, proposal_id)
        if not self.object_store.exists(key):
            raise AgentSystemError("skill_proposal_not_found", "Skill proposal was not found.", 404)
        return self.json_store.read_json(key)

    def materialize_proposal(
        self,
        workspace_id: str,
        identity: RuntimeIdentity,
        request: SkillCreateFromProposalRequest,
    ) -> dict[str, Any]:
        proposal = self.get_proposal(workspace_id, request.proposal_id)
        if proposal["approval_id"] != request.approval_id:
            raise AgentSystemError("approval_required", "Skill creation requires approval.", 409)
        skill_id = request.skill_id or self._slugify(proposal["display_name"])
        version = request.version
        if self.object_store.exists(skill_manifest_key(workspace_id, skill_id, version)):
            raise AgentSystemError("skill_version_exists", "Skill version already exists.", 409)
        now = utc_now_iso()
        has_script = bool(proposal.get("script_required"))
        entrypoints, script_artifacts = self._materialize_entrypoints(
            workspace_id,
            skill_id,
            version,
            proposal["entrypoints"],
        )
        manifest = {
            "schema_version": 1,
            "skill_id": skill_id,
            "workspace_id": workspace_id,
            "version": version,
            "display_name": proposal["display_name"],
            "description": proposal["description"],
            "owner": identity.user_id,
            "status": "disabled" if has_script else "enabled",
            "risk_level": proposal["risk_level"],
            "requires_activation": True,
            "requires_validation": has_script,
            "validation_status": "pending_validation" if has_script else "validated",
            "proposal_id": proposal["proposal_id"],
            "entrypoints": entrypoints,
            "workflow_steps": proposal["workflow_steps"],
            "knowledge_notes": proposal["knowledge_notes"],
            "when_to_use": proposal["when_to_use"],
            "permissions": proposal["permissions"],
            "script_artifacts": script_artifacts,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        self.json_store.write_json(skill_manifest_key(workspace_id, skill_id, version), manifest)
        base_prefix = skill_prefix(workspace_id, skill_id, version)
        self.object_store.write_text(
            f"{base_prefix}/README.md",
            f"# {manifest['display_name']}\n\n{manifest['description']}\n",
        )
        self.object_store.write_text(
            f"{base_prefix}/workflows/workflow.yaml",
            "\n".join(f"- {step}" for step in manifest["workflow_steps"]),
        )
        if manifest["knowledge_notes"]:
            self.object_store.write_text(
                f"{base_prefix}/knowledge/notes.md",
                "\n".join(f"- {note}" for note in manifest["knowledge_notes"]),
            )
        self.json_store.write_json(
            skill_latest_key(workspace_id, skill_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "skill_id": skill_id,
                "version": version,
                "manifest_object_key": skill_manifest_key(workspace_id, skill_id, version),
                "updated_at": now,
                "revision": 1,
            },
        )
        self._upsert_skill_index(workspace_id, manifest)
        proposal["status"] = "materialized"
        proposal["materialized_skill_id"] = skill_id
        proposal["materialized_version"] = version
        proposal["updated_at"] = now
        proposal["revision"] = int(proposal.get("revision") or 0) + 1
        self.json_store.write_json(
            skill_proposal_key(workspace_id, proposal["proposal_id"]),
            proposal,
        )
        return self.view_skill(workspace_id, skill_id, version)

    def get_manifest(
        self,
        workspace_id: str,
        skill_id: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        active_version = version or self._latest_version(workspace_id, skill_id)
        key = skill_manifest_key(workspace_id, skill_id, active_version)
        if not self.object_store.exists(key):
            raise AgentSystemError("skill_not_found", "Skill was not found.", 404)
        return self.json_store.read_json(key)

    def view_skill(
        self,
        workspace_id: str,
        skill_id: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(workspace_id, skill_id, version)
        return self._public_detail(manifest)

    def disable_skill(
        self,
        workspace_id: str,
        skill_id: str,
        request: SkillDisableRequest | None = None,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(workspace_id, skill_id)
        manifest["status"] = "disabled"
        manifest["disable_reason"] = request.reason if request else None
        manifest["updated_at"] = utc_now_iso()
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(
            skill_manifest_key(workspace_id, skill_id, manifest["version"]),
            manifest,
        )
        self._upsert_skill_index(workspace_id, manifest)
        return self._public_detail(manifest)

    def validate_skill_scripts(
        self,
        workspace_id: str,
        skill_id: str,
        request: SkillValidateRequest | None = None,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(workspace_id, skill_id, request.version if request else None)
        try:
            for entrypoint in manifest.get("entrypoints") or []:
                if entrypoint.get("type") == "script" or entrypoint.get("script_required"):
                    validation = self.script_runner.validate_entrypoint(entrypoint)
                    entrypoint["sandbox_profile"] = validation["sandbox_profile"]
            manifest["status"] = "enabled"
            manifest["validation_status"] = "validated"
            manifest["validation_error"] = None
        except AgentSystemError as exc:
            manifest["status"] = "disabled"
            manifest["validation_status"] = "failed"
            manifest["validation_error"] = exc.error_type
            manifest["updated_at"] = utc_now_iso()
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            self.json_store.write_json(
                skill_manifest_key(workspace_id, skill_id, manifest["version"]),
                manifest,
            )
            self._upsert_skill_index(workspace_id, manifest)
            raise
        manifest["updated_at"] = utc_now_iso()
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(
            skill_manifest_key(workspace_id, skill_id, manifest["version"]),
            manifest,
        )
        self._upsert_skill_index(workspace_id, manifest)
        return self._public_detail(manifest)

    def activate_skill(
        self,
        workspace_id: str,
        skill_id: str,
        request: SkillActivateRequest,
    ) -> dict[str, Any]:
        manifest = self.get_manifest(workspace_id, skill_id, request.version)
        if manifest.get("status") != "enabled":
            raise AgentSystemError("skill_disabled", "Skill is disabled.", 409)
        self._validate_activation_context(workspace_id, request)
        now = utc_now_iso()
        entrypoint_tools = [
            self._entrypoint_tool_name(skill_id, entrypoint["name"])
            for entrypoint in manifest.get("entrypoints", [])
        ]
        context_block = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "run_id": request.run_id,
            "thread_id": request.thread_id,
            "skill_id": skill_id,
            "version": manifest["version"],
            "display_name": manifest["display_name"],
            "summary": manifest["description"],
            "workflow_summary": manifest.get("workflow_steps") or [],
            "knowledge_notes": manifest.get("knowledge_notes") or [],
            "entrypoint_tools": entrypoint_tools,
            "reason": redact_runtime_value(request.reason),
            "created_at": now,
        }
        context_key = run_skill_context_key(workspace_id, request.run_id, skill_id)
        self.json_store.write_json(context_key, context_block)
        self._append_skill_activated_event(
            workspace_id=workspace_id,
            thread_id=request.thread_id,
            run_id=request.run_id,
            skill_id=skill_id,
            version=manifest["version"],
            context_block_object_key=context_key,
            activated_entrypoint_tools=entrypoint_tools,
            reason=request.reason,
        )
        return {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "run_id": request.run_id,
            "thread_id": request.thread_id,
            "skill_id": skill_id,
            "version": manifest["version"],
            "reason": redact_runtime_value(request.reason),
            "activated_entrypoint_tools": entrypoint_tools,
            "context_block_object_key": context_key,
            "created_at": now,
        }

    def execute_activated_entrypoint(
        self,
        workspace_id: str,
        run_id: str,
        thread_id: str,
        entrypoint_tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        self._validate_activation_context(
            workspace_id,
            SkillActivateRequest(
                run_id=run_id,
                thread_id=thread_id,
                reason="Execute activated Skill entrypoint.",
            ),
        )
        context_block = self._activated_context_for_tool(
            workspace_id,
            run_id,
            thread_id,
            entrypoint_tool_name,
        )
        skill_id = str(context_block["skill_id"])
        version = str(context_block["version"])
        manifest = self.get_manifest(workspace_id, skill_id, version)
        if manifest.get("status") != "enabled":
            raise AgentSystemError("skill_disabled", "Skill is disabled.", 409)
        entrypoint = self._entrypoint_for_tool(manifest, entrypoint_tool_name)
        is_script_entrypoint = bool(
            entrypoint.get("type") == "script" or entrypoint.get("script_required")
        )
        if is_script_entrypoint and manifest.get("validation_status") != "validated":
            raise AgentSystemError(
                "skill_script_not_validated",
                "Skill script entrypoints must be validated before execution.",
                409,
            )
        started_at = utc_now_iso()
        skill_run_id, artifacts = self._write_skill_run_start(
            workspace_id=workspace_id,
            run_id=run_id,
            thread_id=thread_id,
            tool_call_id=tool_call_id,
            skill_id=skill_id,
            version=version,
            entrypoint=entrypoint,
            entrypoint_tool_name=entrypoint_tool_name,
            args=args or {},
            started_at=started_at,
        )
        if entrypoint.get("write_mode") == "staged_patch":
            approval_plan = self._write_staged_patch_approval_artifacts(
                artifacts=artifacts,
                args=args or {},
                entrypoint=entrypoint,
                entrypoint_tool_name=entrypoint_tool_name,
                manifest=manifest,
                run_id=run_id,
                skill_id=skill_id,
                skill_run_id=skill_run_id,
                thread_id=thread_id,
                version=version,
                workspace_id=workspace_id,
                tool_call_id=tool_call_id,
            )
            result = self._skill_entrypoint_result(
                ok=False,
                error_type="approval_required",
                message_for_model=(
                    "This Skill entrypoint declares staged file writes and requires "
                    "approval before execution."
                ),
                skill_run_id=skill_run_id,
                artifacts=artifacts,
                data={
                    "approval_id": approval_plan["approval_id"],
                    "diff_summary": approval_plan["diff_summary"],
                    "operation_plan_status": approval_plan["status"],
                    "write_mode": "staged_patch",
                },
            )
            self._finish_skill_run(
                artifacts,
                result=result,
                status="waiting_approval",
                finished_at=utc_now_iso(),
            )
            self._append_run_event(
                workspace_id=workspace_id,
                thread_id=thread_id,
                run_id=run_id,
                event_type="skill_entrypoint_approval_required",
                payload={
                    "skill_run_id": skill_run_id,
                    "skill_id": skill_id,
                    "version": version,
                    "entrypoint": entrypoint["name"],
                    "entrypoint_tool_name": entrypoint_tool_name,
                    "approval_id": approval_plan["approval_id"],
                    "diff_summary": approval_plan["diff_summary"],
                    "artifacts": artifacts,
                },
            )
            return result
        if is_script_entrypoint:
            try:
                script_result = self.script_runner.run_readonly(
                    entrypoint=entrypoint,
                    args=args or {},
                    artifacts=artifacts,
                )
            except AgentSystemError as exc:
                self._ensure_stream_artifacts(artifacts)
                script_result = {
                    "ok": False,
                    "error_type": exc.error_type,
                    "message_for_model": exc.message_for_user,
                    "data": exc.details,
                    "stdout_preview": "",
                    "stderr_preview": "",
                }
            result = self._skill_entrypoint_result(
                ok=bool(script_result["ok"]),
                error_type=script_result.get("error_type"),
                message_for_model=str(script_result["message_for_model"]),
                skill_run_id=skill_run_id,
                artifacts=artifacts,
                data={
                    "result": script_result.get("data") or {},
                    "stdout_preview": script_result.get("stdout_preview") or "",
                    "stderr_preview": script_result.get("stderr_preview") or "",
                },
            )
            status = "completed" if script_result["ok"] else "failed"
            self._finish_skill_run(
                artifacts,
                result=result,
                status=status,
                finished_at=utc_now_iso(),
            )
            self._append_run_event(
                workspace_id=workspace_id,
                thread_id=thread_id,
                run_id=run_id,
                event_type=(
                    "skill_entrypoint_completed"
                    if script_result["ok"]
                    else "skill_entrypoint_failed"
                ),
                payload={
                    "skill_run_id": skill_run_id,
                    "skill_id": skill_id,
                    "version": version,
                    "entrypoint": entrypoint["name"],
                    "entrypoint_tool_name": entrypoint_tool_name,
                    "error_type": script_result.get("error_type"),
                    "artifacts": artifacts,
                },
            )
            return result
        result = self._skill_entrypoint_result(
            ok=True,
            error_type=None,
            message_for_model=(
                "Apply this activated Skill workflow to the current task. Use the "
                "workflow_summary and knowledge_notes below as approved guidance."
            ),
            skill_run_id=skill_run_id,
            artifacts=artifacts,
            data={
                "display_name": manifest["display_name"],
                "workflow_summary": manifest.get("workflow_steps") or [],
                "knowledge_notes": manifest.get("knowledge_notes") or [],
                "entrypoint": entrypoint["name"],
                "args": redact_runtime_value(args or {}),
            },
        )
        self._finish_skill_run(
            artifacts,
            result=result,
            status="completed",
            finished_at=utc_now_iso(),
        )
        self._append_run_event(
            workspace_id=workspace_id,
            thread_id=thread_id,
            run_id=run_id,
            event_type="skill_entrypoint_completed",
            payload={
                "skill_run_id": skill_run_id,
                "skill_id": skill_id,
                "version": version,
                "entrypoint": entrypoint["name"],
                "entrypoint_tool_name": entrypoint_tool_name,
                "artifacts": artifacts,
            },
        )
        return result

    def execute_approved_staged_patch(
        self,
        *,
        workspace_id: str,
        run_id: str,
        operation_plan_key: str,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        if plan.get("approval_kind") != "skill_script_staged_patch":
            raise AgentSystemError(
                "invalid_skill_approval_kind",
                "Approval plan is not a Skill staged patch plan.",
                400,
            )
        if plan.get("status") not in {"approved_pending_execution", "executing"}:
            raise AgentSystemError(
                "skill_staged_patch_not_approved",
                "Skill staged patch must be approved before execution.",
                409,
            )
        skill_run_id = str(plan.get("skill_run_id") or "")
        if not skill_run_id:
            raise AgentSystemError(
                "skill_run_missing",
                "Skill staged patch plan is missing the skill run id.",
                409,
            )
        manifest_key = run_skill_run_artifact_key(
            workspace_id,
            run_id,
            skill_run_id,
            "manifest.json",
        )
        if not self.object_store.exists(manifest_key):
            raise AgentSystemError(
                "skill_run_manifest_missing",
                "Skill run manifest is missing for approved staged patch execution.",
                409,
            )
        skill_run_manifest = self.json_store.read_json(manifest_key)
        artifacts = self._skill_run_artifacts(skill_run_manifest, plan)
        args = self.json_store.read_json_or_default(artifacts["args_object_key"], {})
        skill_id = str(plan.get("skill_id") or skill_run_manifest.get("skill_id") or "")
        version = str(
            plan.get("skill_version") or skill_run_manifest.get("skill_version") or ""
        )
        entrypoint_tool_name = str(
            plan.get("entrypoint_tool_name")
            or skill_run_manifest.get("entrypoint_tool_name")
            or ""
        )
        if not skill_id or not version or not entrypoint_tool_name:
            raise AgentSystemError(
                "skill_plan_incomplete",
                "Skill staged patch plan is missing skill or entrypoint metadata.",
                409,
            )
        manifest = self.get_manifest(workspace_id, skill_id, version)
        entrypoint = self._entrypoint_for_tool(manifest, entrypoint_tool_name)
        write_scope = plan.get("write_scope")
        if not isinstance(write_scope, dict):
            write_scope = self._staged_write_scope_summary(entrypoint, manifest)
        accepted_scopes = [
            str(scope)
            for scope in write_scope.get("accepted", [])
            if isinstance(scope, str) and scope.strip()
        ]
        if entrypoint.get("write_mode") != "staged_patch" or not accepted_scopes:
            raise AgentSystemError(
                "skill_staged_patch_scope_invalid",
                "Approved Skill staged patch has no accepted write scope.",
                409,
            )

        now = utc_now_iso()
        plan["status"] = "executing"
        plan["stage"] = "approval_execution"
        plan["script_execution"] = "executing"
        plan["execution_started_at"] = now
        plan["updated_at"] = now
        plan["revision"] = int(plan.get("revision") or 0) + 1
        self.json_store.write_json(operation_plan_key, redact_runtime_value(plan))
        self._set_skill_run_status(artifacts, "executing_after_approval")
        self._append_run_event(
            workspace_id=workspace_id,
            thread_id=str(plan.get("thread_id") or skill_run_manifest["thread_id"]),
            run_id=run_id,
            event_type="skill_entrypoint_approval_execution_started",
            payload={
                "approval_id": plan["approval_id"],
                "skill_run_id": skill_run_id,
                "entrypoint_tool_name": entrypoint_tool_name,
                "operation_plan_object_key": operation_plan_key,
            },
        )

        try:
            script_result = self.script_runner.run_readonly(
                entrypoint=entrypoint,
                args=args if isinstance(args, dict) else {},
                artifacts=artifacts,
            )
        except AgentSystemError as exc:
            self._ensure_stream_artifacts(artifacts)
            script_result = {
                "ok": False,
                "error_type": exc.error_type,
                "message_for_model": exc.message_for_user,
                "data": exc.details,
                "stdout_preview": "",
                "stderr_preview": "",
            }

        try:
            patch_result = self._build_approved_staged_patch_result(
                workspace_id=workspace_id,
                script_data=script_result.get("data") if isinstance(script_result, dict) else {},
                accepted_scopes=accepted_scopes,
            )
        except AgentSystemError as exc:
            patch_result = {
                "ok": False,
                "error_type": exc.error_type,
                "changed_files": [],
                "diff_summary": {
                    "files_changed": 0,
                    "insertions": 0,
                    "deletions": 0,
                    "generated": False,
                },
                "patch": "",
            }
        ok = bool(script_result.get("ok")) and bool(patch_result["ok"])
        error_type = (
            str(script_result.get("error_type"))
            if script_result.get("error_type")
            else patch_result.get("error_type")
        )
        if ok:
            self.object_store.write_text(artifacts["diff_object_key"], patch_result["patch"])
        commit_result = self._commit_approved_staged_files(
            workspace_id=workspace_id,
            run_id=run_id,
            plan=plan,
            ok=ok,
            patch_result=patch_result,
        )
        if ok and not commit_result["ok"]:
            ok = False
            error_type = commit_result["error_type"]
        operation = self._append_staged_patch_operation(
            workspace_id=workspace_id,
            run_id=run_id,
            plan=plan,
            entrypoint_tool_name=entrypoint_tool_name,
            artifacts=artifacts,
            ok=ok,
            error_type=error_type,
            patch_result=patch_result,
            commit_result=commit_result,
        )
        result = self._skill_entrypoint_result(
            ok=ok,
            error_type=error_type,
            message_for_model=(
                "Approved Skill patch executed and generated a diff artifact."
                if ok
                else "Approved Skill patch execution failed."
            ),
            skill_run_id=skill_run_id,
            artifacts=artifacts,
            data={
                "approval_id": plan["approval_id"],
                "result": script_result.get("data") or {},
                "changed_files": patch_result["changed_files"],
                "diff_summary": patch_result["diff_summary"],
                "operation_id": operation["operation_id"],
                "operation_status": operation["status"],
                "operations_stream_prefix": operation["operations_stream_prefix"],
                "workspace_commit_status": commit_result["workspace_commit_status"],
                "rollback_token": commit_result.get("rollback_token"),
                "committed_files": commit_result.get("committed_files") or [],
                "stdout_preview": script_result.get("stdout_preview") or "",
                "stderr_preview": script_result.get("stderr_preview") or "",
            },
        )
        finished_at = utc_now_iso()
        plan["status"] = "executed" if ok else "execution_failed"
        plan["stage"] = plan["status"]
        plan["script_execution"] = "executed" if script_result.get("ok") else "failed"
        plan["overlay_diff_status"] = "generated" if ok else "failed"
        plan["placeholder"] = False
        plan["changed_files"] = patch_result["changed_files"]
        plan["diff_summary"] = patch_result["diff_summary"]
        plan["operation_id"] = operation["operation_id"]
        plan["operation_status"] = operation["status"]
        plan["operations_stream_prefix"] = operation["operations_stream_prefix"]
        plan["workspace_commit_status"] = commit_result["workspace_commit_status"]
        plan["rollback_token"] = commit_result.get("rollback_token")
        plan["committed_files"] = commit_result.get("committed_files") or []
        plan["execution_completed_at"] = finished_at
        plan["execution_result"] = {
            "ok": ok,
            "error_type": error_type,
            "skill_run_id": skill_run_id,
        }
        plan["updated_at"] = finished_at
        plan["revision"] = int(plan.get("revision") or 0) + 1
        plan["artifacts"] = {**artifacts, **(plan.get("artifacts") or {})}
        self.json_store.write_json(operation_plan_key, redact_runtime_value(plan))
        self._finish_skill_run(
            artifacts,
            result=result,
            status="completed" if ok else "failed",
            finished_at=finished_at,
        )
        self._append_run_event(
            workspace_id=workspace_id,
            thread_id=str(plan.get("thread_id") or skill_run_manifest["thread_id"]),
            run_id=run_id,
            event_type=(
                "skill_entrypoint_approval_execution_completed"
                if ok
                else "skill_entrypoint_approval_execution_failed"
            ),
            payload={
                "approval_id": plan["approval_id"],
                "skill_run_id": skill_run_id,
                "ok": ok,
                "error_type": error_type,
                "changed_files": patch_result["changed_files"],
                "diff_summary": patch_result["diff_summary"],
                "operation_id": operation["operation_id"],
                "operation_status": operation["status"],
                "workspace_commit_status": commit_result["workspace_commit_status"],
                "rollback_token": commit_result.get("rollback_token"),
                "operation_plan_object_key": operation_plan_key,
            },
        )
        return result

    def _commit_approved_staged_files(
        self,
        *,
        workspace_id: str,
        run_id: str,
        plan: dict[str, Any],
        ok: bool,
        patch_result: dict[str, Any],
    ) -> dict[str, Any]:
        if not ok:
            return {
                "ok": True,
                "workspace_commit_status": "not_committed",
                "rollback_token": None,
                "committed_files": [],
                "backup_records": [],
            }
        staged_files = patch_result.get("staged_files")
        if not isinstance(staged_files, list) or not staged_files:
            return {
                "ok": True,
                "workspace_commit_status": "not_committed",
                "rollback_token": None,
                "committed_files": [],
                "backup_records": [],
            }
        operation_id = str(plan.get("operation_id") or new_id("op"))
        plan["operation_id"] = operation_id
        rollback_token = str(plan.get("rollback_token") or new_id("rollback"))
        try:
            return WorkspaceFileService(self.object_store).apply_staged_files(
                workspace_id=workspace_id,
                run_id=run_id,
                operation_id=operation_id,
                rollback_token=rollback_token,
                files=staged_files,
            )
        except AgentSystemError as exc:
            return {
                "ok": False,
                "error_type": exc.error_type,
                "workspace_commit_status": "commit_failed",
                "rollback_token": None,
                "committed_files": [],
                "backup_records": [],
            }

    def _append_staged_patch_operation(
        self,
        *,
        workspace_id: str,
        run_id: str,
        plan: dict[str, Any],
        entrypoint_tool_name: str,
        artifacts: dict[str, str],
        ok: bool,
        error_type: str | None,
        patch_result: dict[str, Any],
        commit_result: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now_iso()
        operation_id = str(plan.get("operation_id") or new_id("op"))
        stream_prefix = run_operations_prefix(workspace_id, run_id)
        commit_status = commit_result["workspace_commit_status"]
        status = "committed" if commit_status == "committed" else "staged_patch_generated"
        if not ok:
            status = "failed"
        record = {
            "schema_version": 1,
            "operation_id": operation_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "tool_name": entrypoint_tool_name,
            "operation_type": "skill_staged_patch",
            "approval_id": plan.get("approval_id"),
            "skill_run_id": plan.get("skill_run_id"),
            "skill_id": plan.get("skill_id"),
            "skill_version": plan.get("skill_version"),
            "side_effect": True,
            "effect_type": "workspace_file_write"
            if commit_status == "committed"
            else "diff_artifact_write",
            "workspace_commit_status": commit_status,
            "reversible": commit_status == "committed",
            "rollback_strategy": "file_backup"
            if commit_status == "committed"
            else "not_available_until_workspace_commit",
            "rollback_token": commit_result.get("rollback_token"),
            "status": status,
            "error_type": error_type,
            "changed_files": patch_result.get("changed_files") or [],
            "diff_summary": patch_result.get("diff_summary") or {},
            "committed_files": commit_result.get("committed_files") or [],
            "backup_records": commit_result.get("backup_records") or [],
            "artifacts": {
                "diff_object_key": artifacts.get("diff_object_key"),
                "operation_plan_object_key": artifacts.get("operation_plan_object_key"),
                "result_object_key": artifacts.get("result_object_key"),
                "stdout_object_key": artifacts.get("stdout_object_key"),
                "stderr_object_key": artifacts.get("stderr_object_key"),
            },
            "created_at": now,
            "updated_at": now,
        }
        JsonlSegmentStore(self.object_store, stream_prefix).append(redact_runtime_value(record))
        return {**record, "operations_stream_prefix": stream_prefix}

    def _validate_activation_context(
        self,
        workspace_id: str,
        request: SkillActivateRequest,
    ) -> None:
        try:
            thread_key = thread_manifest_key(workspace_id, request.thread_id)
            run_key = run_manifest_key(workspace_id, request.run_id)
        except ValueError as exc:
            raise AgentSystemError(
                "invalid_skill_activation_context",
                "Skill activation requires a valid existing run and thread.",
                400,
            ) from exc
        if not self.object_store.exists(thread_key) or not self.object_store.exists(run_key):
            raise AgentSystemError(
                "skill_activation_context_not_found",
                "Skill activation requires an existing run and thread.",
                404,
            )
        thread = self.json_store.read_json(thread_key)
        run = self.json_store.read_json(run_key)
        if (
            run.get("thread_id") != request.thread_id
            or thread.get("thread_id") != request.thread_id
        ):
            raise AgentSystemError(
                "skill_activation_context_mismatch",
                "Skill activation run and thread do not match.",
                409,
            )
        if run.get("workspace_id") != workspace_id or thread.get("workspace_id") != workspace_id:
            raise AgentSystemError(
                "skill_activation_context_mismatch",
                "Skill activation context does not belong to this workspace.",
                409,
            )
        if run.get("status") != "running":
            raise AgentSystemError(
                "skill_activation_run_not_running",
                "Skill activation requires a running run.",
                409,
            )

    def _append_skill_activated_event(
        self,
        *,
        workspace_id: str,
        thread_id: str,
        run_id: str,
        skill_id: str,
        version: str,
        context_block_object_key: str,
        activated_entrypoint_tools: list[str],
        reason: str,
    ) -> None:
        self._append_run_event(
            workspace_id=workspace_id,
            thread_id=thread_id,
            run_id=run_id,
            event_type="skill_activated",
            payload={
                "skill_id": skill_id,
                "version": version,
                "context_block_object_key": context_block_object_key,
                "activated_entrypoint_tools": activated_entrypoint_tools,
                "reason": reason,
            },
        )

    def _activated_context_for_tool(
        self,
        workspace_id: str,
        run_id: str,
        thread_id: str,
        entrypoint_tool_name: str,
    ) -> dict[str, Any]:
        prefix = f"{run_prefix(workspace_id, run_id)}/skills"
        for key in self.object_store.list_keys(prefix):
            if not key.endswith("/context_block.json"):
                continue
            block = self.json_store.read_json(key)
            if block.get("thread_id") != thread_id:
                continue
            if entrypoint_tool_name in (block.get("entrypoint_tools") or []):
                return block
        raise AgentSystemError(
            "skill_entrypoint_not_active",
            "Skill entrypoint is not active for this run.",
            409,
        )

    def _entrypoint_for_tool(
        self,
        manifest: dict[str, Any],
        entrypoint_tool_name: str,
    ) -> dict[str, Any]:
        skill_id = manifest["skill_id"]
        for entrypoint in manifest.get("entrypoints") or []:
            if self._entrypoint_tool_name(skill_id, entrypoint["name"]) == entrypoint_tool_name:
                return entrypoint
        raise AgentSystemError(
            "skill_entrypoint_not_found",
            "Skill entrypoint was not found in the activated Skill version.",
            404,
        )

    def _write_skill_run_start(
        self,
        *,
        workspace_id: str,
        run_id: str,
        thread_id: str,
        tool_call_id: str | None,
        skill_id: str,
        version: str,
        entrypoint: dict[str, Any],
        entrypoint_tool_name: str,
        args: dict[str, Any],
        started_at: str,
    ) -> tuple[str, dict[str, str]]:
        skill_run_id = new_id("skillrun")
        artifacts = {
            "manifest_object_key": run_skill_run_artifact_key(
                workspace_id,
                run_id,
                skill_run_id,
                "manifest.json",
            ),
            "args_object_key": run_skill_run_artifact_key(
                workspace_id,
                run_id,
                skill_run_id,
                "args.json",
            ),
            "stdout_object_key": run_skill_run_artifact_key(
                workspace_id,
                run_id,
                skill_run_id,
                "stdout.txt",
            ),
            "stderr_object_key": run_skill_run_artifact_key(
                workspace_id,
                run_id,
                skill_run_id,
                "stderr.txt",
            ),
            "result_object_key": run_skill_run_artifact_key(
                workspace_id,
                run_id,
                skill_run_id,
                "result.json",
            ),
        }
        if entrypoint.get("write_mode") == "staged_patch":
            artifacts["diff_object_key"] = run_skill_run_artifact_key(
                workspace_id,
                run_id,
                skill_run_id,
                "diff.patch",
            )
            artifacts["operation_plan_object_key"] = run_skill_run_artifact_key(
                workspace_id,
                run_id,
                skill_run_id,
                "operation_plan.json",
            )
        self.json_store.write_json(
            artifacts["manifest_object_key"],
            redact_runtime_value(
                {
                    "schema_version": 1,
                    "skill_run_id": skill_run_id,
                    "workspace_id": workspace_id,
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "tool_call_id": tool_call_id,
                    "skill_id": skill_id,
                    "skill_version": version,
                    "entrypoint": entrypoint["name"],
                    "entrypoint_tool_name": entrypoint_tool_name,
                    "entrypoint_type": entrypoint.get("type") or "prompt_workflow",
                    "runtime": entrypoint.get("runtime"),
                    "sandbox_profile": entrypoint.get("sandbox_profile"),
                    "write_mode": entrypoint.get("write_mode") or "none",
                    "script_checksum": entrypoint.get("script_checksum"),
                    "artifacts": artifacts,
                    "status": "running",
                    "started_at": started_at,
                    "finished_at": None,
                }
            ),
        )
        self.json_store.write_json(artifacts["args_object_key"], redact_runtime_value(args))
        return skill_run_id, artifacts

    def _write_staged_patch_approval_artifacts(
        self,
        *,
        artifacts: dict[str, str],
        args: dict[str, Any],
        entrypoint: dict[str, Any],
        entrypoint_tool_name: str,
        manifest: dict[str, Any],
        run_id: str,
        skill_id: str,
        skill_run_id: str,
        thread_id: str,
        version: str,
        workspace_id: str,
        tool_call_id: str | None,
    ) -> dict[str, Any]:
        approval_id = new_id("approval")
        now = utc_now_iso()
        args_hash = self._sha256(
            json.dumps(redact_runtime_value(args), ensure_ascii=False, sort_keys=True)
        )
        write_scope = self._staged_write_scope_summary(entrypoint, manifest)
        diff_summary = {
            "files_changed": 0,
            "insertions": 0,
            "deletions": 0,
            "generated": False,
        }
        plan = redact_runtime_value(
            {
                "schema_version": 1,
                "approval_id": approval_id,
                "approval_kind": "skill_script_staged_patch",
                "status": "waiting_approval",
                "stage": "approval_required",
                "placeholder": True,
                "workspace_id": workspace_id,
                "thread_id": thread_id,
                "run_id": run_id,
                "skill_run_id": skill_run_id,
                "skill_id": skill_id,
                "skill_version": version,
                "entrypoint": entrypoint["name"],
                "entrypoint_tool_name": entrypoint_tool_name,
                "tool_call_id": tool_call_id,
                "risk_level": entrypoint.get("risk_level") or manifest.get("risk_level"),
                "reasons": ["skill_script_file_write", "requires_user_approval"],
                "script_checksum": entrypoint.get("script_checksum"),
                "args_hash": f"sha256:{args_hash}",
                "write_mode": "staged_patch",
                "write_scope": write_scope,
                "approval_ready": bool(write_scope["accepted"]),
                "script_execution": "not_executed",
                "overlay_diff_status": "not_generated",
                "changed_files": [],
                "diff_summary": diff_summary,
                "created_at": now,
                "updated_at": now,
                "revision": 1,
                "execution_policy": (
                    "P0 does not execute staged_patch scripts before approval; "
                    "overlay diff generation is a separate guarded step."
                ),
                "artifacts": {
                    **artifacts,
                    "diff_object_key": artifacts["diff_object_key"],
                    "operation_plan_object_key": artifacts["operation_plan_object_key"],
                },
                "rollback_available": False,
            }
        )
        diff_text = "\n".join(
            [
                "# Skill staged patch pending approval",
                f"# approval_id: {approval_id}",
                f"# skill_run_id: {skill_run_id}",
                "# diff_generated: false",
                "# P0 blocked script execution before overlay diff generation.",
                "",
            ]
        )
        self.object_store.write_text(artifacts["diff_object_key"], diff_text)
        self.json_store.write_json(artifacts["operation_plan_object_key"], plan)
        return plan

    def _skill_run_artifacts(
        self,
        skill_run_manifest: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, str]:
        artifacts = dict(skill_run_manifest.get("artifacts") or {})
        plan_artifacts = plan.get("artifacts") if isinstance(plan.get("artifacts"), dict) else {}
        artifacts.update({key: str(value) for key, value in plan_artifacts.items() if value})
        required_keys = (
            "manifest_object_key",
            "args_object_key",
            "stdout_object_key",
            "stderr_object_key",
            "result_object_key",
        )
        missing = [key for key in required_keys if not artifacts.get(key)]
        if missing:
            raise AgentSystemError(
                "skill_run_artifacts_missing",
                "Approved Skill staged patch is missing run artifacts.",
                409,
            )
        if not artifacts.get("diff_object_key"):
            artifacts["diff_object_key"] = run_skill_run_artifact_key(
                str(skill_run_manifest["workspace_id"]),
                str(skill_run_manifest["run_id"]),
                str(skill_run_manifest["skill_run_id"]),
                "diff.patch",
            )
        if not artifacts.get("operation_plan_object_key"):
            artifacts["operation_plan_object_key"] = run_skill_run_artifact_key(
                str(skill_run_manifest["workspace_id"]),
                str(skill_run_manifest["run_id"]),
                str(skill_run_manifest["skill_run_id"]),
                "operation_plan.json",
            )
        return {key: str(value) for key, value in artifacts.items() if value}

    def _set_skill_run_status(self, artifacts: dict[str, str], status: str) -> None:
        manifest = self.json_store.read_json(artifacts["manifest_object_key"])
        manifest["status"] = status
        manifest["updated_at"] = utc_now_iso()
        manifest["revision"] = int(manifest.get("revision") or 0) + 1
        self.json_store.write_json(artifacts["manifest_object_key"], manifest)

    def _build_approved_staged_patch_result(
        self,
        *,
        workspace_id: str,
        script_data: dict[str, Any] | Any,
        accepted_scopes: list[str],
    ) -> dict[str, Any]:
        payload = script_data if isinstance(script_data, dict) else {}
        files = payload.get("changed_files")
        if not isinstance(files, list):
            files = payload.get("files") if isinstance(payload.get("files"), list) else []
        normalized_files: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = self._normalize_staged_path(str(item.get("path") or ""))
            if not path:
                raise AgentSystemError(
                    "skill_staged_patch_file_path_missing",
                    "Approved staged patch changed file is missing a path.",
                    409,
                )
            if not self._path_matches_scopes(path, accepted_scopes):
                raise AgentSystemError(
                    "skill_staged_patch_scope_violation",
                    "Approved staged patch attempted to write outside the approved scope.",
                    409,
                )
            normalized_files.append(
                {
                    "path": path,
                    "old_content": str(item.get("old_content") or item.get("before") or ""),
                    "new_content": str(
                        item.get("new_content")
                        or item.get("content")
                        or item.get("after")
                        or ""
                    ),
                }
            )

        patch_text = ""
        if isinstance(payload.get("patch"), str) and payload["patch"].strip():
            patch_text = str(payload["patch"])
        elif isinstance(payload.get("diff"), str) and payload["diff"].strip():
            patch_text = str(payload["diff"])
        elif normalized_files:
            patch_text = self._render_staged_patch(normalized_files)

        if patch_text and not normalized_files:
            normalized_files = self._staged_files_from_patch(
                workspace_id=workspace_id,
                patch_text=patch_text,
                accepted_scopes=accepted_scopes,
            )

        if not patch_text:
            return {
                "ok": False,
                "error_type": "skill_staged_patch_diff_missing",
                "changed_files": [],
                "diff_summary": {
                    "files_changed": 0,
                    "insertions": 0,
                    "deletions": 0,
                    "generated": False,
                },
                "patch": "",
                "staged_files": [],
            }

        changed_files = (
            [item["path"] for item in normalized_files]
            if normalized_files
            else self._extract_changed_files_from_patch(patch_text)
        )
        if not changed_files:
            return {
                "ok": False,
                "error_type": "skill_staged_patch_changed_files_missing",
                "changed_files": [],
                "diff_summary": {
                    "files_changed": 0,
                    "insertions": 0,
                    "deletions": 0,
                    "generated": False,
                },
                "patch": "",
                "staged_files": [],
            }
        if changed_files:
            for path in changed_files:
                if not self._path_matches_scopes(path, accepted_scopes):
                    raise AgentSystemError(
                        "skill_staged_patch_scope_violation",
                        "Approved staged patch attempted to write outside the approved scope.",
                        409,
                    )
        insertions = sum(
            1
            for line in patch_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1
            for line in patch_text.splitlines()
            if line.startswith("-") and not line.startswith("---")
        )
        return {
            "ok": True,
            "error_type": None,
            "changed_files": changed_files,
            "diff_summary": {
                "files_changed": len(changed_files),
                "insertions": insertions,
                "deletions": deletions,
                "generated": True,
            },
            "patch": patch_text,
            "staged_files": normalized_files,
        }

    @staticmethod
    def _render_staged_patch(files: list[dict[str, Any]]) -> str:
        chunks: list[str] = []
        for item in files:
            path = item["path"]
            before = str(item.get("old_content") or "")
            after = str(item.get("new_content") or "")
            diff = difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
            chunks.append("".join(diff))
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    @staticmethod
    def _extract_changed_files_from_patch(patch_text: str) -> list[str]:
        files: list[str] = []
        for line in patch_text.splitlines():
            if not line.startswith("+++ b/"):
                continue
            path = line.removeprefix("+++ b/").strip()
            if path and path not in files:
                files.append(path)
        return files

    def _staged_files_from_patch(
        self,
        *,
        workspace_id: str,
        patch_text: str,
        accepted_scopes: list[str],
    ) -> list[dict[str, Any]]:
        staged_files: list[dict[str, Any]] = []
        for file_patch in self._parse_unified_patch_files(patch_text):
            path = file_patch["path"]
            if not self._path_matches_scopes(path, accepted_scopes):
                raise AgentSystemError(
                    "skill_staged_patch_scope_violation",
                    "Approved staged patch attempted to write outside the approved scope.",
                    409,
            )
            object_key = workspace_file_object_key(workspace_id, path)
            old_content = ""
            if self.object_store.exists(object_key):
                old_content = self.object_store.read_text(object_key)
            new_content = self._apply_unified_patch_to_content(
                path=path,
                old_content=old_content,
                hunk_lines=file_patch["hunk_lines"],
            )
            staged_files.append(
                {
                    "path": path,
                    "old_content": old_content,
                    "new_content": new_content,
                }
            )
        return staged_files

    def _parse_unified_patch_files(self, patch_text: str) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        pending_old_path: str | None = None
        for line in patch_text.splitlines(keepends=True):
            if line.startswith("--- "):
                if current is not None:
                    files.append(current)
                    current = None
                pending_old_path = self._clean_patch_path(line[4:].strip())
                continue
            if line.startswith("+++ ") and pending_old_path is not None:
                new_path = self._clean_patch_path(line[4:].strip())
                if new_path == "/dev/null":
                    raise AgentSystemError(
                        "skill_staged_patch_delete_unsupported",
                        "Approved staged patch file deletion is not supported yet.",
                        409,
                    )
                path = new_path or pending_old_path
                if not path or path == "/dev/null":
                    raise AgentSystemError(
                        "skill_staged_patch_file_path_missing",
                        "Approved staged patch is missing a file path.",
                        409,
                    )
                current = {"path": self._normalize_staged_path(path), "hunk_lines": []}
                pending_old_path = None
                continue
            if current is not None:
                current["hunk_lines"].append(line)
        if current is not None:
            files.append(current)
        return [item for item in files if item["hunk_lines"]]

    @staticmethod
    def _clean_patch_path(value: str) -> str:
        path = value.split("\t", 1)[0].split(" ", 1)[0].strip()
        if path.startswith("a/") or path.startswith("b/"):
            return path[2:]
        return path

    def _apply_unified_patch_to_content(
        self,
        *,
        path: str,
        old_content: str,
        hunk_lines: list[str],
    ) -> str:
        old_lines = old_content.splitlines(keepends=True)
        old_index = 0
        new_lines: list[str] = []
        index = 0
        while index < len(hunk_lines):
            line = hunk_lines[index]
            if not line.startswith("@@"):
                index += 1
                continue
            match = re.match(
                r"@@ -(?P<old_start>\d+)(?:,\d+)? \+(?:\d+)(?:,\d+)? @@",
                line,
            )
            if not match:
                raise AgentSystemError(
                    "skill_staged_patch_hunk_invalid",
                    "Approved staged patch has an invalid hunk header.",
                    409,
                )
            target_index = max(int(match.group("old_start")) - 1, 0)
            if target_index < old_index:
                raise AgentSystemError(
                    "skill_staged_patch_hunk_overlap",
                    "Approved staged patch has overlapping hunks.",
                    409,
                )
            new_lines.extend(old_lines[old_index:target_index])
            old_index = target_index
            index += 1
            while index < len(hunk_lines) and not hunk_lines[index].startswith("@@"):
                hunk_line = hunk_lines[index]
                prefix = hunk_line[:1]
                content = hunk_line[1:]
                if prefix == " ":
                    self._assert_patch_source_line(path, old_lines, old_index, content)
                    new_lines.append(old_lines[old_index])
                    old_index += 1
                elif prefix == "-":
                    self._assert_patch_source_line(path, old_lines, old_index, content)
                    old_index += 1
                elif prefix == "+":
                    new_lines.append(content)
                elif prefix == "\\":
                    pass
                index += 1
        new_lines.extend(old_lines[old_index:])
        return "".join(new_lines)

    @staticmethod
    def _assert_patch_source_line(
        path: str,
        old_lines: list[str],
        old_index: int,
        expected: str,
    ) -> None:
        if old_index >= len(old_lines) or old_lines[old_index] != expected:
            raise AgentSystemError(
                "skill_staged_patch_apply_conflict",
                f"Approved staged patch no longer applies to {path}.",
                409,
            )

    def _path_matches_scopes(self, path: str, accepted_scopes: list[str]) -> bool:
        normalized_path = self._normalize_staged_path(path)
        for scope in accepted_scopes:
            normalized_scope = self._normalize_staged_path(scope)
            if self._staged_write_scope_rejection_reason(normalized_scope):
                continue
            if self._scope_allows_path(normalized_scope, normalized_path):
                return True
        return False

    @staticmethod
    def _normalize_staged_path(path: str) -> str:
        return path.replace("\\", "/").strip().removeprefix("./")

    @staticmethod
    def _scope_allows_path(scope: str, path: str) -> bool:
        if scope.endswith("/**"):
            prefix = scope[:-3].rstrip("/")
            return path == prefix or path.startswith(f"{prefix}/")
        if scope.endswith("/*"):
            prefix = scope[:-2].rstrip("/")
            if not (path == prefix or path.startswith(f"{prefix}/")):
                return False
            remaining = path[len(prefix) :].lstrip("/")
            return "/" not in remaining if remaining else True
        return fnmatch.fnmatch(path, scope)

    @classmethod
    def _staged_write_scope_summary(
        cls,
        entrypoint: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        raw_scopes = [
            *list(entrypoint.get("file_write") or []),
            *list((manifest.get("permissions") or {}).get("file_write") or []),
        ]
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        for raw_scope in raw_scopes:
            scope = str(raw_scope).strip()
            reason = cls._staged_write_scope_rejection_reason(scope)
            if reason:
                rejected.append({"scope": str(redact_runtime_value(scope)), "reason": reason})
                continue
            if scope not in accepted:
                accepted.append(scope)
        return {
            "accepted": accepted,
            "rejected": rejected,
            "entrypoint_file_write": redact_runtime_value(entrypoint.get("file_write") or []),
            "skill_file_write": redact_runtime_value(
                (manifest.get("permissions") or {}).get("file_write") or []
            ),
        }

    @staticmethod
    def _staged_write_scope_rejection_reason(scope: str) -> str | None:
        if not scope:
            return "empty_scope"
        normalized = scope.replace("\\", "/")
        if "\x00" in scope or any(ord(char) < 32 for char in scope):
            return "control_character"
        if normalized.startswith("/") or normalized.startswith("//"):
            return "absolute_path"
        if re.match(r"^[A-Za-z]:", scope):
            return "windows_drive_path"
        if any(part == ".." for part in normalized.split("/")):
            return "path_traversal"
        return None

    def _ensure_stream_artifacts(self, artifacts: dict[str, str]) -> None:
        for key_name in ("stdout_object_key", "stderr_object_key"):
            object_key = artifacts.get(key_name)
            if object_key and not self.object_store.exists(object_key):
                self.object_store.write_text(object_key, "")

    def _finish_skill_run(
        self,
        artifacts: dict[str, str],
        *,
        result: dict[str, Any],
        status: str,
        finished_at: str,
    ) -> None:
        manifest = self.json_store.read_json(artifacts["manifest_object_key"])
        manifest["status"] = status
        manifest["finished_at"] = finished_at
        self.json_store.write_json(artifacts["manifest_object_key"], manifest)
        self.json_store.write_json(
            artifacts["result_object_key"],
            redact_runtime_value(result),
        )

    @staticmethod
    def _skill_entrypoint_result(
        *,
        ok: bool,
        error_type: str | None,
        message_for_model: str,
        skill_run_id: str,
        artifacts: dict[str, str],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return redact_runtime_value(
            {
                "ok": ok,
                "error_type": error_type,
                "retryable": False,
                "message_for_model": message_for_model,
                "skill_run_id": skill_run_id,
                "artifacts": artifacts,
                "data": data,
            }
        )

    def _append_run_event(
        self,
        *,
        workspace_id: str,
        thread_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        current = self.json_store.read_json_or_default(
            run_event_index_key(workspace_id, run_id),
            {
                "schema_version": 1,
                "stream_id": run_id,
                "segments": [],
                "event_count": 0,
                "last_event_seq": 0,
                "last_event_id": None,
                "revision": 0,
            },
        )
        event_seq = int(current.get("last_event_seq") or 0) + 1
        event = {
            "schema_version": 1,
            "event_seq": event_seq,
            "event_id": f"evt_{run_id}_{event_seq:012d}",
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "run_id": run_id,
            "type": event_type,
            "created_at": utc_now_iso(),
            "payload": redact_runtime_value(payload),
        }
        store = JsonlSegmentStore(self.object_store, run_events_prefix(workspace_id, run_id))
        store.append(event)
        index = store.rebuild_event_index(run_event_index_key(workspace_id, run_id), run_id)
        run_key = run_manifest_key(workspace_id, run_id)
        run = self.json_store.read_json(run_key)
        run["last_event_id"] = index["last_event_id"]
        run["last_event_seq"] = index["last_event_seq"]
        run["updated_at"] = utc_now_iso()
        run["revision"] = int(run.get("revision") or 0) + 1
        self.json_store.write_json(run_key, run)

    def _skill_index(self, workspace_id: str) -> dict[str, Any]:
        return self.json_store.read_json_or_default(
            skill_index_key(workspace_id),
            {
                "schema_version": 1,
                "workspace_id": workspace_id,
                "skills": [],
                "revision": 0,
            },
        )

    def _upsert_skill_index(self, workspace_id: str, manifest: dict[str, Any]) -> None:
        index = self._skill_index(workspace_id)
        summary = self._public_summary(manifest)
        index["skills"] = [
            item for item in index.get("skills", []) if item["skill_id"] != manifest["skill_id"]
        ]
        index["skills"].append(summary)
        index["skills"] = sorted(index["skills"], key=lambda item: item["updated_at"], reverse=True)
        index["revision"] = int(index.get("revision") or 0) + 1
        self.json_store.write_json(skill_index_key(workspace_id), index)

    def _latest_version(self, workspace_id: str, skill_id: str) -> str:
        key = skill_latest_key(workspace_id, skill_id)
        if not self.object_store.exists(key):
            raise AgentSystemError("skill_not_found", "Skill was not found.", 404)
        return str(self.json_store.read_json(key)["version"])

    def _materialize_entrypoints(
        self,
        workspace_id: str,
        skill_id: str,
        version: str,
        entrypoints: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        materialized: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        for entrypoint in entrypoints:
            item = {key: value for key, value in entrypoint.items() if key != "script_content"}
            item["tool_name_when_activated"] = self._entrypoint_tool_name(skill_id, item["name"])
            script_content = entrypoint.get("script_content")
            if script_content:
                script_key = skill_script_key(workspace_id, skill_id, version, item["name"])
                self.object_store.write_text(script_key, str(script_content))
                checksum = self._sha256(str(script_content))
                item["sandbox_profile"] = item.get("sandbox_profile") or "skill_script_readonly"
                item["script_object_key"] = script_key
                item["script_checksum"] = f"sha256:{checksum}"
                artifacts.append(
                    {
                        "entrypoint": item["name"],
                        "script_object_key": script_key,
                        "script_checksum": f"sha256:{checksum}",
                    }
                )
            materialized.append(item)
        return materialized, artifacts

    @staticmethod
    def _proposal_entrypoint(entrypoint: SkillEntrypointSpec) -> dict[str, Any]:
        item = entrypoint.model_dump()
        item["script_required"] = entrypoint.script_required or entrypoint.type in SCRIPT_RISK_TYPES
        return redact_runtime_value(item)

    @staticmethod
    def _risk_level(entrypoints: list[SkillEntrypointSpec], script_required: bool) -> str:
        risk = "medium" if script_required else "low"
        for entrypoint in entrypoints:
            if entrypoint.type in SCRIPT_RISK_TYPES or entrypoint.script_required:
                risk = "high"
            if RISK_ORDER[entrypoint.risk_level] > RISK_ORDER[risk]:
                risk = entrypoint.risk_level
        return risk

    @staticmethod
    def _is_script_entrypoint(entrypoint: SkillEntrypointSpec) -> bool:
        return entrypoint.type in SCRIPT_RISK_TYPES or entrypoint.script_required

    @staticmethod
    def _public_summary(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "skill_id": manifest["skill_id"],
            "workspace_id": manifest["workspace_id"],
            "display_name": manifest["display_name"],
            "version": manifest["version"],
            "description": manifest["description"],
            "when_to_use": manifest.get("when_to_use") or [],
            "entrypoint_count": len(manifest.get("entrypoints") or []),
            "risk_level": manifest.get("risk_level") or "low",
            "status": manifest.get("status") or "enabled",
            "enabled": manifest.get("status") == "enabled",
            "requires_activation": bool(manifest.get("requires_activation", True)),
            "requires_validation": bool(manifest.get("requires_validation")),
            "updated_at": manifest["updated_at"],
        }

    @staticmethod
    def _public_detail(manifest: dict[str, Any]) -> dict[str, Any]:
        entrypoints = []
        for entrypoint in manifest.get("entrypoints") or []:
            entrypoints.append(
                {
                    "name": entrypoint["name"],
                    "type": entrypoint.get("type") or "prompt_workflow",
                    "tool_name_when_activated": entrypoint.get("tool_name_when_activated"),
                    "risk_level": entrypoint.get("risk_level") or "low",
                    "requires_approval": entrypoint.get("write_mode") == "staged_patch",
                    "args_schema_summary": entrypoint.get("args_schema") or {},
                    "sandbox_profile": entrypoint.get("sandbox_profile"),
                    "write_mode": entrypoint.get("write_mode") or "none",
                    "script_checksum": entrypoint.get("script_checksum"),
                }
            )
        return {
            "schema_version": manifest.get("schema_version", 1),
            "skill_id": manifest["skill_id"],
            "workspace_id": manifest["workspace_id"],
            "version": manifest["version"],
            "display_name": manifest["display_name"],
            "description": manifest["description"],
            "summary": manifest["description"],
            "workflow_summary": manifest.get("workflow_steps") or [],
            "knowledge_sections": [
                {
                    "section_id": "notes",
                    "title": "Knowledge notes",
                    "token_estimate": max(
                        1,
                        len(" ".join(manifest.get("knowledge_notes") or [])) // 4,
                    ),
                }
            ]
            if manifest.get("knowledge_notes")
            else [],
            "entrypoints": entrypoints,
            "permissions": manifest.get("permissions") or {},
            "status": manifest.get("status") or "enabled",
            "enabled": manifest.get("status") == "enabled",
            "risk_level": manifest.get("risk_level") or "low",
            "requires_activation": bool(manifest.get("requires_activation", True)),
            "requires_validation": bool(manifest.get("requires_validation")),
            "validation_status": manifest.get("validation_status") or "unknown",
            "created_at": manifest["created_at"],
            "updated_at": manifest["updated_at"],
        }

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip().lower()).strip("_.-")
        if not slug or not slug[0].isalnum():
            slug = f"skill_{new_id('generated')}"
        return slug[:120]

    @staticmethod
    def _entrypoint_tool_name(skill_id: str, entrypoint: str) -> str:
        safe_skill = re.sub(r"[^A-Za-z0-9_]+", "_", skill_id)
        safe_entrypoint = re.sub(r"[^A-Za-z0-9_]+", "_", entrypoint)
        return f"skill_{safe_skill}_{safe_entrypoint}"

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
