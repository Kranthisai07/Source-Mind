"""Evaluation dataset builder — mines GitHub repos and generates ground_truth.json.

Mines 5 target repositories for 4 artifact types (commits, PRs, issues, discussions)
using GitHubClient (same auth/pagination layer as the connector).

Usage:
    python -m evaluation.dataset_builder --output evaluation/data/ground_truth.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Target repos for the evaluation benchmark
TARGET_REPOS = [
    ("facebook", "react"),
    ("microsoft", "TypeScript"),
    ("rust-lang", "rust"),
    ("vercel", "next.js"),
    ("django", "django"),
]


class DatasetBuilder:
    """Mines GitHub repos and assembles a ground_truth.json for evaluation.

    Args:
        client: Configured :class:`~sourcemind.connectors.github.client.GitHubClient`.
        per_repo_limit: Maximum number of each artifact type to collect per repo.
    """

    def __init__(self, client: Any, per_repo_limit: int = 20) -> None:
        self._client = client
        self._limit = per_repo_limit

    async def build(self, repos: list[tuple[str, str]] = TARGET_REPOS) -> list[dict[str, Any]]:
        """Mine all repos and return the ground_truth list."""
        dataset: list[dict[str, Any]] = []
        for owner, repo in repos:
            print(f"  Mining {owner}/{repo}...")
            repo_items = await self._mine_repo(owner, repo)
            dataset.extend(repo_items)
            print(f"    → {len(repo_items)} items")
        return dataset

    async def _mine_repo(self, owner: str, repo: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        items.extend(await self._mine_commits(owner, repo))
        items.extend(await self._mine_prs(owner, repo))
        items.extend(await self._mine_issues(owner, repo))
        items.extend(await self._mine_discussions(owner, repo))
        return items

    async def _mine_commits(self, owner: str, repo: str) -> list[dict[str, Any]]:
        items = []
        try:
            async for raw in self._client.get_commits(owner, repo, per_page=self._limit):
                sha = raw["sha"]
                commit = raw.get("commit", {})
                message = commit.get("message", "")
                author = (raw.get("author") or {}).get("login") or (commit.get("author") or {}).get("name", "unknown")
                date_str = (commit.get("author") or {}).get("date", "")

                items.append({
                    "id": f"{owner}/{repo}/commits/{sha}",
                    "repo": f"{owner}/{repo}",
                    "artifact_type": "commit",
                    "source_id": sha,
                    "content": f"[{owner}/{repo}] Commit {sha[:12]} by {author}: {message}",
                    "question": f"What did commit {sha[:12]} change in {owner}/{repo}?",
                    "expected_answer": message.split("\n")[0],
                    "metadata": {"sha": sha, "author": author, "date": date_str},
                })
                if len(items) >= self._limit:
                    break
        except Exception as exc:
            print(f"    ⚠ commits failed for {owner}/{repo}: {exc}")
        return items

    async def _mine_prs(self, owner: str, repo: str) -> list[dict[str, Any]]:
        items = []
        try:
            async for raw in self._client.get_pull_requests(owner, repo, state="closed", per_page=self._limit):
                number = raw["number"]
                title = raw.get("title", "")
                body = raw.get("body") or ""
                author = (raw.get("user") or {}).get("login", "unknown")
                state = "merged" if raw.get("merged_at") else "closed"

                items.append({
                    "id": f"{owner}/{repo}/pull/{number}",
                    "repo": f"{owner}/{repo}",
                    "artifact_type": "pull_request",
                    "source_id": str(number),
                    "content": f"[{owner}/{repo}] PR #{number} ({state}): {title}\n{body[:500]}",
                    "question": f"What was the purpose of PR #{number} in {owner}/{repo}?",
                    "expected_answer": title,
                    "metadata": {"number": number, "state": state, "author": author},
                })
                if len(items) >= self._limit:
                    break
        except Exception as exc:
            print(f"    ⚠ PRs failed for {owner}/{repo}: {exc}")
        return items

    async def _mine_issues(self, owner: str, repo: str) -> list[dict[str, Any]]:
        items = []
        try:
            async for raw in self._client.get_issues(owner, repo, state="closed", per_page=self._limit):
                if "pull_request" in raw:
                    continue
                number = raw["number"]
                title = raw.get("title", "")
                body = raw.get("body") or ""
                author = (raw.get("user") or {}).get("login", "unknown")

                items.append({
                    "id": f"{owner}/{repo}/issues/{number}",
                    "repo": f"{owner}/{repo}",
                    "artifact_type": "issue",
                    "source_id": str(number),
                    "content": f"[{owner}/{repo}] Issue #{number}: {title}\n{body[:500]}",
                    "question": f"What was reported in issue #{number} of {owner}/{repo}?",
                    "expected_answer": title,
                    "metadata": {"number": number, "author": author},
                })
                if len(items) >= self._limit:
                    break
        except Exception as exc:
            print(f"    ⚠ issues failed for {owner}/{repo}: {exc}")
        return items

    async def _mine_discussions(self, owner: str, repo: str) -> list[dict[str, Any]]:
        items = []
        try:
            nodes = await self._client.get_discussions(owner, repo, limit=self._limit)
            for raw in nodes:
                number = raw["number"]
                title = raw.get("title", "")
                body = raw.get("body") or ""
                author = (raw.get("author") or {}).get("login", "unknown")
                category = (raw.get("category") or {}).get("name", "General")

                items.append({
                    "id": f"{owner}/{repo}/discussions/{number}",
                    "repo": f"{owner}/{repo}",
                    "artifact_type": "discussion",
                    "source_id": str(number),
                    "content": f"[{owner}/{repo}] Discussion #{number} [{category}]: {title}\n{body[:500]}",
                    "question": f"What was discussed in discussion #{number} of {owner}/{repo}?",
                    "expected_answer": title,
                    "metadata": {"number": number, "author": author, "category": category},
                })
        except Exception as exc:
            print(f"    ⚠ discussions failed for {owner}/{repo}: {exc}")
        return items


async def main(output: str, per_repo_limit: int) -> None:
    import sys
    import os
    sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "api"))

    from sourcemind.core.config import get_settings
    from sourcemind.connectors.github.pat_auth import GitHubPATAuth
    from sourcemind.connectors.github.client import GitHubClient

    settings = get_settings()
    if not settings.github_research_pat:
        print("ERROR: GITHUB_RESEARCH_PAT not set in environment / .env", file=sys.stderr)
        sys.exit(1)

    auth = GitHubPATAuth(token=settings.github_research_pat)
    client = GitHubClient(auth)

    print(f"Building evaluation dataset (limit={per_repo_limit} per repo per type)...")
    builder = DatasetBuilder(client=client, per_repo_limit=per_repo_limit)
    dataset = await builder.build()

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total": len(dataset),
                "repos": [f"{o}/{r}" for o, r in TARGET_REPOS],
                "items": dataset,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"Wrote {len(dataset)} items to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build evaluation ground_truth.json")
    parser.add_argument("--output", default="evaluation/data/ground_truth.json")
    parser.add_argument("--limit", type=int, default=20, help="Items per repo per type")
    args = parser.parse_args()
    asyncio.run(main(args.output, args.limit))
