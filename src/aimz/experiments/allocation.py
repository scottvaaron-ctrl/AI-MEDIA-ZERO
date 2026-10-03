"""Exploration vs exploitation.

Cold start: ``cold_start_explore_ratio`` (70%) of production slots explore. As measured
videos accumulate the ratio decays linearly toward ``min_explore_ratio`` between
``decay_start_sample`` and ``decay_full_sample``. It never reaches zero, so one viral video
cannot lock the strategy.

Exploit slots pick a family by *Thompson-style sampling*: each family's mean performance
score gets a normal posterior whose width shrinks with sample size (plus a prior width for
families under ``family_confidence_sample``). Explore slots pick the least-sampled families.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from aimz.util import clamp


@dataclass
class FamilyStat:
    key: str
    n: int = 0
    mean: float = 0.0
    sd: float = 0.0
    scores: list[float] = field(default_factory=list)
    status: str = "hypothesis"


@dataclass
class AllocationSlot:
    mode: str  # explore | exploit
    family: str | None
    reason: str


def explore_ratio(total_measured: int, cfg: dict[str, Any]) -> float:
    cold = float(cfg.get("cold_start_explore_ratio", 0.7))
    floor = float(cfg.get("min_explore_ratio", 0.25))
    start = int(cfg.get("decay_start_sample", 12))
    full = int(cfg.get("decay_full_sample", 60))
    if total_measured <= start:
        return cold
    if total_measured >= full:
        return floor
    t = (total_measured - start) / max(1, full - start)
    return cold + (floor - cold) * t


def per_video(perf_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per video from per-publication rows: a video posted to three platforms is one sample.

    The row kept is a scored publication's (else the first seen); ``score`` becomes the mean of the video's
    scored publications. Safe to apply twice.
    """
    base: dict[str, dict[str, Any]] = {}
    scores: dict[str, list[float]] = {}
    for row in perf_rows:
        key = str(row.get("video_id") or id(row))
        scored = row.get("score") is not None
        if key not in base or (scored and key not in scores):
            base[key] = dict(row)
        if scored:
            scores.setdefault(key, []).append(float(row["score"]))
    for key, row in base.items():
        vals = scores.get(key)
        row["score"] = sum(vals) / len(vals) if vals else None
    return list(base.values())


def family_stats(
    perf_rows: list[dict[str, Any]], families: dict[str, dict[str, Any]]
) -> dict[str, FamilyStat]:
    stats: dict[str, FamilyStat] = {
        k: FamilyStat(key=k, status=str(v.get("status", "hypothesis"))) for k, v in families.items()
    }
    for row in per_video(perf_rows):
        fam = row.get("content_family")
        score = row.get("score")
        if not fam or score is None:
            continue
        st = stats.setdefault(fam, FamilyStat(key=fam, status="new"))
        st.scores.append(float(score))
    for st in stats.values():
        st.n = len(st.scores)
        if st.n:
            st.mean = sum(st.scores) / st.n
            if st.n > 1:
                var = sum((s - st.mean) ** 2 for s in st.scores) / (st.n - 1)
                st.sd = math.sqrt(var)
    return stats


def _sample(st: FamilyStat, confidence_n: int, rng: random.Random) -> float:
    prior_mean, prior_sd = 0.4, 0.25
    if st.n == 0:
        return rng.gauss(prior_mean, prior_sd)
    shrink = st.n / (st.n + confidence_n)
    mean = shrink * st.mean + (1 - shrink) * prior_mean
    sd = max(0.05, (st.sd if st.n > 1 else prior_sd) / math.sqrt(st.n)) + (1 - shrink) * prior_sd
    return rng.gauss(mean, sd)


def plan_allocation(
    perf_rows: list[dict[str, Any]],
    families: dict[str, dict[str, Any]],
    slots: int,
    cfg: dict[str, Any],
    rng: random.Random | None = None,
    explore_ratio_override: float | None = None,
    available: set[str] | None = None,
    pending: dict[str, int] | None = None,
) -> list[AllocationSlot]:
    """``available``: families that have a candidate idea right now; slots only go to those (when any).
    ``pending``: per family, videos still being made or awaiting their score. They count toward how
    sampled a family is when exploring, so one family is not explored again and again before its first
    result is in. They never enter a family's mean."""
    rng = rng or random.Random()
    pending = pending or {}
    active = {k: v for k, v in families.items() if v.get("status") != "retired"}
    if not active:
        active = dict(families)
    all_stats = family_stats(perf_rows, active)
    stats = all_stats
    if available:
        fillable = {k: s for k, s in all_stats.items() if k in available}
        if fillable:
            stats = fillable
    measured = sum(1 for r in per_video(perf_rows) if r.get("score") is not None)
    ratio = (
        explore_ratio(measured, cfg)
        if explore_ratio_override is None
        else clamp(explore_ratio_override, 0.0, 1.0)
    )
    ratio = clamp(ratio, float(cfg.get("min_explore_ratio", 0.25)), 1.0)
    conf_n = int(cfg.get("family_confidence_sample", 5))

    out: list[AllocationSlot] = []
    tested = [k for k, s in all_stats.items() if s.n > 0]
    planned: dict[str, int] = {}
    for _ in range(max(0, slots)):
        # Constitution: at least three distinguishable families must be tested before exploiting.
        force_explore = len(tested) < 3
        explore = force_explore or rng.random() < ratio
        if explore:

            def tried(s: FamilyStat) -> int:
                return s.n + pending.get(s.key, 0) + planned.get(s.key, 0)

            pick = sorted(stats.values(), key=lambda s: (tried(s), rng.random()))[0]
            in_progress = pending.get(pick.key, 0)
            out.append(
                AllocationSlot(
                    "explore",
                    pick.key,
                    f"least-sampled family (n={pick.n}"
                    + (f", {in_progress} in progress" if in_progress else "")
                    + f"); explore ratio {ratio:.0%}",
                )
            )
            planned[pick.key] = planned.get(pick.key, 0) + 1  # not the same family twice in one plan
        else:
            best = max(stats.values(), key=lambda s: _sample(s, conf_n, rng))
            out.append(
                AllocationSlot(
                    "exploit",
                    best.key,
                    f"posterior sample favored {best.key} (n={best.n}, mean={best.mean:.2f})",
                )
            )
    return out
