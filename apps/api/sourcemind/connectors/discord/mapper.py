"""Discord mapper — converts conversation blocks to ConnectorDocuments."""

from __future__ import annotations

from typing import Any

from sourcemind.connectors.github.mapper import ConnectorDocument


class DiscordMapper:
    """Maps Discord conversation blocks (from :class:`DiscordDatasetLoader`)
    to :class:`ConnectorDocument` instances ready for ingestion.
    """

    TOOL = "discord"
    SOURCE_TYPE = "conversation"

    @classmethod
    def from_block(cls, block: dict[str, Any]) -> ConnectorDocument:
        """Map a conversation block dict to a ConnectorDocument.

        Args:
            block: A block dict as produced by
                :meth:`~DiscordDatasetLoader.load_file`, containing keys:
                ``guild_id``, ``guild_name``, ``channel_id``, ``channel_name``,
                ``category``, ``messages``, ``started_at``, ``ended_at``,
                ``participant_logins``.
        """
        guild_name = block.get("guild_name", "")
        channel_name = block.get("channel_name", "")
        channel_id = block.get("channel_id", "")
        guild_id = block.get("guild_id", "")
        category = block.get("category", "")
        messages: list[dict[str, Any]] = block.get("messages", [])
        started_at = block.get("started_at")
        ended_at = block.get("ended_at")
        participants = block.get("participant_logins", [])

        # Stable source_id: guild/channel/first_message_id
        first_msg_id = messages[0].get("id", "0") if messages else "0"
        source_id = f"{guild_id}/{channel_id}/{first_msg_id}"

        # Build readable content
        content_lines = [
            f"Server: {guild_name}",
            f"Channel: #{channel_name}" + (f" [{category}]" if category else ""),
            f"Participants: {', '.join(participants)}",
            f"Time: {started_at.isoformat() if started_at else 'unknown'}"
            + (f" → {ended_at.isoformat()}" if ended_at else ""),
            "",
        ]
        for msg in messages:
            author = (msg.get("author") or {}).get("name", "unknown")
            nick = (msg.get("author") or {}).get("nickname")
            display = nick or author
            ts = msg.get("timestamp", "")[:16]  # trim to YYYY-MM-DDTHH:MM
            content_lines.append(f"[{ts}] {display}: {msg.get('content', '')}")

        content = "\n".join(content_lines)

        # Title: "#channel — first N chars of first message"
        first_content = messages[0].get("content", "") if messages else ""
        title = f"#{channel_name} — {first_content[:100]}"

        return ConnectorDocument(
            source_tool=cls.TOOL,
            source_type=cls.SOURCE_TYPE,
            source_id=source_id,
            source_url=None,
            source_author=participants[0] if participants else None,
            source_timestamp=started_at,
            title=title,
            content=content,
            idempotency_key=ConnectorDocument.make_key(cls.TOOL, cls.SOURCE_TYPE, source_id),
            metadata={
                "guild_id": guild_id,
                "guild_name": guild_name,
                "channel_id": channel_id,
                "channel_name": channel_name,
                "category": category,
                "message_count": len(messages),
                "participants": participants,
            },
        )
