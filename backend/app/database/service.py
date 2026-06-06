from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.core.errors import AgentSystemError
from app.core.settings import Settings
from app.core.time import utc_now_iso
from app.schemas.database import DatabaseTargetConfig, UpdateDatabaseConfigRequest
from app.schemas.health import ServiceHealth
from app.secret_store.secret_resolver import SecretResolver
from app.secret_store.secret_service import SecretNotFoundError, SecretService
from app.storage.health_check import check_database_services, check_database_services_sync
from app.storage.object_store import JsonObjectStore, ObjectStore
from app.storage.path_builder import database_config_key, database_health_snapshot_key

TARGET_ORDER = ("minio", "milvus", "neo4j", "redis")
SECRET_REF_PREFIX = "secret_ref://"


class DatabaseConfigService:
    def __init__(
        self,
        object_store: ObjectStore,
        settings: Settings,
        *,
        secret_service: SecretService | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> None:
        self.object_store = object_store
        self.json_store = JsonObjectStore(object_store)
        self.settings = settings
        self.secret_service = secret_service
        self.secret_resolver = secret_resolver

    def get_config(self, workspace_id: str) -> dict[str, Any]:
        stored = self.json_store.read_json_or_default(
            database_config_key(workspace_id),
            self._default_config(workspace_id),
        )
        return self._normalize_config(workspace_id, stored)

    def update_config(
        self,
        workspace_id: str,
        request: UpdateDatabaseConfigRequest,
    ) -> dict[str, Any]:
        current = self.get_config(workspace_id)
        by_target = {target["target"]: target for target in self._default_targets()}
        for target in request.targets:
            by_target[target.target] = self._public_target(target.model_dump())
        ordered_targets = [by_target[target] for target in TARGET_ORDER]
        self._validate_secret_refs(workspace_id, ordered_targets)
        next_config = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "targets": ordered_targets,
            "updated_at": utc_now_iso(),
            "revision": int(current.get("revision", 0)) + 1,
        }
        self.json_store.write_json(database_config_key(workspace_id), next_config)
        return self._normalize_config(workspace_id, next_config)

    def get_health_snapshot(self, workspace_id: str) -> dict[str, Any]:
        snapshot = self.json_store.read_json_or_default(
            database_health_snapshot_key(workspace_id),
            {},
        )
        if snapshot:
            return snapshot
        return {
            "ok": False,
            "workspace_id": workspace_id,
            "services": [
                ServiceHealth(
                    target=target,
                    status="unknown",
                    latency_ms=None,
                    message="no_health_snapshot",
                    checked_at=utc_now_iso(),
                ).model_dump()
                for target in TARGET_ORDER
            ],
            "checked_at": None,
            "source": "unknown",
        }

    async def run_health_check(self, workspace_id: str) -> dict[str, Any]:
        config = self.get_config(workspace_id)
        credential_failures = self._credential_health_failures(workspace_id, config)
        settings = self._settings_from_config(
            config,
            resolve_credentials=not credential_failures,
        )
        services = await check_database_services(settings)
        services = self._merge_credential_failures(services, credential_failures)
        return self._write_health_snapshot(workspace_id, services, source="live_check")

    def run_health_check_sync(self, workspace_id: str) -> dict[str, Any]:
        config = self.get_config(workspace_id)
        credential_failures = self._credential_health_failures(workspace_id, config)
        settings = self._settings_from_config(
            config,
            resolve_credentials=not credential_failures,
        )
        services = check_database_services_sync(settings)
        services = self._merge_credential_failures(services, credential_failures)
        return self._write_health_snapshot(workspace_id, services, source="job_check")

    def _default_config(self, workspace_id: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "targets": self._default_targets(),
            "updated_at": utc_now_iso(),
            "revision": 1,
        }

    def _default_targets(self) -> list[dict[str, Any]]:
        return [
            {
                "target": "minio",
                "mode": "local",
                "enabled": True,
                "endpoint": self.settings.minio_endpoint,
                "tls": self.settings.minio_secure,
                "bucket": self.settings.minio_bucket,
                "credential_refs": {},
                "options": {},
            },
            {
                "target": "milvus",
                "mode": "local",
                "enabled": True,
                "endpoint": self.settings.milvus_uri,
                "tls": self.settings.milvus_uri.startswith("https://"),
                "bucket": None,
                "credential_refs": {},
                "options": {},
            },
            {
                "target": "neo4j",
                "mode": "local",
                "enabled": True,
                "endpoint": self.settings.neo4j_uri,
                "tls": self.settings.neo4j_uri.startswith("neo4j+s://"),
                "bucket": None,
                "credential_refs": {},
                "options": {"http_url": self.settings.neo4j_http_url},
            },
            {
                "target": "redis",
                "mode": "local",
                "enabled": True,
                "endpoint": self.settings.redis_url,
                "tls": self.settings.redis_url.startswith("rediss://"),
                "bucket": None,
                "credential_refs": {},
                "options": {"role": "cache_only"},
            },
        ]

    def _normalize_config(self, workspace_id: str, value: dict[str, Any]) -> dict[str, Any]:
        by_target = {target["target"]: target for target in self._default_targets()}
        for target in value.get("targets", []):
            parsed = DatabaseTargetConfig(**target)
            by_target[parsed.target] = self._public_target(parsed.model_dump())
        return {
            "workspace_id": workspace_id,
            "targets": [by_target[target] for target in TARGET_ORDER],
            "updated_at": value.get("updated_at") or utc_now_iso(),
            "revision": int(value.get("revision", 1)),
        }

    @staticmethod
    def _public_target(target: dict[str, Any]) -> dict[str, Any]:
        target = dict(target)
        target["credential_refs"] = {
            str(key): str(value)
            for key, value in target.get("credential_refs", {}).items()
            if value
        }
        target["options"] = {
            str(key): str(value)
            for key, value in target.get("options", {}).items()
            if value is not None
        }
        return target

    def _settings_from_config(
        self,
        config: dict[str, Any],
        *,
        resolve_credentials: bool = True,
    ) -> Any:
        targets = {target["target"]: target for target in config["targets"]}
        neo4j_options = targets["neo4j"].get("options") or {}
        resolved_credentials = (
            self._resolve_config_credentials(
                str(config.get("workspace_id") or ""),
                targets,
            )
            if resolve_credentials
            else {}
        )
        return SimpleNamespace(
            minio_endpoint=targets["minio"]["endpoint"],
            minio_access_key=resolved_credentials.get("minio_access_key"),
            minio_secret_key=resolved_credentials.get("minio_secret_key"),
            milvus_uri=targets["milvus"]["endpoint"],
            milvus_token=resolved_credentials.get("milvus_token"),
            neo4j_uri=targets["neo4j"]["endpoint"],
            neo4j_http_url=neo4j_options.get("http_url") or self.settings.neo4j_http_url,
            neo4j_username_password=resolved_credentials.get("neo4j_username_password"),
            redis_url=targets["redis"]["endpoint"],
            redis_password=resolved_credentials.get("redis_password"),
        )

    def _write_health_snapshot(
        self,
        workspace_id: str,
        services: list[ServiceHealth],
        *,
        source: str,
    ) -> dict[str, Any]:
        snapshot = {
            "schema_version": 1,
            "ok": all(service.status == "healthy" for service in services),
            "workspace_id": workspace_id,
            "services": [service.model_dump() for service in services],
            "checked_at": utc_now_iso(),
            "source": source,
            "revision": int(
                self.json_store.read_json_or_default(
                    database_health_snapshot_key(workspace_id),
                    {"revision": 0},
                ).get("revision", 0)
            )
            + 1,
        }
        self.json_store.write_json(database_health_snapshot_key(workspace_id), snapshot)
        return snapshot

    def _validate_secret_refs(
        self,
        workspace_id: str,
        targets: list[dict[str, Any]],
    ) -> None:
        if self.secret_service is None:
            return
        invalid: list[dict[str, str]] = []
        for target in targets:
            target_name = str(target.get("target") or "")
            refs = target.get("credential_refs") or {}
            if not isinstance(refs, dict):
                continue
            for field, secret_ref in refs.items():
                secret_id = _normalize_secret_ref(str(secret_ref))
                try:
                    summary = self.secret_service.get_secret_summary(workspace_id, secret_id)
                except SecretNotFoundError:
                    invalid.append(
                        {
                            "target": target_name,
                            "field": str(field),
                            "reason": "secret_not_found",
                        }
                    )
                    continue
                if summary.status != "active":
                    invalid.append(
                        {
                            "target": target_name,
                            "field": str(field),
                            "reason": "secret_not_active",
                        }
                    )
        if invalid:
            raise AgentSystemError(
                "database_secret_ref_invalid",
                "Database credential reference is missing or inactive.",
                status_code=400,
                retryable=False,
                details={"invalid_refs": invalid},
            )

    def _resolve_config_credentials(
        self,
        workspace_id: str,
        targets: dict[str, dict[str, Any]],
    ) -> dict[str, str]:
        if not workspace_id or self.secret_resolver is None:
            return {}
        credentials: dict[str, str] = {}
        for target_name, target in targets.items():
            refs = target.get("credential_refs") or {}
            if not isinstance(refs, dict):
                continue
            if target_name == "minio":
                self._resolve_optional_ref(
                    workspace_id,
                    refs,
                    ("access_key",),
                    purpose="minio_connect",
                    caller="minio_connector",
                    output_key="minio_access_key",
                    credentials=credentials,
                )
                self._resolve_optional_ref(
                    workspace_id,
                    refs,
                    ("secret_key",),
                    purpose="minio_connect",
                    caller="minio_connector",
                    output_key="minio_secret_key",
                    credentials=credentials,
                )
            elif target_name == "milvus":
                self._resolve_optional_ref(
                    workspace_id,
                    refs,
                    ("token", "primary"),
                    purpose="milvus_connect",
                    caller="milvus_connector",
                    output_key="milvus_token",
                    credentials=credentials,
                )
            elif target_name == "neo4j":
                self._resolve_optional_ref(
                    workspace_id,
                    refs,
                    ("username_password", "primary"),
                    purpose="neo4j_connect",
                    caller="neo4j_connector",
                    output_key="neo4j_username_password",
                    credentials=credentials,
                )
        return credentials

    def _resolve_optional_ref(
        self,
        workspace_id: str,
        refs: dict[str, Any],
        names: tuple[str, ...],
        *,
        purpose: str,
        caller: str,
        output_key: str,
        credentials: dict[str, str],
    ) -> None:
        for name in names:
            secret_ref = refs.get(name)
            if not secret_ref:
                continue
            if self.secret_resolver is None:
                return
            resolved = self.secret_resolver.resolve(
                workspace_id=workspace_id,
                secret_ref=_normalize_secret_ref(str(secret_ref)),
                purpose=purpose,  # type: ignore[arg-type]
                caller=caller,
            )
            credentials[output_key] = resolved.plaintext
            return

    def _credential_health_failures(
        self,
        workspace_id: str,
        config: dict[str, Any],
    ) -> dict[str, ServiceHealth]:
        if self.secret_resolver is None:
            return {}
        failures: dict[str, ServiceHealth] = {}
        targets = {target["target"]: target for target in config["targets"]}
        for target_name in TARGET_ORDER:
            try:
                self._resolve_config_credentials(
                    workspace_id,
                    {target_name: targets[target_name]},
                )
            except Exception as exc:  # noqa: BLE001 - health reports sanitized credential failures.
                failures[target_name] = ServiceHealth(
                    target=target_name,
                    status="unhealthy",
                    latency_ms=None,
                    message="credential_validation_failed",
                    checked_at=utc_now_iso(),
                    details={"error_type": exc.__class__.__name__},
                )
        return failures

    @staticmethod
    def _merge_credential_failures(
        services: list[ServiceHealth],
        failures: dict[str, ServiceHealth],
    ) -> list[ServiceHealth]:
        if not failures:
            return services
        merged = []
        seen: set[str] = set()
        for service in services:
            if service.target in failures:
                merged.append(failures[service.target])
            else:
                merged.append(service)
            seen.add(service.target)
        for target_name, failure in failures.items():
            if target_name not in seen:
                merged.append(failure)
        return merged


def _normalize_secret_ref(value: str) -> str:
    return str(value).removeprefix(SECRET_REF_PREFIX)
