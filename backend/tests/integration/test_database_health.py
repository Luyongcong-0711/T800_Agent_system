from fastapi.testclient import TestClient

from app.api.dependencies import get_object_store
from app.main import app
from app.storage.local_object_store import LocalObjectStore


def test_database_health_contract_without_running_dependencies(tmp_path) -> None:
    object_store = LocalObjectStore(tmp_path / "objects")
    app.dependency_overrides[get_object_store] = lambda: object_store

    try:
        client = TestClient(app)
        response = client.get("/workspaces/default/database/health")
    finally:
        app.dependency_overrides.clear()
        get_object_store.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == "default"
    assert {service["target"] for service in body["services"]} == {
        "minio",
        "milvus",
        "neo4j",
        "redis",
    }
    assert all(
        service["status"] in {"healthy", "unhealthy", "unknown"}
        for service in body["services"]
    )
