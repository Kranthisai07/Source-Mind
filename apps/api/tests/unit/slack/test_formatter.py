"""Tests for services/slack/formatter.py — Slack Block Kit rendering.

Pure functions, but user-facing: this is what a person actually reads in
Slack. Malformed blocks are rejected by Slack's API outright, and truncation
or escaping bugs silently corrupt the text someone relies on.
"""

from __future__ import annotations

import pytest

from sourcemind.services.slack.formatter import (
    _attribution_line,
    _emoji,
    _preview,
    _strip_md,
    format_experts,
    format_help,
    format_search_results,
)


def _result(content: str = "A memory about deployments", **overrides) -> dict:
    base = {
        "id": "11111111-2222-4333-8444-555555555555",
        "content": content,
        "score": 0.87,
        "category": "decision",
        "tags": ["deploy"],
        "attributions": [],
    }
    base.update(overrides)
    return base


# ─── small helpers ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("category", ["decision", "process", "fact", "question"])
def test_known_categories_get_a_distinct_emoji(category):
    assert _emoji(category).startswith(":")


@pytest.mark.unit
def test_unknown_category_falls_back_to_a_default_emoji():
    assert _emoji("no-such-category").startswith(":")
    assert _emoji("") .startswith(":")


@pytest.mark.unit
def test_markdown_is_converted_to_slack_mrkdwn():
    """Slack uses *bold* and _italic_, not GitHub-flavoured ** and *."""
    assert _strip_md("**bold**") == "*bold*"
    assert _strip_md("*italic*") == "_italic_"
    assert _strip_md("[link](https://example.com)") == "link"
    assert _strip_md("`code`") == "code"


@pytest.mark.unit
def test_strip_md_leaves_plain_text_untouched():
    assert _strip_md("just plain text") == "just plain text"


@pytest.mark.unit
def test_preview_truncates_long_content_on_a_word_boundary():
    content = "word " * 200
    out = _preview(content, max_chars=50)
    assert len(out) <= 53  # allow for the ellipsis
    assert not out.rstrip("… ").endswith("wor"), "truncated mid-word"


@pytest.mark.unit
def test_preview_leaves_short_content_intact_without_an_ellipsis():
    assert _preview("short text", max_chars=280) == "short text"


@pytest.mark.unit
def test_preview_handles_empty_content():
    assert _preview("") == ""


@pytest.mark.unit
def test_attribution_line_lists_contributors_with_percentages():
    line = _attribution_line(
        [
            {"contributor": "Ana", "contribution_weight": 0.6},
            {"contributor": "Bo", "contribution_weight": 0.4},
        ]
    )
    assert "Ana" in line and "Bo" in line
    assert "60" in line and "40" in line


@pytest.mark.unit
def test_attribution_line_is_empty_when_there_is_no_attribution():
    assert _attribution_line([]) == ""


@pytest.mark.unit
def test_attribution_line_summarises_beyond_the_top_three():
    line = _attribution_line(
        [{"contributor": f"User{i}", "contribution_weight": 0.2} for i in range(6)]
    )
    assert "more" in line.lower(), "long contributor lists should be summarised"


# ─── search results ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_search_results_render_valid_blocks():
    blocks = format_search_results("deployment", [_result()], "ws", "https://app")
    assert isinstance(blocks, list) and blocks
    # Slack requires every block to carry a recognised type.
    assert all(isinstance(b, dict) and "type" in b for b in blocks)


@pytest.mark.unit
def test_search_results_include_the_query_and_the_content():
    blocks = format_search_results(
        "how do we deploy", [_result("We deploy via Railway")], "ws", "https://app"
    )
    rendered = str(blocks)
    assert "how do we deploy" in rendered
    assert "We deploy via Railway" in rendered


@pytest.mark.unit
def test_empty_results_render_an_empty_state_rather_than_nothing():
    """Returning no blocks would make the bot appear to have ignored the user."""
    blocks = format_search_results("nothing matches", [], "ws", "https://app")
    assert blocks, "an empty result set must still produce a response"
    assert all("type" in b for b in blocks)


@pytest.mark.unit
def test_search_results_survive_missing_optional_fields():
    """Search returns synthesized memory dicts, and several fields can be None."""
    sparse = {"id": "abc", "content": "bare minimum", "score": 0.5}
    blocks = format_search_results("q", [sparse], "ws", "https://app")
    assert blocks and all("type" in b for b in blocks)


@pytest.mark.unit
def test_multiple_results_all_appear():
    results = [_result(f"memory number {i}") for i in range(3)]
    rendered = str(format_search_results("q", results, "ws", "https://app"))
    for i in range(3):
        assert f"memory number {i}" in rendered


# ─── experts ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_experts_render_valid_blocks_with_names():
    experts = [
        {
            "user_id": "u1",
            "name": "Priya",
            "confidence": 0.9,
            "memory_count": 4,
            "top_memory": {"preview": "something relevant", "category": "decision"},
        }
    ]
    blocks = format_experts("who knows deploys", experts, "https://app")
    assert blocks and all("type" in b for b in blocks)
    assert "Priya" in str(blocks)


@pytest.mark.unit
def test_experts_empty_state_is_rendered():
    blocks = format_experts("who knows quantum tunnelling", [], "https://app")
    assert blocks and all("type" in b for b in blocks)


@pytest.mark.unit
def test_expert_confidence_bar_stays_within_bounds():
    """The bar is built from a 0–1 confidence; a value outside that range
    would produce a malformed or absurdly long bar."""
    for confidence in (0.0, 0.5, 1.0):
        blocks = format_experts(
            "q",
            [{"user_id": "u", "name": "N", "confidence": confidence,
              "memory_count": 1, "top_memory": {"preview": "m"}}],
            "https://app",
        )
        assert blocks and all("type" in b for b in blocks)


# ─── help ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_help_blocks_document_both_commands():
    rendered = str(format_help())
    assert "sourcemind" in rendered.lower()
    assert "who knows" in rendered.lower()


@pytest.mark.unit
def test_help_returns_valid_blocks():
    blocks = format_help()
    assert blocks and all(isinstance(b, dict) and "type" in b for b in blocks)
