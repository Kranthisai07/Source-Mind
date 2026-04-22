"""GitHub artifact mapper — converts raw GitHub API payloads to ConnectorDocuments."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConnectorDocument(BaseModel):
    """Normalized document produced by any connector before ingestion.

    The ``idempotency_key`` is a deterministic SHA-256 hash over the tuple
    ``(source_tool, source_type, source_id)`` so duplicate sync runs never
    create duplicate memories.
    """

    source_tool: str = Field(description="Connector type, e.g. 'github'")
    source_type: str = Field(description="Artifact type, e.g. 'commit', 'pull_request'")
    source_id: str = Field(description="Stable external ID for dedup")
    source_url: str | None = Field(default=None)
    source_author: str | None = Field(default=None)
    source_timestamp: datetime | None = Field(default=None)
    title: str = Field(description="Short human-readable title")
    content: str = Field(description="Full text to ingest")
    idempotency_key: str = Field(description="SHA-256(source_tool+source_type+source_id)")
    memory_type: str = Field(default="", description="Semantic memory category (e.g. 'technical_decision')")
    tags: list[str] = Field(default_factory=list, description="Searchable labels derived from artifact metadata")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def make_key(cls, source_tool: str, source_type: str, source_id: str) -> str:
        """Compute the deterministic SHA-256 idempotency key."""
        raw = json.dumps([source_tool, source_type, source_id], separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class GitHubMapper:
    """Maps raw GitHub API payloads to :class:`ConnectorDocument` instances."""

    TOOL = "github"

    # ── Commits ───────────────────────────────────────────────────────────────

    @classmethod
    def from_commit(cls, owner: str, repo: str, raw: dict[str, Any]) -> ConnectorDocument:
        """Map a GitHub Commits API item.

        Args:
            owner: Repository owner login.
            repo: Repository name.
            raw: Raw commit object from ``GET /repos/{owner}/{repo}/commits``.
        """
        sha = raw["sha"]
        commit = raw.get("commit", {})
        author = commit.get("author") or {}
        committer_login = (raw.get("author") or {}).get("login") or author.get("name")

        message: str = commit.get("message", "")
        title_line = message.split("\n")[0][:200]
        timestamp_str: str | None = author.get("date")
        timestamp = _parse_dt(timestamp_str)

        content_parts = [
            f"Repository: {owner}/{repo}",
            f"Commit: {sha[:12]}",
            f"Author: {committer_login or 'unknown'}",
            f"Date: {timestamp_str or 'unknown'}",
            "",
            message,
        ]
        content = "\n".join(content_parts)

        source_id = f"{owner}/{repo}/commits/{sha}"
        return ConnectorDocument(
            source_tool=cls.TOOL,
            source_type="commit",
            source_id=source_id,
            source_url=raw.get("html_url"),
            source_author=committer_login,
            source_timestamp=timestamp,
            title=f"[{owner}/{repo}] {title_line}",
            content=content,
            idempotency_key=ConnectorDocument.make_key(cls.TOOL, "commit", source_id),
            metadata={
                "sha": sha,
                "repo": f"{owner}/{repo}",
                "files_changed": len(raw.get("files", [])),
            },
        )

    # ── Pull Requests ─────────────────────────────────────────────────────────

    @classmethod
    def from_pull_request(
        cls, owner: str, repo: str, raw: dict[str, Any]
    ) -> ConnectorDocument:
        """Map a GitHub Pull Requests API item.

        Args:
            owner: Repository owner login.
            repo: Repository name.
            raw: Raw PR object from ``GET /repos/{owner}/{repo}/pulls``.
        """
        number = raw["number"]
        title = raw.get("title", "")
        body = raw.get("body") or ""
        author_login = (raw.get("user") or {}).get("login")
        state = raw.get("state", "unknown")
        merged = raw.get("merged_at") is not None
        source_id = f"{owner}/{repo}/pull/{number}"

        content_parts = [
            f"Repository: {owner}/{repo}",
            f"Pull Request #{number}: {title}",
            f"Author: {author_login or 'unknown'}",
            f"State: {'merged' if merged else state}",
            f"Base: {(raw.get('base') or {}).get('ref', 'unknown')}",
            f"Head: {(raw.get('head') or {}).get('ref', 'unknown')}",
            "",
            body,
        ]
        if raw.get("labels"):
            labels = ", ".join(lbl["name"] for lbl in raw["labels"])
            content_parts.insert(5, f"Labels: {labels}")

        return ConnectorDocument(
            source_tool=cls.TOOL,
            source_type="pull_request",
            source_id=source_id,
            source_url=raw.get("html_url"),
            source_author=author_login,
            source_timestamp=_parse_dt(raw.get("updated_at") or raw.get("created_at")),
            title=f"[{owner}/{repo}] PR #{number}: {title[:150]}",
            content="\n".join(content_parts),
            idempotency_key=ConnectorDocument.make_key(cls.TOOL, "pull_request", source_id),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "state": state,
                "merged": merged,
                "labels": [lbl["name"] for lbl in raw.get("labels", [])],
            },
        )

    @classmethod
    def pull_request_to_document(
        cls,
        raw: dict[str, Any],
        owner: str,
        repo: str,
        workspace_id: Any,  # uuid.UUID — typed as Any to avoid import overhead
        comments: list[dict[str, Any]] | None = None,
        reviews: list[dict[str, Any]] | None = None,
        review_comments: list[dict[str, Any]] | None = None,
    ) -> "ConnectorDocument":
        """Map a PR with enriched comments/reviews to a :class:`ConnectorDocument`.

        Produces richer content than :meth:`from_pull_request` by appending
        review decisions, inline code comments, and top-level conversation
        comments.  Also derives ``memory_type`` and ``tags`` for downstream
        ingestion routing.

        Args:
            raw: Raw PR object from the GitHub REST API.
            owner: Repository owner login.
            repo: Repository name.
            workspace_id: Destination workspace UUID (stored in metadata).
            comments: Issue-thread comments from ``get_pr_comments``.
            reviews: Review objects from ``get_pr_reviews``.
            review_comments: Inline diff comments from ``get_pr_review_comments``.
        """
        comments = comments or []
        reviews = reviews or []
        review_comments = review_comments or []

        number = raw["number"]
        title = raw.get("title", "")
        body = raw.get("body") or ""
        author_login = (raw.get("user") or {}).get("login")
        state = raw.get("state", "unknown")
        merged = raw.get("merged_at") is not None
        labels = [lbl["name"] for lbl in raw.get("labels", [])]
        source_id = f"{owner}/{repo}/pull/{number}"

        # ── Content ───────────────────────────────────────────────────────────
        content_parts = [
            f"Repository: {owner}/{repo}",
            f"Pull Request #{number}: {title}",
            f"Author: {author_login or 'unknown'}",
            f"State: {'merged' if merged else state}",
            f"Base: {(raw.get('base') or {}).get('ref', 'unknown')}",
            f"Head: {(raw.get('head') or {}).get('ref', 'unknown')}",
        ]
        if labels:
            content_parts.append(f"Labels: {', '.join(labels)}")
        content_parts.extend(["", body or "(no description)"])

        if reviews:
            approvals = [r for r in reviews if r.get("state") == "APPROVED"]
            rejections = [r for r in reviews if r.get("state") == "CHANGES_REQUESTED"]
            content_parts.append(
                f"\n--- Reviews ({len(reviews)} total, "
                f"{len(approvals)} approved, {len(rejections)} changes requested) ---"
            )
            for review in reviews[:10]:  # cap to keep content manageable
                reviewer = (review.get("user") or {}).get("login", "unknown")
                review_state = review.get("state", "")
                review_body = (review.get("body") or "").strip()
                line = f"{reviewer} [{review_state}]"
                if review_body:
                    line += f": {review_body[:200]}"
                content_parts.append(line)

        if review_comments:
            content_parts.append(f"\n--- Inline Comments ({len(review_comments)}) ---")
            for rc in review_comments[:15]:
                rc_author = (rc.get("user") or {}).get("login", "unknown")
                rc_body = (rc.get("body") or "").strip()[:300]
                rc_path = rc.get("path", "")
                content_parts.append(f"{rc_author} on {rc_path}: {rc_body}")

        if comments:
            content_parts.append(f"\n--- Discussion ({len(comments)} comments) ---")
            for comment in comments[:10]:
                c_author = (comment.get("user") or {}).get("login", "unknown")
                c_body = (comment.get("body") or "").strip()[:300]
                content_parts.append(f"{c_author}: {c_body}")

        # ── memory_type ───────────────────────────────────────────────────────
        # Derive a semantic category from the PR's label set and merge status.
        memory_type = "pull_request"
        label_set = {lbl.lower() for lbl in labels}
        if label_set & {"breaking change", "breaking-change", "semver-major"}:
            memory_type = "breaking_change"
        elif label_set & {"bug", "fix", "hotfix", "regression"}:
            memory_type = "bug_fix"
        elif label_set & {"feature", "enhancement", "feat"}:
            memory_type = "feature"
        elif label_set & {"refactor", "cleanup", "tech-debt", "chore"}:
            memory_type = "refactor"
        elif label_set & {"docs", "documentation"}:
            memory_type = "documentation"

        # ── tags ─────────────────────────────────────────────────────────────
        tags: list[str] = list(labels)
        tags.append("merged" if merged else state)
        tags.append(f"repo:{owner}/{repo}")
        # Add reviewer logins as tags for attribution-aware search
        for review in reviews:
            reviewer_login = (review.get("user") or {}).get("login")
            if reviewer_login:
                reviewer_tag = f"reviewer:{reviewer_login}"
                if reviewer_tag not in tags:
                    tags.append(reviewer_tag)

        return ConnectorDocument(
            source_tool=cls.TOOL,
            source_type="pull_request",
            source_id=source_id,
            source_url=raw.get("html_url"),
            source_author=author_login,
            source_timestamp=_parse_dt(raw.get("updated_at") or raw.get("created_at")),
            title=f"[{owner}/{repo}] PR #{number}: {title[:150]}",
            content="\n".join(content_parts),
            idempotency_key=ConnectorDocument.make_key(cls.TOOL, "pull_request", source_id),
            memory_type=memory_type,
            tags=tags,
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "workspace_id": str(workspace_id),
                "state": state,
                "merged": merged,
                "labels": labels,
                "review_count": len(reviews),
                "comment_count": len(comments),
                "review_comment_count": len(review_comments),
                "approvals": sum(1 for r in reviews if r.get("state") == "APPROVED"),
            },
        )

    # ── Issues ────────────────────────────────────────────────────────────────

    @classmethod
    def from_issue(cls, owner: str, repo: str, raw: dict[str, Any]) -> ConnectorDocument:
        """Map a GitHub Issues API item.

        Args:
            owner: Repository owner login.
            repo: Repository name.
            raw: Raw issue object from ``GET /repos/{owner}/{repo}/issues``.
        """
        number = raw["number"]
        title = raw.get("title", "")
        body = raw.get("body") or ""
        author_login = (raw.get("user") or {}).get("login")
        state = raw.get("state", "unknown")
        source_id = f"{owner}/{repo}/issues/{number}"

        content_parts = [
            f"Repository: {owner}/{repo}",
            f"Issue #{number}: {title}",
            f"Author: {author_login or 'unknown'}",
            f"State: {state}",
        ]
        if raw.get("labels"):
            labels = ", ".join(lbl["name"] for lbl in raw["labels"])
            content_parts.append(f"Labels: {labels}")
        if raw.get("assignees"):
            assignees = ", ".join(a["login"] for a in raw["assignees"])
            content_parts.append(f"Assignees: {assignees}")
        content_parts.extend(["", body])

        return ConnectorDocument(
            source_tool=cls.TOOL,
            source_type="issue",
            source_id=source_id,
            source_url=raw.get("html_url"),
            source_author=author_login,
            source_timestamp=_parse_dt(raw.get("updated_at") or raw.get("created_at")),
            title=f"[{owner}/{repo}] Issue #{number}: {title[:150]}",
            content="\n".join(content_parts),
            idempotency_key=ConnectorDocument.make_key(cls.TOOL, "issue", source_id),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "state": state,
                "labels": [lbl["name"] for lbl in raw.get("labels", [])],
            },
        )

    # ── Discussions ───────────────────────────────────────────────────────────

    @classmethod
    def from_discussion(
        cls, owner: str, repo: str, raw: dict[str, Any]
    ) -> ConnectorDocument:
        """Map a GitHub Discussions GraphQL node.

        Args:
            owner: Repository owner login.
            repo: Repository name.
            raw: Raw discussion node from the GraphQL Discussions query.
        """
        number = raw["number"]
        title = raw.get("title", "")
        body = raw.get("body") or ""
        author_login = (raw.get("author") or {}).get("login")
        category = (raw.get("category") or {}).get("name", "General")
        source_id = f"{owner}/{repo}/discussions/{number}"

        content_parts = [
            f"Repository: {owner}/{repo}",
            f"Discussion #{number}: {title}",
            f"Author: {author_login or 'unknown'}",
            f"Category: {category}",
            "",
            body,
        ]
        comments = (raw.get("comments") or {}).get("nodes", [])
        if comments:
            content_parts.append("\n--- Comments ---")
            for comment in comments:
                comment_author = (comment.get("author") or {}).get("login", "unknown")
                content_parts.append(f"{comment_author}: {comment.get('body', '')}")

        return ConnectorDocument(
            source_tool=cls.TOOL,
            source_type="discussion",
            source_id=source_id,
            source_url=raw.get("url"),
            source_author=author_login,
            source_timestamp=_parse_dt(raw.get("updatedAt") or raw.get("createdAt")),
            title=f"[{owner}/{repo}] Discussion #{number}: {title[:150]}",
            content="\n".join(content_parts),
            idempotency_key=ConnectorDocument.make_key(cls.TOOL, "discussion", source_id),
            metadata={
                "number": number,
                "repo": f"{owner}/{repo}",
                "category": category,
                "comment_count": len(comments),
            },
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to a timezone-aware datetime, or return None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
