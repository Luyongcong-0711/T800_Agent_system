from __future__ import annotations

from app.rag_pipeline.paths import document_prefix, knowledge_base_prefix, safe_path_id


def document_graph_prefix(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/graph"


def graph_entities_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_graph_prefix(workspace_id, knowledge_base_id, doc_id)}/entities.json"


def graph_mentions_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_graph_prefix(workspace_id, knowledge_base_id, doc_id)}/mentions.json"


def graph_relation_facts_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_graph_prefix(workspace_id, knowledge_base_id, doc_id)}/relation_facts.json"


def graph_evidence_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_graph_prefix(workspace_id, knowledge_base_id, doc_id)}/evidence.json"


def graph_decisions_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_graph_prefix(workspace_id, knowledge_base_id, doc_id)}/decisions.json"


def graph_index_key(workspace_id: str, knowledge_base_id: str) -> str:
    return f"{knowledge_base_prefix(workspace_id, knowledge_base_id)}/graph/index.json"


def graph_operation_key(workspace_id: str, knowledge_base_id: str, operation_id: str) -> str:
    safe_operation_id = safe_path_id("operation_id", operation_id)
    prefix = knowledge_base_prefix(workspace_id, knowledge_base_id)
    return f"{prefix}/graph/operations/{safe_operation_id}.json"
