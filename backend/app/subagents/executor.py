from __future__ import annotations

from typing import Any, TypedDict

from app.core.errors import AgentSystemError
from app.runtime.tools import redact_runtime_value
from app.storage.object_store import ObjectStore

EXECUTOR_NAME = "langgraph_local_subagent_executor"


class SubAgentExecutionState(TypedDict, total=False):
    manifest: dict[str, Any]
    scoped_keys: list[str]
    findings: list[dict[str, Any]]
    risks: list[str]
    open_questions: list[str]
    summary: str
    execution: dict[str, Any]


class SubAgentExecutor:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store
        try:
            from langgraph.graph import END, START, StateGraph
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("langgraph"):
                raise AgentSystemError(
                    "subagent_graph_dependency_missing",
                    "SubAgent LangGraph dependency is not installed.",
                    status_code=503,
                    retryable=False,
                    details={"missing_module": exc.name},
                ) from exc
            raise
        graph = StateGraph(SubAgentExecutionState)
        graph.add_node("collect_scope_evidence", self._collect_scope_evidence)
        graph.add_node("analyze_objective", self._analyze_objective)
        graph.add_node("finalize_result", self._finalize_result)
        graph.add_edge(START, "collect_scope_evidence")
        graph.add_edge("collect_scope_evidence", "analyze_objective")
        graph.add_edge("analyze_objective", "finalize_result")
        graph.add_edge("finalize_result", END)
        self.graph = graph.compile()

    def execute(self, manifest: dict[str, Any]) -> dict[str, Any]:
        final_state = self.graph.invoke(
            {
                "manifest": redact_runtime_value(manifest),
                "scoped_keys": [],
                "findings": [],
                "risks": [],
                "open_questions": [],
                "execution": {
                    "executor": EXECUTOR_NAME,
                    "steps": [],
                },
            }
        )
        return {
            "status": "completed",
            "summary": final_state["summary"],
            "findings": final_state["findings"],
            "changed_files": [],
            "risks": final_state["risks"],
            "open_questions": final_state["open_questions"],
            "execution": final_state["execution"],
            "evidence": {
                "scoped_object_keys": final_state["scoped_keys"][:20],
                "scoped_object_key_count": len(final_state["scoped_keys"]),
            },
        }

    def _collect_scope_evidence(
        self,
        state: SubAgentExecutionState,
    ) -> SubAgentExecutionState:
        manifest = state["manifest"]
        scoped_keys: list[str] = []
        for scope in manifest.get("read_scope") or []:
            prefix = str(scope).replace("\\", "/").strip("/")
            if not prefix:
                continue
            scoped_keys.extend(_list_keys(self.object_store, prefix))
        scoped_keys = sorted(dict.fromkeys(scoped_keys))
        return {
            **state,
            "scoped_keys": scoped_keys,
            "execution": _append_step(
                state,
                "collect_scope_evidence",
                {
                    "read_scope_count": len(manifest.get("read_scope") or []),
                    "key_count": len(scoped_keys),
                },
            ),
        }

    def _analyze_objective(self, state: SubAgentExecutionState) -> SubAgentExecutionState:
        manifest = state["manifest"]
        agent_type = str(manifest.get("agent_type") or "subagent")
        findings = [
            {
                "severity": "info",
                "title": f"{agent_type} executed scoped task",
                "evidence": (
                    f"Objective was evaluated with {len(state['scoped_keys'])} "
                    "object-store keys visible inside declared read_scope."
                ),
                "recommendation": (
                    "Main Agent must review this result before using it in a user-facing answer."
                ),
            }
        ]
        allowed = {str(item) for item in manifest.get("allowed_tools") or []}
        forbidden = {str(item) for item in manifest.get("forbidden_tools") or []}
        overlap = sorted(allowed.intersection(forbidden))
        risks: list[str] = []
        open_questions: list[str] = []
        if overlap:
            risks.append(f"allowed_tools overlaps forbidden_tools: {', '.join(overlap)}")
        if manifest.get("mode") == "write":
            findings.append(
                {
                    "severity": "warning",
                    "title": "Write-mode SubAgent requires approval before mutation",
                    "evidence": (
                        "The executor observed declared write_scope but did not modify "
                        "project artifacts directly."
                    ),
                    "recommendation": (
                        "Route any proposed write through Hook/approval and main review."
                    ),
                }
            )
        if not state["scoped_keys"]:
            open_questions.append(
                "No object-store evidence matched read_scope; verify the scope points to "
                "persisted runtime artifacts."
            )
        role_findings = _role_findings(agent_type, state["scoped_keys"])
        return {
            **state,
            "findings": [*findings, *role_findings],
            "risks": risks,
            "open_questions": open_questions,
            "execution": _append_step(
                state,
                "analyze_objective",
                {"finding_count": len(findings) + len(role_findings), "risk_count": len(risks)},
            ),
        }

    def _finalize_result(self, state: SubAgentExecutionState) -> SubAgentExecutionState:
        manifest = state["manifest"]
        summary = (
            f"{manifest.get('agent_type')} executed via {EXECUTOR_NAME}; "
            f"{len(state['findings'])} findings returned for main Agent review."
        )
        return {
            **state,
            "summary": summary,
            "execution": _append_step(
                state,
                "finalize_result",
                {
                    "needs_main_review": True,
                    "can_directly_finalize": False,
                },
            ),
        }


def _list_keys(object_store: ObjectStore, prefix: str) -> list[str]:
    if hasattr(object_store, "list_keys"):
        try:
            return list(object_store.list_keys(prefix))
        except Exception:  # noqa: BLE001 - SubAgent evidence collection is best effort.
            return []
    return []


def _append_step(
    state: SubAgentExecutionState,
    name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    execution = dict(state.get("execution") or {"executor": EXECUTOR_NAME, "steps": []})
    steps = list(execution.get("steps") or [])
    steps.append({"name": name, "payload": redact_runtime_value(payload)})
    execution["steps"] = steps
    return execution


def _role_findings(agent_type: str, scoped_keys: list[str]) -> list[dict[str, str]]:
    if agent_type == "log_analyst":
        log_keys = [
            key
            for key in scoped_keys
            if any(part in key for part in ("logs", "events", "operations"))
        ]
        return [
            {
                "severity": "info",
                "title": "Log evidence inventory collected",
                "evidence": f"{len(log_keys)} log/event/operation keys matched declared scope.",
                "recommendation": "Use these keys as supporting evidence during main review.",
            }
        ]
    if agent_type == "database_checker":
        database_keys = [key for key in scoped_keys if "database/" in key]
        return [
            {
                "severity": "info",
                "title": "Database evidence inventory collected",
                "evidence": f"{len(database_keys)} database keys matched declared scope.",
                "recommendation": (
                    "Run database_health_check_job for live connectivity evidence when needed."
                ),
            }
        ]
    if agent_type == "researcher":
        return [
            {
                "severity": "info",
                "title": "Research scope inventory collected",
                "evidence": f"{len(scoped_keys)} scoped keys are available for synthesis.",
                "recommendation": "Cite scoped artifact keys in the main Agent review.",
            }
        ]
    return [
        {
            "severity": "info",
            "title": "Code review scope inventory collected",
            "evidence": f"{len(scoped_keys)} scoped keys are available for review.",
            "recommendation": "Treat this as supporting evidence, not as release approval.",
        }
    ]
