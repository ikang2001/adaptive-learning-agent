from typing import ClassVar

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.tasks import execute_job, purge_due_accounts


class WorkerSettings:
    functions: ClassVar[list[object]] = [execute_job]
    cron_jobs: ClassVar[list[object]] = [cron(purge_due_accounts, hour=3, minute=0)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 25
    job_timeout = 600
    max_tries = 3
    health_check_interval = 30
