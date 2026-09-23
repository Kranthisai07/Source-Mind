"""SSRF-resistant HTTP fetching for untrusted ingestion URLs."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver

from sourcemind.core.config import get_settings
from sourcemind.core.exceptions import ContentTooLargeError, ValidationError

_ALLOWED_CONTENT_TYPES = frozenset({"text/html", "text/plain", "application/xhtml+xml"})
_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".home.arpa",
)


@dataclass(frozen=True)
class ValidatedTarget:
    """Normalized URL plus the public IP addresses resolved for its host."""

    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


class _PinnedResolver(AbstractResolver):
    def __init__(self, hostname: str, addresses: tuple[str, ...]) -> None:
        self._hostname = hostname
        self._addresses = addresses

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, object]]:
        if host.rstrip(".").lower() != self._hostname:
            raise OSError("Resolver received an unvalidated hostname")
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in self._addresses
        ]

    async def close(self) -> None:
        return None


def _require_public_address(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError as exc:
        raise ValidationError("URL hostname resolved to an invalid address.") from exc
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    if not parsed.is_global:
        raise ValidationError("URL hostname must resolve only to public addresses.")
    return address


async def validate_public_url(url: str) -> ValidatedTarget:
    """Validate URL syntax and resolve every address before connection."""
    hostname, expected_port = validate_url_syntax(url)

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        addresses = (_require_public_address(hostname),)
    else:
        try:
            resolved = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                expected_port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise ValidationError("URL hostname could not be resolved.") from exc
        addresses = tuple(
            dict.fromkeys(_require_public_address(item[4][0]) for item in resolved)
        )
        if not addresses:
            raise ValidationError("URL hostname did not resolve to a public address.")

    return ValidatedTarget(
        url=url,
        hostname=hostname,
        port=expected_port,
        addresses=addresses,
    )


def validate_url_syntax(url: str) -> tuple[str, int]:
    """Validate URL structure without performing DNS or network I/O."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("URL is malformed.") from exc

    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("URL must use http or https.")
    if not parsed.hostname:
        raise ValidationError("URL must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("URL credentials are not permitted.")
    expected_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != expected_port:
        raise ValidationError("URL must use the default port for its scheme.")

    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname == "localhost"
        or hostname.endswith(_BLOCKED_HOST_SUFFIXES)
        or "%" in hostname
    ):
        raise ValidationError("Local and internal hostnames are not permitted.")

    return hostname, expected_port


async def fetch_public_text(url: str) -> tuple[str, str]:
    """Fetch one bounded main document, validating and pinning every redirect."""
    settings = get_settings()
    current_url = url
    timeout = aiohttp.ClientTimeout(total=settings.url_ingestion_timeout_seconds)

    for redirect_count in range(settings.url_ingestion_max_redirects + 1):
        target = await validate_public_url(current_url)
        connector = aiohttp.TCPConnector(
            resolver=_PinnedResolver(target.hostname, target.addresses),
            use_dns_cache=False,
        )
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                cookie_jar=aiohttp.DummyCookieJar(),
                trust_env=False,
                headers={"User-Agent": "SourceMindBot/1.0"},
            ) as client:
                async with client.get(target.url, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise ValidationError("URL redirect omitted its destination.")
                        if redirect_count >= settings.url_ingestion_max_redirects:
                            raise ValidationError("URL exceeded the redirect limit.")
                        current_url = urljoin(target.url, location)
                        continue
                    if response.status >= 400:
                        raise ValidationError("URL could not be fetched successfully.")

                    content_type = response.content_type.lower()
                    if content_type not in _ALLOWED_CONTENT_TYPES:
                        raise ValidationError("URL did not return supported text content.")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        body.extend(chunk)
                        if len(body) > settings.url_ingestion_max_bytes:
                            raise ContentTooLargeError(
                                "Fetched URL content exceeds the byte limit."
                            )
                    encoding = response.charset or "utf-8"
                    try:
                        return body.decode(encoding, errors="replace"), current_url
                    except LookupError:
                        return body.decode("utf-8", errors="replace"), current_url
        except TimeoutError as exc:
            raise ValidationError("URL fetch timed out.") from exc
        except aiohttp.ClientError as exc:
            raise ValidationError("URL could not be fetched.") from exc

    raise ValidationError("URL exceeded the redirect limit.")
