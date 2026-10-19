"""Two defects found on the first hosted staging run (no provider key, real exception logs)."""

from __future__ import annotations

import io
import json
import logging
import uuid
from unittest.mock import AsyncMock, patch

import structlog

from sourcemind.core.config import Environment, get_settings
from sourcemind.core.logging import configure_logging
from sourcemind.services.search import hybrid


async def test_keyword_mode_never_requests_an_embedding() -> None:
    with (
        patch.object(hybrid, "_get_query_embedding", new=AsyncMock(side_effect=AssertionError)),
        patch.object(hybrid, "_keyword_search", new=AsyncMock(return_value=[])),
    ):
        out = await hybrid.hybrid_search(
            session=None, query="redis", workspace_id=uuid.uuid4(), mode="keyword"
        )
    assert out["mode"] == "keyword" and out["results"] == []


def test_exception_logs_do_not_contain_local_variable_values() -> None:
    secret = "hunter2-" + uuid.uuid4().hex
    stream = io.StringIO()
    settings = get_settings()
    with patch.object(settings, "environment", Environment.STAGING):
        configure_logging()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        try:
            password = secret  # noqa: F841 - a local that must not reach the log
            raise RuntimeError("boom")
        except RuntimeError:
            structlog.get_logger().exception("failed")
    finally:
        root.removeHandler(handler)
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert "frames" in json.dumps(line) and secret not in stream.getvalue()
