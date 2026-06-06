from fastapi.testclient import TestClient

from app.main import app


def test_phase_a_bootstrap_contract() -> None:
    client = TestClient(app)

    response = client.get("/bootstrap")

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"user_id": "default_user", "role": "owner"}
    assert body["workspace"] == {
        "workspace_id": "default",
        "workspace_role": "owner",
    }
    assert body["feature_flags"] == {
        "login_enabled": False,
        "workspace_switch_enabled": False,
    }
