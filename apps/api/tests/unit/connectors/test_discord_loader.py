"""Unit tests for DiscordDatasetLoader and DiscordMapper."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sourcemind.connectors.discord.dataset_loader import DiscordDatasetLoader
from sourcemind.connectors.discord.mapper import DiscordMapper


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_export(messages: list[dict], channel_name: str = "general") -> dict:
    return {
        "guild": {"id": "111", "name": "Test Guild"},
        "channel": {"id": "222", "name": channel_name, "category": "Text Channels"},
        "messages": messages,
        "messageCount": len(messages),
    }


def _msg(msg_id: str, content: str, ts: str, author: str = "alice") -> dict:
    return {
        "id": msg_id,
        "type": "Default",
        "timestamp": ts,
        "content": content,
        "author": {"id": "1", "name": author, "nickname": None},
        "attachments": [],
        "embeds": [],
        "reactions": [],
        "mentions": [],
    }


# ── DiscordDatasetLoader tests ────────────────────────────────────────────────

@pytest.mark.unit
def test_loader_groups_close_messages_into_one_block():
    messages = [
        _msg("1", "Hello!", "2024-01-01T10:00:00+00:00"),
        _msg("2", "World!", "2024-01-01T10:05:00+00:00"),
        _msg("3", "How are you?", "2024-01-01T10:10:00+00:00"),
    ]
    export = _make_export(messages)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(export, f)
        path = f.name

    loader = DiscordDatasetLoader(window_minutes=30, min_block_length=1)
    blocks = loader.load_file(path)
    assert len(blocks) == 1
    assert len(blocks[0]["messages"]) == 3


@pytest.mark.unit
def test_loader_splits_on_time_gap():
    messages = [
        _msg("1", "Early message", "2024-01-01T08:00:00+00:00"),
        _msg("2", "Much later message", "2024-01-01T12:00:00+00:00"),
    ]
    export = _make_export(messages)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(export, f)
        path = f.name

    loader = DiscordDatasetLoader(window_minutes=30, min_block_length=1)
    blocks = loader.load_file(path)
    assert len(blocks) == 2


@pytest.mark.unit
def test_loader_skips_empty_messages():
    messages = [
        _msg("1", "", "2024-01-01T10:00:00+00:00"),  # empty — should be filtered
        _msg("2", "Real content", "2024-01-01T10:01:00+00:00"),
        _msg("3", "More content", "2024-01-01T10:02:00+00:00"),
    ]
    export = _make_export(messages)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(export, f)
        path = f.name

    loader = DiscordDatasetLoader(window_minutes=30, min_block_length=1)
    blocks = loader.load_file(path)
    # Empty message filtered, 2 real messages in one block
    assert len(blocks) == 1
    assert len(blocks[0]["messages"]) == 2


@pytest.mark.unit
def test_loader_min_block_length_filters_singletons():
    messages = [
        _msg("1", "Solo message", "2024-01-01T10:00:00+00:00"),
        _msg("2", "Another solo", "2024-01-01T14:00:00+00:00"),
    ]
    export = _make_export(messages)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(export, f)
        path = f.name

    # min_block_length=2 means singleton blocks are discarded
    loader = DiscordDatasetLoader(window_minutes=30, min_block_length=2)
    blocks = loader.load_file(path)
    assert len(blocks) == 0


@pytest.mark.unit
def test_loader_participants_extracted():
    messages = [
        _msg("1", "Hi!", "2024-01-01T10:00:00+00:00", author="alice"),
        _msg("2", "Hey!", "2024-01-01T10:01:00+00:00", author="bob"),
    ]
    export = _make_export(messages)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(export, f)
        path = f.name

    loader = DiscordDatasetLoader(window_minutes=30, min_block_length=1)
    blocks = loader.load_file(path)
    assert set(blocks[0]["participant_logins"]) == {"alice", "bob"}


# ── DiscordMapper tests ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_discord_mapper_produces_connector_document():
    block = {
        "guild_id": "111",
        "guild_name": "Test Guild",
        "channel_id": "222",
        "channel_name": "general",
        "category": "Text Channels",
        "messages": [
            {
                "id": "m1",
                "content": "Hello from #general",
                "timestamp": "2024-01-01T10:00:00+00:00",
                "author": {"name": "alice", "nickname": None},
            }
        ],
        "started_at": datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2024, 1, 1, 10, 5, tzinfo=timezone.utc),
        "participant_logins": ["alice"],
    }
    doc = DiscordMapper.from_block(block)
    assert doc.source_tool == "discord"
    assert doc.source_type == "conversation"
    assert "111/222/m1" == doc.source_id
    assert "general" in doc.title
    assert "alice" in doc.content
    assert "Hello from #general" in doc.content


@pytest.mark.unit
def test_discord_mapper_idempotency_key_deterministic():
    block = {
        "guild_id": "111",
        "guild_name": "G",
        "channel_id": "222",
        "channel_name": "c",
        "category": "",
        "messages": [{"id": "m1", "content": "hi", "timestamp": "", "author": {"name": "u"}}],
        "started_at": None,
        "ended_at": None,
        "participant_logins": ["u"],
    }
    doc1 = DiscordMapper.from_block(block)
    doc2 = DiscordMapper.from_block(block)
    assert doc1.idempotency_key == doc2.idempotency_key
    assert len(doc1.idempotency_key) == 64
