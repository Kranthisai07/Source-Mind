"""
Standalone Slack bot entry point — Socket Mode (no public URL needed).

Usage:
    cd apps/api
    .venv/Scripts/python.exe -m sourcemind.slack_bot

Required env vars (add to apps/api/.env):
    SLACK_BOT_TOKEN          xoxb-...   (Bot User OAuth Token)
    SLACK_APP_TOKEN          xapp-...   (App-Level Token with connections:write scope)
    SLACK_SIGNING_SECRET     ...        (from App credentials page)
    SLACK_DEFAULT_WORKSPACE_ID  <uuid>  (SourceMind workspace to search against)

Optional:
    SOURCEMIND_APP_URL  https://app.sourcemind.ai  (for View buttons)

How to create the Slack app:
    1. Go to https://api.slack.com/apps → Create New App → From Manifest
    2. Paste the manifest from docs/slack_app_manifest.yml
    3. Install the app to your workspace
    4. Copy Bot User OAuth Token (SLACK_BOT_TOKEN)
    5. Enable Socket Mode → copy App-Level Token (SLACK_APP_TOKEN)
    6. Copy Signing Secret from Basic Information (SLACK_SIGNING_SECRET)
"""

import asyncio
import sys

import structlog

from sourcemind.core.logging import configure_logging

configure_logging()
log = structlog.get_logger(__name__)


def main() -> None:
    from sourcemind.core.config import get_settings
    settings = get_settings()

    missing = []
    if not settings.slack_bot_token:
        missing.append("SLACK_BOT_TOKEN")
    if not settings.slack_app_token:
        missing.append("SLACK_APP_TOKEN")
    if not settings.slack_signing_secret:
        missing.append("SLACK_SIGNING_SECRET")

    if missing:
        log.error(
            "slack_bot.missing_config",
            missing=missing,
            hint="Add these to apps/api/.env then restart.",
        )
        sys.exit(1)

    log.info("slack_bot.starting", mode="socket_mode")

    from sourcemind.services.slack.bot import run_socket_mode
    asyncio.run(run_socket_mode(settings))


if __name__ == "__main__":
    main()
