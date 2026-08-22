"""
Slack Block Kit message formatter for SourceMind search results.

Converts raw search results and who-would-know experts into
structured Slack messages with rich formatting.
"""

from __future__ import annotations

import re
from typing import Any

_DASHBOARD_URL = "https://app.sourcemind.ai"  # overridden via SOURCEMIND_APP_URL env var

CATEGORY_EMOJI = {
    "architecture": ":building_construction:",
    "decision":     ":memo:",
    "incident":     ":rotating_light:",
    "onboarding":   ":wave:",
    "general":      ":page_facing_up:",
    "security":     ":lock:",
    "performance":  ":zap:",
    "api":          ":electric_plug:",
    "database":     ":floppy_disk:",
    "deployment":   ":rocket:",
}


def _emoji(category: str) -> str:
    return CATEGORY_EMOJI.get((category or "general").lower(), ":page_facing_up:")


def _strip_md(text: str) -> str:
    """Strip markdown for Slack plain-text contexts."""
    text = re.sub(r"```[\s\S]*?```", "[code]", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold must be parked behind a sentinel first. Rewriting **bold** to
    # *bold* and then applying the italic rule turns it straight into _bold_,
    # so every bold span silently rendered as italic in Slack.
    bold_sentinel = "\x00BOLD\x00"
    text = re.sub(r"\*\*([^*]+)\*\*", rf"{bold_sentinel}\1{bold_sentinel}", text)
    text = re.sub(r"\*([^*]+)\*", r"_\1_", text)      # italic → Slack italic
    text = text.replace(bold_sentinel, "*")           # sentinel → Slack bold
    text = re.sub(r"~~([^~]+)~~", r"~\1~", text)        # strike stays
    text = re.sub(r"^[-*+]\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "• ", text, flags=re.MULTILINE)
    return text.strip()


def _preview(content: str, max_chars: int = 280) -> str:
    text = _strip_md(content)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def _attribution_line(attributions: list[dict]) -> str:
    if not attributions:
        return ""
    parts = []
    for a in attributions[:3]:
        name  = a.get("contributor") or a.get("name") or "unknown"
        score = a.get("contribution_weight") or a.get("score") or 0
        pct   = round(float(score) * 100)
        parts.append(f"{name} ({pct}%)")
    line = ", ".join(parts)
    if len(attributions) > 3:
        line += f" +{len(attributions) - 3} more"
    return line


def format_search_results(
    query: str,
    results: list[dict],
    workspace_id: str = "",
    app_url: str = "",
) -> list[dict]:
    """
    Build Slack Block Kit blocks for search results.

    Returns a list of block dicts suitable for the `blocks` field
    of a Slack message payload.
    """
    base_url = (app_url or _DASHBOARD_URL).rstrip("/")

    if not results:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":mag: No memories found for *{query}*.\n"
                        "Try a broader term or check the SourceMind dashboard."
                    ),
                },
            }
        ]

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":mag: *{len(results)} "
                    f"result{'s' if len(results) != 1 else ''} for:* _{query}_"
                ),
            },
        },
        {"type": "divider"},
    ]

    for i, r in enumerate(results[:5]):
        memory_id  = r.get("memory_id") or r.get("id") or ""
        content    = r.get("content") or ""
        category   = r.get("category") or "general"
        tags       = r.get("tags") or []
        score      = r.get("score") or r.get("similarity") or 0
        attr       = r.get("attribution") or []
        preview    = _preview(content)
        cat_emoji  = _emoji(category)
        score_pct  = round(float(score) * 100)
        attr_line  = _attribution_line(attr)

        tag_str = "  ".join(f"`{t}`" for t in tags[:4]) if tags else ""

        text_parts = [f"{cat_emoji} *{category.capitalize()}*"]
        if score_pct:
            text_parts.append(f"  _{score_pct}% match_")
        text_parts.append(f"\n{preview}")
        if attr_line:
            text_parts.append(f"\n:bust_in_silhouette: {attr_line}")
        if tag_str:
            text_parts.append(f"\n{tag_str}")

        block: dict[str, Any] = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(text_parts)},
        }

        if memory_id:
            block["accessory"] = {
                "type": "button",
                "text": {"type": "plain_text", "text": "View", "emoji": True},
                "url": f"{base_url}/memories/{memory_id}",
                "action_id": f"view_memory_{i}",
            }

        blocks.append(block)

        if i < len(results) - 1 and i < 4:
            blocks.append({"type": "divider"})

    if len(results) > 5:
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": (
                    f":information_source: Showing 5 of {len(results)} results. "
                    f"<{base_url}/memories?q={query}|See all →>"
                ),
            }],
        })

    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                ":brain: Powered by <https://sourcemind.ai|SourceMind> · "
                "knowledge memory for engineering teams"
            ),
        }],
    })

    return blocks


def format_experts(
    query: str,
    experts: list[dict],
    app_url: str = "",
) -> list[dict]:
    """
    Build Slack Block Kit blocks for who-would-know results.

    NOTE: app_url is accepted for signature parity with the other
    formatters but is unused, because these blocks contain no links back
    to the dashboard. format_search_results does link. If expert blocks
    should link to profiles, that is a feature to add, not a dead local
    to keep.
    """

    if not experts:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":busts_in_silhouette: Nobody has specific expertise on *{query}* yet.\n"
                        "Consider documenting this in SourceMind!"
                    ),
                },
            }
        ]

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":busts_in_silhouette: *Who would know about:* _{query}_",
            },
        },
        {"type": "divider"},
    ]

    medal = [":first_place_medal:", ":second_place_medal:", ":third_place_medal:"]

    for i, e in enumerate(experts[:5]):
        name       = e.get("name") or e.get("login") or "Unknown"
        login      = e.get("login") or ""
        confidence = float(e.get("confidence") or 0)
        mem_count  = int(e.get("memory_count") or 0)
        top_mem    = e.get("top_memory") or {}
        preview    = _preview(top_mem.get("preview") or "", 160)
        category   = top_mem.get("category") or "general"
        conf_pct   = round(confidence * 100)
        rank_icon  = medal[i] if i < 3 else f"*{i + 1}.*"

        bar_filled = round(confidence * 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        text = (
            f"{rank_icon}  *{name}*"
            + (f"  `@{login}`" if login and login != name else "")
            + f"\n`{bar}` {conf_pct}% confidence · "
            + f"{mem_count} memor{'y' if mem_count == 1 else 'ies'}"
        )
        if preview:
            text += f"\n_{_emoji(category)} {preview}_"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        })

    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": ":brain: Powered by <https://sourcemind.ai|SourceMind>",
        }],
    })

    return blocks


def format_help() -> list[dict]:
    """Return Block Kit blocks for the /sourcemind help command."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*:brain: SourceMind Slack Bot*\n"
                    "AI-powered knowledge memory for your engineering team."
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Commands:*\n"
                    "• `/sourcemind <query>` — search memories\n"
                    "• `/sourcemind who knows <topic>` — find the right person to ask\n"
                    "• `/sourcemind help` — show this message\n\n"
                    "*Examples:*\n"
                    "• `/sourcemind how did we handle the auth migration?`\n"
                    "• `/sourcemind who knows about rate limiting`\n"
                    "• `/sourcemind postgres RLS policies`"
                ),
            },
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "You can also mention @SourceMind in any channel with a question.",
            }],
        },
    ]
