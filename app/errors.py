from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass(slots=True)
class AppError(Exception):
    status_code: int
    code: str
    detail: str
    extra: dict[str, Any] | None = None


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        body: dict[str, Any] = {
            "type": f"https://errors.learning-agent.local/{exc.code}",
            "title": exc.code,
            "status": exc.status_code,
            "detail": exc.detail,
            "instance": str(request.url.path),
        }
        if exc.extra:
            body["errors"] = exc.extra
        return JSONResponse(
            body, status_code=exc.status_code, media_type="application/problem+json"
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            {
                "type": "https://errors.learning-agent.local/VALIDATION_ERROR",
                "title": "VALIDATION_ERROR",
                "status": 422,
                "detail": "request validation failed",
                "instance": request.url.path,
                "errors": exc.errors(),
            },
            status_code=422,
            media_type="application/problem+json",
        )
