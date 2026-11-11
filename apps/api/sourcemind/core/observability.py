"""Error reporting to Sentry (API process only).

Nothing here sends request bodies, cookies, credentials or query strings: memory content and
tokens must never leave the system. Unhandled errors only; expected 4xx errors are not reported.
"""

from __future__ import annotations

import os
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from sourcemind.core.config import Settings

_DROPPED_HEADERS = {"authorization", "cookie", "x-api-key", "x-clerk-auth-token"}


def scrub_event(event: Any, hint: Any = None) -> Any:
    """Remove everything from an event that could carry user data or credentials."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: v for k, v in headers.items() if k.lower() not in _DROPPED_HEADERS
            }
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
    return event


def init_sentry(settings: Settings, transport: Any = None) -> bool:
    """Start Sentry when a DSN is configured; a no-op otherwise. ``transport`` is for tests."""
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=str(settings.environment),
        release=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        # Frame locals hold the whole request (headers, cookies, text) and bypass the scrubber.
        include_local_variables=False,
        # Log lines are kept out entirely: as events they duplicate the error the Starlette
        # integration already reports, and as breadcrumbs their text (tracebacks with request
        # locals) is not scrubbed.
        integrations=[LoggingIntegration(level=None, event_level=None)],
        before_send=scrub_event,
        transport=transport,
    )
    return True
