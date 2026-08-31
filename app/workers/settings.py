from typing import Any, ClassVar, cast
from wsgiref.simple_server import WSGIServer

from arq import cron
from arq.connections import RedisSettings
from prometheus_client import start_http_server

from app.config import get_settings
from app.workers.tasks import (
    execute_job,
    expire_proposals,
    purge_due_accounts,
    reconcile_stale_jobs,
)


async def startup(ctx: dict[str, Any]) -> None:
    server, thread = start_http_server(9101)
    ctx["metrics_server"] = server
    ctx["metrics_thread"] = thread


async def shutdown(ctx: dict[str, Any]) -> None:
    server = cast(WSGIServer | None, ctx.get("metrics_server"))
    if server is not None:
        server.shutdown()


class WorkerSettings:
    functions: ClassVar[list[object]] = [execute_job]
    cron_jobs: ClassVar[list[object]] = [
        cron(purge_due_accounts, hour=3, minute=0),
        cron(reconcile_stale_jobs, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(expire_proposals, minute={2, 12, 22, 32, 42, 52}),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 25
    job_timeout = 600
    max_tries = 1
    health_check_interval = 30
    on_startup = startup
    on_shutdown = shutdown
