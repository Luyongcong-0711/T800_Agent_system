from __future__ import annotations

from app.core.settings import Settings
from app.secret_store.crypto import encode_master_key


class SecretStoreUnavailableError(Exception):
    pass


class MasterKeyProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def current(self, key_version: str = "v1") -> bytes:
        if key_version != "v1":
            raise SecretStoreUnavailableError(f"Unsupported key version: {key_version}")
        raw_key = self.settings.agent_master_key
        if not raw_key:
            raise SecretStoreUnavailableError("Secret store master key is not configured.")
        try:
            return encode_master_key(raw_key)
        except ValueError as exc:
            raise SecretStoreUnavailableError("Secret store master key is invalid.") from exc


def assert_master_key_available(settings: Settings) -> None:
    if settings.app_env != "development" and not settings.agent_master_key:
        raise SecretStoreUnavailableError(
            "Secret store master key is required outside development."
        )
    if settings.app_env != "development":
        MasterKeyProvider(settings).current()
