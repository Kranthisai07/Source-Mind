"""Configuration for the read-only MCP interface.

Kept apart from ``core.config`` on purpose: the interface is off by default, and a
deployment enables it by setting ``MCP_ENABLED=true`` and the canonical resource URL
(``MCP_RESOURCE_URL``, e.g. ``https://api.example.com/mcp``). Nothing here is a secret.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCP_", extra="ignore")

    enabled: bool = False
    #: Canonical URI of the MCP endpoint. Tokens must be issued for exactly this audience.
    resource_url: str = ""
    #: OAuth issuer (the Clerk Frontend API URL). Empty = derive from CLERK_PUBLISHABLE_KEY.
    issuer_url: str = ""
    #: Host header values accepted by the MCP transport (DNS-rebinding protection).
    #: Empty = derive ``host`` and ``host:*`` from ``resource_url``.
    allowed_hosts: list[str] = Field(default_factory=list)
    #: Origin header values accepted. Empty = browsers are not allowed (CLI clients send none).
    allowed_origins: list[str] = Field(default_factory=list)
    #: Scopes a token must carry. Empty = the audience binding is the only gate.
    required_scopes: list[str] = Field(default_factory=list)
    #: Reads per call.
    max_search_results: int = 10
    default_search_results: int = 5
    search_content_chars: int = 1500
    get_content_chars: int = 8000

    @model_validator(mode="after")
    def _validate(self) -> McpSettings:
        if self.enabled:
            parts = urlsplit(self.resource_url)
            if parts.scheme not in {"https", "http"} or not parts.netloc or parts.fragment:
                raise ValueError(
                    "MCP_RESOURCE_URL must be an absolute URL without a fragment "
                    "when MCP_ENABLED is true."
                )
        return self

    def effective_allowed_hosts(self) -> list[str]:
        if self.allowed_hosts:
            return list(self.allowed_hosts)
        host = urlsplit(self.resource_url).netloc
        bare = host.split(":")[0]
        return sorted({host, bare, f"{bare}:*"})


@lru_cache(maxsize=1)
def get_mcp_settings() -> McpSettings:
    return McpSettings()
