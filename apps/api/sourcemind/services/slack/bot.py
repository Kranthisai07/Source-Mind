"""
Slack Bolt app — command handlers and app-mention handler.

Supports two modes:
  Socket Mode  (SLACK_APP_TOKEN set)  — no public URL needed, great for local dev
  HTTP Mode    (no SLACK_APP_TOKEN)   — standard webhooks, used in production

Commands handled:
  /sourcemind <query>               — hybrid memory search
  /sourcemind who knows <topic>     — reverse-expertise lookup
  /sourcemind help                  — usage guide

App mentions:
  @SourceMind <query>               — same as /sourcemind <query>
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import structlog
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from sourcemind.core.config import get_settings
from sourcemind.core.database import init_db, close_db, get_session_factory
from sourcemind.core.redis_client import init_redis, close_redis
from sourcemind.services.slack.formatter import (
    format_search_results,
    format_experts,
    format_help,
)

log = structlog.get_logger(__name__)

_WHO_KNOWS_RE = re.compile(
    r"^who\s+(?:knows?|would\s+know|should\s+i\s+ask)\s+(?:about\s+)?(.+)$",
    re.IGNORECASE,
)


def _build_app(settings: Any = None) -> AsyncApp:
    if settings is None:
        settings = get_settings()
    return AsyncApp(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
    )


async def _do_search(query: str, workspace_id: str, app_url: str) -> list[dict]:
    """Run hybrid search and return formatted Slack blocks."""
    from sourcemind.services.search.hybrid import hybrid_search

    ws_uuid = _parse_workspace_id(workspace_id)
    factory = get_session_factory()
    async with factory() as session:
        result = await hybrid_search(
            session=session,
            query=query,
            workspace_id=ws_uuid,
            limit=5,
            min_similarity=0.20,
            mode="hybrid",
            user_role="member",
            include_attribution=True,
        )
    results = result.get("results") or []
    return format_search_results(query, results, workspace_id, app_url)


async def _do_who_knows(query: str, workspace_id: str, app_url: str) -> list[dict]:
    """Run who-would-know lookup and return formatted Slack blocks."""
    from sourcemind.services.analytics.workspace import who_would_know

    ws_uuid = _parse_workspace_id(workspace_id)
    factory = get_session_factory()
    async with factory() as session:
        result = await who_would_know(session, ws_uuid, query, limit=5)
    return format_experts(query, result.get("experts") or [], app_url)


def _parse_workspace_id(workspace_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(workspace_id)
    except (ValueError, AttributeError):
        # Fall back to a nil UUID — the query will simply return empty results
        return uuid.UUID("00000000-0000-0000-0000-000000000000")


def register_handlers(app: AsyncApp, workspace_id: str, app_url: str) -> None:
    """Attach all slash-command and event handlers to the Bolt app."""

    @app.command("/sourcemind")
    async def handle_slash_command(ack, body, respond):
        await ack()

        text = (body.get("text") or "").strip()

        if not text or text.lower() == "help":
            await respond(blocks=format_help(), response_type="ephemeral")
            return

        who_match = _WHO_KNOWS_RE.match(text)
        if who_match:
            topic = who_match.group(1).strip()
            try:
                blocks = await _do_who_knows(topic, workspace_id, app_url)
            except Exception as exc:
                log.error("slack.who_knows_error", error=str(exc))
                blocks = [{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": ":x: Search failed. Please try again."},
                }]
            await respond(blocks=blocks, response_type="in_channel")
            return

        # Default: memory search
        try:
            blocks = await _do_search(text, workspace_id, app_url)
        except Exception as exc:
            log.error("slack.search_error", error=str(exc))
            blocks = [{
                "type": "section",
                "text": {"type": "mrkdwn", "text": ":x: Search failed. Please try again."},
            }]
        await respond(blocks=blocks, response_type="in_channel")

    @app.event("app_mention")
    async def handle_mention(event, say):
        # Strip the bot mention from the text: "<@U123> who knows about X"
        raw  = event.get("text") or ""
        text = re.sub(r"<@[A-Z0-9]+>", "", raw).strip()

        if not text or text.lower() == "help":
            await say(blocks=format_help())
            return

        who_match = _WHO_KNOWS_RE.match(text)
        if who_match:
            topic = who_match.group(1).strip()
            try:
                blocks = await _do_who_knows(topic, workspace_id, app_url)
            except Exception as exc:
                log.error("slack.mention.who_knows_error", error=str(exc))
                blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": ":x: Lookup failed."}}]
            await say(blocks=blocks)
            return

        try:
            blocks = await _do_search(text, workspace_id, app_url)
        except Exception as exc:
            log.error("slack.mention.search_error", error=str(exc))
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": ":x: Search failed."}}]
        await say(blocks=blocks)


async def run_socket_mode(settings: Any = None) -> None:
    """
    Start the bot in Socket Mode — no public URL required.
    Requires SLACK_APP_TOKEN (starts with xapp-).
    """
    if settings is None:
        settings = get_settings()

    await init_db()
    await init_redis()

    app = _build_app(settings)
    register_handlers(
        app,
        workspace_id=settings.slack_default_workspace_id,
        app_url=settings.sourcemind_app_url,
    )

    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    log.info("slack.socket_mode.starting")
    try:
        await handler.start_async()
    finally:
        await close_db()
        await close_redis()
