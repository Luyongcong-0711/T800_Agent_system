from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

import httpx

from app.core.settings import Settings
from app.core.time import utc_now_iso
from app.schemas.health import ServiceHealth


async def _check_http(target: str, url: str, timeout_s: float = 5.0) -> ServiceHealth:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            response = await client.get(url)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if 200 <= response.status_code < 400:
            return ServiceHealth(
                target=target,
                status="healthy",
                latency_ms=latency_ms,
                message=f"HTTP {response.status_code}",
                checked_at=utc_now_iso(),
            )
        return ServiceHealth(
            target=target,
            status="unhealthy",
            latency_ms=latency_ms,
            message=f"HTTP {response.status_code}",
            checked_at=utc_now_iso(),
        )
    except Exception as exc:  # noqa: BLE001 - health checks must report every failure.
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return ServiceHealth(
            target=target,
            status="unhealthy",
            latency_ms=latency_ms,
            message=exc.__class__.__name__,
            checked_at=utc_now_iso(),
        )


def _check_http_sync(target: str, url: str, timeout_s: float = 5.0) -> ServiceHealth:
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            response = client.get(url)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if 200 <= response.status_code < 400:
            return ServiceHealth(
                target=target,
                status="healthy",
                latency_ms=latency_ms,
                message=f"HTTP {response.status_code}",
                checked_at=utc_now_iso(),
            )
        return ServiceHealth(
            target=target,
            status="unhealthy",
            latency_ms=latency_ms,
            message=f"HTTP {response.status_code}",
            checked_at=utc_now_iso(),
        )
    except Exception as exc:  # noqa: BLE001 - health checks must report every failure.
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return ServiceHealth(
            target=target,
            status="unhealthy",
            latency_ms=latency_ms,
            message=exc.__class__.__name__,
            checked_at=utc_now_iso(),
        )


def _check_tcp(target: str, url: str, timeout_s: float = 2.0) -> ServiceHealth:
    started = time.perf_counter()
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port
    if port is None:
        port = 6379 if parsed.scheme == "redis" else 7687
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return ServiceHealth(
                target=target,
                status="healthy",
                latency_ms=latency_ms,
                message="tcp_connect_ok",
                checked_at=utc_now_iso(),
                details={"host": host, "port": str(port)},
            )
    except Exception as exc:  # noqa: BLE001 - health checks must report every failure.
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return ServiceHealth(
            target=target,
            status="unhealthy",
            latency_ms=latency_ms,
            message=exc.__class__.__name__,
            checked_at=utc_now_iso(),
            details={"host": host, "port": str(port)},
        )


def _target_enabled(settings: Settings, target: str) -> bool:
    enabled_targets = getattr(settings, "enabled_targets", None)
    if isinstance(enabled_targets, dict) and target in enabled_targets:
        return bool(enabled_targets[target])
    return True


def _disabled_service(target: str) -> ServiceHealth:
    return ServiceHealth(
        target=target,
        status="disabled",
        latency_ms=None,
        message="service_disabled",
        checked_at=utc_now_iso(),
        details={"reason": "database_target_disabled"},
    )


async def check_database_services(settings: Settings) -> list[ServiceHealth]:
    minio = (
        await _check_http("minio", f"{settings.minio_endpoint.rstrip('/')}/minio/health/live")
        if _target_enabled(settings, "minio")
        else _disabled_service("minio")
    )
    milvus_health_url = settings.milvus_uri.replace(":19530", ":9091").rstrip("/") + "/healthz"
    milvus = (
        await _check_http("milvus", milvus_health_url)
        if _target_enabled(settings, "milvus")
        else _disabled_service("milvus")
    )
    neo4j = (
        await _check_http("neo4j", settings.neo4j_http_url)
        if _target_enabled(settings, "neo4j")
        else _disabled_service("neo4j")
    )
    redis = (
        _check_tcp("redis", settings.redis_url)
        if _target_enabled(settings, "redis")
        else _disabled_service("redis")
    )
    return [minio, milvus, neo4j, redis]


def check_database_services_sync(settings: Settings) -> list[ServiceHealth]:
    minio = (
        _check_http_sync("minio", f"{settings.minio_endpoint.rstrip('/')}/minio/health/live")
        if _target_enabled(settings, "minio")
        else _disabled_service("minio")
    )
    milvus_health_url = settings.milvus_uri.replace(":19530", ":9091").rstrip("/") + "/healthz"
    milvus = (
        _check_http_sync("milvus", milvus_health_url)
        if _target_enabled(settings, "milvus")
        else _disabled_service("milvus")
    )
    neo4j = (
        _check_http_sync("neo4j", settings.neo4j_http_url)
        if _target_enabled(settings, "neo4j")
        else _disabled_service("neo4j")
    )
    redis = (
        _check_tcp("redis", settings.redis_url)
        if _target_enabled(settings, "redis")
        else _disabled_service("redis")
    )
    return [minio, milvus, neo4j, redis]
