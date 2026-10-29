"""Wire-format tests for graph projection events."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from uuid import uuid4

import pytest

from sourcemind.events.envelope import (
    EVENT_MEMORY_STATE,
    SCHEMA_VERSION,
    EnvelopeError,
    build_envelope,
    dumps,
    parse_envelope,
)


def _envelope(**overrides):
    base = {
        "event_id": uuid4(),
        "seq": 7,
        "occurred_at": datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
        "workspace_id": uuid4(),
        "event_type": EVENT_MEMORY_STATE,
        "aggregate_id": uuid4(),
        "payload": {"memory_id": "m"},
    }
    return build_envelope(**{**base, **overrides})


@pytest.mark.unit
def test_round_trip_preserves_every_field() -> None:
    wire = dumps(_envelope(seq=42))
    event = parse_envelope(wire)
    assert event.seq == 42
    assert event.type == EVENT_MEMORY_STATE
    assert event.payload == {"memory_id": "m"}
    assert json.loads(wire)["schema_version"] == SCHEMA_VERSION


@pytest.mark.unit
def test_serialisation_is_deterministic() -> None:
    envelope = _envelope()
    assert dumps(envelope) == dumps(dict(reversed(list(envelope.items()))))


@pytest.mark.unit
def test_serialiser_handles_the_types_the_old_producer_rejected() -> None:
    class Kind(Enum):
        A = "a"

    wire = dumps({"when": datetime(2026, 1, 1), "n": Decimal("0.5"), "k": Kind.A, "u": uuid4()})
    data = json.loads(wire)
    assert data["when"].endswith("+00:00")  # naive datetimes are treated as UTC
    assert data["n"] == 0.5
    assert data["k"] == "a"


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [b"not json", b"[]", b"{}", json.dumps({"schema_version": 1}).encode()],
)
def test_malformed_messages_are_rejected(raw: bytes) -> None:
    with pytest.raises(EnvelopeError):
        parse_envelope(raw)


@pytest.mark.unit
def test_unknown_schema_version_is_rejected_not_guessed() -> None:
    data = json.loads(dumps(_envelope()))
    data["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(EnvelopeError, match="schema_version"):
        parse_envelope(json.dumps(data))


@pytest.mark.unit
@pytest.mark.parametrize("seq", [0, -1, True, "7", 1.5])
def test_seq_must_be_a_positive_integer(seq: object) -> None:
    data = json.loads(dumps(_envelope()))
    data["seq"] = seq
    with pytest.raises(EnvelopeError, match="seq"):
        parse_envelope(json.dumps(data))


@pytest.mark.unit
def test_payload_must_be_an_object() -> None:
    data = json.loads(dumps(_envelope()))
    data["payload"] = ["not", "an", "object"]
    with pytest.raises(EnvelopeError, match="payload"):
        parse_envelope(json.dumps(data))
