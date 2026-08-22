"""Discord dataset loader — loads pre-exported Discord channel archives.

The Discord connector does NOT use a live bot (requires privileged intents and
server membership). Instead it consumes JSON exports produced by tools such as
DiscordChatExporter (https://github.com/Tyrrrz/DiscordChatExporter).

Expected export format (single-channel JSON):
{
  "guild": {"id": "...", "name": "..."},
  "channel": {"id": "...", "name": "...", "categoryId": "...", "category": "..."},
  "messages": [
    {
      "id": "...",
      "type": "Default",
      "timestamp": "2024-01-15T10:23:45+00:00",
      "content": "...",
      "author": {"id": "...", "name": "...", "nickname": "..."},
      "attachments": [],
      "embeds": [],
      "reactions": [],
      "mentions": [],
      "reference": {"messageId": "..."}   // optional thread reference
    },
    ...
  ],
  "messageCount": 1234
}

The loader groups consecutive messages into thread-based or time-windowed
conversation blocks (default 30-minute windows) so each ingested document
represents a coherent discussion rather than a single message.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_WINDOW_MINUTES = 30
_MIN_BLOCK_LENGTH = 2   # skip single-message isolated blocks
_MAX_BLOCK_CHARS = 8000


class DiscordDatasetLoader:
    """Load and group a DiscordChatExporter JSON archive into conversation blocks.

    Args:
        window_minutes: Maximum gap (minutes) between messages to include in
                        the same block. Messages more than this apart start a
                        new block.
        min_block_length: Blocks with fewer messages than this are discarded.
        max_block_chars: Blocks exceeding this character length are split.
    """

    def __init__(
        self,
        window_minutes: int = _DEFAULT_WINDOW_MINUTES,
        min_block_length: int = _MIN_BLOCK_LENGTH,
        max_block_chars: int = _MAX_BLOCK_CHARS,
    ) -> None:
        self._window = timedelta(minutes=window_minutes)
        self._min_length = min_block_length
        self._max_chars = max_block_chars

    def load_file(self, path: str | Path) -> list[dict[str, Any]]:
        """Load and group messages from a DiscordChatExporter JSON file.

        Args:
            path: Path to the exported JSON file.

        Returns:
            List of conversation block dicts, each with keys:
            ``guild_id``, ``guild_name``, ``channel_id``, ``channel_name``,
            ``category``, ``messages``, ``started_at``, ``ended_at``,
            ``participant_logins``.
        """
        path = Path(path)
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        guild = data.get("guild", {})
        channel = data.get("channel", {})
        messages: list[dict[str, Any]] = data.get("messages", [])

        # Filter to Default and Reply message types only
        messages = [
            m for m in messages
            if m.get("type") in ("Default", "Reply", "default", "reply")
            and m.get("content", "").strip()
        ]

        if not messages:
            log.warning("discord_loader_no_messages", path=str(path))
            return []

        blocks = self._group_into_blocks(messages)

        result = []
        for block in blocks:
            if len(block) < self._min_length:
                continue
            participants = list({
                (m.get("author") or {}).get("name", "unknown")
                for m in block
            })
            started_at = _parse_dt(block[0].get("timestamp"))
            ended_at = _parse_dt(block[-1].get("timestamp"))

            result.append({
                "guild_id": guild.get("id", ""),
                "guild_name": guild.get("name", ""),
                "channel_id": channel.get("id", ""),
                "channel_name": channel.get("name", ""),
                "category": channel.get("category", ""),
                "messages": block,
                "started_at": started_at,
                "ended_at": ended_at,
                "participant_logins": participants,
            })

        log.info(
            "discord_loader_loaded",
            path=str(path),
            raw_messages=len(data.get("messages", [])),
            filtered_messages=len(messages),
            blocks=len(result),
        )
        return result

    def load_directory(self, directory: str | Path) -> list[dict[str, Any]]:
        """Load all ``*.json`` exports in a directory.

        Args:
            directory: Path to a directory containing one JSON file per channel.

        Returns:
            Concatenated list of all conversation blocks across all files.
        """
        directory = Path(directory)
        blocks: list[dict[str, Any]] = []
        for json_file in sorted(directory.glob("*.json")):
            try:
                blocks.extend(self.load_file(json_file))
            except Exception as exc:
                log.warning(
                    "discord_loader_file_error",
                    file=str(json_file),
                    error=str(exc),
                )
        return blocks

    # ── Grouping logic ────────────────────────────────────────────────────────

    def _group_into_blocks(
        self, messages: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """Group messages into time-windowed blocks.

        A new block starts when:
          - More than ``window_minutes`` elapsed since the last message.
          - The current accumulated block exceeds ``max_block_chars``.

        Args:
            messages: Time-sorted list of filtered message dicts.

        Returns:
            List of message groups (each group is a list of message dicts).
        """
        blocks: list[list[dict[str, Any]]] = []
        current_block: list[dict[str, Any]] = []
        current_chars = 0
        last_ts: datetime | None = None

        for msg in messages:
            ts = _parse_dt(msg.get("timestamp"))
            content = msg.get("content", "")
            content_len = len(content)

            # Start a new block?
            new_window = (
                last_ts is not None
                and ts is not None
                and (ts - last_ts) > self._window
            )
            overflow = current_chars + content_len > self._max_chars and current_block

            if (new_window or overflow) and current_block:
                blocks.append(current_block)
                current_block = []
                current_chars = 0

            current_block.append(msg)
            current_chars += content_len
            if ts:
                last_ts = ts

        if current_block:
            blocks.append(current_block)

        return blocks


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string to an aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
