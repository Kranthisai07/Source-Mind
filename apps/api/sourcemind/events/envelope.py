"""Wire format of graph projection events.

One event is the full current state of one memory (see
docs/architecture/GRAPH_PROJECTION.md), identified by ``event_id`` and ordered
by ``seq``, the outbox identity value. Because events are state, not deltas,
replaying, duplicating or reordering them cannot corrupt the graph: the
projector applies an event only if its ``seq`` is newer than the one it already
holds for that node.

Payloads carry ids, versions and weights only. Never memory content,
embeddings, emails or display names: Kafka retains what it is given.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

SCHEMA_VERSION = 1
EVENT_MEMORY_STATE = "memory.state"

_REQUIRED = ("schema_version", "event_id", "seq", "occurred_at", "workspace_id", "type", "payload")


class EnvelopeError(ValueError):
    """The message is not a graph event this code version understands."""


@dataclass(frozen=True)
class GraphEvent:
    event_id: str
    seq: int
    occurred_at: str
    workspace_id: str
    type: str
    aggregate_id: str
    payload: dict[str, Any]


def _json_default(obj: Any) -> Any:
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return (obj if obj.tzinfo else obj.replace(tzinfo=UTC)).isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def dumps(value: Any) -> bytes:
    """Serialise deterministically (sorted keys) so equal events are equal bytes."""
    return json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":")).encode()


def build_envelope(
    *,
    event_id: UUID | str,
    seq: int,
    occurred_at: datetime,
    workspace_id: UUID | str,
    event_type: str,
    aggregate_id: UUID | str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """The dict the relay sends for one outbox row."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(event_id),
        "seq": int(seq),
        "occurred_at": _json_default(occurred_at),
        "workspace_id": str(workspace_id),
        "type": event_type,
        "aggregate_id": str(aggregate_id),
        "payload": payload,
    }


def parse_envelope(raw: bytes | str) -> GraphEvent:
    """Validate and decode one message. Raises EnvelopeError on anything unexpected."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise EnvelopeError("message is not valid JSON") from exc
    if not isinstance(data, dict):
        raise EnvelopeError("message is not a JSON object")
    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        raise EnvelopeError(f"message is missing {', '.join(missing)}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise EnvelopeError(f"unsupported schema_version {data['schema_version']!r}")
    if not isinstance(data["seq"], int) or isinstance(data["seq"], bool) or data["seq"] < 1:
        raise EnvelopeError("seq must be a positive integer")
    if not isinstance(data["payload"], dict):
        raise EnvelopeError("payload must be an object")
    for key in ("event_id", "workspace_id", "type"):
        if not isinstance(data[key], str) or not data[key]:
            raise EnvelopeError(f"{key} must be a non-empty string")
    return GraphEvent(
        event_id=data["event_id"],
        seq=data["seq"],
        occurred_at=str(data["occurred_at"]),
        workspace_id=data["workspace_id"],
        type=data["type"],
        aggregate_id=str(data.get("aggregate_id", "")),
        payload=data["payload"],
    )
