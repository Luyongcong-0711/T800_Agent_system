from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    doc_id: str
    doc_version_id: str
    score: float
    object_key: str
    metadata: dict[str, Any] | None = None


class MilvusVectorStoreError(Exception):
    def __init__(
        self,
        error_type: str,
        message_for_user: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.error_type = error_type
        self.message_for_user = message_for_user
        self.retryable = retryable
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message_for_user)


class MilvusHttpVectorStore:
    def __init__(
        self,
        *,
        uri: str,
        token: str | None = None,
        timeout_ms: int = 60000,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.uri = uri.rstrip("/")
        self.token = token
        self.timeout_ms = timeout_ms
        self.http_client = http_client or httpx.Client()

    def ensure_collection(self, *, collection: str, dimension: int) -> None:
        describe = self._post(
            "/v2/vectordb/collections/describe",
            {"collectionName": collection},
            raise_for_status=False,
        )
        describe_error = _milvus_error_from_response(describe)
        if describe_error is None:
            return
        if describe_error.error_type != "collection_not_found":
            raise describe_error
        self._post(
            "/v2/vectordb/collections/create",
            {
                "collectionName": collection,
                "dimension": int(dimension),
                "idType": "VarChar",
                "metricType": "COSINE",
                "primaryFieldName": "chunk_id",
                "vectorFieldName": "vector",
                "autoId": False,
                "params": {
                    "enableDynamicField": True,
                    "max_length": "512",
                },
            },
        )

    def upsert(self, *, collection: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        data = [_milvus_entity(record) for record in records]
        response = self._post(
            "/v2/vectordb/entities/upsert",
            {"collectionName": collection, "data": data},
        )
        payload = response.json()
        upserted_count = _milvus_mutation_count(payload, fallback=len(data))
        return {
            "ok": upserted_count == len(data),
            "backend": "milvus_http",
            "upserted_count": upserted_count,
            "raw_code": payload.get("code"),
        }

    def delete_by_ids(self, *, collection: str, ids: list[str]) -> dict[str, Any]:
        safe_ids = [str(item) for item in ids if item]
        if not safe_ids:
            return {"ok": True, "backend": "milvus_http", "deleted_count": 0}
        response = self._post(
            "/v2/vectordb/entities/delete",
            {
                "collectionName": collection,
                "filter": _milvus_in_filter("chunk_id", safe_ids),
            },
        )
        payload = response.json()
        return {
            "ok": True,
            "backend": "milvus_http",
            "deleted_count": len(safe_ids),
            "raw_code": payload.get("code"),
        }

    def delete_by_doc_id(self, *, collection: str, doc_id: str) -> dict[str, Any]:
        response = self._post(
            "/v2/vectordb/entities/delete",
            {
                "collectionName": collection,
                "filter": f"doc_id == {json.dumps(str(doc_id), ensure_ascii=False)}",
            },
        )
        payload = response.json()
        return {
            "ok": True,
            "backend": "milvus_http",
            "doc_id": doc_id,
            "raw_code": payload.get("code"),
        }

    def search(
        self,
        *,
        collection: str,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        payload: dict[str, Any] = {
            "collectionName": collection,
            "data": [vector],
            "limit": max(1, min(int(top_k), 100)),
            "outputFields": [
                "chunk_id",
                "doc_id",
                "doc_version_id",
                "workspace_id",
                "knowledge_base_id",
                "object_key",
                "text",
            ],
        }
        filter_expr = _milvus_filter(filters or {})
        if filter_expr:
            payload["filter"] = filter_expr
        response = self._post("/v2/vectordb/entities/search", payload)
        data = response.json().get("data") or []
        return [_hit_from_record(item) for item in data if isinstance(item, dict)]

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.http_client.post(
                f"{self.uri}{path}",
                headers=headers,
                json=payload,
                timeout=self.timeout_ms / 1000,
            )
        except httpx.TimeoutException as exc:
            raise MilvusVectorStoreError(
                "timeout",
                "Milvus request timed out.",
                retryable=True,
                details={"path": path},
            ) from exc
        except httpx.RequestError as exc:
            raise MilvusVectorStoreError(
                "network_error",
                "Milvus network request failed.",
                retryable=True,
                details={"path": path, "reason": exc.__class__.__name__},
            ) from exc
        if raise_for_status:
            _raise_for_milvus_error(response)
        return response


def _milvus_entity(record: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(record.get("metadata") or {})
    entity = {
        "chunk_id": str(record["chunk_id"]),
        "doc_id": str(record.get("doc_id") or metadata.get("doc_id") or ""),
        "doc_version_id": str(
            record.get("doc_version_id") or metadata.get("doc_version_id") or ""
        ),
        "knowledge_base_id": str(
            record.get("knowledge_base_id") or metadata.get("knowledge_base_id") or ""
        ),
        "object_key": str(record.get("object_key") or ""),
        "text": str(record.get("text") or ""),
        "vector": [float(value) for value in record["vector"]],
        "workspace_id": str(record.get("workspace_id") or metadata.get("workspace_id") or ""),
    }
    for key, value in metadata.items():
        if key not in entity and isinstance(value, str | int | float | bool):
            entity[key] = value
    return entity


def _milvus_filter(filters: dict[str, Any]) -> str:
    clauses = []
    for key, value in sorted(filters.items()):
        if not _safe_identifier(key) or value is None:
            continue
        if isinstance(value, str):
            clauses.append(f'{key} == {json.dumps(value, ensure_ascii=False)}')
        elif isinstance(value, bool):
            clauses.append(f"{key} == {str(value).lower()}")
        elif isinstance(value, int | float):
            clauses.append(f"{key} == {value}")
    return " and ".join(clauses)


def _milvus_in_filter(field: str, values: list[str]) -> str:
    quoted = ", ".join(json.dumps(value, ensure_ascii=False) for value in values)
    return f"{field} in [{quoted}]"


def _safe_identifier(value: str) -> bool:
    return value.replace("_", "").isalnum() and not value[0].isdigit()


def _hit_from_record(record: dict[str, Any]) -> VectorHit:
    score = record.get("score", record.get("distance", 0))
    entity = record.get("entity") if isinstance(record.get("entity"), dict) else record
    return VectorHit(
        chunk_id=str(entity.get("chunk_id") or record.get("id") or ""),
        doc_id=str(entity.get("doc_id") or ""),
        doc_version_id=str(entity.get("doc_version_id") or ""),
        score=float(score or 0),
        object_key=str(entity.get("object_key") or ""),
        metadata=dict(entity),
    )


def _raise_for_milvus_error(response: httpx.Response) -> None:
    error = _milvus_error_from_response(response)
    if error is not None:
        raise error


def _milvus_error_from_response(response: httpx.Response) -> MilvusVectorStoreError | None:
    payload = _response_json(response)
    if response.status_code >= 400:
        return _classify_milvus_error(response.status_code, payload)
    code = payload.get("code")
    if code not in {None, 0, 200}:
        return _classify_milvus_error(response.status_code, payload)
    return None


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"message": response.text}
    return payload if isinstance(payload, dict) else {"message": str(payload)}


def _milvus_mutation_count(payload: dict[str, Any], *, fallback: int) -> int:
    for key in ("upserted_count", "inserted_count", "upsertCount", "insertCount"):
        raw_count = payload.get(key)
        if raw_count is None:
            continue
        try:
            return int(raw_count)
        except (TypeError, ValueError):
            continue
    return int(fallback)


def _classify_milvus_error(
    status_code: int | None,
    payload: dict[str, Any],
) -> MilvusVectorStoreError:
    raw_message = str(
        payload.get("message")
        or payload.get("reason")
        or payload.get("error")
        or payload.get("code")
        or "Milvus request failed."
    )
    lowered = raw_message.lower()
    details = {
        "milvus_code": payload.get("code"),
        "message": raw_message,
    }
    if (
        "not found" in lowered
        or "not exist" in lowered
        or "does not exist" in lowered
        or "can't find collection" in lowered
        or "cannot find collection" in lowered
    ):
        return MilvusVectorStoreError(
            "collection_not_found",
            "Milvus collection does not exist.",
            retryable=False,
            status_code=status_code,
            details=details,
        )
    if "dimension" in lowered or "dim" in lowered:
        return MilvusVectorStoreError(
            "dimension_mismatch",
            "Milvus vector dimension does not match collection schema.",
            retryable=False,
            status_code=status_code,
            details=details,
        )
    if status_code in {408, 429}:
        return MilvusVectorStoreError(
            "milvus_busy",
            "Milvus request should be retried later.",
            retryable=True,
            status_code=status_code,
            details=details,
        )
    if status_code is not None and 500 <= status_code <= 599:
        return MilvusVectorStoreError(
            "milvus_5xx",
            "Milvus service failed.",
            retryable=True,
            status_code=status_code,
            details=details,
        )
    if status_code in {401, 403}:
        return MilvusVectorStoreError(
            "auth_failed",
            "Milvus authentication failed.",
            retryable=False,
            status_code=status_code,
            details=details,
        )
    if status_code is not None and 400 <= status_code <= 499:
        return MilvusVectorStoreError(
            "invalid_request",
            "Milvus rejected the request.",
            retryable=False,
            status_code=status_code,
            details=details,
        )
    return MilvusVectorStoreError(
        "milvus_error",
        "Milvus request failed.",
        retryable=False,
        status_code=status_code,
        details=details,
    )
