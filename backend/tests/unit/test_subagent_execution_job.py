from __future__ import annotations

from app.jobs.handlers import build_subagent_execution_handler
from app.jobs.service import JobService
from app.jobs.worker import JobWorker
from app.storage.local_object_store import LocalObjectStore
from app.tools.builtin.subagent_tools import build_default_subagent_tools


def test_subagent_tool_can_queue_long_running_task_and_worker_executes_it(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    object_store.write_text(
        "workspaces/default/runs/run_parent_001/events/segment_000001.jsonl",
        "",
    )
    object_store.write_text("logs/system_full/example.log", "SubAgent job evidence")
    tools = {tool.name: tool for tool in build_default_subagent_tools(object_store)}

    queued = tools["call_subagent_log_analyst"].invoke(
        {
            "workspace_id": "default",
            "user_id": "default_user",
            "parent_run_id": "run_parent_001",
            "parent_thread_id": "thread_parent_001",
            "objective": "Analyze logs as a long-running task.",
            "mode": "readonly",
            "read_scope": ["logs"],
            "write_scope": [],
            "allowed_tools": ["read_file", "search_files"],
            "forbidden_tools": ["write_file", "apply_patch", "exec"],
            "timeout_ms": 1200000,
            "token_budget": 12000,
            "expected_output": "Findings with evidence.",
            "execution_mode": "job",
        }
    )

    data = queued["data"]
    assert queued["ok"] is True
    assert data["status"] == "queued"
    assert data["created_job_id"]
    assert data["needs_main_review"] is True
    assert data["can_directly_finalize"] is False

    job_service = JobService(object_store, runtime_instance_id="rt_worker")
    worker = JobWorker(
        job_service,
        {"subagent_execution_job": build_subagent_execution_handler(object_store)},
    )
    processed = worker.process_next("default", job_types=["subagent_execution_job"])

    assert processed["claimed"] is True
    assert processed["job"]["job_id"] == data["created_job_id"]
    assert processed["job"]["status"] == "succeeded"
    task = object_store.read_text(
        f"workspaces/default/subagents/tasks/{data['task_id']}/result.json"
    )
    assert "langgraph_local_subagent_executor" in task
    leaf_state = object_store.read_text("workspaces/default/runs/run_parent_001/leaf_state.json")
    assert data["task_id"] in leaf_state
