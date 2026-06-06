from __future__ import annotations

import re
from time import perf_counter
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.bootstrap import router as bootstrap_router
from app.api.database import router as database_router
from app.api.dependencies import build_job_worker, build_secret_service, get_object_store
from app.api.documents import router as documents_router
from app.api.graph import router as graph_router
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.mcp import router as mcp_router
from app.api.mcp import tool_inventory_router
from app.api.memory import router as memory_router
from app.api.memory import snapshot_router as memory_snapshot_router
from app.api.model_configs import router as model_configs_router
from app.api.observability import router as observability_router
from app.api.runs import router as runs_router
from app.api.secrets import router as secrets_router
from app.api.skills import router as skills_router
from app.api.subagents import router as subagents_router
from app.api.threads import router as threads_router
from app.core.errors import AgentSystemError
from app.core.ids import new_id
from app.core.settings import get_settings
from app.jobs.worker import JobWorkerDaemon
from app.observability.service import ObservabilityService
from app.schemas.common import ErrorResponse
from app.schemas.secret import CreateSecretRequest
from app.secret_store.master_key import assert_master_key_available

SENSITIVE_FIELDS = {
    "plaintext",
    "authorization",
    "token",
    "password",
    "ciphertext",
    "nonce",
    "tag",
    "secret",
    "cookie",
    "api_key",
}


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(field in lowered for field in SENSITIVE_FIELDS)


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if _is_sensitive_key(key) else _redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive(item) for item in value)
    return value


def _redact_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    redacted: list[dict[str, Any]] = []
    for error in errors:
        next_error = dict(error)
        next_error.pop("input", None)
        if isinstance(next_error.get("ctx"), dict):
            next_error["ctx"] = {
                key: value
                if isinstance(value, str | int | float | bool | type(None))
                else str(value)
                for key, value in next_error["ctx"].items()
            }
        next_error = _redact_sensitive(next_error)
        redacted.append(next_error)
    return redacted


def _workspace_from_path(path: str) -> str | None:
    match = re.search(r"/workspaces/([^/]+)", path)
    return match.group(1) if match else None


def _record_request_log(
    app: FastAPI,
    *,
    trace_id: str,
    request: Request,
    status_code: int | None,
    duration_ms: int,
    error_type: str | None = None,
) -> None:
    try:
        provider = app.dependency_overrides.get(get_object_store, get_object_store)
        ObservabilityService(provider()).record_event(
            component="api",
            event_type="http_request_failed" if error_type else "http_request_finished",
            message="HTTP request failed." if error_type else "HTTP request finished.",
            workspace_id=_workspace_from_path(request.url.path),
            severity="ERROR" if error_type else "INFO",
            trace_id=trace_id,
            duration_ms=duration_ms,
            status="error" if error_type else "ok",
            error_type=error_type,
            payload_summary={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
            },
        )
    except Exception:
        return


def _request_trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None) or request.headers.get("x-trace-id")


async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    body = ErrorResponse(
        error_type="validation_failed",
        message_for_user="Request validation failed.",
        retryable=False,
        trace_id=_request_trace_id(request),
        details={"errors": _redact_validation_errors(exc.errors())},
    )
    return JSONResponse(status_code=422, content=body.model_dump())


async def pydantic_validation_error_handler(
    request: Request,
    exc: ValidationError,
) -> JSONResponse:
    body = ErrorResponse(
        error_type="validation_failed",
        message_for_user="Request validation failed.",
        retryable=False,
        trace_id=_request_trace_id(request),
        details={"errors": _redact_validation_errors(exc.errors())},
    )
    return JSONResponse(status_code=422, content=body.model_dump())


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = _redact_sensitive(exc.detail)
    error_type = detail if isinstance(detail, str) else "http_error"
    body = ErrorResponse(
        error_type=error_type,
        message_for_user=error_type,
        retryable=False,
        trace_id=_request_trace_id(request),
        details={} if isinstance(detail, str) else {"detail": detail},
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


async def agent_system_error_handler(request: Request, exc: AgentSystemError) -> JSONResponse:
    body = ErrorResponse(
        error_type=exc.error_type,
        message_for_user=exc.message_for_user,
        retryable=exc.retryable,
        trace_id=_request_trace_id(request),
        details=_redact_sensitive(exc.details),
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    message = str(exc)
    if "path identifiers may not contain slashes or traversal" not in message:
        raise exc
    body = ErrorResponse(
        error_type="invalid_identifier",
        message_for_user="Invalid path identifier.",
        retryable=False,
        trace_id=_request_trace_id(request),
        details={},
    )
    return JSONResponse(status_code=400, content=body.model_dump())


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Agent System API",
        version="0.1.0",
        description="P0 Agent System backend: REST + SSE API entrypoint.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url, "http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(AgentSystemError, agent_system_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(ValidationError, pydantic_validation_error_handler)
    app.add_exception_handler(ValueError, value_error_handler)

    @app.middleware("http")
    async def observability_middleware(request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or new_id("trace")
        request.state.trace_id = trace_id
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            _record_request_log(
                app,
                trace_id=trace_id,
                request=request,
                status_code=None,
                duration_ms=int((perf_counter() - started) * 1000),
                error_type=exc.__class__.__name__,
            )
            raise
        response.headers["x-trace-id"] = trace_id
        _record_request_log(
            app,
            trace_id=trace_id,
            request=request,
            status_code=response.status_code,
            duration_ms=int((perf_counter() - started) * 1000),
        )
        return response

    app.include_router(bootstrap_router)
    app.include_router(health_router)
    app.include_router(database_router)
    app.include_router(model_configs_router)
    app.include_router(threads_router)
    app.include_router(runs_router)
    app.include_router(jobs_router)
    app.include_router(secrets_router)
    app.include_router(documents_router)
    app.include_router(graph_router)
    app.include_router(mcp_router)
    app.include_router(tool_inventory_router)
    app.include_router(memory_router)
    app.include_router(memory_snapshot_router)
    app.include_router(skills_router)
    app.include_router(subagents_router)
    app.include_router(observability_router)

    @app.on_event("startup")
    async def verify_secret_store_configuration() -> None:
        assert_master_key_available(settings)
        if not settings.is_development_like:
            get_object_store()
        _seed_development_default_model_secret(settings)
        _seed_development_default_embedding_secret(settings)
        if settings.job_worker_autostart:
            object_store = get_object_store()
            worker = build_job_worker(object_store, settings=settings)
            daemon = JobWorkerDaemon(
                worker,
                workspace_id=settings.default_workspace_id,
                poll_interval_seconds=settings.job_worker_poll_interval_seconds,
                max_jobs_per_tick=settings.job_worker_max_jobs_per_tick,
            )
            app.state.job_worker_daemons = {settings.default_workspace_id: daemon}
            await daemon.start()

    @app.on_event("shutdown")
    async def stop_job_workers() -> None:
        registry = getattr(app.state, "job_worker_daemons", {})
        if isinstance(registry, dict):
            for daemon in list(registry.values()):
                if isinstance(daemon, JobWorkerDaemon):
                    await daemon.stop()

    return app


app = create_app()


def _seed_development_default_model_secret(settings) -> None:
    if not settings.is_development_like:
        return
    if not settings.default_model_api_key or not settings.default_model_api_key_ref:
        return
    secret_id = settings.default_model_api_key_ref.removeprefix("secret_ref://")
    object_store = get_object_store()
    build_secret_service(object_store, settings).ensure_static_secret(
        settings.default_workspace_id,
        secret_id=secret_id,
        request=CreateSecretRequest(
            type="model_api_key",
            display_name="Default local model API key",
            plaintext=settings.default_model_api_key,
        ),
        created_by=settings.default_user_id,
    )


def _seed_development_default_embedding_secret(settings) -> None:
    if not settings.is_development_like:
        return
    if not settings.default_embedding_api_key or not settings.default_embedding_api_key_ref:
        return
    secret_id = settings.default_embedding_api_key_ref.removeprefix("secret_ref://")
    object_store = get_object_store()
    build_secret_service(object_store, settings).ensure_static_secret(
        settings.default_workspace_id,
        secret_id=secret_id,
        request=CreateSecretRequest(
            type="embedding_api_key",
            display_name="Default local embedding API key",
            plaintext=settings.default_embedding_api_key,
        ),
        created_by=settings.default_user_id,
    )
