"""Discovery per scope (C2/C3/C7) against the in-memory fake GitHub (SIMULATED)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from sourcemind.connectors.github.contracts import (
    DiscoveryLimits,
    RateLimitedError,
    ScopeState,
)
from sourcemind.connectors.github.discovery import discover_scope
from tests.fakes.fake_github import FakeGitHub, make_client

REPO = "acme/repo"


@pytest.fixture
def fake() -> FakeGitHub:
    f = FakeGitHub()
    f.add_repo(REPO)
    return f


async def run(fake, scope, state=None, *, now=None, limits=None, **kw):
    client = make_client(fake)
    r = discover_scope(
        client,
        owner="acme",
        repo="repo",
        scope=scope,
        state=state or ScopeState(),
        now=now or fake.now,
        limits=limits or DiscoveryLimits(),
        **kw,
    )
    items = [i async for i in r.items()]
    assert r.result is not None
    return items, r.result, r


def ids(items):
    return [i.document.source_id for i in items]


def old(fake, days):
    return fake.now - timedelta(days=days)


# ── T-D1 .. T-D3 ─────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_td1_comment_on_year_old_pr_is_discovered(fake):
    n = fake.add_pull(REPO, "op", "ancient", "b", at=old(fake, 365))
    fake.tick(10)
    cid = fake.add_issue_comment(REPO, n, "late", "still relevant")
    items, result, _ = await run(fake, "issue_comments")
    assert ids(items) == [f"acme/repo/issues/{n}/comment/{cid}"]
    assert items[0].document.content == "still relevant"
    assert result.inventory_complete and result.state.listing_complete


@pytest.mark.unit
async def test_td2_year_old_issue_edited_is_discovered(fake):
    n = fake.add_issue(REPO, "op", "old", "v1", at=old(fake, 365))
    items, _, _ = await run(fake, "issues")
    assert items == []  # outside the window, untouched
    fake.tick(5)
    fake.edit_item(REPO, n, body="v2 edited")
    items, _, _ = await run(fake, "issues")
    assert ids(items) == [f"acme/repo/issues/{n}"]
    assert items[0].document.content == "v2 edited"


@pytest.mark.unit
async def test_td3_review_on_old_pr_found_via_touched_pr(fake):
    n = fake.add_pull(REPO, "op", "old pr", "body", at=old(fake, 300))
    fake.tick(5)
    rid = fake.add_review(REPO, n, "rev", "needs work", bump_pr=True)
    items, _, _ = await run(fake, "pull_requests")
    assert f"acme/repo/pull/{n}/review/{rid}" in ids(items)
    review = next(i for i in items if i.document.source_type == "pull_review")
    assert review.document.metadata["author"]["login"] == "rev"


# ── T-D4 / T-D5 commits ──────────────────────────────────────────────────────


@pytest.mark.unit
async def test_td4_merge_with_old_commit_dates_found_via_compare(fake):
    base = fake.add_commit(REPO, "base", "a")
    items, result, _ = await run(fake, "commits")
    assert ids(items) == [f"acme/repo/commits/{base}"]
    assert result.state.head_sha == base

    side1 = fake.add_commit(REPO, "old feature 1", "bob", date=old(fake, 40), advance=False)
    side2 = fake.add_commit(
        REPO, "old feature 2", "bob", date=old(fake, 39), parents=[side1], advance=False
    )
    merge = fake.merge(REPO, side2)
    items, result, _ = await run(fake, "commits", result.state, now=fake.now)
    assert set(ids(items)) == {f"acme/repo/commits/{s}" for s in (side1, side2, merge)}
    assert result.state.head_sha == merge and result.inventory_complete


@pytest.mark.unit
async def test_td5_force_push_no_skip_and_no_duplicate(fake):
    c1 = fake.add_commit(REPO, "c1", "a")
    c2 = fake.add_commit(REPO, "c2", "a")
    c3 = fake.add_commit(REPO, "c3", "a")
    first, res1, _ = await run(fake, "commits")
    assert len(first) == 3
    # Rewrite: drop c3, replace with c3b and add c4 (c1, c2 survive).
    c3b = fake.add_commit(REPO, "c3 rewritten", "a", parents=[c2], advance=False)
    c4 = fake.add_commit(REPO, "c4", "a", parents=[c3b], advance=False)
    fake.force_push(REPO, c4)
    second, res2, run2 = await run(fake, "commits", res1.state)
    got = ids(second)
    assert len(got) == len(set(got))  # SHA identity: never twice in a run
    assert {c3b, c4} <= {g.rsplit("/", 1)[1] for g in got}
    union = set(ids(first)) | set(got)
    assert union == {f"acme/repo/commits/{s}" for s in (c1, c2, c3, c3b, c4)}
    assert run2.history_rewritten is True
    assert res2.state.head_sha == c4


@pytest.mark.unit
async def test_commits_unchanged_head_yields_nothing(fake):
    fake.add_commit(REPO, "c1", "a")
    _, res, _ = await run(fake, "commits")
    items, res2, _ = await run(fake, "commits", res.state)
    assert items == [] and res2.inventory_complete and res2.state.head_sha == res.state.head_sha


@pytest.mark.unit
async def test_commit_first_import_is_capped_and_reported(fake):
    for i in range(6):
        fake.add_commit(REPO, f"c{i}", "a")
    items, res, _ = await run(fake, "commits", limits=DiscoveryLimits(max_items_per_type=4))
    assert len(items) == 4
    assert res.truncated["commits_over_cap"] == 1
    assert res.inventory_complete is False
    # The declared first-import bound was reached, so the head advances: otherwise the next
    # sync re-reads the same capped window and never sees newer commits.
    assert res.state.head_sha is not None and res.state.listing_complete


@pytest.mark.unit
async def test_commit_cap_does_not_stall_later_syncs(fake):
    for i in range(6):
        fake.add_commit(REPO, f"c{i}", "a")
    limits = DiscoveryLimits(max_items_per_type=4)
    first, res, _ = await run(fake, "commits", limits=limits)
    newer = fake.add_commit(REPO, "c-new", "a")
    second, res2, _ = await run(fake, "commits", res.state, limits=limits)
    assert [i.document.source_id for i in second] == [f"acme/repo/commits/{newer}"]
    assert len(first) == 4 and res2.state.head_sha == newer


@pytest.mark.unit
async def test_co_authored_commit_is_shared_and_bot_commit_filtered(fake):
    fake.add_commit(REPO, "x\n\nCo-authored-by: Z <z@x>", "a")
    fake.user("dependabot", type_="Bot")
    fake.add_commit(REPO, "bump", "dependabot")
    items, res, _ = await run(fake, "commits")
    assert len(items) == 1 and items[0].document.authorship == "shared"
    assert res.filtered == {"skip_bots": 1}


# ── T-D6 / T-D7 ──────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_td6_item_updated_during_sync_lands_in_next_window(fake):
    n1 = fake.add_issue(REPO, "a", "first", "b1")
    fake.tick(5)
    start = fake.now
    fake.tick(30)
    n2 = fake.add_issue(REPO, "a", "after window start", "b2")  # updated_at > window_high
    items, res, _ = await run(fake, "issues", now=start)
    assert ids(items) == [f"acme/repo/issues/{n1}"]
    assert res.state.window_high == start and res.state.listing_complete
    items2, res2, _ = await run(fake, "issues", res.state, now=start + timedelta(minutes=2))
    assert f"acme/repo/issues/{n2}" in ids(items2)
    assert res2.state.window_low == start - timedelta(minutes=5)  # 5 minute overlap


@pytest.mark.unit
async def test_td7_pr_and_issue_feeds_are_separate(fake):
    i = fake.add_issue(REPO, "a", "an issue", "ib")
    p = fake.add_pull(REPO, "b", "a pr", "pb")
    issues, _, _ = await run(fake, "issues")
    prs, _, _ = await run(fake, "pull_requests")
    assert ids(issues) == [f"acme/repo/issues/{i}"]
    assert ids(prs) == [f"acme/repo/pull/{p}"]
    assert all(x.document.source_type == "issue" for x in issues)
    assert all(x.document.source_type == "pull_request" for x in prs)


# ── T-V1 .. T-V3 reconciliation sweep ────────────────────────────────────────


def _closed_prs(fake, count, days_ago=10):
    nums = []
    for k in range(count):
        at = old(fake, days_ago - k * 0.01)
        n = fake.add_pull(REPO, "op", f"pr{k}", "b", state="closed", at=at)
        nums.append(n)
    return nums


@pytest.mark.unit
async def test_tv1_review_on_closed_pr_without_timestamp_change_found_by_sweep(fake):
    (n,) = _closed_prs(fake, 1)
    _, done, _ = await run(fake, "pull_requests")  # PR body already ingested
    before = fake._repo(REPO).items[n]["updated_at"]
    fake.tick(100)
    rid = fake.add_review(REPO, n, "late", "found you", bump_pr=False)
    assert fake._repo(REPO).items[n]["updated_at"] == before
    touched, _, _ = await run(fake, "pull_requests", done.state, now=fake.now)
    assert touched == []  # PR timestamp unchanged: window listing cannot see it
    items, res, _ = await run(fake, "review_reconcile")
    assert ids(items) == [f"acme/repo/pull/{n}/review/{rid}"]
    assert res.state.listing_complete and res.inventory_complete


@pytest.mark.unit
async def test_tv2_sweep_resumes_after_interruption_without_rereading(fake):
    nums = _closed_prs(fake, 5)
    for n in nums:
        fake.add_review(REPO, n, "r", f"review {n}", bump_pr=False)
    limits = DiscoveryLimits(reconcile_batch=2)
    seen: list[str] = []
    state = ScopeState()
    for expected_done in (False, False, True):
        fake.log.clear()
        items, res, _ = await run(fake, "review_reconcile", state, limits=limits)
        seen += ids(items)
        assert res.state.listing_complete is expected_done
        state = res.state
        read = {p for _, p in fake.log if p.endswith("/reviews")}
        already = {f"/repos/{REPO}/pulls/{n}/reviews" for n in nums[: len(seen) - len(items)]}
        assert not (read & already), "finished PRs were re-read"
    assert len(seen) == 5 and len(set(seen)) == 5


@pytest.mark.unit
async def test_tv3_prs_beyond_cap_reported_out_of_scope_not_deleted(fake):
    nums = _closed_prs(fake, 5)
    for n in nums:
        fake.add_review(REPO, n, "r", f"review {n}")
    items, res, _ = await run(
        fake, "review_reconcile", limits=DiscoveryLimits(max_items_per_type=3, reconcile_batch=25)
    )
    assert res.truncated["out_of_scope_prs"] == 2
    assert res.inventory_complete is False  # a capped inventory never supports deletion (C7)
    assert len(items) == 3


@pytest.mark.unit
async def test_sweep_uses_etag_cache_to_skip_unchanged_prs(fake):
    (n,) = _closed_prs(fake, 1)
    fake.add_review(REPO, n, "r", "hello")
    cache: dict[str, str] = {}
    first, _, _ = await run(fake, "review_reconcile", etag_cache=cache)
    assert len(first) == 1 and cache
    second, res, _ = await run(fake, "review_reconcile", etag_cache=cache)
    assert second == [] and res.state.listing_complete


# ── Filters, caps, limits ────────────────────────────────────────────────────


@pytest.mark.unit
async def test_filters_are_counted_per_reason_and_still_marked_seen(fake):
    n = fake.add_pull(REPO, "op", "p", "body")
    fake.user("botty", type_="Bot")
    fake.add_review(REPO, n, "botty", "auto", bump_pr=True)
    fake.add_review(REPO, n, "human", "   ", state="APPROVED", bump_pr=True)
    fake.add_review(REPO, n, "human", "k", bump_pr=True)  # one char: no min-length filter
    items, res, _ = await run(fake, "pull_requests")
    assert [i.document.content for i in items] == ["body", "k"]
    assert res.filtered == {"skip_bots": 1, "skip_empty_body": 1}
    assert len(res.seen_source_ids) == 4


@pytest.mark.unit
async def test_filters_can_be_switched_off(fake):
    n = fake.add_pull(REPO, "op", "p", "body")
    fake.add_review(REPO, n, "human", "", state="APPROVED", bump_pr=True)
    items, res, _ = await run(
        fake, "pull_requests", limits=DiscoveryLimits(skip_empty_body=False)
    )
    assert len(items) == 2 and res.filtered == {}


@pytest.mark.unit
async def test_segments_per_pr_cap_counts_truncation(fake):
    n = fake.add_pull(REPO, "op", "p", "body")
    for k in range(5):
        fake.add_review(REPO, n, f"r{k}", f"rv{k}", bump_pr=True)
    items, res, _ = await run(fake, "pull_requests", limits=DiscoveryLimits(max_segments_per_pr=3))
    assert len(items) == 3 and res.truncated["segments_over_pr_cap"] == 3


@pytest.mark.unit
async def test_max_items_continuation_covers_everything_once(fake):
    made = [fake.add_issue(REPO, "a", f"i{k}", f"b{k}") for k in range(7)]
    limits = DiscoveryLimits(max_items=3)
    state = ScopeState()
    collected: list[str] = []
    for _ in range(5):
        items, res, _ = await run(fake, "issues", state, limits=limits)
        collected += ids(items)
        state = res.state
        if state.listing_complete:
            break
        assert not res.inventory_complete
        assert state.resume_id is not None
    assert collected == [f"acme/repo/issues/{n}" for n in made]


@pytest.mark.unit
async def test_pr_position_is_previous_key_until_last_segment(fake):
    n = fake.add_pull(REPO, "op", "p", "body")
    fake.add_review(REPO, n, "r", "rv", bump_pr=True)
    items, _, _ = await run(fake, "pull_requests")
    assert len(items) == 2
    assert items[0].position != items[1].position
    assert items[1].position[1] == str(n)


@pytest.mark.unit
async def test_review_comments_scope_and_pr_conversation_comment_numbering(fake):
    n = fake.add_pull(REPO, "op", "p", "b")
    rc = fake.add_review_comment(REPO, n, "r", "inline note")
    items, _, _ = await run(fake, "review_comments")
    assert ids(items) == [f"acme/repo/pull/{n}/review_comment/{rc}"]


@pytest.mark.unit
async def test_edited_comment_is_rediscovered_with_new_revision(fake):
    n = fake.add_issue(REPO, "a", "i")
    cid = fake.add_issue_comment(REPO, n, "c", "v1")
    first, res, _ = await run(fake, "issue_comments")
    fake.tick(10)
    fake.edit_comment(REPO, cid, "v2")
    second, _, _ = await run(fake, "issue_comments", res.state, now=fake.now)
    assert ids(second) == ids(first)
    assert second[0].document.source_revision != first[0].document.source_revision


# ── Errors propagate; nothing partial is a success ───────────────────────────


@pytest.mark.unit
async def test_rate_limit_propagates_and_leaves_no_result(fake):
    fake.add_issue(REPO, "a", "i")
    fake.inject("/issues", "primary", times=None)
    client = make_client(fake)
    r = discover_scope(
        client, owner="acme", repo="repo", scope="issues", state=ScopeState(),
        now=fake.now, limits=DiscoveryLimits(),
    )
    with pytest.raises(RateLimitedError):
        _ = [i async for i in r.items()]
    assert r.result is None


@pytest.mark.unit
async def test_page_cap_reports_incomplete_not_complete(fake):
    for k in range(101):
        fake.add_issue(REPO, "a", f"i{k}", "b")
    client = make_client(fake, max_pages=1)
    r = discover_scope(
        client, owner="acme", repo="repo", scope="issues", state=ScopeState(),
        now=fake.now, limits=DiscoveryLimits(),
    )
    _ = [i async for i in r.items()]
    assert r.result.inventory_complete is False
    assert r.result.state.listing_complete is False


@pytest.mark.unit
async def test_unknown_scope_rejected(fake):
    with pytest.raises(ValueError):
        discover_scope(
            make_client(fake), owner="a", repo="b", scope="discussions",
            state=ScopeState(), now=fake.now, limits=DiscoveryLimits(),
        )


@pytest.mark.unit
async def test_discussions_graphql_is_never_called(fake):
    n = fake.add_pull(REPO, "op", "p", "b")
    fake.add_review(REPO, n, "r", "x", bump_pr=True)
    fake.add_issue(REPO, "a", "i")
    fake.add_commit(REPO, "c", "a")
    for scope in ("issues", "issue_comments", "review_comments", "pull_requests",
                  "review_reconcile", "commits"):
        await run(fake, scope)
    assert not any("graphql" in p for _, p in fake.log)


@pytest.mark.unit
@pytest.mark.parametrize("scope", ["issues", "pull_requests"])
async def test_restarts_cannot_grow_the_initial_import_bound(fake, scope):
    """Per-run budget 2, per-type bound 5, 12 items in the window: however many runs it
    takes, the cumulative import stops at the declared bound (restarts are not new
    allowances)."""
    for i in range(12):
        if scope == "issues":
            fake.add_issue(REPO, "op", f"i{i}", f"body {i}")
        else:
            fake.add_pull(REPO, "op", f"p{i}", f"body {i}")
        fake.tick(1)
    limits = DiscoveryLimits(max_items=2, max_items_per_type=5)
    state = ScopeState()
    imported: list[str] = []
    for _ in range(12):
        items, res, _ = await run(fake, scope, state, limits=limits)
        imported += ids(items)
        state = res.state
        if res.state.listing_complete:
            break
    assert res.state.listing_complete, "the capped import must finish"
    assert len(imported) == len(set(imported)) == 5
    assert res.truncated["items_per_type"] >= 1
