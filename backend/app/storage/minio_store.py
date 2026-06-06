from __future__ import annotations

from io import BytesIO

from app.storage.object_store import (
    ObjectMetadata,
    ObjectNotFoundError,
    ObjectStoreError,
    RevisionConflictError,
)


class MinioObjectStore:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        from minio import Minio
        from minio.error import S3Error

        normalized_endpoint = endpoint.removeprefix("http://").removeprefix("https://")
        self.client = Minio(
            normalized_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self.bucket = bucket
        self.s3_error_type = S3Error

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def read_bytes(self, key: str) -> bytes:
        try:
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except Exception as exc:  # noqa: BLE001 - translate SDK errors at boundary.
            if self._is_not_found_error(exc):
                raise ObjectNotFoundError(key) from exc
            raise ObjectStoreError(f"Failed to read object {key}") from exc

    def write_text(self, key: str, value: str, expected_etag: str | None = None) -> ObjectMetadata:
        return self.write_bytes(
            key,
            value.encode("utf-8"),
            expected_etag=expected_etag,
            content_type="application/json; charset=utf-8",
        )

    def write_bytes(
        self,
        key: str,
        value: bytes,
        expected_etag: str | None = None,
        content_type: str | None = None,
    ) -> ObjectMetadata:
        try:
            if expected_etag is not None and self.stat(key).etag != expected_etag:
                raise RevisionConflictError(f"Object etag conflict for {key}")
            self.client.put_object(
                self.bucket,
                key,
                BytesIO(value),
                length=len(value),
                content_type=content_type or "application/octet-stream",
            )
            return self.stat(key)
        except RevisionConflictError:
            raise
        except ObjectStoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - translate SDK errors at boundary.
            raise ObjectStoreError(f"Failed to write object {key}") from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except Exception as exc:  # noqa: BLE001 - existence probe.
            if self._is_not_found_error(exc):
                return False
            raise ObjectStoreError(f"Failed to stat object {key}") from exc

    def stat(self, key: str) -> ObjectMetadata:
        try:
            stat = self.client.stat_object(self.bucket, key)
            return ObjectMetadata(
                key=key,
                etag=stat.etag,
                size_bytes=stat.size,
            )
        except Exception as exc:  # noqa: BLE001 - translate SDK errors at boundary.
            if self._is_not_found_error(exc):
                raise ObjectNotFoundError(key) from exc
            raise ObjectStoreError(f"Failed to stat object {key}") from exc

    def ensure_bucket(self) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
        except Exception as exc:  # noqa: BLE001 - translate SDK errors at boundary.
            raise ObjectStoreError(f"Failed to ensure bucket {self.bucket}") from exc

    def list_keys(self, prefix: str) -> list[str]:
        try:
            return [
                item.object_name
                for item in self.client.list_objects(self.bucket, prefix=prefix, recursive=True)
                if item.object_name
            ]
        except Exception as exc:  # noqa: BLE001 - translate SDK errors at boundary.
            raise ObjectStoreError(f"Failed to list objects under {prefix}") from exc

    def delete(self, key: str) -> None:
        try:
            self.client.remove_object(self.bucket, key)
        except Exception as exc:  # noqa: BLE001 - translate SDK errors at boundary.
            if self._is_not_found_error(exc):
                raise ObjectNotFoundError(key) from exc
            raise ObjectStoreError(f"Failed to delete object {key}") from exc

    def _is_not_found_error(self, exc: Exception) -> bool:
        return isinstance(exc, self.s3_error_type) and getattr(exc, "code", None) in {
            "NoSuchKey",
            "NoSuchBucket",
            "NoSuchObject",
        }
