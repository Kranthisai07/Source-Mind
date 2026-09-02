"""Query-adaptive fusion weighting (D-004).

Two mechanisms are under test and they are deliberately separated, because the
investigation that produced them found that only one carries the gain:

  1. the identifier-aware keyword arm, which lets BM25 return anything at all
     for an identifier-bearing question (measured: 294 of 300 evaluation
     questions previously returned zero keyword rows), and
  2. the fusion reweighting that then favours it.

The third thing being tested is the absence of an effect: for a query with no
identifier in it, nothing here may change behaviour.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sourcemind.services.search.hybrid import (
    _IDENT_W_KEYWORD,
    _IDENT_W_SEMANTIC,
    _extract_identifiers,
    _keyword_search,
    _rrf_merge,
)

_WS = uuid.uuid4()


def _hit(mid: str, kind: str) -> dict:
    return {"id": mid, "content": f"content {mid}", "score": 0.5, "match_type": kind}


def _session_returning(rows: list[tuple]) -> MagicMock:
    """An AsyncSession stub that records the SQL and params it was handed."""
    session = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = rows
    session.execute = AsyncMock(return_value=result)
    return session


# ─────────────────────────── identifier detection ───────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # The real shapes, taken verbatim from evaluation/data/ground_truth.json.
        ("What did commit 926fa8554175 change in facebook/react?", ["926fa8554175"]),
        ("What was the purpose of PR #36554 in facebook/react?", ["36554"]),
        ("What was reported in issue #36381 of facebook/react?", ["36381"]),
        ("What was discussed in discussion #67365 of vercel/next.js?", ["67365"]),
        # Full 40-char SHA, the other end of the length range.
        (
            "Explain 926fa8554175aaa0000bbbb1111cccc2222dddd",
            ["926fa8554175aaa0000bbbb1111cccc2222dddd"],
        ),
        # Several identifiers in one question.
        ("Did commit 142cfde89eda close #36047?", ["142cfde89eda", "36047"]),
        # Case-insensitive: hashes are sometimes pasted uppercase.
        ("commit 926FA8554175", ["926fa8554175"]),
    ],
)
def test_various_identifier_patterns(query, expected):
    assert _extract_identifiers(query) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "query",
    [
        "How do we handle backpressure in the Flight server?",
        "What is the retry policy for ingestion?",
        # Hex-letter English words. A bare [0-9a-f]{7,40} matches all three, and
        # would fire the keyword boost on ordinary prose; the digit lookahead is
        # what stops it.
        "Why was the config defaced during the migration?",
        "The effaced entries were restored",
        "Explain the deadbeef sentinel value",
        # Short hex-ish token below the 7-char floor.
        "the abc123 branch",
        # A number that is not an identifier reference.
        "we saw 500 errors in staging",
    ],
)
def test_conversational_queries_yield_no_identifiers(query):
    assert _extract_identifiers(query) == []


@pytest.mark.unit
def test_identifiers_are_deduplicated_preserving_order():
    q = "Does #36047 relate to #36047 and to commit 142cfde89eda?"
    assert _extract_identifiers(q) == ["142cfde89eda", "36047"]


# ─────────────────────────── the keyword arm ───────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_identifier_query_searches_on_the_identifier_alone():
    """The AND-conjunction is what made this arm return nothing.

    plainto_tsquery renders the real question as
    'commit' & '926fa8554175' & 'chang' & 'facebook/react', which fails whenever
    a memory lacks any one filler word - measured as zero rows for 294 of 300
    evaluation questions. With an identifier present the arm must search on the
    identifier by itself, OR-ed and unstemmed.
    """
    session = _session_returning([("m1", "Commit 926fa8554175 removed caches", 0.4)])

    out = await _keyword_search(
        session,
        "What did commit 926fa8554175 change in facebook/react?",
        _WS,
        50,
        identifiers=["926fa8554175"],
    )

    sql = str(session.execute.await_args.args[0])
    params = session.execute.await_args.args[1]
    assert "to_tsquery('simple', :query)" in sql
    assert "plainto_tsquery" not in sql, "the AND-conjunction must not be used here"
    assert params["query"] == "926fa8554175", "must query the identifier, not the prose"
    assert out == [
        {
            "id": "m1",
            "content": "Commit 926fa8554175 removed caches",
            "score": 0.4,
            "match_type": "keyword",
        }
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multiple_identifiers_are_or_ed():
    session = _session_returning([])

    await _keyword_search(
        session, "Did 142cfde89eda close #36047?", _WS, 50,
        identifiers=["142cfde89eda", "36047"],
    )

    assert session.execute.await_args.args[1]["query"] == "142cfde89eda | 36047"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_identifier_query_keeps_the_original_and_behaviour():
    """No identifier means the keyword arm is byte-for-byte what it always was."""
    session = _session_returning([])
    q = "How do we handle backpressure in the Flight server?"

    await _keyword_search(session, q, _WS, 50, identifiers=[])

    sql = str(session.execute.await_args.args[0])
    assert "plainto_tsquery('english', :query)" in sql
    assert "to_tsquery('simple'" not in sql
    assert session.execute.await_args.args[1]["query"] == q


@pytest.mark.unit
@pytest.mark.asyncio
async def test_identifiers_argument_is_optional():
    """Callers predating D-004 pass four positional args and must still work."""
    session = _session_returning([])

    await _keyword_search(session, "plain query", _WS, 50)

    assert "plainto_tsquery('english', :query)" in str(session.execute.await_args.args[0])


# ─────────────────────────── weighted fusion ───────────────────────────


@pytest.mark.unit
def test_identifier_query_detected_and_weighted_toward_bm25():
    """A keyword-only hit must outrank a semantic-only hit once weighted.

    Both sit at rank 1 of their own list, so under equal weights they tie at
    1/61 and the ordering between them is incidental. Weighting is what makes
    the exact-identifier match win deterministically.
    """
    semantic = [_hit("sem", "semantic")]
    keyword = [_hit("kw", "keyword")]

    equal = _rrf_merge(semantic, keyword)
    assert equal[0]["score"] == pytest.approx(equal[1]["score"]), (
        "precondition: equal weights leave these tied"
    )

    weighted = _rrf_merge(semantic, keyword, _IDENT_W_SEMANTIC, _IDENT_W_KEYWORD)
    assert weighted[0]["id"] == "kw"
    assert weighted[0]["score"] > weighted[1]["score"]


@pytest.mark.unit
def test_weighting_can_lift_a_keyword_hit_over_a_higher_ranked_semantic_one():
    """The case that matters: correct memory ranked 1st by BM25, 4th by dense.

    This is the attractor-commit shape - the dense arm ranked the correct commit
    as low as 33rd of 50 while an exact hash match sits at the top of the
    keyword list.
    """
    semantic = [_hit(f"s{i}", "semantic") for i in range(4)]
    keyword = [_hit("target", "keyword")]

    assert _rrf_merge(semantic, keyword)[0]["id"] == "s0"

    weighted = _rrf_merge(semantic, keyword, _IDENT_W_SEMANTIC, _IDENT_W_KEYWORD)
    assert weighted[0]["id"] == "target"


@pytest.mark.unit
def test_non_identifier_query_uses_default_weighting_unchanged():
    """Strictly additive: with no identifier, fusion output is bit-identical.

    Compared against an explicit 1.0/1.0 call rather than a hardcoded expected
    list, so this keeps holding if RRF's constants are ever retuned.
    """
    semantic = [_hit(f"s{i}", "semantic") for i in range(5)]
    keyword = [_hit("s2", "keyword"), _hit("k1", "keyword")]

    assert _rrf_merge(semantic, keyword) == _rrf_merge(semantic, keyword, 1.0, 1.0)


@pytest.mark.unit
def test_weighting_preserves_the_default_score_scale():
    """The two weights sum to 2.0, as the 1.0 + 1.0 default does.

    A memory found by both arms at the same rank therefore scores the same
    whether or not the boost fired, so `score` stays comparable across queries.
    """
    both = [_hit("x", "semantic")]

    default = _rrf_merge(both, [_hit("x", "keyword")])[0]["score"]
    boosted = _rrf_merge(
        both, [_hit("x", "keyword")], _IDENT_W_SEMANTIC, _IDENT_W_KEYWORD
    )[0]["score"]

    assert boosted == pytest.approx(default)


@pytest.mark.unit
def test_weighted_merge_keeps_match_type_and_content():
    """Reweighting must not disturb the response contract."""
    merged = _rrf_merge(
        [_hit("x", "semantic")],
        [_hit("x", "keyword")],
        _IDENT_W_SEMANTIC,
        _IDENT_W_KEYWORD,
    )
    assert merged[0]["match_type"] == "semantic+keyword"
    assert merged[0]["content"] == "content x"
