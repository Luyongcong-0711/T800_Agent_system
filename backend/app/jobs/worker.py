from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Any, Literal

from app.jobs.service import JobService

JobOutcomeStatus = Literal["succeeded", "partial_success", "failed"]


@dataclass(frozen=True)
class JobContext:
    workspace_id: str
    job_id: str
    job_type: str
    manifest: dict[str, Any]
    fencing_token: str
    runtime_instance_id: str
    job_service: JobService

    @property
    def input(self) -> dict[str, Any]:
        value = self.manifest.get("input") or {}
        return value if isinstance(value, dict) else {}

    @property
    def target_scope(self) -> dict[str, Any]:
        value = self.manifest.get("target_scope") or {}
        return value if isinstance(value, dict) else {}

    def mark_running(self, *, stage: str, message: str, percent: float = 0) -> dict[str, Any]:
        return self.job_service.mark_job_running(
            self.workspace_id,
            self.job_id,
            stage=stage,
            message=message,
            percent=percent,
            fencing_token=self.fencing_token,
        )


@dataclass(frozen=True)
class JobHandlerResult:
    status: JobOutcomeStatus
    stage: str
    message: str
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error_type: str | None = None
    retryable: bool = False

    @classmethod
    def succeeded(
        cls,
        *,
        stage: str,
        message: str,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> JobHandlerResult:
        return cls(
            status="succeeded",
            stage=stage,
            message=message,
            artifacts=artifacts or [],
        )

    @classmethod
    def failed(
        cls,
        *,
        stage: str,
        message: str,
        error_type: str,
        retryable: bool = False,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> JobHandlerResult:
        return cls(
            status="failed",
            stage=stage,
            message=message,
            artifacts=artifacts or [],
            error_type=error_type,
            retryable=retryable,
        )

    @classmethod
    def partial_success(
        cls,
        *,
        stage: str,
        message: str,
        artifacts: list[dict[str, Any]] | None = None,
        error_type: str | None = None,
        retryable: bool = True,
    ) -> JobHandlerResult:
        return cls(
            status="partial_success",
            stage=stage,
            message=message,
            artifacts=artifacts or [],
            error_type=error_type,
            retryable=retryable,
        )


JobHandler = Callable[[JobContext], JobHandlerResult]


class JobWorker:
    def __init__(
        self,
        job_service: JobService,
        handlers: dict[str, JobHandler] | None = None,
    ) -> None:
        self.job_service = job_service
        self.handlers = dict(handlers or {})

    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        self.handlers[job_type] = handler

    def process_batch(
        self,
        workspace_id: str,
        *,
        job_types: list[str] | None = None,
        max_jobs: int = 1,
    ) -> dict[str, Any]:
        processed_jobs: list[dict[str, Any]] = []
        limit = max(1, min(int(max_jobs), 100))
        for _ in range(limit):
            result = self.process_next(workspace_id, job_types=job_types)
            if not result["claimed"]:
                return {
                    "workspace_id": workspace_id,
                    "claimed": bool(processed_jobs),
                    "processed_count": len(processed_jobs),
                    "jobs": processed_jobs,
                    "drained": True,
                }
            processed_jobs.append(result["job"])
        return {
            "workspace_id": workspace_id,
            "claimed": bool(processed_jobs),
            "processed_count": len(processed_jobs),
            "jobs": processed_jobs,
            "drained": False,
        }

    def process_next(
        self,
        workspace_id: str,
        *,
        job_types: list[str] | None = None,
    ) -> dict[str, Any]:
        effective_job_types = job_types
        if effective_job_types is None and self.handlers:
            effective_job_types = list(self.handlers)
        claimed = self.job_service.claim_next_job_for_worker(
            workspace_id,
            job_types=effective_job_types,
        )
        if claimed is None:
            return {"workspace_id": workspace_id, "claimed": False, "job": None}

        job_id = claimed["job_id"]
        owner = claimed.get("owner") or {}
        fencing_token = str(owner.get("fencing_token") or "")
        context = JobContext(
            workspace_id=workspace_id,
            job_id=job_id,
            job_type=claimed["job_type"],
            manifest=claimed,
            fencing_token=fencing_token,
            runtime_instance_id=self.job_service.runtime_instance_id,
            job_service=self.job_service,
        )
        handler = self.handlers.get(context.job_type)
        if handler is None:
            job = self.job_service.mark_job_failed(
                workspace_id,
                job_id,
                stage="dispatch",
                message=f"No Job handler registered for {context.job_type}.",
                error_type="job_handler_not_found",
                retryable=False,
                fencing_token=fencing_token,
            )
            return {"workspace_id": workspace_id, "claimed": True, "job": job}

        try:
            result = handler(context)
        except Exception as exc:  # noqa: BLE001 - worker boundary records handler failure.
            job = self.job_service.mark_job_failed(
                workspace_id,
                job_id,
                stage="handler_exception",
                message="Job handler failed.",
                error_type=exc.__class__.__name__,
                retryable=True,
                fencing_token=fencing_token,
            )
            return {"workspace_id": workspace_id, "claimed": True, "job": job}

        if result.status == "succeeded":
            job = self.job_service.mark_job_succeeded(
                workspace_id,
                job_id,
                stage=result.stage,
                message=result.message,
                artifacts=result.artifacts,
                fencing_token=fencing_token,
            )
        elif result.status == "partial_success":
            job = self.job_service.mark_job_partial_success(
                workspace_id,
                job_id,
                stage=result.stage,
                message=result.message,
                artifacts=result.artifacts,
                error_type=result.error_type or "job_handler_partial_success",
                retryable=result.retryable,
                fencing_token=fencing_token,
            )
        else:
            job = self.job_service.mark_job_failed(
                workspace_id,
                job_id,
                stage=result.stage,
                message=result.message,
                error_type=result.error_type or "job_handler_failed",
                retryable=result.retryable,
                artifacts=result.artifacts,
                fencing_token=fencing_token,
            )
        return {"workspace_id": workspace_id, "claimed": True, "job": job}


class JobWorkerDaemon:
    def __init__(
        self,
        worker: JobWorker,
        *,
        workspace_id: str,
        job_types: list[str] | None = None,
        poll_interval_seconds: float = 1.0,
        max_jobs_per_tick: int = 5,
    ) -> None:
        self.worker = worker
        self.workspace_id = workspace_id
        self.job_types = job_types
        self.poll_interval_seconds = max(0.05, min(float(poll_interval_seconds), 60.0))
        self.max_jobs_per_tick = max(1, min(int(max_jobs_per_tick), 100))
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.last_tick_at: str | None = None
        self.last_error: dict[str, str] | None = None
        self.last_result: dict[str, Any] | None = None
        self.tick_count = 0
        self.processed_count = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    async def start(self) -> dict[str, Any]:
        if self.running:
            return self.status()
        self._stop_event = Event()
        self.started_at = _now_iso()
        self.stopped_at = None
        self.last_error = None
        self._thread = Thread(
            target=self._run_loop_sync,
            name=f"job-worker-{self.workspace_id}",
            daemon=True,
        )
        self._thread.start()
        return self.status()

    async def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 5)
        with self._lock:
            self.stopped_at = self.stopped_at or _now_iso()
        return self.status()

    async def run_once(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._run_once_sync)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "workspace_id": self.workspace_id,
                "running": self.running,
                "job_types": self.job_types or list(self.worker.handlers),
                "poll_interval_seconds": self.poll_interval_seconds,
                "max_jobs_per_tick": self.max_jobs_per_tick,
                "tick_count": self.tick_count,
                "processed_count": self.processed_count,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "last_tick_at": self.last_tick_at,
                "last_error": self.last_error,
                "last_result": self.last_result,
            }

    def _run_loop_sync(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._run_once_sync()
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            with self._lock:
                self.stopped_at = _now_iso()

    def _run_once_sync(self) -> dict[str, Any]:
        with self._lock:
            self.last_tick_at = _now_iso()
            self.tick_count += 1
        try:
            result = self.worker.process_batch(
                self.workspace_id,
                job_types=self.job_types,
                max_jobs=self.max_jobs_per_tick,
            )
        except Exception as exc:  # noqa: BLE001 - daemon boundary stores health.
            error = {
                "error_type": exc.__class__.__name__,
                "message": str(exc) or exc.__class__.__name__,
            }
            with self._lock:
                self.last_error = error
                self.last_result = None
            return {
                "workspace_id": self.workspace_id,
                "processed_count": 0,
                "error": error,
            }
        with self._lock:
            self.last_error = None
            self.last_result = result
            self.processed_count += int(result.get("processed_count") or 0)
        return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
