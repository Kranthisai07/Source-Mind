"""Edit distance behind attribution signal S1 (35% weight).

It now comes from rapidfuzz (MIT) instead of python-Levenshtein (GPL, D-054). The swap must
not change a single score, so the expected values are the textbook Levenshtein distances,
checked here and, when python-Levenshtein is installed in the environment, against it on a
deterministic set of unicode strings.
"""

from __future__ import annotations

import random

import pytest

from sourcemind.services.attribution import scorer


@pytest.mark.unit
def test_signal_1_is_not_degraded():
    assert scorer.SIGNAL_1_DEGRADED is False
    assert scorer._lev_distance is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("kitten", "sitting", 3),
        ("", "", 0),
        ("abc", "", 3),
        ("", "abc", 3),
        ("same text", "same text", 0),
        ("flaw", "lawn", 2),
        ("café", "cafe", 1),
        ("東京", "京都", 2),
    ],
)
def test_distance_matches_the_textbook_values(before: str, after: str, expected: int):
    assert scorer._lev_distance(before, after) == expected


@pytest.mark.unit
def test_distance_agrees_with_python_levenshtein_when_it_is_installed():
    legacy = pytest.importorskip("Levenshtein")
    rng = random.Random(7)
    alphabet = "abcdefghij ABCDEFGHIJ\n\t.,;:(){}[]'\"_-+*/=<>#é東"
    for _ in range(500):
        first = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        second = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        assert scorer._lev_distance(first, second) == legacy.distance(first, second)
