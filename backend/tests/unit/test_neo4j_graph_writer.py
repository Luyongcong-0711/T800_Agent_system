from __future__ import annotations

from typing import Any

from app.graph_pipeline.neo4j_writer import Neo4jGraphWriter


class _FakeDriver:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute_query(self, query: str, **params: Any) -> tuple[list[Any], Any, list[str]]:
        self.calls.append({"query": query, "params": params})
        return [], None, []


def test_neo4j_graph_writer_merges_entities_mentions_and_relation_facts() -> None:
    driver = _FakeDriver()
    writer = Neo4jGraphWriter(driver=driver)

    result = writer.write_graph_batch_internal(
        {
            "workspace_id": "default",
            "knowledge_base_id": "kb_default",
            "doc_id": "doc_001",
            "doc_version_id": "docv_001",
            "entities": [
                {
                    "entity_id": "ent_party_b",
                    "name": "Party B",
                    "type": "ORG",
                    "source_chunk_ids": ["chk_001"],
                }
            ],
            "mentions": [
                {
                    "chunk_id": "chk_001",
                    "entity_id": "ent_party_b",
                    "mention_id": "men_001",
                    "text": "Party B",
                }
            ],
            "relation_facts": [
                {
                    "fact_id": "fact_001",
                    "predicate": "MUST_DELIVER",
                    "subject_entity_id": "ent_party_b",
                    "object_entity_id": "ent_delivery",
                }
            ],
            "evidence": [{"evidence_id": "ev_001", "chunk_id": "chk_001"}],
        },
        caller_type="graph_build_job",
        job_id="job_graph_001",
        operation_id="op_graph_001",
    )

    assert result == {
        "backend": "neo4j",
        "entity_count": 1,
        "evidence_count": 1,
        "mention_count": 1,
        "ok": True,
        "relation_fact_count": 1,
    }
    assert len(driver.calls) == 4
    assert all("MERGE" in call["query"] for call in driver.calls)
    serialized = str(driver.calls).lower()
    assert "api_key" not in serialized
    assert "password" not in serialized
    assert "plaintext" not in serialized
    assert driver.calls[0]["params"]["entities"][0]["job_id"] == "job_graph_001"
    assert driver.calls[0]["params"]["entities"][0]["operation_id"] == "op_graph_001"
    relation_fact = driver.calls[2]["params"]["relation_facts"][0]
    assert relation_fact["source_entity_id"] == "ent_party_b"
    assert relation_fact["target_entity_id"] == "ent_delivery"


def test_neo4j_graph_writer_upserts_and_deletes_memory_nodes() -> None:
    driver = _FakeDriver()
    writer = Neo4jGraphWriter(driver=driver)

    upsert_result = writer.upsert_memory(
        record={
            "memory_id": "mem_001",
            "workspace_id": "default",
            "user_id": "default_user",
            "scope": "global",
            "type": "user_profile",
            "field": "name",
            "value": "Zhang San",
            "summary": "User name is Zhang San.",
            "content_object_key": "users/default_user/memory/mem_001.json",
            "status": "active",
            "enabled_for_model_context": True,
        },
        operation_id="evt_001",
        job_id="job_memory_sync",
    )
    delete_result = writer.delete_memory(
        memory_id="mem_001",
        operation_id="evt_002",
        job_id="job_memory_sync",
    )

    assert upsert_result == {
        "action": "upsert",
        "backend": "neo4j",
        "memory_id": "mem_001",
        "ok": True,
    }
    assert delete_result["action"] == "delete"
    assert len(driver.calls) == 3
    assert driver.calls[0]["params"]["memory"]["memory_id"] == "mem_001"
    assert driver.calls[1]["params"] == {
        "memory_id": "mem_001",
        "user_id": "default_user",
    }
    assert driver.calls[2]["params"]["memory_id"] == "mem_001"
