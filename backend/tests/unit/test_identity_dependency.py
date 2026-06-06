from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.dependencies import get_identity


def test_default_identity_is_allowed_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")

    identity = get_identity()

    assert identity.user_id == "default_user"
    assert identity.workspace_id == "default"


def test_default_identity_is_rejected_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(HTTPException) as exc_info:
        get_identity()

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "authentication_required"
