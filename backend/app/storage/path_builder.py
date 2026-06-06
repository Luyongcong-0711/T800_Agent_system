from __future__ import annotations

import hashlib
import re

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _safe_id(name: str, value: str) -> str:
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid {name}: path identifiers may not contain slashes or traversal.")
    return value


def _safe_file_path(name: str, value: str) -> str:
    normalized = value.replace("\\", "/").strip().removeprefix("./")
    if not normalized or normalized.startswith("/") or normalized.startswith("//"):
        raise ValueError(f"Invalid {name}: file path must be relative.")
    if re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"Invalid {name}: file path must not contain a drive prefix.")
    parts = [part for part in normalized.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"Invalid {name}: file path identifiers may not contain traversal.")
    return "/".join(_safe_id(f"{name}_part", part) for part in parts)


def _file_path_hash(value: str) -> str:
    normalized = value.replace("\\", "/").strip().removeprefix("./")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def workspace_prefix(workspace_id: str) -> str:
    return f"workspaces/{_safe_id('workspace_id', workspace_id)}"


def secret_object_key(workspace_id: str, secret_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/secrets/{_safe_id('secret_id', secret_id)}.json"


def secrets_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/indexes/secrets_index.json"


def secret_audit_prefix(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/secrets/audit"


def threads_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/indexes/threads_index.json"


def workspace_runs_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/indexes/runs_index.json"


def thread_prefix(workspace_id: str, thread_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/sessions/{_safe_id('thread_id', thread_id)}"


def thread_manifest_key(workspace_id: str, thread_id: str) -> str:
    return f"{thread_prefix(workspace_id, thread_id)}/manifest.json"


def thread_messages_prefix(workspace_id: str, thread_id: str) -> str:
    return f"{thread_prefix(workspace_id, thread_id)}/messages"


def thread_message_index_key(workspace_id: str, thread_id: str) -> str:
    return f"{thread_messages_prefix(workspace_id, thread_id)}/message_index.json"


def thread_runs_index_key(workspace_id: str, thread_id: str) -> str:
    return f"{thread_prefix(workspace_id, thread_id)}/runs_index.json"


def run_prefix(workspace_id: str, run_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/runs/{_safe_id('run_id', run_id)}"


def run_manifest_key(workspace_id: str, run_id: str) -> str:
    return f"{run_prefix(workspace_id, run_id)}/manifest.json"


def run_events_prefix(workspace_id: str, run_id: str) -> str:
    return f"{run_prefix(workspace_id, run_id)}/events"


def run_event_index_key(workspace_id: str, run_id: str) -> str:
    return f"{run_prefix(workspace_id, run_id)}/event_index.json"


def run_operations_prefix(workspace_id: str, run_id: str) -> str:
    return f"{run_prefix(workspace_id, run_id)}/operations"


def run_operation_backups_prefix(
    workspace_id: str,
    run_id: str,
    operation_id: str,
) -> str:
    run_hash = hashlib.sha256(_safe_id("run_id", run_id).encode("utf-8")).hexdigest()[:8]
    return (
        f"{workspace_prefix(workspace_id)}/op_b/{run_hash}/"
        f"{_safe_id('operation_id', operation_id)}"
    )


def run_leaf_state_key(workspace_id: str, run_id: str) -> str:
    return f"{run_prefix(workspace_id, run_id)}/leaf_state.json"


def workspace_jobs_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/indexes/jobs_index.json"


def workspace_file_object_key(workspace_id: str, relative_path: str) -> str:
    return (
        f"{workspace_prefix(workspace_id)}/workspace_files/"
        f"{_safe_file_path('file_path', relative_path)}"
    )


def workspace_file_backup_key(
    workspace_id: str,
    run_id: str,
    operation_id: str,
    relative_path: str,
) -> str:
    return (
        f"{run_operation_backups_prefix(workspace_id, run_id, operation_id)}/"
        f"{_file_path_hash(relative_path)[:24]}.json"
    )


def job_prefix(workspace_id: str, job_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/jobs/{_safe_id('job_id', job_id)}"


def job_manifest_key(workspace_id: str, job_id: str) -> str:
    return f"{job_prefix(workspace_id, job_id)}/manifest.json"


def job_events_prefix(workspace_id: str, job_id: str) -> str:
    return f"{job_prefix(workspace_id, job_id)}/events"


def job_event_index_key(workspace_id: str, job_id: str) -> str:
    return f"{job_prefix(workspace_id, job_id)}/event_index.json"


def job_leaf_state_key(workspace_id: str, job_id: str) -> str:
    return f"{job_prefix(workspace_id, job_id)}/leaf_state.json"


def job_errors_key(workspace_id: str, job_id: str) -> str:
    return f"{job_prefix(workspace_id, job_id)}/errors.jsonl"


def database_config_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/database/config.json"


def database_health_snapshot_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/database/health/latest.json"


def model_config_key(workspace_id: str, config_id: str) -> str:
    return (
        f"{workspace_prefix(workspace_id)}/model_configs/"
        f"{_safe_id('config_id', config_id)}.json"
    )


def mcp_servers_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/indexes/mcp_servers_index.json"


def mcp_server_prefix(workspace_id: str, server_name: str) -> str:
    return f"{workspace_prefix(workspace_id)}/mcp/servers/{_safe_id('server_name', server_name)}"


def mcp_server_manifest_key(workspace_id: str, server_name: str) -> str:
    return f"{mcp_server_prefix(workspace_id, server_name)}/manifest.json"


def mcp_capability_snapshot_key(workspace_id: str, server_name: str) -> str:
    return f"{mcp_server_prefix(workspace_id, server_name)}/capability_snapshot.json"


def user_prefix(user_id: str) -> str:
    return f"users/{_safe_id('user_id', user_id)}"


def user_memory_index_key(user_id: str) -> str:
    return f"{user_prefix(user_id)}/memory/index.json"


def user_memory_object_key(user_id: str, memory_id: str) -> str:
    return f"{user_prefix(user_id)}/memory/{_safe_id('memory_id', memory_id)}.json"


def user_disabled_memory_patterns_key(user_id: str) -> str:
    return f"{user_prefix(user_id)}/memory/disabled_patterns.json"


def workspace_memory_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/indexes/memory_index.json"


def workspace_memory_object_key(workspace_id: str, memory_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/memory/{_safe_id('memory_id', memory_id)}.json"


def workspace_disabled_memory_patterns_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/memory/disabled_patterns.json"


def memory_snapshot_key(workspace_id: str, snapshot_id: str) -> str:
    return (
        f"{workspace_prefix(workspace_id)}/memory_snapshots/"
        f"{_safe_id('snapshot_id', snapshot_id)}.json"
    )


def memory_sync_events_prefix(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/memory/sync/events"


def memory_sync_event_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/memory/sync/event_index.json"


def memory_sync_state_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/memory/sync/state.json"


def thread_compaction_key(workspace_id: str, thread_id: str, compaction_id: str) -> str:
    return (
        f"{thread_prefix(workspace_id, thread_id)}/compactions/"
        f"{_safe_id('compaction_id', compaction_id)}.json"
    )


def thread_compactions_prefix(workspace_id: str, thread_id: str) -> str:
    return f"{thread_prefix(workspace_id, thread_id)}/compactions"


def thread_compaction_latest_key(workspace_id: str, thread_id: str) -> str:
    return f"{thread_compactions_prefix(workspace_id, thread_id)}/latest.json"


def thread_compaction_lock_key(workspace_id: str, thread_id: str) -> str:
    return f"{thread_compactions_prefix(workspace_id, thread_id)}/lock.json"


def skill_workspace_prefix(workspace_id: str) -> str:
    return f"skills/{_safe_id('workspace_id', workspace_id)}"


def skill_index_key(workspace_id: str) -> str:
    return f"{skill_workspace_prefix(workspace_id)}/skill_index.json"


def skill_proposal_key(workspace_id: str, proposal_id: str) -> str:
    return (
        f"{skill_workspace_prefix(workspace_id)}/proposals/"
        f"{_safe_id('proposal_id', proposal_id)}.json"
    )


def skill_prefix(workspace_id: str, skill_id: str, version: str) -> str:
    return (
        f"{skill_workspace_prefix(workspace_id)}/{_safe_id('skill_id', skill_id)}/"
        f"{_safe_id('version', version)}"
    )


def skill_manifest_key(workspace_id: str, skill_id: str, version: str) -> str:
    return f"{skill_prefix(workspace_id, skill_id, version)}/skill.yaml"


def skill_latest_key(workspace_id: str, skill_id: str) -> str:
    return f"{skill_workspace_prefix(workspace_id)}/{_safe_id('skill_id', skill_id)}/latest.json"


def skill_script_key(workspace_id: str, skill_id: str, version: str, script_name: str) -> str:
    return (
        f"{skill_prefix(workspace_id, skill_id, version)}/scripts/"
        f"{_safe_id('script_name', script_name)}.py"
    )


def run_skill_context_key(workspace_id: str, run_id: str, skill_id: str) -> str:
    return (
        f"{run_prefix(workspace_id, run_id)}/skills/"
        f"{_safe_id('skill_id', skill_id)}/context_block.json"
    )


def run_skill_run_prefix(workspace_id: str, run_id: str, skill_run_id: str) -> str:
    return (
        f"{run_prefix(workspace_id, run_id)}/skill_runs/"
        f"{_safe_id('skill_run_id', skill_run_id)}"
    )


def run_skill_run_artifact_key(
    workspace_id: str,
    run_id: str,
    skill_run_id: str,
    file_name: str,
) -> str:
    return (
        f"{run_skill_run_prefix(workspace_id, run_id, skill_run_id)}/"
        f"{_safe_id('file_name', file_name)}"
    )


def subagent_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/indexes/subagent_tasks_index.json"


def subagent_task_prefix(workspace_id: str, task_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/subagents/tasks/{_safe_id('task_id', task_id)}"


def subagent_task_manifest_key(workspace_id: str, task_id: str) -> str:
    return f"{subagent_task_prefix(workspace_id, task_id)}/manifest.json"


def subagent_task_result_key(workspace_id: str, task_id: str) -> str:
    return f"{subagent_task_prefix(workspace_id, task_id)}/result.json"


def subagent_task_review_key(workspace_id: str, task_id: str) -> str:
    return f"{subagent_task_prefix(workspace_id, task_id)}/review.json"


def system_logs_prefix(date: str, runtime_instance_id: str) -> str:
    return (
        f"system/logs/{_safe_id('date', date)}/"
        f"{_safe_id('runtime_instance_id', runtime_instance_id)}"
    )


def system_summary_log_key(date: str, runtime_instance_id: str) -> str:
    return f"{system_logs_prefix(date, runtime_instance_id)}/system_summary/part-000001.log"


def system_full_logs_prefix(date: str, runtime_instance_id: str) -> str:
    return f"{system_logs_prefix(date, runtime_instance_id)}/system_full"


def system_error_logs_prefix(date: str, runtime_instance_id: str) -> str:
    return f"{system_logs_prefix(date, runtime_instance_id)}/errors"


def system_component_logs_prefix(date: str, runtime_instance_id: str, component: str) -> str:
    return (
        f"{system_logs_prefix(date, runtime_instance_id)}/components/"
        f"{_safe_id('component', component)}"
    )


def log_archive_prefix(date: str, runtime_instance_id: str) -> str:
    return f"{system_logs_prefix(date, runtime_instance_id)}/log_archives"


def log_archive_manifest_key(date: str, runtime_instance_id: str) -> str:
    return f"{log_archive_prefix(date, runtime_instance_id)}/manifest.json"


def log_archive_object_key(
    date: str,
    runtime_instance_id: str,
    archive_id: str,
    file_name: str,
) -> str:
    return (
        f"{log_archive_prefix(date, runtime_instance_id)}/files/"
        f"{_safe_id('archive_id', archive_id)}/{_safe_id('file_name', file_name)}"
    )


def diagnostic_bundle_prefix(date: str, runtime_instance_id: str, bundle_id: str) -> str:
    return (
        f"{system_logs_prefix(date, runtime_instance_id)}/diagnostic_bundles/"
        f"{_safe_id('bundle_id', bundle_id)}"
    )


def diagnostic_bundle_manifest_key(date: str, runtime_instance_id: str, bundle_id: str) -> str:
    return f"{diagnostic_bundle_prefix(date, runtime_instance_id, bundle_id)}/manifest.json"


def diagnostic_bundle_payload_key(date: str, runtime_instance_id: str, bundle_id: str) -> str:
    return f"{diagnostic_bundle_prefix(date, runtime_instance_id, bundle_id)}/bundle.json"


def diagnostic_bundle_package_key(date: str, runtime_instance_id: str, bundle_id: str) -> str:
    return f"{diagnostic_bundle_prefix(date, runtime_instance_id, bundle_id)}/bundle.zip"
