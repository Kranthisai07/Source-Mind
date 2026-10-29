"""ensure_topics waits for the graph topics to be usable, and fails loudly when they never are."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka.errors import TopicAlreadyExistsError

from sourcemind.events import producer

TOPICS = ["graph.test.a", "graph.test.a.dlq"]
SETTINGS = SimpleNamespace(
    kafka_bootstrap_servers="127.0.0.1:1",
    kafka_graph_topic=TOPICS[0],
    kafka_graph_dlq_topic=TOPICS[1],
    kafka_graph_partitions=2,
)


def _described(*, ready: bool = True, names=TOPICS) -> list[dict]:
    part = {"error_code": 0, "partition": 0, "leader": 0 if ready else -1}
    return [
        {"topic": n, "error_code": 0 if ready else 37, "partitions": [part] if ready else []}
        for n in names
    ]


def _admin(describes: list[list[dict]], *, create_side_effect=None) -> MagicMock:
    admin = MagicMock()
    admin.start = AsyncMock()
    admin.close = AsyncMock()
    admin.create_topics = AsyncMock(side_effect=create_side_effect)
    admin.describe_topics = AsyncMock(side_effect=describes)
    return admin


def _run(admin):
    return (
        patch.object(producer, "AIOKafkaAdminClient", return_value=admin),
        patch.object(producer, "get_settings", return_value=SETTINGS),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("description", "ready"),
    [
        (None, False),
        ({"topic": "t", "error_code": 3, "partitions": []}, False),  # unknown topic
        ({"topic": "t", "error_code": 0, "partitions": []}, False),  # listed, no partitions yet
        (
            {"topic": "t", "error_code": 0, "partitions": [{"error_code": 0, "leader": -1}]},
            False,  # partition without a leader
        ),
        (
            {"topic": "t", "error_code": 0, "partitions": [{"error_code": 5, "leader": 0}]},
            False,  # partition in error
        ),
        (
            {"topic": "t", "error_code": 0, "partitions": [{"error_code": 0, "leader": 0}] * 3},
            True,
        ),
    ],
)
def test_a_topic_is_ready_only_with_partitions_and_leaders(description, ready) -> None:
    assert producer._topic_ready(description) is ready


@pytest.mark.unit
async def test_it_waits_until_every_topic_has_partitions_and_leaders() -> None:
    admin = _admin([_described(ready=False), _described(names=TOPICS[:1]), _described()])
    a, b = _run(admin)
    with a, b:
        await producer.ensure_topics()
    assert admin.describe_topics.await_count == 3  # it did not return after creating
    assert admin.create_topics.await_count == 2
    admin.close.assert_awaited_once()


@pytest.mark.unit
async def test_topics_are_created_with_the_configured_partition_count() -> None:
    admin = _admin([_described()])
    a, b = _run(admin)
    with a, b:
        await producer.ensure_topics()
    created = [call.args[0][0] for call in admin.create_topics.await_args_list]
    assert [t.num_partitions for t in created] == [2, 2]


@pytest.mark.unit
async def test_existing_topics_are_fine() -> None:
    admin = _admin([_described()], create_side_effect=TopicAlreadyExistsError())
    a, b = _run(admin)
    with a, b:
        await producer.ensure_topics()
    assert admin.describe_topics.await_count == 1


@pytest.mark.unit
async def test_a_broker_that_never_makes_them_usable_fails_loudly() -> None:
    """The case that burned the first version: a broker out of partition budget accepts the
    create and then never allocates, so the topic stays in error forever."""
    admin = _admin([_described(ready=False)] * 50)
    a, b = _run(admin)
    with a, b, patch.object(producer, "TOPIC_READY_TIMEOUT_SECONDS", 0.0):
        with pytest.raises(producer.TopicsNotReadyError, match="partition and memory limits"):
            await producer.ensure_topics()
    admin.close.assert_awaited_once()  # the admin client is released on failure too
