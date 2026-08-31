from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest, multiprocess
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import api_router
from app.config import get_settings
from app.errors import register_error_handlers
from app.infrastructure.db.session import engine
from app.infrastructure.redis import get_redis
from app.observability.logging import configure_logging
from app.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.observability.tracing import configure_tracing

configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("application_started", environment=settings.app_env)
    yield
    await get_redis().aclose()
    await engine.dispose()
    logger.info("application_stopped")


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    lifespan=lifespan,
)
register_error_handlers(app)
app.include_router(api_router, prefix=settings.api_prefix)
configure_tracing(
    app,
    engine,
    service_name="adaptive-learning-agent",
    endpoint=settings.otel_exporter_otlp_endpoint,
)
if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - started
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_LATENCY.labels(request.method, path).observe(duration)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health/live", tags=["operations"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["operations"])
async def readiness() -> dict[str, str]:
    dependencies: dict[str, str] = {}
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        dependencies["database"] = "ok"
    except SQLAlchemyError:
        dependencies["database"] = "unavailable"
    try:
        await get_redis().ping()
        dependencies["redis"] = "ok"
    except RedisError:
        dependencies["redis"] = "unavailable"
    if "unavailable" in dependencies.values():
        from app.errors import AppError

        raise AppError(503, "DEPENDENCY_UNAVAILABLE", "one or more dependencies are unavailable")
    return {"status": "ready", **dependencies}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        payload = generate_latest(registry)
    else:
        payload = generate_latest()
    return Response(payload, media_type=CONTENT_TYPE_LATEST)
