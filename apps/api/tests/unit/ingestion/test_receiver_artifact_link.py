"""receive() writes a connector's ArtifactLink atomically with its Document.

Before D-021 the connector inserted the link after receive() had already
committed the Document and published the ingestion task, so the worker could
run before the link existed. The link (and the sync-time attribution decision
on the Document) now share the Document's transaction and are durable before
the task is published. The sync-time link never carries resolved_user_id.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.models.connector import ArtifactLink
from sourcemind.models.document import Document


class _ReceiverSession:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.added: list[object] = []
        self._calls = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)
        self.events.append(f"add:{type(obj).__name__}")

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
        self.events.append("flush")

    async def commit(self) -> None:
        self.events.append("commit")

    async def execute(self, stmt, *args, **kwargs):
        self._calls += 1
        result = MagicMock()
        if self._calls == 1:  # workspace existence
            result.scalar_one_or_none.return_value = SimpleNamespace(id=uuid.uuid4())
        elif self._calls == 2:  # sha256 duplicate check
            result.scalar_one_or_none.return_value = None
        else:  # dispatch lock: the document just created
            doc = next(o for o in self.added if isinstance(o, Document))
            result.scalar_one.return_value = doc
        return result


def _redis() -> MagicMock:
    redis = MagicMock()
    redis.eval = AsyncMock(side_effect=[["reserved", "", 30_000], 1])
    return redis


async def _receive(**extra):
    from sourcemind.services.ingestion.receiver import receive

    events: list[str] = []
    session = _ReceiverSession(events)

    def publish(**kwargs):
        events.append("publish")
        return SimpleNamespace(id=kwargs["task_id"])

    with (
        patch("sourcemind.services.ingestion.receiver.get_redis", return_value=_redis()),
        patch("sourcemind.workers.ingestion.process_document.apply_async", side_effect=publish),
    ):
        response = await receive(
            session=session,
            workspace_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            content="GitHub artifact content " + uuid.uuid4().hex,
            title="t",
            idempotency_key=uuid.uuid4().hex,
            **extra,
        )
    return response, session, events


_LINK = {
    "source_tool": "github",
    "source_type": "commit",
    "source_id": "acme/repo/commits/abc",
    "source_url": "https://github.com/acme/repo/commit/abc",
    "source_author": "alice",
    "source_timestamp": None,
    "artifact_metadata": {"author": {"github_user_id": 101, "login": "alice"}},
}
_ATTRIBUTION = {
    "mode": "external",
    "source_tool": "github",
    "github_user_id": 101,
    "source_author": "alice",
    "link_user_id": None,
    "resolution": "unlinked",
}


@pytest.mark.unit
async def test_link_is_in_the_document_transaction_before_publication() -> None:
    response, session, events = await _receive(
        artifact_link=dict(_LINK), attribution=dict(_ATTRIBUTION)
    )

    links = [o for o in session.added if isinstance(o, ArtifactLink)]
    docs = [o for o in session.added if isinstance(o, Document)]
    assert len(links) == 1 and len(docs) == 1
    link, doc = links[0], docs[0]

    first_commit = events.index("commit")
    assert events.index("add:ArtifactLink") < first_commit
    assert events.index("publish") > first_commit
    assert link.document_id == doc.id
    assert str(doc.id) == response["document_id"]
    assert link.workspace_id == doc.workspace_id
    assert link.memory_id is None
    assert link.resolved_user_id is None
    assert link.source_author == "alice"
    assert link.artifact_metadata["author"]["github_user_id"] == 101


@pytest.mark.unit
async def test_attribution_decision_is_stored_on_the_document() -> None:
    _response, session, _events = await _receive(
        artifact_link=dict(_LINK), attribution=dict(_ATTRIBUTION)
    )

    doc = next(o for o in session.added if isinstance(o, Document))
    assert doc.pipeline_data["attribution"] == _ATTRIBUTION


@pytest.mark.unit
@pytest.mark.parametrize(
    "forbidden",
    ["resolved_user_id", "memory_id", "document_id", "workspace_id", "identity_confidence"],
)
async def test_link_payload_cannot_preset_identity_or_scope(forbidden: str) -> None:
    from sourcemind.core.exceptions import ValidationError

    payload = dict(_LINK)
    payload[forbidden] = str(uuid.uuid4())
    with pytest.raises(ValidationError):
        await _receive(artifact_link=payload, attribution=dict(_ATTRIBUTION))


@pytest.mark.unit
async def test_plain_ingest_writes_no_link_and_no_attribution_mode() -> None:
    _response, session, _events = await _receive()

    assert not [o for o in session.added if isinstance(o, ArtifactLink)]
    doc = next(o for o in session.added if isinstance(o, Document))
    assert "attribution" not in doc.pipeline_data
