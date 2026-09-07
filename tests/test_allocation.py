from __future__ import annotations

import random

from aimz.experiments.allocation import explore_ratio, family_stats, plan_allocation

CFG = {
    "cold_start_explore_ratio": 0.7,
    "min_explore_ratio": 0.25,
    "family_confidence_sample": 5,
    "decay_start_sample": 12,
    "decay_full_sample": 60,
}
FAMS = {k: {"status": "hypothesis"} for k in ["a", "b", "c", "d"]}


def test_explore_ratio_decays_but_never_below_floor() -> None:
    assert explore_ratio(0, CFG) == 0.7
    assert explore_ratio(12, CFG) == 0.7
    mid = explore_ratio(36, CFG)
    assert 0.25 < mid < 0.7
    assert explore_ratio(60, CFG) == 0.25
    assert explore_ratio(10_000, CFG) == 0.25


def test_cold_start_forces_exploration_until_three_families_tested() -> None:
    rng = random.Random(0)
    rows = [{"content_family": "a", "score": 0.9}] * 10  # one viral family only
    slots = plan_allocation(rows, FAMS, 4, CFG, rng, explore_ratio_override=0.0)
    assert all(s.mode == "explore" for s in slots)
    assert "a" not in {s.family for s in slots}  # least-sampled families first


def test_exploit_prefers_better_family_with_enough_evidence() -> None:
    rng = random.Random(1)
    rows = (
        [{"content_family": "a", "score": 0.8}] * 12
        + [{"content_family": "b", "score": 0.2}] * 12
        + [{"content_family": "c", "score": 0.3}] * 12
    )
    picks = [s.family for s in plan_allocation(rows, FAMS, 60, CFG, rng, explore_ratio_override=0.0)]
    assert picks.count("a") > picks.count("b") and picks.count("a") > picks.count("c")


def test_one_viral_video_does_not_lock_strategy() -> None:
    rng = random.Random(2)
    rows = [
        {"content_family": "a", "score": 1.0},
        {"content_family": "b", "score": 0.3},
        {"content_family": "c", "score": 0.3},
    ]
    picks = [s.family for s in plan_allocation(rows, FAMS, 100, CFG, rng)]
    assert len(set(picks)) >= 3  # still spreading across families
    assert picks.count("a") < 70


def test_family_stats_and_retired_exclusion() -> None:
    fams = {"a": {"status": "retired"}, "b": {"status": "testing"}}
    st = family_stats([{"content_family": "b", "score": 0.5}, {"content_family": "b", "score": 0.7}], fams)
    assert st["b"].n == 2 and abs(st["b"].mean - 0.6) < 1e-9
    slots = plan_allocation([], fams, 3, CFG, random.Random(3))
    assert all(s.family == "b" for s in slots)
