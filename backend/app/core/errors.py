from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.schemas.common import ErrorResponse


class AgentSystemError(Exception):
    def __init__(
        self,
        error_type: str,
        message_for_user: str,
        status_code: int = 400,
        retryable: bool = False,
        details: dict | None = None,
    ) -> None:
        self.error_type = error_type
        self.message_for_user = message_for_user
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message_for_user)


async def agent_error_handler(_: Request, exc: AgentSystemError) -> JSONResponse:
    body = ErrorResponse(
        error_type=exc.error_type,
        message_for_user=exc.message_for_user,
        retryable=exc.retryable,
        details=exc.details,
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


async def validation_error_handler(_: Request, exc: ValidationError) -> JSONResponse:
    body = ErrorResponse(
        error_type="validation_failed",
        message_for_user="参数校验失败。",
        retryable=False,
        details={"errors": exc.errors()},
    )
    return JSONResponse(status_code=422, content=body.model_dump())

