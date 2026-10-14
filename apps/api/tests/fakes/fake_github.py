"""Deterministic in-memory fake GitHub served over ``httpx.MockTransport``.

Implements exactly the endpoints the automatic-ingestion client uses. Everything is
SIMULATED: nothing here is evidence of real GitHub behavior (pagination, ``since``
semantics, rate-limit headers and permission responses are this fake's assumptions,
written from GitHub's documentation).

``fake.log`` is the operation log: a list of ``(method, path)`` calls, so tests can
assert "zero content calls".
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

BASE = "https://api.github.com"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class _Rule:
    contains: str
    kind: str
    times: int | None  # None = forever
    retry_after: int | None = None


@dataclass
class _Repo:
    private: bool = True
    default_branch: str = "main"
    items: dict[int, dict[str, Any]] = field(default_factory=dict)  # issues + PRs by number
    reviews: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    issue_comments: list[dict[str, Any]] = field(default_factory=list)
    review_comments: list[dict[str, Any]] = field(default_factory=list)
    commits: dict[str, dict[str, Any]] = field(default_factory=dict)
    head: str | None = None
    next_number: int = 1


class FakeGitHub:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        self.repos: dict[str, _Repo] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.permissions: dict[str, Any] = {}  # login -> "write"/"none"/dict body
        self.log: list[tuple[str, str]] = []
        self.rules: list[_Rule] = []
        self.etags_enabled = True
        self._ids = 1000
        self._seq = 0
        self.transport = httpx.MockTransport(self._handle)

    # ── setup helpers ────────────────────────────────────────────────────────
    def tick(self, seconds: float = 1) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now

    def _id(self) -> int:
        self._ids += 1
        return self._ids

    def user(self, login: str, uid: int | None = None, type_: str = "User") -> dict[str, Any]:
        if login not in self.users:
            self.users[login] = {"login": login, "id": uid or self._id(), "type": type_}
        return self.users[login]

    def add_repo(self, full: str, *, private: bool = True) -> _Repo:
        self.repos[full] = _Repo(private=private)
        return self.repos[full]

    def _repo(self, full: str) -> _Repo:
        return self.repos[full]

    def _stamp(self, at: datetime | None) -> str:
        return _iso(at or self.tick())

    def add_issue(
        self,
        full: str,
        user: str,
        title: str,
        body: str = "",
        *,
        state: str = "open",
        at: datetime | None = None,
    ) -> int:
        r = self._repo(full)
        n = r.next_number
        r.next_number += 1
        ts = self._stamp(at)
        r.items[n] = {
            "id": self._id(),
            "number": n,
            "title": title,
            "body": body,
            "state": state,
            "user": self.user(user),
            "labels": [],
            "created_at": ts,
            "updated_at": ts,
            "html_url": f"https://github.com/{full}/issues/{n}",
            "_pr": False,
        }
        return n

    def add_pull(
        self,
        full: str,
        user: str,
        title: str,
        body: str = "",
        *,
        state: str = "open",
        at: datetime | None = None,
    ) -> int:
        n = self.add_issue(full, user, title, body, state=state, at=at)
        item = self._repo(full).items[n]
        item["_pr"] = True
        item["html_url"] = f"https://github.com/{full}/pull/{n}"
        item["merged_at"] = None
        item["head"] = {"ref": "feature"}
        item["base"] = {"ref": "main"}
        return n

    def edit_item(
        self, full: str, number: int, *, bump: bool = True, at: datetime | None = None, **fields
    ) -> None:
        item = self._repo(full).items[number]
        item.update(fields)
        if bump:
            item["updated_at"] = self._stamp(at)

    def add_review(
        self,
        full: str,
        number: int,
        user: str,
        body: str = "",
        *,
        state: str = "COMMENTED",
        bump_pr: bool = False,
        at: datetime | None = None,
    ) -> int:
        """Reviews do NOT bump the PR's ``updated_at`` unless *bump_pr* (the T-V1 switch)."""
        r = self._repo(full)
        rid = self._id()
        ts = self._stamp(at)
        r.reviews.setdefault(number, []).append(
            {
                "id": rid,
                "user": self.user(user),
                "body": body,
                "state": state,
                "submitted_at": ts,
                "html_url": f"https://github.com/{full}/pull/{number}#pullrequestreview-{rid}",
            }
        )
        if bump_pr:
            r.items[number]["updated_at"] = ts
        return rid

    def add_issue_comment(
        self, full: str, number: int, user: str, body: str, *, at: datetime | None = None
    ) -> int:
        r = self._repo(full)
        cid = self._id()
        ts = self._stamp(at)
        r.issue_comments.append(
            {
                "id": cid,
                "user": self.user(user),
                "body": body,
                "created_at": ts,
                "updated_at": ts,
                "issue_url": f"{BASE}/repos/{full}/issues/{number}",
                "html_url": f"https://github.com/{full}/issues/{number}#issuecomment-{cid}",
            }
        )
        r.items[number]["updated_at"] = ts  # a comment touches its issue/PR
        return cid

    def add_review_comment(
        self, full: str, number: int, user: str, body: str, *, at: datetime | None = None
    ) -> int:
        r = self._repo(full)
        cid = self._id()
        ts = self._stamp(at)
        r.review_comments.append(
            {
                "id": cid,
                "user": self.user(user),
                "body": body,
                "path": "a.py",
                "created_at": ts,
                "updated_at": ts,
                "pull_request_url": f"{BASE}/repos/{full}/pulls/{number}",
                "html_url": f"https://github.com/{full}/pull/{number}#discussion_r{cid}",
            }
        )
        return cid

    def edit_comment(self, full: str, comment_id: int, body: str, *, at: datetime | None = None):
        r = self._repo(full)
        for c in r.issue_comments + r.review_comments:
            if c["id"] == comment_id:
                c["body"] = body
                c["updated_at"] = self._stamp(at)
                return
        raise KeyError(comment_id)

    def delete_comment(self, full: str, comment_id: int) -> None:
        r = self._repo(full)
        r.issue_comments = [c for c in r.issue_comments if c["id"] != comment_id]
        r.review_comments = [c for c in r.review_comments if c["id"] != comment_id]

    def add_commit(
        self,
        full: str,
        message: str,
        author: str | None,
        *,
        date: datetime | None = None,
        parents: list[str] | None = None,
        advance: bool = True,
        git_name: str | None = None,
    ) -> str:
        """Add a commit. ``advance=False`` creates it on a side branch (not yet on main)."""
        r = self._repo(full)
        self._seq += 1
        sha = hashlib.sha1(f"{full}{self._seq}{message}".encode()).hexdigest()  # noqa: S324
        if parents is None:
            parents = [r.head] if r.head else []
        r.commits[sha] = {
            "sha": sha,
            "_seq": self._seq,
            "_parents": parents,
            "html_url": f"https://github.com/{full}/commit/{sha}",
            "commit": {
                "message": message,
                "author": {
                    "name": git_name or author or "anon",
                    "date": _iso(date or self.tick()),
                },
                "committer": {"name": "committer", "date": _iso(self.tick())},
            },
            "author": self.user(author) if author else None,
            "committer": {"login": "committer", "id": 999999, "type": "User"},
        }
        if advance:
            r.head = sha
        return sha

    def merge(self, full: str, side_head: str, message: str = "Merge branch") -> str:
        """Merge a side branch into main (merge commit has both parents)."""
        r = self._repo(full)
        return self.add_commit(full, message, "merger", parents=[r.head, side_head] if r.head else [side_head])

    def force_push(self, full: str, new_head: str) -> None:
        self._repo(full).head = new_head

    # ── failure injection ────────────────────────────────────────────────────
    def inject(self, contains: str, kind: str, *, times: int | None = 1, retry_after=None):
        """kind: primary | secondary | permission | 404 | 410 | 500 | 502 | bad_json."""
        self.rules.append(_Rule(contains, kind, times, retry_after))

    def clear_rules(self) -> None:
        self.rules.clear()

    def content_calls(self) -> list[tuple[str, str]]:
        """Calls that read repository content (everything except meta endpoints)."""
        return [c for c in self.log if not re.search(r"/(installation|user/)|/permission$", c[1])]

    # ── request handling ─────────────────────────────────────────────────────
    def _ancestors(self, full: str, sha: str) -> set[str]:
        r = self._repo(full)
        seen: set[str] = set()
        stack = [sha]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in r.commits:
                continue
            seen.add(cur)
            stack.extend(r.commits[cur]["_parents"])
        return seen

    def _public(self, obj: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in obj.items() if not k.startswith("_")}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path
        self.log.append((request.method, path))
        for rule in list(self.rules):
            if rule.contains in path and (rule.times is None or rule.times > 0):
                if rule.times is not None:
                    rule.times -= 1
                return self._failure(rule)
        query = {k: v[0] for k, v in parse_qs(url.query.decode()).items()}
        try:
            return self._route(request, path, query)
        except KeyError:
            return httpx.Response(404, json={"message": "Not Found"})

    def _failure(self, rule: _Rule) -> httpx.Response:
        k = rule.kind
        if k == "primary":
            reset = int((self.now + timedelta(minutes=30)).timestamp())
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
            )
        if k == "secondary":
            headers = {"Retry-After": str(rule.retry_after if rule.retry_after else 60)}
            return httpx.Response(
                403,
                json={"message": "You have exceeded a secondary rate limit."},
                headers=headers,
            )
        if k == "permission":
            return httpx.Response(403, json={"message": "Resource not accessible by integration"})
        if k == "404":
            return httpx.Response(404, json={"message": "Not Found"})
        if k == "410":
            return httpx.Response(410, json={"message": "Gone"})
        if k == "bad_json":
            return httpx.Response(200, content=b"<html>nope</html>")
        return httpx.Response(int(k), json={"message": "server error"})

    def _paged(self, request: httpx.Request, items: list[Any], query: dict[str, str], wrap=None):
        per_page = min(int(query.get("per_page", 30)), 100)
        page = int(query.get("page", 1))
        chunk = items[(page - 1) * per_page : page * per_page]
        headers: dict[str, str] = {}
        if page * per_page < len(items):
            q = dict(query)
            q["page"] = str(page + 1)
            nxt = f"{BASE}{request.url.path}?{urlencode(q)}"
            headers["Link"] = f'<{nxt}>; rel="next"'
        body: Any = wrap(chunk) if wrap else chunk
        if self.etags_enabled and wrap is None:
            tag = '"' + hashlib.md5(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest() + '"'  # noqa: S324
            if request.headers.get("If-None-Match") == tag and page == 1:
                return httpx.Response(304, headers={"ETag": tag})
            headers["ETag"] = tag
        return httpx.Response(200, json=body, headers=headers)

    def _route(self, request: httpx.Request, path: str, q: dict[str, str]) -> httpx.Response:
        if m := re.fullmatch(r"/user/(\d+)", path):
            uid = int(m.group(1))
            for u in self.users.values():
                if u["id"] == uid:
                    return httpx.Response(200, json=u)
            return httpx.Response(404, json={"message": "Not Found"})
        if path == "/installation/repositories":
            repos = [
                {"full_name": n, "private": r.private, "name": n.split("/")[1]}
                for n, r in self.repos.items()
            ]
            return self._paged(request, repos, q, wrap=lambda c: {"total_count": len(repos), "repositories": c})
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)(/.*)?", path)
        if not m:
            return httpx.Response(404, json={"message": "Not Found"})
        full, rest = m.group(1), m.group(2) or ""
        r = self._repo(full)
        if rest == "":
            return httpx.Response(
                200,
                json={"full_name": full, "private": r.private, "default_branch": r.default_branch},
            )
        if mm := re.fullmatch(r"/branches/(.+)", rest):
            if not r.head:
                return httpx.Response(404, json={"message": "Branch not found"})
            return httpx.Response(200, json={"name": mm.group(1), "commit": {"sha": r.head}})
        if mm := re.fullmatch(r"/collaborators/([^/]+)/permission", rest):
            perm = self.permissions.get(mm.group(1))
            if perm is None:
                return httpx.Response(404, json={"message": "Not Found"})
            body = perm if isinstance(perm, dict) else {"permission": perm, "role_name": perm}
            return httpx.Response(200, json=body)
        if rest == "/commits":
            return self._commits(request, full, q)
        if mm := re.fullmatch(r"/compare/(.+)\.\.\.(.+)", rest):
            return self._compare(request, full, mm.group(1), mm.group(2), q)
        if rest == "/issues":
            return self._issues(request, r, q)
        if rest == "/issues/comments":
            return self._feed(request, r.issue_comments, q)
        if rest == "/pulls/comments":
            return self._feed(request, r.review_comments, q)
        if rest == "/pulls":
            prs = [self._pr(full, i) for i in r.items.values() if i["_pr"]]
            prs = self._filter_state(prs, q)
            prs.sort(key=lambda i: (i["updated_at"], i["number"]), reverse=q.get("direction", "desc") == "desc")
            return self._paged(request, prs, q)
        if mm := re.fullmatch(r"/pulls/(\d+)/reviews", rest):
            return self._paged(request, [self._public(x) for x in r.reviews.get(int(mm.group(1)), [])], q)
        if mm := re.fullmatch(r"/issues/(\d+)/comments", rest):
            n = int(mm.group(1))
            if n not in r.items:
                raise KeyError(n)
            url = f"{BASE}/repos/{full}/issues/{n}"
            return self._paged(request, [c for c in r.issue_comments if c["issue_url"] == url], q)
        return httpx.Response(404, json={"message": "Not Found"})

    def _pr(self, full: str, item: dict[str, Any]) -> dict[str, Any]:
        return self._public(item)

    @staticmethod
    def _filter_state(items: list[dict[str, Any]], q: dict[str, str]) -> list[dict[str, Any]]:
        state = q.get("state", "open")
        return items if state == "all" else [i for i in items if i["state"] == state]

    def _issues(self, request, r: _Repo, q) -> httpx.Response:
        out = []
        for i in r.items.values():
            d = self._public(i)
            if i["_pr"]:
                d["pull_request"] = {"url": d["html_url"]}
            out.append(d)
        out = self._filter_state(out, q)
        if "since" in q:
            out = [i for i in out if _dt(i["updated_at"]) >= _dt(q["since"])]
        key = "created_at" if q.get("sort") == "created" else "updated_at"
        out.sort(key=lambda i: (i[key], i["number"]), reverse=q.get("direction", "desc") == "desc")
        return self._paged(request, out, q)

    def _feed(self, request, comments: list[dict[str, Any]], q) -> httpx.Response:
        out = [c for c in comments]
        if "since" in q:
            out = [c for c in out if _dt(c["updated_at"]) >= _dt(q["since"])]
        out.sort(key=lambda c: (c["updated_at"], c["id"]), reverse=q.get("direction", "asc") == "desc")
        return self._paged(request, out, q)

    def _commits(self, request, full: str, q) -> httpx.Response:
        r = self._repo(full)
        start = q.get("sha") or r.head
        if start is None:
            return self._paged(request, [], q)
        out = [r.commits[s] for s in self._ancestors(full, start)]
        if "since" in q:
            out = [c for c in out if _dt(c["commit"]["author"]["date"]) >= _dt(q["since"])]
        if "until" in q:
            out = [c for c in out if _dt(c["commit"]["author"]["date"]) <= _dt(q["until"])]
        out.sort(key=lambda c: c["_seq"], reverse=True)
        return self._paged(request, [self._public(c) for c in out], q)

    def _compare(self, request, full: str, base: str, head: str, q) -> httpx.Response:
        r = self._repo(full)
        if base not in r.commits or head not in r.commits:
            return httpx.Response(404, json={"message": "Not Found"})
        a_head, a_base = self._ancestors(full, head), self._ancestors(full, base)
        ahead = sorted(a_head - a_base, key=lambda s: r.commits[s]["_seq"])
        behind = a_base - a_head
        if base == head:
            status = "identical"
        elif not behind:
            status = "ahead"
        elif not ahead:
            status = "behind"
        else:
            status = "diverged"
        per_page = min(int(q.get("per_page", 30)), 100)
        page = int(q.get("page", 1))
        chunk = ahead[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(ahead):
            nq = dict(q)
            nq["page"] = str(page + 1)
            headers["Link"] = f'<{BASE}{request.url.path}?{urlencode(nq)}>; rel="next"'
        return httpx.Response(
            200,
            json={
                "status": status,
                "ahead_by": len(ahead),
                "behind_by": len(behind),
                "commits": [self._public(r.commits[s]) for s in chunk],
            },
            headers=headers,
        )


def make_client(fake: FakeGitHub, *, sleeps: list[float] | None = None, **kwargs):
    """A ``GitHubClient`` wired to *fake*; sleeps are recorded, never awaited for real."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sourcemind.connectors.github.client import GitHubClient

    async def _sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    auth = SimpleNamespace(
        get_token=AsyncMock(return_value="ghs_fake"), invalidate_cache=AsyncMock()
    )
    client = GitHubClient(auth, transport=fake.transport, sleep=_sleep, **kwargs)
    client.auth_stub = auth  # type: ignore[attr-defined]
    return client
