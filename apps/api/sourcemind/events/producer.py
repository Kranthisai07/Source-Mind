"""
Kafka event producer for SourceMind event sourcing backbone.

Events published:
  - memory.created    → triggers relationship detection, search indexing
  - memory.updated    → triggers attribution recompute
  - memory.deleted    → soft-delete propagation
  - attribution.computed → triggers Neo4j graph update

Topic naming convention: sourcemind.<entity>.<event>
"""

import json
from typing import Any
from uuid import UUID

import structlog
from aiokafka import AIOKafkaProducer

from sourcemind.core.config import get_settings

logger = structlog.get_logger(__name__)

_producer: AIOKafkaProducer | None = None


async def init_producer() -> None:
    """Initialize Kafka async producer. Called at application startup."""
    global _producer
    settings = get_settings()

    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v, default=_json_default).encode(),
        key_serializer=lambda k: k.encode() if k else None,
        acks="all",               # Wait for all replicas
        enable_idempotence=True,  # Exactly-once producer semantics
        max_batch_size=16384,
        compression_type="snappy",
    )

    try:
        await _producer.start()
        logger.info("kafka.producer_started", servers=settings.kafka_bootstrap_servers)
    except Exception as exc:
        logger.error("kafka.producer_start_failed", error=str(exc))
        raise


async def close_producer() -> None:
    """Stop Kafka producer. Called at application shutdown."""
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None
        logger.info("kafka.producer_stopped")


async def publish_event(topic: str, event: dict[str, Any], key: str | None = None) -> None:
    """
    Publish an event to the specified Kafka topic.

    Args:
        topic: Kafka topic name (e.g. "sourcemind.memory.created")
        event: Event payload (must be JSON-serializable)
        key: Optional partition key (e.g. workspace_id for ordering guarantees)
    """
    if _producer is None:
        logger.warning("kafka.producer_not_initialized", topic=topic)
        return

    try:
        await _producer.send_and_wait(topic, value=event, key=key)
        logger.debug("kafka.event_published", topic=topic, key=key)
    except Exception as exc:
        logger.error("kafka.publish_failed", topic=topic, error=str(exc))
        raise


def _json_default(obj: Any) -> Any:
    """Custom JSON serializer for non-standard types."""
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ─── Topic constants ──────────────────────────────────────────────────────────

TOPIC_MEMORY_CREATED = "sourcemind.memory.created"
TOPIC_MEMORY_UPDATED = "sourcemind.memory.updated"
TOPIC_MEMORY_DELETED = "sourcemind.memory.deleted"
TOPIC_ATTRIBUTION_COMPUTED = "sourcemind.attribution.computed"
TOPIC_CONFLICT_DETECTED = "sourcemind.conflict.detected"
TOPIC_JOB_STATUS = "sourcemind.ingestion.job_status"
