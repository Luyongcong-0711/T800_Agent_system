from __future__ import annotations

from typing import Any

import pytest

from app.graph_pipeline.neo4j_readonly import (
    QUERY_ERROR_TYPE,
    READONLY_ERROR_TYPE,
    Neo4jReadOnlyQueryAdapter,
    validate_readonly_cypher,
)


class _FakeDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute_query(self, query: str, **kwargs: Any) -> tuple[list[Any], Any, list[str]]:
        self.calls.append({"query": query, "kwargs": kwargs})
        return [{"name": "Party B"}], _FakeSummary(), ["name"]


class _FailingDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute_query(self, query: str, **kwargs: Any) -> tuple[list[Any], Any, list[str]]:
        self.calls.append({"query": query, "kwargs": kwargs})
        raise RuntimeError("transient unavailable")


class _HighLevelFakeDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute_query(self, query: str, **kwargs: Any) -> tuple[list[Any], Any, list[str]]:
        self.calls.append({"query": query, "kwargs": kwargs})
        if "MATCH (e:GraphEntity)" in query:
            return [
                {
                    "entity": {
                        "entity_id": "ent_party_b",
                        "name": "Party B",
                        "entity_type": "Role",
                        "evidence_count": 1,
                    },
                    "score": 1.0,
                }
            ], _FakeSummary(), ["entity", "score"]
        return [], _FakeSummary(), []


class _FakeSummary:
    counters = {"nodes_created": 0, "relationships_created": 0, "properties_set": 0}


def test_readonly_adapter_executes_match_with_separate_parameters_and_read_routing() -> None:
    driver = _FakeDriver()
    adapter = Neo4jReadOnlyQueryAdapter(driver=driver, database="neo4j", max_limit=100)

    result = adapter.execute_readonly(
        "MATCH (e:GraphEntity {name: $name}) RETURN e.name AS name LIMIT $limit",
        parameters={"name": "Party B"},
        limit=250,
    )

    assert result == {
        "ok": True,
        "data": {
            "records": [{"name": "Party B"}],
            "keys": ["name"],
            "limit": 100,
            "counters": {
                "nodes_created": 0,
                "relationships_created": 0,
                "properties_set": 0,
            },
        },
    }
    assert driver.calls == [
        {
            "query": "MATCH (e:GraphEntity {name: $name}) RETURN e.name AS name LIMIT $limit",
            "kwargs": {
                "parameters_": {"name": "Party B", "limit": 100},
                "routing_": "r",
                "database_": "neo4j",
            },
        }
    ]


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) CREATE (m:GraphEntity) RETURN n",
        "MATCH (n) MERGE (m:GraphEntity {id: $id}) RETURN m",
        "MATCH (n) SET n.name = $name RETURN n",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) REMOVE n.name RETURN n",
        "MATCH (n) DROP INDEX graph_entity IF EXISTS",
        "LOAD CSV FROM $url AS row RETURN row",
        "CALL dbms.components() YIELD name RETURN name",
        "CALL apoc.periodic.iterate($read, $write, {}) YIELD batches RETURN batches",
    ],
)
def test_readonly_adapter_rejects_write_procedure_and_import_clauses(query: str) -> None:
    driver = _FakeDriver()
    adapter = Neo4jReadOnlyQueryAdapter(driver=driver)

    result = adapter.execute_readonly(
        query,
        parameters={
            "id": "ent_001",
            "name": "Party B",
            "url": "file:///tmp/data.csv",
            "read": "MATCH (n) RETURN n",
            "write": "CREATE (n)",
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == READONLY_ERROR_TYPE
    assert result["retryable"] is False
    assert driver.calls == []


def test_readonly_adapter_rejects_multiple_statements_before_driver_call() -> None:
    driver = _FakeDriver()
    adapter = Neo4jReadOnlyQueryAdapter(driver=driver)

    result = adapter.execute_readonly("MATCH (n) RETURN n; MATCH (m) RETURN m")

    assert result["ok"] is False
    assert result["error_type"] == READONLY_ERROR_TYPE
    assert "Multiple Cypher statements" in result["message_for_model"]
    assert driver.calls == []


def test_readonly_validator_ignores_forbidden_words_inside_literals_and_comments() -> None:
    result = validate_readonly_cypher(
        """
        // CREATE is only documentation here.
        MATCH (e:GraphEntity)
        WHERE e.note = 'MERGE and DELETE are literal text'
        RETURN e.name AS name
        """,
        {},
    )

    assert result is None


def test_readonly_adapter_requires_parameters_supplied_separately() -> None:
    driver = _FakeDriver()
    adapter = Neo4jReadOnlyQueryAdapter(driver=driver)

    result = adapter.execute_readonly(
        "MATCH (e:GraphEntity {entity_id: $entity_id}) RETURN e LIMIT $limit",
        parameters={},
    )

    assert result["ok"] is False
    assert result["error_type"] == READONLY_ERROR_TYPE
    assert result["detail"] == {"missing_parameters": ["entity_id"]}
    assert driver.calls == []


def test_readonly_adapter_returns_structured_error_when_driver_fails() -> None:
    driver = _FailingDriver()
    adapter = Neo4jReadOnlyQueryAdapter(driver=driver)

    result = adapter.execute_readonly("MATCH (e:GraphEntity) RETURN e LIMIT $limit")

    assert result == {
        "ok": False,
        "error_type": QUERY_ERROR_TYPE,
        "message_for_model": "Neo4j read-only query failed.",
        "retryable": True,
        "detail": "RuntimeError",
    }
    assert len(driver.calls) == 1


def test_readonly_adapter_high_level_entity_search_maps_neo4j_records() -> None:
    driver = _HighLevelFakeDriver()
    adapter = Neo4jReadOnlyQueryAdapter(driver=driver)

    entities = adapter.entity_search(
        "default",
        "kb_default",
        "Party B",
        entity_types=["Role"],
        limit=10,
    )

    assert entities == [
        {
            "entity_id": "ent_party_b",
            "name": "Party B",
            "entity_type": "Role",
            "evidence_count": 1,
            "score": 1.0,
            "match_type": "name_or_alias",
        }
    ]
    assert driver.calls[0]["kwargs"]["parameters_"]["workspace_id"] == "default"
    assert driver.calls[0]["kwargs"]["parameters_"]["knowledge_base_id"] == "kb_default"
