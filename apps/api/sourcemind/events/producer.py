"""Kafka (Redpanda) producer used by the graph outbox relay.

Only the relay publishes. Request handlers and Celery tasks write to the
``graph_outbox`` table in their own transaction instead (see
``sourcemind.services.graph.outbox``): a long-lived async producer cannot live
in a Celery worker, where every task runs under its own ``asyncio.run`` loop,
and publishing inline would lose events on a crash between commit and send.

Unlike the producer this replaces, failures here are raised, never swallowed:
the relay must know a publish did not happen so it leaves the row unpublished.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from sourcemind.core.config import get_settings
from sourcemind.events.envelope import dumps

logger = structlog.get_logger(__name__)


def _encode_key(key: str | None) -> bytes | None:
    return key.encode() if key else None


class GraphEventProducer:
    """Owns one producer bound to the running event loop. Use as an async context manager."""

    def __init__(self, bootstrap_servers: str | None = None, topic: str | None = None) -> None:
        settings = get_settings()
        self._servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self.topic = topic or settings.kafka_graph_topic
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        # No compression: python-snappy is not a dependency, and aiokafka refuses a
        # codec it cannot load. Events are small ids-and-weights documents.
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._servers,
            value_serializer=dumps,
            key_serializer=_encode_key,
            acks="all",
            enable_idempotence=True,
        )
        await self._producer.start()
        logger.info("graph.producer_started", servers=self._servers, topic=self.topic)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def __aenter__(self) -> GraphEventProducer:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def send(self, value: dict[str, Any], key: str | None = None) -> None:
        """Send one JSON document to this producer's topic and wait for the acknowledgement."""
        if self._producer is None:
            raise RuntimeError("GraphEventProducer is not started")
        await self._producer.send_and_wait(self.topic, value=value, key=key)

    async def publish(self, envelope: dict[str, Any]) -> None:
        """Send one graph event.

        Keyed by workspace so one workspace's events stay in order on one partition.
        """
        await self.send(envelope, key=str(envelope["workspace_id"]))


TOPIC_READY_TIMEOUT_SECONDS = 30.0


class TopicsNotReadyError(RuntimeError):
    """The broker did not make the graph topics usable in time."""


def _topic_ready(description: dict[str, Any] | None) -> bool:
    """Created AND usable: a topic can be listed before it has partitions or leaders.

    A broker that is out of partition budget (a 512 MB development Redpanda allows about 128)
    accepts the create request and then never allocates the partitions; the topic stays in an
    error state, and nothing publishing to it can make progress.
    """
    if not description or description.get("error_code") or not description.get("partitions"):
        return False
    return all(
        not p.get("error_code") and p.get("leader", -1) >= 0 for p in description["partitions"]
    )


async def ensure_topics(
    bootstrap_servers: str | None = None, topics: list[str] | None = None
) -> None:
    """Create the graph topics if they do not exist and wait until they are usable.

    Idempotent. Raises ``TopicsNotReadyError`` if they are not usable within the timeout, so a
    broker that cannot host them fails the relay or projector loudly at start instead of leaving
    them waiting on a topic that will never appear.
    """
    settings = get_settings()
    wanted = topics or [settings.kafka_graph_topic, settings.kafka_graph_dlq_topic]
    admin = AIOKafkaAdminClient(
        bootstrap_servers=bootstrap_servers or settings.kafka_bootstrap_servers
    )
    await admin.start()
    try:
        for name in wanted:
            try:
                await admin.create_topics(
                    [
                        NewTopic(
                            name=name,
                            num_partitions=settings.kafka_graph_partitions,
                            replication_factor=1,
                        )
                    ]
                )
                logger.info("graph.topic_created", topic=name)
            except TopicAlreadyExistsError:
                continue
        deadline = time.monotonic() + TOPIC_READY_TIMEOUT_SECONDS
        while True:
            described = {d["topic"]: d for d in await admin.describe_topics(wanted)}
            pending = [name for name in wanted if not _topic_ready(described.get(name))]
            if not pending:
                return
            if time.monotonic() >= deadline:
                raise TopicsNotReadyError(
                    f"topics not usable after {TOPIC_READY_TIMEOUT_SECONDS:.0f}s: {pending}; "
                    "check the broker's partition and memory limits"
                )
            await asyncio.sleep(0.2)
    finally:
        await admin.close()
