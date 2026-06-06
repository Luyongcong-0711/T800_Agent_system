from __future__ import annotations

import re
from pathlib import PurePath

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def safe_path_id(name: str, value: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid {name}: path identifiers may not contain slashes or traversal.")
    return value


def safe_file_name(value: str | None, default: str = "document.txt") -> str:
    raw = (value or default).replace("\\", "/")
    name = PurePath(raw).name.strip() or default
    name = re.sub(r"[^\w.\- \u4e00-\u9fff]", "_", name, flags=re.UNICODE)
    return name[:180] or default


def workspace_prefix(workspace_id: str) -> str:
    return f"workspaces/{safe_path_id('workspace_id', workspace_id)}"


def knowledge_base_prefix(workspace_id: str, knowledge_base_id: str) -> str:
    return (
        f"{workspace_prefix(workspace_id)}/knowledge_bases/"
        f"{safe_path_id('knowledge_base_id', knowledge_base_id)}"
    )


def knowledge_bases_index_key(workspace_id: str) -> str:
    return f"{workspace_prefix(workspace_id)}/indexes/knowledge_bases_index.json"


def knowledge_base_manifest_key(workspace_id: str, knowledge_base_id: str) -> str:
    return f"{knowledge_base_prefix(workspace_id, knowledge_base_id)}/manifest.json"


def active_embedding_key(workspace_id: str, knowledge_base_id: str) -> str:
    return f"{knowledge_base_prefix(workspace_id, knowledge_base_id)}/active_embedding.json"


def embedding_versions_index_key(workspace_id: str, knowledge_base_id: str) -> str:
    return f"{knowledge_base_prefix(workspace_id, knowledge_base_id)}/embedding_versions/index.json"


def embedding_version_manifest_key(
    workspace_id: str,
    knowledge_base_id: str,
    version_id: str,
) -> str:
    return (
        f"{knowledge_base_prefix(workspace_id, knowledge_base_id)}/embedding_versions/"
        f"{safe_path_id('embedding_version_id', version_id)}/manifest.json"
    )


def documents_index_key(workspace_id: str, knowledge_base_id: str) -> str:
    return f"{knowledge_base_prefix(workspace_id, knowledge_base_id)}/documents_index.json"


def document_prefix(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return (
        f"{knowledge_base_prefix(workspace_id, knowledge_base_id)}/documents/"
        f"{safe_path_id('doc_id', doc_id)}"
    )


def document_manifest_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/manifest.json"


def document_versions_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/versions.json"


def document_original_key(
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    source_file_name: str,
) -> str:
    file_name = safe_file_name(source_file_name)
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/original/{file_name}"


def document_representation_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/parsed/document.json"


def parsed_text_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/parsed/text.json"


def document_chunks_index_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/chunks/chunks.json"


def document_chunk_errors_prefix(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/chunks/errors"


def legacy_document_chunks_index_key(workspace_id: str, knowledge_base_id: str, doc_id: str) -> str:
    return f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/chunks.json"


def document_chunk_key(
    workspace_id: str,
    knowledge_base_id: str,
    doc_id: str,
    chunk_id: str,
) -> str:
    return (
        f"{document_prefix(workspace_id, knowledge_base_id, doc_id)}/chunks/"
        f"{safe_path_id('chunk_id', chunk_id)}.json"
    )


def search_index_key(workspace_id: str, knowledge_base_id: str) -> str:
    return f"{knowledge_base_prefix(workspace_id, knowledge_base_id)}/search/search_index.json"
