from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class ObjectStoreError(Exception):
    pass


class ObjectNotFoundError(ObjectStoreError):
    pass


class RevisionConflictError(ObjectStoreError):
    pass


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    etag: str | None = None
    size_bytes: int | None = None
    revision: int | None = None


class ObjectStore(Protocol):
    def read_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def read_text(self, key: str) -> str:
        raise NotImplementedError

    def write_bytes(
        self,
        key: str,
        value: bytes,
        expected_etag: str | None = None,
        content_type: str | None = None,
    ) -> ObjectMetadata:
        raise NotImplementedError

    def write_text(self, key: str, value: str, expected_etag: str | None = None) -> ObjectMetadata:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def stat(self, key: str) -> ObjectMetadata:
        raise NotImplementedError

    def list_keys(self, prefix: str) -> list[str]:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class JsonObjectStore:
    def __init__(self, object_store: ObjectStore) -> None:
        self.object_store = object_store

    def read_json(self, key: str) -> dict[str, Any]:
        return json.loads(self.object_store.read_text(key))

    def write_json(
        self,
        key: str,
        value: dict[str, Any],
        expected_revision: int | None = None,
        expected_etag: str | None = None,
    ) -> None:
        current_etag = expected_etag
        if expected_revision is not None:
            metadata = self.stat(key)
            current_etag = current_etag or metadata.etag
            current = self.read_json(key)
            current_revision = current.get("revision")
            if current_revision != expected_revision:
                raise RevisionConflictError(
                    f"Object revision conflict for {key}: expected {expected_revision}, "
                    f"got {current_revision}"
                )
        self.object_store.write_text(
            key,
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            expected_etag=current_etag,
        )

    def read_json_or_default(self, key: str, default: dict[str, Any]) -> dict[str, Any]:
        if not self.object_store.exists(key):
            return default
        return self.read_json(key)

    def stat(self, key: str) -> ObjectMetadata:
        metadata = self.object_store.stat(key)
        revision = metadata.revision
        if revision is None:
            try:
                revision = self.read_json(key).get("revision")
            except (ObjectStoreError, json.JSONDecodeError, AttributeError):
                revision = None
        return ObjectMetadata(
            key=metadata.key,
            etag=metadata.etag,
            size_bytes=metadata.size_bytes,
            revision=revision,
        )
