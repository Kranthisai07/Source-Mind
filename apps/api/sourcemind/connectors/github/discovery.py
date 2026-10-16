"""Per-scope discovery (contract C2/C3/C7): turns GitHub listings into ``Discovered`` segments.

``discover_scope`` returns a :class:`ScopeRun` for one scope. It performs no writes; the
orchestrator persists ``ScopeRun.result.state`` after the last handoff and each
``Discovered.position`` in the handoff's own transaction. Typed fetch errors propagate
(nothing is swallowed); a partial pass never reports ``inventory_complete``.

Resume rule (issues / comment feeds): items are visited in ascending ``(updated_at, id)``
order and an item is skipped only when it sorts at or before the resume key, so replays
after a crash re-yield at most items the receiver already de-duplicates.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from sourcemind.connectors.github.client import GitHubClient
from sourcemind.connectors.github.contracts import (
    SCOPES,
    Discovered,
    DiscoveryLimits,
    GitHubFetchError,
    GoneError,
    NotFoundError,
    ScopeResult,
    ScopeState,
)
from sourcemind.connectors.github.mapper import (
    ConnectorDocument,
    GitHubMapper,
    _parse_dt,
    skip_bots,
    skip_empty_body,
)

WINDOW_OVERLAP = timedelta(minutes=5)
COMMIT_FIRST_IMPORT_MAX = 200
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id_key(value: Any) -> tuple[int, int | str]:
    """Total order on ids: numeric ids numerically, anything else as text."""
    text = str(value)
    return (0, int(text)) if text.isdigit() else (1, text)


class _Run:
    """Common machinery: window, budget, filter counters, result assembly."""

    def __init__(
        self,
        client: GitHubClient,
        *,
        owner: str,
        repo: str,
        scope: str,
        state: ScopeState,
        now: datetime,
        limits: DiscoveryLimits,
        etag_cache: dict[str, str] | None,
        pr_max_pages: int | None,
    ) -> None:
        self.client = client
        self.owner = owner
        self.repo = repo
        self.scope = scope
        self.state = state
        self.now = now
        self.limits = limits
        self.etags = etag_cache if etag_cache is not None else {}
        self.pr_max_pages = pr_max_pages
        self.result: ScopeResult | None = None
        self.history_rewritten = False
        self.filtered: Counter[str] = Counter()
        self.truncated: Counter[str] = Counter()
        self.seen: set[str] = set()
        self.inventory_complete = True
        self._complete = False
        self._new_head: str | None = state.head_sha

        floor = now - timedelta(days=limits.import_days)
        self.continuing = state.window_high is not None and not state.listing_complete
        if self.continuing:
            self.high = state.window_high  # type: ignore[assignment]
            self.low = state.window_low or floor
        else:
            self.high = now
            self.low = (state.window_high - WINDOW_OVERLAP) if state.window_high else floor
        self.low = max(self.low, floor) if state.window_high is None else self.low
        self.backfill = state.window_high is None or (
            self.continuing and state.phase == "backfill"
        )
        self.resume_ts = state.resume_ts if self.continuing else None
        self.resume_id = state.resume_id if self.continuing else None
        # Cumulative backfill consumption (items read since the import began). A restart
        # must continue from it, otherwise every run would get a fresh per-type allowance
        # and the declared initial-import bound would grow with the number of runs.
        self.consumed_base = (
            state.consumed if self.continuing and state.phase == "backfill" else 0
        )
        self._consumed_mark = self.consumed_base  # as of the last item actually handed off
        self._pending_mark = self.consumed_base  # set by _produce just before it yields

    # -- helpers -------------------------------------------------------------
    def verbatim(self) -> int:
        return self.limits.short_segment_verbatim_max_chars

    def reject(self, user: dict[str, Any] | None, body: str | None) -> str | None:
        if self.limits.skip_bots and (reason := skip_bots(user)):
            return reason
        if self.limits.skip_empty_body and (reason := skip_empty_body(body)):
            return reason
        return None

    def make(
        self,
        doc: ConnectorDocument,
        position: tuple[datetime | None, str],
        user: dict[str, Any] | None,
        body: str | None,
    ) -> Discovered | None:
        """Record the segment as seen and return it, or count why it is filtered."""
        self.seen.add(doc.source_id)
        reason = self.reject(user, body)
        if reason:
            self.filtered[reason] += 1
            return None
        return Discovered(document=doc, position=position)

    def before_resume(self, ts: datetime | None, item_id: Any) -> bool:
        if self.resume_ts is None:
            return False
        ts = ts or _EPOCH
        if ts != self.resume_ts:
            return ts < self.resume_ts
        return self.resume_id is not None and _id_key(item_id) <= _id_key(self.resume_id)

    # -- driver --------------------------------------------------------------
    async def items(self) -> AsyncIterator[Discovered]:
        emitted = 0
        last: tuple[datetime | None, str] | None = None
        async for item in self._produce():
            if emitted >= self.limits.max_items:
                self._finish(last, complete=False)
                return
            emitted += 1
            last = item.position
            self._consumed_mark = self._pending_mark
            yield item
        self._finish(last, complete=self._complete)

    def _finish(self, last: tuple[datetime | None, str] | None, *, complete: bool) -> None:
        if complete:
            state = ScopeState(
                phase="incremental",
                window_low=self.low,
                window_high=self.high,
                resume_ts=None,
                resume_id=None,
                listing_complete=True,
                head_sha=self._new_head,
            )
        else:
            resume_ts = last[0] if last else self.resume_ts
            resume_id = last[1] if last else self.resume_id
            state = ScopeState(
                phase="backfill" if self.backfill else "incremental",
                window_low=self.low,
                window_high=self.high,
                resume_ts=resume_ts,
                resume_id=resume_id,
                listing_complete=False,
                head_sha=self._new_head if self.scope != "commits" else self.state.head_sha,
                consumed=self._consumed_mark,
            )
        self.result = ScopeResult(
            state=state,
            inventory_complete=complete and self.inventory_complete,
            filtered=dict(self.filtered),
            truncated=dict(self.truncated),
            seen_source_ids=frozenset(self.seen),
        )

    def _produce(self) -> AsyncIterator[Discovered]:  # pragma: no cover - abstract
        raise NotImplementedError


# ── Repo-wide ascending feeds ────────────────────────────────────────────────


class _FeedRun(_Run):
    """issues / issue_comments / review_comments: ascending by update, since=low."""

    def _paginator(self, since: str) -> Any:
        c, o, r = self.client, self.owner, self.repo
        if self.scope == "issues":
            return c.list_issues(
                o, r, state="all", since=since, sort="updated", direction="asc"
            )
        if self.scope == "issue_comments":
            return c.list_issue_comments(o, r, since=since, sort="updated", direction="asc")
        return c.list_review_comments(o, r, since=since, sort="updated", direction="asc")

    def _map(self, raw: dict[str, Any]) -> tuple[ConnectorDocument, Any, Any, str] | None:
        v = self.verbatim()
        if self.scope == "issues":
            if "pull_request" in raw:  # PRs are served by the pull_requests scope (T-D7)
                return None
            doc = GitHubMapper.from_issue(self.owner, self.repo, raw, verbatim_max_chars=v)
        elif self.scope == "issue_comments":
            doc = GitHubMapper.from_issue_comment(
                self.owner, self.repo, raw, verbatim_max_chars=v
            )
        else:
            doc = GitHubMapper.from_pull_review_comment(
                self.owner, self.repo, raw, verbatim_max_chars=v
            )
        key = raw.get("id") if raw.get("id") is not None else raw.get("number")
        return doc, raw.get("user"), raw.get("body"), str(key)

    async def _produce(self) -> AsyncIterator[Discovered]:
        since = self.resume_ts if self.resume_ts is not None else self.low
        pager = self._paginator(_iso(since))
        consumed = self.consumed_base
        async for raw in pager:
            ts = _parse_dt(raw.get("updated_at") or raw.get("created_at"))
            if ts is not None and ts > self.high:
                # Belongs to the next window; ascending order means all later ones do too.
                self._complete = True
                return
            mapped = self._map(raw)
            if mapped is None:
                continue
            doc, user, body, item_id = mapped
            if self.before_resume(ts, item_id):
                continue
            consumed += 1
            if self.backfill and consumed > self.limits.max_items_per_type:
                self.truncated["items_per_type"] += 1
                self.inventory_complete = False
                self._complete = True
                return
            found = self.make(doc, (ts, item_id), user, body)
            if found is not None:
                self._pending_mark = consumed
                yield found
        if pager.exhausted is False:
            self.inventory_complete = False
            return  # page cap: not complete, resume continues from the last key
        self._complete = True


# ── Pull requests ────────────────────────────────────────────────────────────


class _PullBase(_Run):
    async def _pr_segments(
        self, pr: dict[str, Any], *, include_body: bool, comments: bool
    ) -> list[tuple[ConnectorDocument, Any, Any]]:
        """Authored segments of one PR: body, reviews, optionally conversation comments."""
        number = pr["number"]
        title = pr.get("title", "") or ""
        v = self.verbatim()
        out: list[tuple[ConnectorDocument, Any, Any]] = []
        if include_body:
            doc = GitHubMapper.from_pull_request(self.owner, self.repo, pr, verbatim_max_chars=v)
            out.append((doc, pr.get("user"), pr.get("body")))
        key = f"{self.owner}/{self.repo}/pull/{number}/reviews"
        pager = self.client.list_pull_reviews(
            self.owner, self.repo, number, etag=self.etags.get(key)
        )
        reviews = [r async for r in pager]
        if pager.exhausted is False:
            self.inventory_complete = False
        if pager.etag:
            self.etags[key] = pager.etag
        for rv in reviews:
            if rv.get("state") == "PENDING":
                continue
            doc = GitHubMapper.from_pull_review(
                self.owner, self.repo, number, rv, pr_title=title, verbatim_max_chars=v
            )
            out.append((doc, rv.get("user"), rv.get("body")))
        if comments:
            ckey = f"{self.owner}/{self.repo}/pull/{number}/comments"
            cpager = self.client.list_pull_issue_comments(
                self.owner, self.repo, number, etag=self.etags.get(ckey)
            )
            cs = [c async for c in cpager]
            if cpager.exhausted is False:
                self.inventory_complete = False
            if cpager.etag:
                self.etags[ckey] = cpager.etag
            for c in cs:
                doc = GitHubMapper.from_issue_comment(
                    self.owner,
                    self.repo,
                    c,
                    number=number,
                    issue_title=title,
                    verbatim_max_chars=v,
                )
                out.append((doc, c.get("user"), c.get("body")))
        cap = self.limits.max_segments_per_pr
        if len(out) > cap:
            self.truncated["segments_over_pr_cap"] += len(out) - cap
            out = out[:cap]
        return out

    def _emit_pr(
        self,
        segments: list[tuple[ConnectorDocument, Any, Any]],
        before: tuple[datetime | None, str],
        own: tuple[datetime | None, str],
    ) -> list[Discovered]:
        """Non-final segments carry the previous PR's key; only the last carries this
        PR's, so a crash mid-PR resumes by replaying this PR (receiver dedups)."""
        made: list[Discovered] = []
        for doc, user, body in segments:
            found = self.make(doc, before, user, body)
            if found is not None:
                made.append(found)
        if made:
            made[-1] = Discovered(document=made[-1].document, position=own)
        return made


class _PullRequestsRun(_PullBase):
    async def _produce(self) -> AsyncIterator[Discovered]:
        floor = self.resume_ts if self.resume_ts is not None else self.low
        pager = self.client.list_pulls(
            self.owner,
            self.repo,
            state="all",
            sort="updated",
            direction="desc",
            max_pages=self.pr_max_pages,
        )
        touched: list[tuple[datetime, dict[str, Any]]] = []
        async for pr in pager:
            ts = _parse_dt(pr.get("updated_at") or pr.get("created_at")) or _EPOCH
            if ts > self.high:
                continue  # metadata-only prefix: next window's work
            if ts < floor:
                self._complete = True
                break
            touched.append((ts, pr))
        else:
            if pager.exhausted is False:
                # Prefix longer than the page cap: reported, never silently complete.
                self.inventory_complete = False
                self.truncated["pull_list_page_cap"] += 1
                return
            self._complete = True
        touched.sort(key=lambda t: (t[0], _id_key(t[1]["number"])))
        prev: tuple[datetime | None, str] = (self.resume_ts, self.resume_id or "")
        if self.resume_ts is None:
            prev = (floor, "0")
        consumed = self.consumed_base
        for ts, pr in touched:
            if self.before_resume(ts, pr["number"]):
                continue
            consumed += 1
            if self.backfill and consumed > self.limits.max_items_per_type:
                # Same declared per-type initial-import bound as the feeds.
                self.truncated["items_per_type"] += 1
                self.inventory_complete = False
                self._complete = True
                return
            own = (ts, str(pr["number"]))
            segments = await self._pr_segments(pr, include_body=True, comments=False)
            for found in self._emit_pr(segments, prev, own):
                if found.position == own:  # the PR's last segment: it is fully handed off
                    self._pending_mark = consumed
                yield found
            prev = own
        if not self._complete:
            return


class _ReviewReconcileRun(_PullBase):
    """Bounded sweep of every PR inside the import scope; resume key = PR number."""

    async def _produce(self) -> AsyncIterator[Discovered]:
        floor = self.now - timedelta(days=self.limits.import_days)
        pager = self.client.list_pulls(
            self.owner,
            self.repo,
            state="all",
            sort="updated",
            direction="desc",
            max_pages=self.pr_max_pages,
        )
        in_scope: list[dict[str, Any]] = []
        async for pr in pager:
            ts = _parse_dt(pr.get("updated_at") or pr.get("created_at")) or _EPOCH
            if ts < floor:
                break
            in_scope.append(pr)
        else:
            if pager.exhausted is False:
                self.inventory_complete = False
                self.truncated["pull_list_page_cap"] += 1
        cap = self.limits.max_items_per_type
        if len(in_scope) > cap:
            self.truncated["out_of_scope_prs"] += len(in_scope) - cap
            self.inventory_complete = False  # reported out of scope; never deleted
            in_scope = in_scope[:cap]
        in_scope.sort(key=lambda p: p["number"])

        resume_number = int(self.resume_id) if self.resume_id and self.resume_id.isdigit() else 0
        todo = [p for p in in_scope if p["number"] > resume_number]
        batch = todo[: self.limits.reconcile_batch]
        prev_key: tuple[datetime | None, str] = (None, str(resume_number))
        for pr in batch:
            own = (None, str(pr["number"]))
            segments = await self._pr_segments(pr, include_body=False, comments=True)
            for found in self._emit_pr(segments, prev_key, own):
                yield found
            prev_key = own
        if len(batch) == len(todo):
            self._complete = True  # sweep finished; the next sweep restarts at the lowest PR
        else:
            self._reconcile_last = prev_key

    def _finish(self, last: tuple[datetime | None, str] | None, *, complete: bool) -> None:
        super()._finish(last, complete=complete)
        assert self.result is not None
        if not complete:
            # The key is the last PR READ (even if it produced no segment).
            key = getattr(self, "_reconcile_last", last)
            if key is not None:
                self.result.state = ScopeState(
                    phase=self.result.state.phase,
                    window_low=self.result.state.window_low,
                    window_high=self.result.state.window_high,
                    resume_ts=None,
                    resume_id=key[1],
                    listing_complete=False,
                    head_sha=self.result.state.head_sha,
                )


# ── Commits ──────────────────────────────────────────────────────────────────


class _CommitsRun(_Run):
    _capped_by_policy = False
    _partial_head: str | None = None

    async def _commit_list(self, head: str) -> list[dict[str, Any]]:
        """Oldest-first commits that are new relative to ``state.head_sha``."""
        base = self.state.head_sha
        floor = self.now - timedelta(days=self.limits.import_days)
        if base is None:
            return await self._walk(head, floor)
        if base == head:
            return []
        try:
            cmp = await self.client.compare(self.owner, self.repo, base, head)
        except (NotFoundError, GoneError):
            cmp = None  # previous head no longer exists: a rewrite
        if cmp is None or cmp.status in ("diverged", "behind"):
            self.history_rewritten = True
            return await self._walk(head, floor)
        commits = list(cmp.commits)
        if not cmp.exhausted:
            self.inventory_complete = False
            if commits:
                # More commits than one comparison returns: cover what came back and let
                # the next sync compare from the last one, so the backlog drains.
                self._partial_head = commits[-1]["sha"]
        return commits

    async def _walk(self, head: str, floor: datetime) -> list[dict[str, Any]]:
        cap = min(COMMIT_FIRST_IMPORT_MAX, self.limits.max_items_per_type)
        pager = self.client.list_commits(
            self.owner, self.repo, sha=head, since=_iso(floor), until=_iso(self.high)
        )
        newest_first: list[dict[str, Any]] = []
        async for c in pager:
            if len(newest_first) >= cap:
                self.truncated["commits_over_cap"] += 1
                self.inventory_complete = False
                # The declared first-import bound was reached: the older commits are out
                # of scope for good, so the head still advances and later syncs are
                # incremental from it (otherwise the scope would re-read the same window).
                self._capped_by_policy = True
                break
            newest_first.append(c)
        else:
            if pager.exhausted is False:
                self.inventory_complete = False
        newest_first.reverse()
        return newest_first

    async def _produce(self) -> AsyncIterator[Discovered]:
        head = await self.client.get_head_sha(self.owner, self.repo)
        commits = await self._commit_list(head)
        shas = [c["sha"] for c in commits]
        start = 0
        if self.resume_id and self.resume_id in shas:
            start = shas.index(self.resume_id) + 1
        seen_in_run: set[str] = set()
        v = self.verbatim()
        for raw in commits[start:]:
            if raw["sha"] in seen_in_run:  # SHA identity dedup
                continue
            seen_in_run.add(raw["sha"])
            doc = GitHubMapper.from_commit(self.owner, self.repo, raw, verbatim_max_chars=v)
            ts = doc.source_timestamp
            msg = (raw.get("commit") or {}).get("message")
            found = self.make(doc, (ts, raw["sha"]), raw.get("author"), msg)
            if found is not None:
                yield found
        if self.inventory_complete or self._capped_by_policy:
            self._new_head = head
            self._complete = True
        elif self._partial_head is not None:
            self._new_head = self._partial_head
            self._complete = True


def discover_scope(
    client: GitHubClient,
    *,
    owner: str,
    repo: str,
    scope: str,
    state: ScopeState,
    now: datetime,
    limits: DiscoveryLimits,
    etag_cache: dict[str, str] | None = None,
    pr_max_pages: int | None = None,
) -> _Run:
    """Build the :class:`ScopeRun` for *scope* (see module docstring)."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    kwargs = {
        "owner": owner,
        "repo": repo,
        "scope": scope,
        "state": state,
        "now": now,
        "limits": limits,
        "etag_cache": etag_cache,
        "pr_max_pages": pr_max_pages,
    }
    if scope in ("issues", "issue_comments", "review_comments"):
        return _FeedRun(client, **kwargs)
    if scope == "pull_requests":
        return _PullRequestsRun(client, **kwargs)
    if scope == "review_reconcile":
        return _ReviewReconcileRun(client, **kwargs)
    return _CommitsRun(client, **kwargs)


__all__ = ["GitHubFetchError", "discover_scope"]
