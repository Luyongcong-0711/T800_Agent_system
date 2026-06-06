from __future__ import annotations

import json
from typing import Any

from app.graph_pipeline.extraction import LLMGraphExtractor
from app.schemas.model import ModelConfig, ModelResult, ModelUsage


class _FakeConnector:
    def __init__(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.calls: list[dict[str, Any]] = []

    def call(self, workspace_id: str, config: ModelConfig, request: Any) -> ModelResult:
        payload = self.payloads[min(len(self.calls), len(self.payloads) - 1)]
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "config": config,
                "request": request,
            }
        )
        return ModelResult(
            content=json.dumps(payload),
            usage=ModelUsage(input_tokens=40, output_tokens=30, total_tokens=70),
        )


def test_llm_graph_extractor_normalizes_entities_relations_and_evidence() -> None:
    connector = _FakeConnector(
        {
            "entities": [
                {
                    "name": "Party A",
                    "entity_type": "Role",
                    "chunk_ids": ["chk_001"],
                    "confidence": 0.91,
                },
                {
                    "name": "Party B",
                    "entity_type": "Role",
                    "chunk_ids": ["chk_001"],
                    "confidence": 0.9,
                },
            ],
            "relations": [
                {
                    "subject": "Party A",
                    "predicate": "SIGNS_WITH",
                    "object": "Party B",
                    "chunk_ids": ["chk_001"],
                    "evidence_text": "Party A signs with Party B.",
                    "confidence": 0.88,
                }
            ],
        }
    )
    extractor = LLMGraphExtractor(
        llm_connector=connector,
        model_config=ModelConfig(provider="fake", model="graph-test"),
    )

    records = extractor.extract(
        workspace_id="default",
        knowledge_base_id="kb_default",
        doc_id="doc_001",
        doc_version_id="docv_001",
        chunks=[
            {
                "chunk_id": "chk_001",
                "text": "Party A signs with Party B.",
                "source": {"source_file_name": "contract.md"},
            }
        ],
    )

    assert len(records["entities"]) == 2
    assert len(records["mentions"]) == 2
    assert len(records["relation_facts"]) == 1
    assert len(records["evidence"]) == 1
    assert records["relation_facts"][0]["predicate"] == "SIGNS_WITH"
    assert records["relation_facts"][0]["extraction_source"] == "graphrag_llm"
    assert records["decisions"][-1]["source"] == "graphrag_llm"
    assert connector.calls[0]["request"].tools == []


def test_llm_graph_extractor_batches_all_chunks() -> None:
    connector = _FakeConnector(
        [
            {
                "entities": [
                    {"name": "Alpha System", "chunk_ids": ["chk_001"], "confidence": 0.8}
                ],
                "relations": [],
            },
            {
                "entities": [
                    {"name": "Beta Project", "chunk_ids": ["chk_002"], "confidence": 0.8}
                ],
                "relations": [],
            },
        ]
    )
    extractor = LLMGraphExtractor(
        llm_connector=connector,
        model_config=ModelConfig(provider="fake", model="graph-test"),
        max_chunks_per_call=1,
    )

    records = extractor.extract(
        workspace_id="default",
        knowledge_base_id="kb_default",
        doc_id="doc_001",
        doc_version_id="docv_001",
        chunks=[
            {"chunk_id": "chk_001", "text": "Alpha System overview."},
            {"chunk_id": "chk_002", "text": "Beta Project roadmap."},
        ],
    )

    assert len(connector.calls) == 2
    assert {item["name"] for item in records["entities"]} == {"Alpha System", "Beta Project"}
    summary = next(item for item in records["decisions"] if item["type"] == "graph_extraction_summary")
    assert summary["batch_count"] == 2
