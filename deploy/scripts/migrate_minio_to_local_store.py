from __future__ import annotations

import os
from pathlib import Path

from minio import Minio


def main() -> None:
    bucket = os.getenv("MINIO_BUCKET", "agent-system")
    endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
    endpoint = endpoint.removeprefix("http://").removeprefix("https://")
    access_key = (
        os.getenv("MINIO_ACCESS_KEY")
        or os.getenv("MINIO_ROOT_USER")
        or "agentadmin"
    )
    secret_key = os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD")
    if not secret_key:
        raise RuntimeError("MINIO_SECRET_KEY or MINIO_ROOT_PASSWORD is required.")

    root = Path(os.getenv("LOCAL_OBJECT_STORE_DIR", "/app/.agent_state")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    client = Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=os.getenv("MINIO_SECURE", "false").lower() in {"1", "true", "yes"},
    )

    count = 0
    bytes_written = 0
    for item in client.list_objects(bucket, recursive=True):
        target = (root / item.object_name).resolve()
        if target != root and root not in target.parents:
            raise RuntimeError(f"Object name escapes local store root: {item.object_name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        response = client.get_object(bucket, item.object_name)
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()
        target.write_bytes(data)
        count += 1
        bytes_written += len(data)

    print(f"migrated_objects={count} bytes_written={bytes_written}")


if __name__ == "__main__":
    main()
