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

import re
import uuid
from typing import Any

import structlog
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from sourcemind.core.config import get_settings
from sourcemind.core.database import (
    close_db,
    get_session_factory,
    init_db,
    set_rls_user_context,
)
from sourcemind.core.dependencies import (
    WorkspacePermission,
    require_workspace_permission,
)
from sourcemind.core.exceptions import UnauthorizedError
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.core.redis_client import close_redis, init_redis
from sourcemind.services.slack.formatter import (
    format_experts,
    format_help,
    format_search_results,
)


def _get_bot_factory():
    return get_session_factory()

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


def _resolve_slack_identity(body: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    """Map Slack IDs through an explicit operator-owned allowlist."""
    settings = get_settings()
    if not settings.slack_memory_commands_enabled:
        raise UnauthorizedError("Slack memory commands are disabled.")

    team_id = body.get("team_id")
    channel_id = body.get("channel_id")
    slack_user_id = body.get("user_id")
    if not all(
        isinstance(value, str) and value
        for value in (team_id, channel_id, slack_user_id)
    ):
        raise UnauthorizedError("Slack request identity is incomplete.")

    installation = settings.slack_installations.get(team_id)
    if not isinstance(installation, dict):
        raise UnauthorizedError("Slack installation is not authorized.")
    channels = installation.get("allowed_channel_ids")
    users = installation.get("users")
    if not isinstance(channels, list) or channel_id not in channels:
        raise UnauthorizedError("Slack channel is not authorized.")
    if not isinstance(users, dict) or slack_user_id not in users:
        raise UnauthorizedError("Slack user is not mapped.")
    try:
        return uuid.UUID(str(installation["workspace_id"])), uuid.UUID(
            str(users[slack_user_id])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("Slack installation mapping is invalid.") from exc


async def _authorize_slack_request(
    body: dict[str, Any], session: Any
) -> tuple[uuid.UUID, str]:
    workspace_id, user_id = _resolve_slack_identity(body)
    await set_rls_user_context(session, user_id)
    role = await require_workspace_permission(
        session, user_id, workspace_id, WorkspacePermission.READ
    )
    await enforce_rate_limit(RateLimitedOperation.SLACK, user_id, workspace_id)
    return workspace_id, role


async def _do_search(query: str, body: dict[str, Any], app_url: str) -> list[dict]:
    """Run keyword search and return formatted Slack blocks.

    """
    from sourcemind.services.search.hybrid import hybrid_search

    session = _get_bot_factory()()
    try:
        workspace_id, user_role = await _authorize_slack_request(body, session)
        result = await hybrid_search(
            session=session,
            query=query,
            workspace_id=workspace_id,
            limit=5,
            min_similarity=0.20,
            mode="hybrid",
            user_role=user_role,
            include_attribution=True,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
    results = result.get("results") or []
    return format_search_results(query, results, str(workspace_id), app_url)


async def _do_who_knows(query: str, body: dict[str, Any], app_url: str) -> list[dict]:
    """Run who-would-know lookup and return formatted Slack blocks."""
    from sourcemind.services.analytics.workspace import who_would_know

    session = _get_bot_factory()()
    try:
        workspace_id, _role = await _authorize_slack_request(body, session)
        result = await who_would_know(session, workspace_id, query, limit=5)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
    return format_experts(query, result.get("experts") or [], app_url)


def register_handlers(app: AsyncApp, app_url: str) -> None:
    """Attach all slash-command and event handlers to the Bolt app."""

    @app.command("/sourcemind")
    async def handle_slash_command(ack, body, respond):
        """Route /sourcemind into search, who-knows, or help."""
        await ack()

        text = (body.get("text") or "").strip()

        if not text or text.lower() == "help":
            await respond(blocks=format_help(), response_type="ephemeral")
            return

        who_match = _WHO_KNOWS_RE.match(text)
        if who_match:
            topic = who_match.group(1).strip()
            try:
                blocks = await _do_who_knows(topic, body, app_url)
            except Exception as exc:
                log.error("slack.who_knows_error", error_type=type(exc).__name__)
                blocks = [{
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": ":x: Search failed. Please try again."},
                }]
            await respond(blocks=blocks, response_type="ephemeral")
            return

        # Default: memory search
        try:
            blocks = await _do_search(text, body, app_url)
        except Exception as exc:
            log.error("slack.search_error", error_type=type(exc).__name__)
            blocks = [{
                "type": "section",
                "text": {"type": "mrkdwn", "text": ":x: Search failed. Please try again."},
            }]
        await respond(blocks=blocks, response_type="ephemeral")

    @app.event("app_mention")
    async def handle_mention(event, say):
        """Keep memory results out of public channels."""
        await say(
            text="Use the /sourcemind command for a private SourceMind response.",
            thread_ts=event.get("ts"),
        )


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
        app_url=settings.sourcemind_app_url,
    )

    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    log.info("slack.socket_mode.starting")
    try:
        await handler.start_async()
    finally:
        await close_db()
        await close_redis()
