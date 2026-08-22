"""
Slack HTTP endpoint — receives slash commands and events via webhooks.

Used in production where a public URL is available.
For local development without a public URL, use Socket Mode instead:
    python -m sourcemind.slack_bot

Slack verifies every request with an HMAC-SHA256 signature using the
signing secret — we validate it via slack-bolt's built-in middleware.

Endpoint: POST /v1/slack/events
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request, Response
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp

from sourcemind.core.config import get_settings
from sourcemind.services.slack.bot import register_handlers

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])

_bolt_app: AsyncApp | None = None
_handler: AsyncSlackRequestHandler | None = None


def _get_handler() -> AsyncSlackRequestHandler:
    global _bolt_app, _handler
    if _handler is None:
        settings = get_settings()

        if not settings.slack_bot_token or not settings.slack_signing_secret:
            raise RuntimeError(
                "SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET must be set "
                "to use the Slack HTTP endpoint."
            )

        _bolt_app = AsyncApp(
            token=settings.slack_bot_token,
            signing_secret=settings.slack_signing_secret,
        )
        register_handlers(
            _bolt_app,
            workspace_id=settings.slack_default_workspace_id,
            app_url=settings.sourcemind_app_url,
        )
        _handler = AsyncSlackRequestHandler(_bolt_app)

    return _handler


@router.post("/events")
async def slack_events(req: Request) -> Response:
    """
    Single entry point for all Slack events, slash commands, and
    interactive payloads. Bolt's request handler routes internally.
    """
    try:
        handler = _get_handler()
    except RuntimeError as exc:
        log.warning("slack.not_configured", error=str(exc))
        return Response(content=str(exc), status_code=503)

    return await handler.handle(req)
