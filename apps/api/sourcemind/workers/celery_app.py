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
        "sourcemind.workers.connector_tasks",
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
        "sourcemind.workers.connector_tasks.*": {"queue": "connectors"},
    },
    task_default_queue="default",
)


def connector_beat_schedule(cfg) -> dict:
    """Beat entries for connectors; empty unless the scheduler is explicitly enabled."""
    if not cfg.connector_scheduler_enabled:
        return {}
    return {
        "connector-sync-tick": {
            "task": "sourcemind.workers.connector_tasks.dispatch_due_syncs",
            "schedule": float(cfg.connector_sync_tick_seconds),
            "options": {"queue": "connectors"},
        }
    }


# Embedded beat (worker started with -B): the tick runs only when explicitly enabled.
app.conf.beat_schedule = connector_beat_schedule(settings)
