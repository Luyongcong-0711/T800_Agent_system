from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from app.storage.object_store import ObjectMetadata, ObjectNotFoundError, RevisionConflictError

T = TypeVar("T")


class LocalObjectStore:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        safe_key = key.replace("\\", "/").lstrip("/")
        path = (self.root_dir / safe_key).resolve()
        root = self.root_dir.resolve()
        if root != path and root not in path.parents:
            raise ValueError(f"Object key escapes store root: {key}")
        return path

    def read_text(self, key: str) -> str:
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        return _retry_transient_file_lock(lambda: path.read_text(encoding="utf-8"))

    def read_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        return _retry_transient_file_lock(path.read_bytes)

    def write_text(self, key: str, value: str, expected_etag: str | None = None) -> ObjectMetadata:
        return self.write_bytes(key, value.encode("utf-8"), expected_etag=expected_etag)

    def write_bytes(
        self,
        key: str,
        value: bytes,
        expected_etag: str | None = None,
        content_type: str | None = None,
    ) -> ObjectMetadata:
        path = self._resolve(key)
        if expected_etag is not None:
            current_etag = self.stat(key).etag
            if current_etag != expected_etag:
                raise RevisionConflictError(f"Object etag conflict for {key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(5):
            temp_path = path.parent / f".t{attempt}-{time.time_ns() & 0xffff:x}"
            try:
                temp_path.write_bytes(value)
                temp_path.replace(path)
                return self.stat(key)
            except (FileNotFoundError, PermissionError) as exc:
                last_error = exc
                if path.exists():
                    return self.stat(key)
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)
                if attempt == 4:
                    break
                time.sleep(0.01 * (attempt + 1))
        if last_error is not None:
            raise last_error
        return self.stat(key)

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def stat(self, key: str) -> ObjectMetadata:
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        data = _retry_transient_file_lock(path.read_bytes)
        return ObjectMetadata(
            key=key.replace("\\", "/").lstrip("/"),
            etag=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )

    def list_keys(self, prefix: str) -> list[str]:
        prefix_path = self._resolve(prefix)
        if not prefix_path.exists():
            return []
        return [
            path.relative_to(self.root_dir).as_posix()
            for path in prefix_path.rglob("*")
            if path.is_file()
        ]

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        path.unlink()


def _retry_transient_file_lock(operation: Callable[[], T]) -> T:
    for attempt in range(5):
        try:
            return operation()
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.01 * (attempt + 1))
    return operation()
