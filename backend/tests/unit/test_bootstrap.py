from fastapi.testclient import TestClient

from app.main import app


def test_bootstrap_returns_default_identity() -> None:
    client = TestClient(app)

    response = client.get("/bootstrap")

    assert response.status_code == 200
    assert response.json() == {
        "user": {"user_id": "default_user", "role": "owner"},
        "workspace": {"workspace_id": "default", "workspace_role": "owner"},
        "feature_flags": {
            "login_enabled": False,
            "workspace_switch_enabled": False,
        },
    }

