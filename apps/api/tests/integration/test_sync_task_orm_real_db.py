"""The Celery sync entry point must hand the connector a usable config row.

Review finding (#31): ``_run_sync`` loads the ORM ``ConnectorConfig``, calls
``session.rollback()`` (which expires every instance) and then reads
``config.installation_id`` and passes ``config`` on. In async SQLAlchemy a lazy refresh
of an expired attribute raises ``MissingGreenlet``. Earlier tests passed a
``SimpleNamespace`` so the real path was never exercised.
"""

# ruff: noqa: F811

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.integration.test_ingestion_worker_contract import (  # noqa: F401 - fixtures
    ingestion_worker_database_url,
    ingestion_worker_engine,
    ingestion_worker_environment,
    ingestion_worker_redis_url,
    worker_harness,
)
from tests.integration.test_revision_publication_real_db import (  # noqa: F401 - fixtures
    _no_broker,
    world,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_run_sync_gives_the_connector_a_readable_config(world) -> None:
    from sourcemind.workers import connector_tasks as tasks

    seen: dict[str, Any] = {}

    class SpyConnector:
        def __init__(self, *, config: Any, **_kw: Any) -> None:
            # What GitHubConnector does on every call: read scalar fields off the row.
            seen["id"] = config.id

        async def run(self, **_kw: Any) -> Any:
            return SimpleNamespace(status="completed", error_code=None, counts={})

    class SpyAuth:
        def __init__(self, **_kw: Any) -> None:
            pass

    w = world
    # The key is never used (auth is stubbed); it only has to be readable.
    w.mp.setenv("GITHUB_APP_PRIVATE_KEY_PEM", "stub-not-a-key")
    from sourcemind.core.config import get_settings

    get_settings.cache_clear()
    w.mp.setattr(tasks, "GitHubConnector", SpyConnector)
    w.mp.setattr(tasks, "GitHubAppAuth", SpyAuth)
    w.mp.setattr(tasks, "_build_verifier", lambda _auth: None)

    out = await tasks._run_sync(
        w.connector, w.data.target_workspace_id, w.data.member_id, "manual", "incremental"
    )

    assert out["status"] == "completed", out
    assert seen["id"] == w.connector
