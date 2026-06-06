from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest

MODEL_VISIBLE_GRAPH_TOOLS = {
    "graph_schema_get",
    "graph_entity_search",
    "graph_expand_entity",
    "graph_find_relationship",
    "graph_find_paths",
    "graph_get_evidence",
    "graph_timeline_query",
    "graph_readonly_cypher",
    "graphrag_search",
}

FORBIDDEN_MODEL_GRAPH_TERMS = {
    "write",
    "upsert",
    "update",
    "delete",
    "remove",
    "drop",
    "merge",
    "create",
    "rebuild",
    "driver",
    "session",
    "write_graph_batch",
    "mergestatement",
    "neo4j_driver",
}


class _ResolvedEntity:
    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        self.is_ambiguous = False
        self.candidates: list[dict[str, Any]] = []

    def summary(self) -> dict[str, str]:
        return {"entity_id": self.entity_id, "name": self.entity_id}


class _FakeGraphQuery:
    def __init__(self) -> None:
        self.find_paths_calls: list[dict[str, Any]] = []

    def resolve_entity(self, *args: Any, **kwargs: Any) -> _ResolvedEntity:
        value = args[2] if len(args) >= 3 else kwargs.get("name_or_id") or kwargs.get("entity")
        return _ResolvedEntity(str(value))

    def resolve_entity_id_or_fail(
        self,
        entity_id: str,
        *_args: Any,
        **_kwargs: Any,
    ) -> _ResolvedEntity:
        return _ResolvedEntity(entity_id)

    def find_paths(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.find_paths_calls.append(kwargs)
        return [
            {
                "path_id": "path_001",
                "depth": kwargs["max_depth"],
                "nodes": [{"entity_id": "ent_a"}, {"entity_id": "ent_b"}],
                "relationships": [
                    {
                        "type": "PARTICIPATED_IN",
                        "direction": "outgoing",
                        "fact_id": "fact_001",
                        "evidence_ids": ["ev_001"],
                    }
                ],
            }
        ]


class _FakeAuditLog:
    def write_tool_event(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _graph_tools_module() -> Any:
    candidates = (
        "app.tools.builtin.graph_tools",
        "app.graph_pipeline.tools",
        "app.runtime.graph_tools",
    )
    for module_name in candidates:
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    pytest.fail(
        "Phase H requires model-callable readonly graph tools in "
        "app.tools.builtin.graph_tools or an equivalent tested module."
    )


def _make_tool(module: Any, builder_name: str, tool_name: str, **deps: Any) -> Any:
    builder = getattr(module, builder_name, None)
    if builder is not None:
        signature = inspect.signature(builder)
        kwargs = {name: value for name, value in deps.items() if name in signature.parameters}
        return builder(**kwargs)
    tool = getattr(module, tool_name, None)
    if tool is None:
        pytest.fail(f"Phase H graph contract requires {builder_name}() or {tool_name}.")
    return tool


def _invoke(tool: Any, args: dict[str, Any]) -> dict[str, Any]:
    if hasattr(tool, "invoke"):
        result = tool.invoke(args)
    else:
        result = tool(**args)
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    assert isinstance(result, dict)
    return result


def _data(result: dict[str, Any]) -> dict[str, Any]:
    assert result.get("ok", True) is True
    data = result.get("data", result)
    assert isinstance(data, dict)
    return data


def test_graph_find_paths_clamps_model_requested_depth_to_two() -> None:
    module = _graph_tools_module()
    graph_query = _FakeGraphQuery()
    tool = _make_tool(
        module,
        "build_graph_find_paths_tool",
        "graph_find_paths_tool",
        graph_query=graph_query,
        audit_log=_FakeAuditLog(),
    )

    result = _invoke(
        tool,
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "source_entity": "ent_a",
            "target_entity": "ent_b",
            "max_depth": 99,
            "relationship_allowlist": ["PARTICIPATED_IN"],
            "limit": 10,
        },
    )

    data = _data(result)
    assert graph_query.find_paths_calls
    assert graph_query.find_paths_calls[0]["max_depth"] == 2
    assert all(path["depth"] <= 2 for path in data["paths"])


def test_runtime_model_visible_graph_tools_are_readonly_only(tmp_path) -> None:
    from app.runtime.tools import ToolRegistry, build_default_tool_registry
    from app.storage.local_object_store import LocalObjectStore

    module = _graph_tools_module()
    graph_query = _FakeGraphQuery()
    graph_tools = []
    for builder_name, tool_name in [
        ("build_graph_find_paths_tool", "graph_find_paths_tool"),
    ]:
        tool = _make_tool(
            module,
            builder_name,
            tool_name,
            graph_query=graph_query,
            audit_log=_FakeAuditLog(),
        )
        graph_tools.append(tool)

    default_registry = build_default_tool_registry(LocalObjectStore(tmp_path / "objects"))
    missing_graph_tools = [
        tool for tool in graph_tools if tool.name not in default_registry._tools
    ]
    registry = ToolRegistry([*default_registry._tools.values(), *missing_graph_tools])
    specs = registry.model_safe_specs()
    graph_names = {spec["name"] for spec in specs if spec["name"].startswith("graph_")}

    assert graph_names
    assert graph_names <= MODEL_VISIBLE_GRAPH_TOOLS
    for name in graph_names:
        lowered = name.lower()
        assert not any(term in lowered for term in FORBIDDEN_MODEL_GRAPH_TERMS)
    graph_specs = [spec for spec in specs if spec["name"].startswith("graph_")]
    serialized = str(graph_specs).lower()
    assert "graph_write_batch_internal" not in serialized
    assert "write_graph_batch" not in serialized
    assert "graph_readonly_query" not in serialized
    assert "driver" not in serialized
    assert "session" not in serialized
    assert "merge" not in serialized


def test_graph_readonly_cypher_requires_workspace_and_knowledge_base_scope() -> None:
    module = _graph_tools_module()

    class _ScopedCypherGraphQuery:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def execute_readonly(
            self,
            query: str,
            *,
            parameters: dict[str, Any],
            limit: int,
        ) -> dict[str, Any]:
            self.calls.append({"query": query, "parameters": parameters, "limit": limit})
            return {"ok": True, "data": {"records": []}}

    graph_query = _ScopedCypherGraphQuery()
    tool = _make_tool(
        module,
        "build_graph_readonly_cypher_tool",
        "graph_readonly_cypher_tool",
        graph_query=graph_query,
        audit_log=_FakeAuditLog(),
    )

    rejected = _invoke(
        tool,
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "query": "MATCH (e:GraphEntity) RETURN e LIMIT $limit",
            "parameters": {},
            "limit": 10,
        },
    )
    accepted = _invoke(
        tool,
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "query": (
                "MATCH (e:GraphEntity) "
                "WHERE e.workspace_id = $workspace_id "
                "AND e.knowledge_base_id = $knowledge_base_id "
                "RETURN e LIMIT $limit"
            ),
            "parameters": {"workspace_id": "wrong"},
            "limit": 10,
        },
    )

    assert rejected["ok"] is False
    assert rejected["error_type"] == "graph_readonly_scope_required"
    assert accepted["ok"] is True
    assert graph_query.calls[0]["parameters"]["workspace_id"] == "default"
    assert graph_query.calls[0]["parameters"]["knowledge_base_id"] == "kb_default"
