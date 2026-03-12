"""
Celery application configuration.

Celery handles async background tasks:
  - Document ingestion pipeline (7 stages)
  - Attribution recomputation
  - Conflict detection
  - Knowledge gap analysis

The broker and result backend both use Redis.
"""

from celery import Celery

from sourcemind.core.config import get_settings

settings = get_settings()

app = Celery(
    "sourcemind",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "sourcemind.workers.ingestion",
        "sourcemind.workers.attribution",
    ],
)

app.conf.update(
    # Task serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Idempotency: all tasks must be safe to run twice
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Result TTL (keep results for 1 hour)
    result_expires=3600,

    # Retry settings
    task_max_retries=3,
    task_default_retry_delay=60,  # seconds

    # Routing
    task_routes={
        "sourcemind.workers.ingestion.*": {"queue": "ingestion"},
        "sourcemind.workers.attribution.*": {"queue": "attribution"},
    },
    task_default_queue="default",
)
