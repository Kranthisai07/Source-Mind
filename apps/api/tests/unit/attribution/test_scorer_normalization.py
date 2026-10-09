"""Score normalisation: termination, validity and the floor's feasibility (D0).

Scope is the normalisation step only (AttributionScorer._normalize). No signal
formula, weight, substantive-edit rule, approval handling or merge/handoff rule
is exercised or changed here.

Failures carry a prefix so they can be told apart:
  TIMEOUT:        a bounded child did not finish (non-terminating normalisation)
  MISSING-SYMBOL: a name the fix introduces does not exist yet
  BEHAVIOR:       the code ran and returned something that breaks a requirement

Anything that could hang runs in a child process with a per-case watchdog, so a
regression fails by timeout instead of hanging the suite. Only tiny, provably
safe inputs (three contributors or fewer) run in-process.
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sourcemind.core.exceptions import AttributionStateConflictError
from sourcemind.services.attribution import scorer as scorer_module
from sourcemind.services.attribution.scorer import AttributionScorer, ContributorScore

pytestmark = pytest.mark.unit

API_ROOT = Path(__file__).resolve().parents[3]
FLOOR = 0.02
ROUND_TOL = 5e-7  # each weight is rounded to 6 decimals, so totals may drift by n * 5e-7


# ── failure helpers ──────────────────────────────────────────────────────────


def _fail(kind: str, message: str) -> None:
    pytest.fail(f"{kind}: {message}", pytrace=False)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        _fail("BEHAVIOR", message)


def _require_symbol(name: str) -> Any:
    if not hasattr(scorer_module, name):
        _fail("MISSING-SYMBOL", f"scorer.{name} does not exist")
    return getattr(scorer_module, name)


def _valid(weights: dict[str, float], label: str) -> None:
    values = list(weights.values())
    _expect(len(values) > 0, f"{label}: no weights returned")
    _expect(all(math.isfinite(v) for v in values), f"{label}: a weight is not finite")
    _expect(all(v >= 0 for v in values), f"{label}: negative weight {min(values)!r}")
    total = sum(values)
    _expect(
        abs(total - 1.0) <= len(values) * ROUND_TOL + 1e-9,
        f"{label}: total {total!r} is not within {len(values) * ROUND_TOL:g} of 1",
    )


# ── shared helpers (also imported by the bounded child) ──────────────────────

Spec = list[tuple[str, float, bool]]  # (user id, raw score, floor-eligible)


def make(spec: Spec) -> list[ContributorScore]:
    return [ContributorScore(user_id=u, raw_score=r, has_substantive_edit=e) for u, r, e in spec]


def boundary_policy_case(spec: Spec) -> bool:
    """Exactly MAX eligible and at least one non-eligible contributor with positive credit."""
    eligible = sum(1 for _, _, e in spec if e)
    return eligible == 50 and any((not e) and r > 0 for _, r, e in spec)


def legacy_normalize(
    contributors: list[ContributorScore], cap: int = 400
) -> dict[str, float] | None:
    """Verbatim copy of AttributionScorer._normalize at main 0856a85, with a pass cap.

    Returns None when the legacy loop has not settled within ``cap`` passes.
    """
    if not contributors:
        return {}
    total = sum(c.raw_score for c in contributors)
    if total == 0:
        equal = 1.0 / len(contributors)
        return {c.user_id: equal for c in contributors}
    normalized = {c.user_id: c.raw_score / total for c in contributors}
    floor_applied = {c.user_id: c.has_substantive_edit for c in contributors}
    changed = True
    passes = 0
    while changed:
        passes += 1
        if passes > cap:
            return None
        changed = False
        floored_ids = [
            uid for uid, is_sub in floor_applied.items() if is_sub and normalized[uid] < FLOOR
        ]
        if not floored_ids:
            break
        floored_total = len(floored_ids) * FLOOR
        non_floored_total = sum(normalized[uid] for uid in normalized if uid not in floored_ids)
        if non_floored_total == 0:
            for uid in floored_ids:
                normalized[uid] = FLOOR
            break
        scale = (1.0 - floored_total) / non_floored_total
        for uid in floored_ids:
            normalized[uid] = FLOOR
            changed = True
        for uid in normalized:
            if uid not in floored_ids:
                normalized[uid] *= scale
    final_total = sum(normalized.values())
    if final_total > 0:
        normalized = {uid: w / final_total for uid, w in normalized.items()}
    return {uid: round(w, 6) for uid, w in normalized.items()}


def _random_spec(rng: random.Random, n: int, k: int, style: str) -> Spec:
    flags = [i < k for i in range(n)]
    rng.shuffle(flags)
    spec: Spec = []
    for i in range(n):
        if style == "log":
            raw = 10 ** rng.uniform(-6, 3)
        elif style == "wide":
            raw = 10 ** rng.uniform(-300, 300)
        elif style == "zeros":
            raw = 0.0 if rng.random() < 0.3 else 10 ** rng.uniform(-3, 2)
        else:  # "equal"
            raw = 1.0
        spec.append((f"u{i}", raw, flags[i]))
    return spec


def _normalize_weights(spec: Spec) -> dict[str, float]:
    return {o.user_id: o.contribution_weight for o in AttributionScorer()._normalize(make(spec))}


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    """Executed inside the bounded child."""
    kind = task["kind"]
    if kind == "normalize":
        spec = [tuple(x) for x in task["spec"]]
        try:
            out = AttributionScorer()._normalize(make(spec))  # type: ignore[arg-type]
        except BaseException as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        return {"ok": True, "weights": {o.user_id: o.contribution_weight for o in out}}
    if kind == "compare":
        return _compare(task["seed"], task["count"])
    if kind == "property":
        return _properties(task["seed"], task["count"])
    if kind == "end_to_end":
        return _end_to_end(task["contributors"])
    raise ValueError(kind)


def _compare(seed: int, count: int) -> dict[str, Any]:
    """New implementation against the frozen legacy copy, on cases with at most 50 eligible."""
    rng = random.Random(seed)
    summary: dict[str, Any] = {
        "total": 0,
        "equal_exact": 0,
        "equal_within_tolerance": 0,
        "legacy_unsettled_boundary_policy": 0,
        "legacy_unsettled_below_boundary": 0,
        "boundary_policy_settled": 0,
        "boundary_policy_settled_differs": 0,
        "unexpected_diffs": 0,
        "invalid_new": 0,
        "examples": [],
    }
    for i in range(count):
        if i % 5 == 0:  # concentrate on the boundary where the legacy loop misbehaves
            n = rng.choice([50, 51, 60, 100])
            k = rng.choice([49, 50])
        else:
            n = rng.choice([1, 2, 3, 5, 10, 20, 40, 49, 50, 60, 100])
            k = rng.randint(0, min(n, 50))
        spec = _random_spec(rng, n, min(k, n), "log")
        summary["total"] += 1
        new = _normalize_weights(spec)
        values = list(new.values())
        if not (
            all(math.isfinite(v) and v >= 0 for v in values)
            and abs(sum(values) - 1) <= len(values) * ROUND_TOL + 1e-9
        ):
            summary["invalid_new"] += 1
        boundary = boundary_policy_case(spec)
        old = legacy_normalize(make(spec))
        if old is None:
            key = (
                "legacy_unsettled_boundary_policy"
                if boundary
                else "legacy_unsettled_below_boundary"
            )
            summary[key] += 1
            continue
        gap = max(abs(old[u] - new[u]) for u in new)
        if boundary:
            summary["boundary_policy_settled"] += 1
            summary["boundary_policy_settled_differs"] += int(gap > 2e-6)
        elif gap > 2e-6:
            summary["unexpected_diffs"] += 1
            if len(summary["examples"]) < 3:
                summary["examples"].append({"n": n, "k": k})
        elif gap == 0.0:
            summary["equal_exact"] += 1
        else:
            summary["equal_within_tolerance"] += 1
    return summary


def _properties(seed: int, count: int) -> dict[str, Any]:
    rng = random.Random(seed)
    bad = {
        "nonfinite": 0,
        "negative": 0,
        "total": 0,
        "floor": 0,
        "proportional": 0,
        "permutation": 0,
        "raised": 0,
    }
    for _ in range(count):
        n = rng.choice([1, 2, 3, 4, 5, 8, 13, 21, 34, 49, 50, 51, 52, 60, 100, 150])
        k = rng.randint(0, n)
        style = rng.choice(["log", "wide", "zeros", "equal"])
        spec = _random_spec(rng, n, k, style)
        try:
            w = _normalize_weights(spec)
        except Exception:  # noqa: BLE001
            bad["raised"] += 1
            continue
        vals = list(w.values())
        if not all(math.isfinite(v) for v in vals):
            bad["nonfinite"] += 1
            continue
        if min(vals) < 0:
            bad["negative"] += 1
        if abs(sum(vals) - 1) > len(vals) * ROUND_TOL + 1e-9:
            bad["total"] += 1
        total_raw = (
            math.fsum(r / max(r2 for _, r2, _ in spec) for _, r, _ in spec)
            if any(r > 0 for _, r, _ in spec)
            else 0.0
        )
        eligible = [u for u, _, e in spec if e]
        feasible = len(eligible) <= 50 and not boundary_policy_case(spec) and total_raw > 0
        if feasible and any(w[u] < FLOOR - 2e-6 for u in eligible):
            bad["floor"] += 1
        if len(eligible) > 50 and total_raw > 0:
            peak = max(r for _, r, _ in spec)
            if any(abs(w[u] - (r / peak) / total_raw) > 2e-6 for u, r, _ in spec):
                bad["proportional"] += 1
        shuffled = spec[:]
        rng.shuffle(shuffled)
        w2 = _normalize_weights(shuffled)
        if any(abs(w[u] - w2[u]) > 2e-6 for u in w):
            bad["permutation"] += 1
    return {"count": count, "violations": bad}


class _StubSbert:
    """cos(x, y) = 1 for any pair; keeps Signal 2 out of the way."""

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):  # noqa: ARG002
        import numpy as np

        return np.array([[1.0, 0.0] for _ in texts])


def _end_to_end(contributors: int) -> dict[str, Any]:
    from sourcemind.services.attribution.scorer import EditEvent

    scorer = AttributionScorer()
    scorer._sbert = _StubSbert()
    events = [EditEvent("alice", None, "Seed text for the memory.", 1, "create")]
    text = "Seed text for the memory."
    position = 2
    for i in range(12):  # alice keeps editing, so her raw mass dominates
        new = text + f" Alice addition {i}."
        events.append(EditEvent("alice", text, new, position, "edit"))
        text, position = new, position + 1
    for j in range(contributors - 1):
        new = text + f" Note from teammate {j:03d}."
        events.append(EditEvent(f"u{j:03d}", text, new, position, "edit"))
        text, position = new, position + 1
    out = scorer.compute_scores(events)
    return {"ok": True, "weights": {o.user_id: o.contribution_weight for o in out}}


# ── bounded child runner ─────────────────────────────────────────────────────

_CHILD = """
import json, os, sys, threading
sys.path.insert(0, ".")
import tests.unit.attribution.test_scorer_normalization as t
tasks = json.loads(sys.stdin.read())
for task in tasks:
    box = {}
    def run(task=task, box=box):
        try:
            box["result"] = t.run_task(task)
        except BaseException as exc:
            box["result"] = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(task["timeout"])
    print("CASE:" + json.dumps({"id": task["id"], "result": box.get("result", {"timeout": True})}),
          flush=True)
os._exit(0)
"""

_CACHE: dict[str, dict[str, Any]] = {}


def _bounded(batch: str, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Run tasks in one child. A task that exceeds its own timeout is reported as such."""
    if batch in _CACHE:
        return _CACHE[batch]
    hard = sum(t["timeout"] for t in tasks) + 60
    try:
        proc = subprocess.run(
            [sys.executable, "-W", "ignore", "-c", _CHILD],
            input=json.dumps(tasks),
            capture_output=True,
            text=True,
            timeout=hard,
            cwd=str(API_ROOT),
            env={**os.environ, "PYTHONPATH": str(API_ROOT)},
        )
        stdout = proc.stdout
    except subprocess.TimeoutExpired as exc:
        stdout = (
            (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        )
    results: dict[str, Any] = {}
    for line in stdout.splitlines():
        if line.startswith("CASE:"):
            item = json.loads(line[5:])
            results[item["id"]] = item["result"]
    _CACHE[batch] = results
    return results


def _result(batch: str, tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    result = _bounded(batch, tasks).get(task_id)
    if result is None or result.get("timeout"):
        _fail("TIMEOUT", f"{task_id} did not finish within its time limit (non-terminating)")
    return result


# ── cases ────────────────────────────────────────────────────────────────────

NORMALIZE_TIMEOUT = 6


def _sweep_spec(n: int) -> Spec:
    return [("big", 100.0, True)] + [(f"u{i}", 0.1, True) for i in range(n - 1)]


SWEEP_COUNTS = [49, 50, 51, 52, 60, 100]

MIXED: dict[str, Spec] = {
    "30 eligible tiny + 20 ineligible": (
        [(f"e{i}", 0.1, True) for i in range(30)]
        + [(f"i{i}", 4.0, False) for i in range(10)]
        + [(f"j{i}", 12.0, False) for i in range(10)]
    ),
    "51 eligible + 10 ineligible": (
        [(f"e{i}", 0.1, True) for i in range(51)] + [(f"i{i}", 5.0, False) for i in range(10)]
    ),
    "50 eligible tiny + 1 large ineligible": (
        [(f"e{i}", 0.1, True) for i in range(50)] + [("big", 100.0, False)]
    ),
    "60 ineligible only": [(f"i{i}", 1.0 + i, False) for i in range(60)],
    "10 eligible all above the floor": [(f"e{i}", 5.0 + i, True) for i in range(10)],
    "zero-raw ineligible beside floored eligible": [
        ("big", 100.0, True),
        ("tiny", 0.1, True),
        ("idle", 0.0, False),
    ],
}

HUGE = float(sys.float_info.max)  # 1.7976931348623157e308

HUGE_CASES: dict[str, Spec] = {
    "two at 1e308 (sum overflows)": [("a", 1e308, False), ("b", 1e308, False)],
    "100 at float max": [(f"u{i}", HUGE, False) for i in range(100)],
    "1e308 beside 1.0": [("a", 1e308, True), ("b", 1.0, False)],
    "three at 1e308 ineligible": [("a", 1e308, False), ("b", 1e308, False), ("c", 1e308, False)],
    "two huge ineligible + one small eligible": [
        ("a", 1e308, False),
        ("b", 1e308, False),
        ("c", 1.0, True),
    ],
    "51 eligible at 1e308": [(f"u{i}", 1e308, True) for i in range(51)],
    "1e308 beside 1e-300, both eligible": [("a", 1e308, True), ("b", 1e-300, True)],
}


def _normalize_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for n in SWEEP_COUNTS:
        tasks.append(
            {
                "id": f"sweep-{n}",
                "kind": "normalize",
                "spec": _sweep_spec(n),
                "timeout": NORMALIZE_TIMEOUT,
            }
        )
    for name, spec in MIXED.items():
        tasks.append(
            {"id": f"mixed-{name}", "kind": "normalize", "spec": spec, "timeout": NORMALIZE_TIMEOUT}
        )
    for name, spec in HUGE_CASES.items():
        tasks.append(
            {"id": f"huge-{name}", "kind": "normalize", "spec": spec, "timeout": NORMALIZE_TIMEOUT}
        )
    tasks.append(
        {
            "id": "equal-51",
            "kind": "normalize",
            "spec": [(f"u{i}", 1.0, True) for i in range(51)],
            "timeout": NORMALIZE_TIMEOUT,
        }
    )
    tasks.append(
        {
            "id": "equal-60",
            "kind": "normalize",
            "spec": [(f"u{i}", 1.0, True) for i in range(60)],
            "timeout": NORMALIZE_TIMEOUT,
        }
    )
    return tasks


def _norm(task_id: str) -> dict[str, float]:
    result = _result("normalize", _normalize_tasks(), task_id)
    if not result.get("ok"):
        _fail("BEHAVIOR", f"{task_id}: raised {result.get('error')}: {result.get('message')}")
    return result["weights"]


# ── the demonstrated hang and negative-weight cases: 49, 50, 51, 52, 60, 100 ─


@pytest.mark.parametrize("n", SWEEP_COUNTS)
def test_one_dominant_plus_small_eligible_contributors_terminate_with_valid_weights(n):
    weights = _norm(f"sweep-{n}")
    _valid(weights, f"{n} eligible")
    assert len(weights) == n


@pytest.mark.parametrize("n", [49, 50])
def test_a_feasible_floor_is_applied_and_leaves_the_rest_to_the_dominant_contributor(n):
    weights = _norm(f"sweep-{n}")
    small = [w for u, w in weights.items() if u != "big"]
    _expect(
        min(small) >= FLOOR - 2e-6,
        f"{n} eligible: a floored contributor is below 2% ({min(small)!r})",
    )
    _expect(
        abs(weights["big"] - (1.0 - FLOOR * (n - 1))) <= 2e-6,
        f"{n} eligible: dominant got {weights['big']!r}, expected {1.0 - FLOOR * (n - 1)!r}",
    )


@pytest.mark.parametrize("n", [51, 52, 60, 100])
def test_an_infeasible_floor_is_disabled_and_scores_are_normalised_proportionally(n):
    weights = _norm(f"sweep-{n}")
    total_raw = 100.0 + 0.1 * (n - 1)
    _expect(
        abs(weights["big"] - 100.0 / total_raw) <= 2e-6,
        f"{n} eligible: dominant {weights['big']!r} is not proportional to its raw score",
    )
    small = [w for u, w in weights.items() if u != "big"]
    _expect(
        max(small) < 0.01, f"{n} eligible: floor was applied or credit equalised ({max(small)!r})"
    )
    _expect(
        all(abs(w - 0.1 / total_raw) <= 2e-6 for w in small),
        f"{n} eligible: small contributors are not proportional to their raw scores",
    )


@pytest.mark.parametrize("task_id, n", [("equal-51", 51), ("equal-60", 60)])
def test_equal_contributors_above_capacity_keep_their_equal_proportional_share(task_id, n):
    weights = _norm(task_id)
    _valid(weights, task_id)
    _expect(all(abs(w - 1 / n) <= 2e-6 for w in weights.values()), f"{task_id}: not 1/{n} each")


# ── mixed eligible / ineligible ──────────────────────────────────────────────


@pytest.mark.parametrize("name", list(MIXED))
def test_mixed_cases_terminate_with_valid_weights(name):
    _valid(_norm(f"mixed-{name}"), name)


def test_feasible_floor_gives_the_remainder_to_ineligible_contributors_proportionally():
    weights = _norm("mixed-30 eligible tiny + 20 ineligible")
    eligible = [w for u, w in weights.items() if u.startswith("e")]
    _expect(
        all(abs(w - FLOOR) <= 2e-6 for w in eligible), "eligible contributors are not at the floor"
    )
    low = sum(w for u, w in weights.items() if u.startswith("i"))
    high = sum(w for u, w in weights.items() if u.startswith("j"))
    _expect(
        abs((low + high) - (1 - 30 * FLOOR)) <= 1e-5,
        "ineligible contributors do not share the remainder",
    )
    _expect(
        abs(high / low - 3.0) <= 1e-3,
        "the 4:12 raw ratio among ineligible contributors was not preserved",
    )


def test_above_capacity_with_ineligible_contributors_disables_the_floor():
    weights = _norm("mixed-51 eligible + 10 ineligible")
    total = 51 * 0.1 + 10 * 5.0
    _expect(
        all(abs(w - 0.1 / total) <= 2e-6 for u, w in weights.items() if u.startswith("e")),
        "eligible contributors were floored although the floor is disabled above capacity",
    )


def test_boundary_policy_50_eligible_beside_a_positive_ineligible_keeps_its_credit():
    """The floor would give the ineligible contributor exactly 0 despite positive raw credit."""
    weights = _norm("mixed-50 eligible tiny + 1 large ineligible")
    _expect(weights["big"] > 0.9, f"positive raw credit was driven down to {weights['big']!r}")
    _expect(
        abs(weights["big"] - 100.0 / 105.0) <= 2e-6, "credit is not proportional to the raw score"
    )
    _expect(
        max(w for u, w in weights.items() if u != "big") < 0.01, "the floor was applied after all"
    )


SUBNORMAL = 5e-324  # smallest positive float: positive, but it underflows once normalised

UNEQUAL_ELIGIBLE = {
    "linear 1..50": [float(i) for i in range(1, 51)],
    "geometric 1.5": [1.5**i for i in range(50)],
    "squares": [float(i * i) for i in range(1, 51)],
}


def _floor_disabled_events(log: _Log) -> list[dict[str, Any]]:
    return [kw for e, kw in log.events if e == "normalize.floor_disabled"]


@pytest.mark.parametrize("name", list(UNEQUAL_ELIGIBLE))
def test_50_eligible_with_unequal_scores_beside_a_subnormal_positive_ineligible_disables_the_floor(
    name, log
):
    """The ineligible contributor's RAW score is strictly positive, but its normalised share
    underflows to 0.0. The boundary decision must use the raw positivity, not the share."""
    raws = UNEQUAL_ELIGIBLE[name]
    total = sum(raws)
    _expect(SUBNORMAL > 0 and SUBNORMAL / total == 0.0, "precondition: the share must underflow")
    spec: Spec = [(f"e{i}", r, True) for i, r in enumerate(raws)] + [("idle", SUBNORMAL, False)]

    out = AttributionScorer()._normalize(make(spec))

    weights = {o.user_id: o.contribution_weight for o in out}
    _valid(weights, name)
    _expect(
        all(abs(weights[f"e{i}"] - r / total) <= 2e-6 for i, r in enumerate(raws)),
        "eligible shares do not follow proportional normalisation",
    )
    _expect(
        not all(abs(weights[f"e{i}"] - FLOOR) <= 1e-9 for i in range(50)),
        "every eligible contributor was floored at 2% each",
    )
    # Not claimed: that the subnormal share survives. It may become 0 through floating-point
    # underflow or six-decimal rounding; only the bounds are asserted.
    _expect(0.0 <= weights["idle"] <= 1e-6, f"ineligible share is {weights['idle']!r}")
    events = _floor_disabled_events(log)
    _expect(
        len(events) == 1 and events[0].get("reason") == "would_zero_positive_credit",
        f"expected one floor_disabled warning, reason would_zero_positive_credit ({log.events})",
    )


def test_positivity_is_taken_before_overflow_scaling():
    """Scores whose sum overflows are rescaled; the rescaled subnormal vanishes, the raw stays."""
    raws = [1e306 * i for i in range(1, 51)]
    _expect(not math.isfinite(sum(raws)), "precondition: the sum must overflow")
    spec: Spec = [(f"e{i}", r, True) for i, r in enumerate(raws)] + [("idle", SUBNORMAL, False)]
    fake = _Log()
    original = scorer_module.log
    scorer_module.log = fake
    try:
        out = AttributionScorer()._normalize(make(spec))
    finally:
        scorer_module.log = original
    weights = {o.user_id: o.contribution_weight for o in out}
    _valid(weights, "overflowing sum beside a subnormal ineligible contributor")
    total = sum(range(1, 51))
    _expect(
        all(abs(weights[f"e{i}"] - (i + 1) / total) <= 2e-6 for i in range(50)),
        "eligible shares do not follow proportional normalisation",
    )
    events = _floor_disabled_events(fake)
    _expect(
        len(events) == 1 and events[0].get("reason") == "would_zero_positive_credit",
        f"expected the boundary warning (saw {fake.events})",
    )


@pytest.mark.parametrize("ineligible_raw", [0.0, -1.0])
def test_50_eligible_beside_an_ineligible_with_no_positive_credit_keeps_the_floor(
    ineligible_raw, log
):
    """Zero, or negative and therefore clamped to zero, is not positive credit to preserve."""
    raws = [float(i) for i in range(1, 51)]
    spec: Spec = [(f"e{i}", r, True) for i, r in enumerate(raws)] + [
        ("idle", ineligible_raw, False)
    ]

    weights = {o.user_id: o.contribution_weight for o in AttributionScorer()._normalize(make(spec))}

    _valid(weights, f"ineligible raw {ineligible_raw}")
    _expect(
        all(abs(weights[f"e{i}"] - FLOOR) <= 2e-6 for i in range(50)),
        "the floor was disabled although no contributor has positive credit to preserve",
    )
    _expect(weights["idle"] == 0.0, f"zero credit became {weights['idle']!r}")
    _expect(not _floor_disabled_events(log), f"unexpected floor_disabled (saw {log.events})")


def test_ineligible_contributors_are_never_floored():
    weights = _norm("mixed-60 ineligible only")
    total = sum(1.0 + i for i in range(60))
    _expect(
        all(abs(weights[f"i{i}"] - (1.0 + i) / total) <= 2e-6 for i in range(60)),
        "weights are not proportional to raw scores",
    )


def test_eligible_contributors_above_the_floor_are_left_proportional():
    weights = _norm("mixed-10 eligible all above the floor")
    total = sum(5.0 + i for i in range(10))
    _expect(
        all(abs(weights[f"e{i}"] - (5.0 + i) / total) <= 2e-6 for i in range(10)),
        "the floor changed weights it should not touch",
    )


def test_zero_raw_ineligible_contributor_gets_zero_while_a_floored_one_gets_the_floor():
    weights = _norm("mixed-zero-raw ineligible beside floored eligible")
    _expect(weights["idle"] == 0.0, f"zero raw score became {weights['idle']!r}")
    _expect(abs(weights["tiny"] - FLOOR) <= 2e-6, "the eligible contributor was not floored")


# ── very large finite values: aggregate overflow ─────────────────────────────


@pytest.mark.parametrize("name", list(HUGE_CASES))
def test_very_large_finite_scores_neither_overflow_nor_vanish(name):
    weights = _norm(f"huge-{name}")
    _valid(weights, name)


def test_overflowing_sum_still_normalises_proportionally():
    two = _norm("huge-two at 1e308 (sum overflows)")
    _expect(
        abs(two["a"] - 0.5) <= 2e-6 and abs(two["b"] - 0.5) <= 2e-6, f"two huge scores gave {two!r}"
    )
    many = _norm("huge-100 at float max")
    _expect(
        all(abs(w - 0.01) <= 2e-6 for w in many.values()), "100 equal huge scores are not 1% each"
    )
    three = _norm("huge-three at 1e308 ineligible")
    _expect(
        all(abs(w - 1 / 3) <= 2e-6 for w in three.values()), f"three huge scores gave {three!r}"
    )
    beside_one = _norm("huge-1e308 beside 1.0")
    _expect(beside_one["a"] > 0.999999 and beside_one["b"] >= 0.0, f"gave {beside_one!r}")
    mixed = _norm("huge-two huge ineligible + one small eligible")
    _expect(
        abs(mixed["c"] - FLOOR) <= 2e-6 and abs(mixed["a"] - mixed["b"]) <= 2e-6,
        f"huge scores beside a floored contributor gave {mixed!r}",
    )
    above = _norm("huge-51 eligible at 1e308")
    _expect(
        all(abs(w - 1 / 51) <= 2e-6 for w in above.values()),
        "51 huge eligible scores are not 1/51 each",
    )
    both_eligible = _norm("huge-1e308 beside 1e-300, both eligible")
    _expect(abs(both_eligible["b"] - FLOOR) <= 2e-6, f"gave {both_eligible!r}")


# ── zero totals and invalid values (tiny inputs, in-process) ─────────────────


class _Log:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _record(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    info = debug = error = _record
    warning = _record


@pytest.fixture
def log(monkeypatch):
    fake = _Log()
    monkeypatch.setattr(scorer_module, "log", fake)
    return fake


def _names(log: _Log) -> list[str]:
    return [e for e, _ in log.events]


def test_empty_input_returns_nothing():
    assert AttributionScorer()._normalize([]) == []


def test_zero_total_is_the_documented_equal_split_with_no_signal_scores_and_a_warning(log):
    out = AttributionScorer()._normalize(make([("a", 0.0, True), ("b", 0.0, False)]))
    _expect([o.contribution_weight for o in out] == [0.5, 0.5], "zero total did not split equally")
    _expect(
        all(o.char_diff_score is None and o.approval_score is None for o in out),
        "zero total reported signal scores",
    )
    _expect("normalize.zero_total" in _names(log), f"no zero-total warning (saw {_names(log)})")


def test_single_contributor_with_zero_total_is_one_with_a_warning(log):
    out = AttributionScorer()._normalize(make([("a", 0.0, True)]))
    _expect(out[0].contribution_weight == 1.0, "single zero-total contributor is not 1.0")
    _expect("normalize.zero_total" in _names(log), f"no zero-total warning (saw {_names(log)})")


def test_a_negative_finite_score_is_clamped_to_zero_with_a_warning(log):
    out = AttributionScorer()._normalize(make([("a", -0.5, False), ("b", 5.0, False)]))
    weights = {o.user_id: o.contribution_weight for o in out}
    _expect(weights == {"a": 0.0, "b": 1.0}, f"negative score gave {weights!r}")
    clamped = [kw for e, kw in log.events if e == "normalize.negative_raw_clamped"]
    _expect(
        len(clamped) == 1 and clamped[0].get("count") == 1, f"clamp not reported (saw {log.events})"
    )


def test_all_negative_scores_clamp_to_zero_and_take_the_zero_total_path(log):
    out = AttributionScorer()._normalize(make([("a", -1.0, True), ("b", -2.0, False)]))
    _expect(
        [o.contribution_weight for o in out] == [0.5, 0.5],
        "all-negative did not reach the zero-total path",
    )
    _expect(
        {"normalize.negative_raw_clamped", "normalize.zero_total"} <= set(_names(log)),
        f"expected both warnings (saw {_names(log)})",
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("shape", ["pair", "single"])
def test_non_finite_scores_are_refused_even_for_a_single_contributor(bad, shape):
    spec = [("a", bad, True), ("b", 1.0, False)] if shape == "pair" else [("a", bad, True)]
    try:
        AttributionScorer()._normalize(make(spec))
    except AttributionStateConflictError as exc:
        _expect("invalid_score_input" in str(exc), f"refusal does not name the reason: {exc}")
    except Exception as exc:  # noqa: BLE001
        _fail(
            "BEHAVIOR",
            f"{bad!r} ({shape}) raised {type(exc).__name__} instead of the handled refusal",
        )
    else:
        _fail("BEHAVIOR", f"{bad!r} ({shape}) was accepted instead of refused")


def test_single_contributor_is_one_including_at_float_max():
    for raw in (1.0, 1e308, HUGE, 5e-324):
        out = AttributionScorer()._normalize(make([("a", raw, True)]))
        _expect(out[0].contribution_weight == 1.0, f"single contributor with {raw!r} is not 1.0")


# ── the replacement keeps what the old loop actually produced ────────────────

COMPARE_TASK = {"id": "compare", "kind": "compare", "seed": 20261009, "count": 3000, "timeout": 120}


def test_feasible_results_match_the_frozen_legacy_loop_wherever_it_settles():
    result = _result("compare", [COMPARE_TASK], "compare")
    print(f"\nlegacy comparison: {json.dumps(result)}")
    _expect(
        result["invalid_new"] == 0, f"the new result was invalid in {result['invalid_new']} cases"
    )
    _expect(
        result["unexpected_diffs"] == 0,
        f"{result['unexpected_diffs']} outputs differ from the legacy loop outside the permitted "
        f"classes: {result['examples']}",
    )
    compared = result["equal_exact"] + result["equal_within_tolerance"]
    _expect(compared > 1000, f"only {compared} cases were actually compared")


# ── properties over wide seeded input ────────────────────────────────────────

PROPERTY_TASK = {"id": "property", "kind": "property", "seed": 7, "count": 3000, "timeout": 120}


def test_properties_hold_over_wide_seeded_inputs():
    result = _result("property", [PROPERTY_TASK], "property")
    print(f"\nproperty run: {json.dumps(result)}")
    _expect(not any(result["violations"].values()), f"violations: {result['violations']}")


# ── end to end through compute_scores ────────────────────────────────────────

E2E_TASK = {"id": "e2e-60", "kind": "end_to_end", "contributors": 60, "timeout": 60}


def test_sixty_contributors_through_compute_scores_terminate_with_valid_weights():
    result = _result("e2e", [E2E_TASK], "e2e-60")
    _expect(
        result.get("ok") is True,
        f"compute_scores raised {result.get('error')}: {result.get('message')}",
    )
    weights = result["weights"]
    _expect(len(weights) == 60, f"expected 60 contributors, got {len(weights)}")
    _valid(weights, "60 contributors")
    _expect(
        weights["alice"] == max(weights.values()),
        "the dominant contributor is no longer the largest",
    )


# ── the infeasibility threshold is derived, not hard-coded ───────────────────


def test_capacity_is_derived_from_the_floor_constant():
    capacity = _require_symbol("_MAX_FLOORED")
    _expect(
        capacity == math.floor(1.0 / scorer_module._FLOOR + 1e-9) == 50,
        f"_MAX_FLOORED is {capacity!r}",
    )


def test_lowering_the_capacity_disables_the_floor_and_says_why(monkeypatch, log):
    _require_symbol("_MAX_FLOORED")
    monkeypatch.setattr(scorer_module, "_MAX_FLOORED", 2)
    spec = [("big", 100.0, True), ("a", 0.1, True), ("b", 0.1, True)]
    weights = {o.user_id: o.contribution_weight for o in AttributionScorer()._normalize(make(spec))}
    _expect(
        abs(weights["a"] - 0.1 / 100.2) <= 2e-6,
        f"floor was applied above the patched capacity: {weights!r}",
    )
    events = [kw for e, kw in log.events if e == "normalize.floor_disabled"]
    _expect(
        len(events) == 1 and events[0].get("reason") == "eligible_exceeds_capacity",
        f"expected floor_disabled with reason eligible_exceeds_capacity (saw {log.events})",
    )


def test_boundary_policy_is_reported_with_its_own_reason(monkeypatch, log):
    _require_symbol("_MAX_FLOORED")
    monkeypatch.setattr(scorer_module, "_MAX_FLOORED", 2)
    spec = [("a", 0.1, True), ("b", 0.1, True), ("big", 100.0, False)]
    weights = {o.user_id: o.contribution_weight for o in AttributionScorer()._normalize(make(spec))}
    _expect(weights["big"] > 0.99, f"positive raw credit was not preserved: {weights!r}")
    events = [kw for e, kw in log.events if e == "normalize.floor_disabled"]
    _expect(
        len(events) == 1 and events[0].get("reason") == "would_zero_positive_credit",
        f"expected reason would_zero_positive_credit (saw {log.events})",
    )


def test_the_loop_is_structurally_bounded_and_falls_back_if_it_were_exhausted(monkeypatch, log):
    _require_symbol("_floor_pass_limit")
    monkeypatch.setattr(scorer_module, "_floor_pass_limit", lambda eligible: 0)
    spec = [("big", 100.0, True), ("a", 0.1, True), ("b", 0.1, True)]
    weights = {o.user_id: o.contribution_weight for o in AttributionScorer()._normalize(make(spec))}
    _valid(weights, "pass bound exhausted")
    _expect(
        abs(weights["a"] - 0.1 / 100.2) <= 2e-6,
        "exhausted loop did not fall back to unfloored scores",
    )
    events = [kw for e, kw in log.events if e == "normalize.floor_disabled"]
    _expect(
        len(events) == 1 and events[0].get("reason") == "no_convergence",
        f"expected reason no_convergence (saw {log.events})",
    )
