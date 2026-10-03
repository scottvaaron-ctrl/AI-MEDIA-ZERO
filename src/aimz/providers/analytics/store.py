"""SQLiteAnalyticsProvider: the local metrics store, manual entry, and the performance model. Cost: $0.

Every platform metric snapshot (from an API or typed in by the owner) lands in ``metrics``.
The learning loop reads *this* table only, so API-fed and manually-entered numbers are treated
identically. ``performance_score`` turns a snapshot into a single 0..1 number that the
allocation and experiment engines compare across videos.
"""

from __future__ import annotations

import math
from typing import Any

from aimz.db import Database
from aimz.domain.models import MetricsSnapshot
from aimz.providers.base import AnalyticsProvider, HealthStatus, ProviderContext
from aimz.util import clamp, dumps, new_id, now_iso

# Weights follow the Editor-in-Chief's optimization goals: retention > shares > subs > completion,
# plus reach: how many people the platform chose to show the video to is the audience's first verdict.
WEIGHTS = {"retention": 0.35, "shares": 0.25, "subs": 0.20, "completion": 0.20, "reach": 0.20}

# Percentages from a handful of viewers are noise: 19 views at 85% watched is not better evidence than
# 250 views at 24%. Percent metrics are shrunk toward a neutral prior, worth this many views of evidence.
PRIOR_VIEWS = 50
PRIOR_PERCENT = 0.35
# Shares and subscribers are rare events, so their per-1,000 rates need more views before they mean much.
RATE_PRIOR_VIEWS = 200

# Videos are compared at the same age: the first snapshot at least this old. Views level off within
# about two days, and YouTube Analytics (retention, shares, subscribers) lags one to two days.
SCORE_AT_HOURS = 72.0
# A score taken after this age was not taken on time (collection missed the window), so it is flagged.
LATE_SCORE_HOURS = 120.0
# At 72 h YouTube Analytics often has no rows yet, and a score without them is almost all reach. A YouTube
# video is scored on its first snapshot with Analytics rows; if none has arrived by this age, it is scored
# on reach alone and flagged ``reach_only``.
REACH_ONLY_AFTER_HOURS = 120.0
# Platforms whose score depends on Analytics rows (retention, shares, subscribers) arriving after the views.
ANALYTICS_LAG_PLATFORMS = {"youtube"}


def _shrunk(pct: float, views: int) -> float:
    return (clamp(pct / 100.0, 0, 1) * views + PRIOR_PERCENT * PRIOR_VIEWS) / (views + PRIOR_VIEWS)


# Plan stage 6: parts that point at money. Their weights come from config.yaml `scoring` and are the
# owner's decision (they define what success is); at weight 0 they are shown but change nothing.
# watch_time: watch minutes per view (a proxy for the watch hours YouTube's Partner Program counts);
# revenue: estimated revenue per 1,000 views, from the monetary scope once the channel earns.
EXTRA_WEIGHTS = {"watch_time": 0.0, "revenue": 0.0}
WATCH_MINUTES_PER_VIEW_MAX = 1.0  # a Short watched for a full minute per view scores 1
REVENUE_PER_1000_MAX = 0.20  # USD per 1,000 views that scores 1 (Shorts RPM is usually a few cents)


def score_weights(cfg: Any = None) -> dict[str, float]:
    """Base weights plus the stage-6 parts, overridden by config.yaml ``scoring.weights``."""
    weights = {**WEIGHTS, **EXTRA_WEIGHTS}
    raw = (cfg.get("scoring.weights", {}) if cfg is not None else {}) or {}
    for k, v in dict(raw).items():
        if k in weights:
            weights[k] = max(0.0, float(v))
    return weights


def score_parts(m: dict[str, Any], weights: dict[str, float] | None = None) -> dict[str, float] | None:
    """Each score part (0..1) that this metrics row can support, or None without a view count."""
    if m.get("views") is None:
        return None  # no denominator (Bluesky reports none): rates and reach cannot be compared
    views = int(m["views"])
    parts: dict[str, float] = {}
    pct = m.get("avg_percent_viewed")
    if pct is None:
        pct = m.get("retention_3s")
    if pct is not None:
        parts["retention"] = _shrunk(float(pct), views)
    if m.get("shares") is not None:
        # per 1,000 views, with RATE_PRIOR_VIEWS added to the denominator so 1 share on 5 views is not 200/1k
        rate = m["shares"] * 1000 / (views + RATE_PRIOR_VIEWS)
        parts["shares"] = clamp(rate / 15.0, 0, 1)  # 15 shares/1k = max
    gained = m.get("subscribers_gained")
    if gained is None:
        gained = m.get("followers_gained")
    if gained is not None:
        rate = gained * 1000 / (views + RATE_PRIOR_VIEWS)
        parts["subs"] = clamp(rate / 10.0, 0, 1)  # 10 subs/1k = max
    if m.get("completion_rate") is not None:
        parts["completion"] = _shrunk(float(m["completion_rate"]), views)
    parts["reach"] = clamp(math.log10(1 + views) / 4.0, 0, 1)  # 10,000 views = max
    if m.get("watch_time_minutes") is not None and views:
        parts["watch_time"] = clamp(float(m["watch_time_minutes"]) / views / WATCH_MINUTES_PER_VIEW_MAX, 0, 1)
    if m.get("revenue_usd") is not None:
        rpm = float(m["revenue_usd"]) * 1000 / (views + RATE_PRIOR_VIEWS)
        parts["revenue"] = clamp(rpm / REVENUE_PER_1000_MAX, 0, 1)
    return parts


def performance_score(m: dict[str, Any], weights: dict[str, float] | None = None) -> float | None:
    """0..1 composite from a metrics row. Returns None when nothing usable is present.

    Every component is computed the same way at every view count, so a video that reached few
    people cannot outscore one that reached many just because some metrics were skipped for it.
    """
    parts = score_parts(m)
    if parts is None:
        return None
    w = weights or {**WEIGHTS, **EXTRA_WEIGHTS}
    used = [(w.get(k, 0.0), v) for k, v in parts.items() if w.get(k, 0.0) > 0]
    wsum = sum(x for x, _ in used)
    return sum(x * v for x, v in used) / wsum if wsum else None


class SQLiteAnalyticsProvider(AnalyticsProvider):
    name = "SQLiteAnalyticsProvider"
    platform = "*"
    is_paid = False

    def __init__(self, db: Database, weights: dict[str, float] | None = None):
        self.db = db
        self.weights = weights or score_weights()

    def health(self) -> HealthStatus:
        n = self.db.count("metrics")
        return HealthStatus(True, f"metrics store OK ({n} snapshots)")

    def fetch_metrics(self, ctx: ProviderContext, publication: dict[str, Any]) -> MetricsSnapshot | None:
        return None  # this provider stores; remote providers fetch

    # -- writes ------------------------------------------------------------------------
    @staticmethod
    def hours_live(publication: dict[str, Any], at: str | None = None) -> float | None:
        """Hours since the post went public. A scheduled upload (``scheduled_for`` later than ``posted_at``)
        is counted from its publish time, and a snapshot taken before that counts as 0, never negative."""
        from datetime import UTC, datetime

        start = None
        for key in ("posted_at", "scheduled_for"):
            try:
                t = datetime.fromisoformat(str(publication.get(key) or ""))
            except ValueError:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=UTC)  # stored times are UTC
            start = t if start is None else max(start, t)
        if start is None:
            return None
        hours = (datetime.fromisoformat(at or now_iso()) - start).total_seconds() / 3600
        return round(max(0.0, hours), 2)

    def record(self, publication: dict[str, Any], snap: MetricsSnapshot, source: str) -> str:
        hours = self.hours_live(publication)
        # API revenue is lifetime-to-date, so only the increase since the last snapshot is new money.
        new_revenue = snap.revenue_usd
        if snap.revenue_usd is not None and source != "manual":
            before = self.db.scalar(
                "SELECT MAX(revenue_usd) FROM metrics WHERE publication_id=? AND source != 'manual'",
                [publication["id"]],
                None,
            )
            new_revenue = snap.revenue_usd - float(before or 0)
        mid = new_id("met")
        self.db.insert(
            "metrics",
            {
                "id": mid,
                "publication_id": publication["id"],
                "video_id": publication["video_id"],
                "platform": publication["platform"],
                "captured_at": now_iso(),
                "source": source,
                "hours_since_post": hours,
                "views": snap.views,
                "impressions": snap.impressions,
                "ctr": snap.ctr,
                "retention_3s": snap.retention_3s,
                "avg_watch_time_s": snap.avg_watch_time_s,
                "avg_percent_viewed": snap.avg_percent_viewed,
                "completion_rate": snap.completion_rate,
                "likes": snap.likes,
                "comments": snap.comments,
                "shares": snap.shares,
                "subscribers_gained": snap.subscribers_gained,
                "followers_gained": snap.followers_gained,
                "returning_viewers": snap.returning_viewers,
                "watch_time_minutes": snap.watch_time_minutes,
                "revenue_usd": snap.revenue_usd,
                "raw_json": dumps(snap.raw) if snap.raw else None,
            },
        )
        if new_revenue and new_revenue > 0:
            from aimz.core.budget import BudgetManager

            BudgetManager(self.db, 0.0).record_revenue(
                new_revenue, note=f"metrics {mid}", content_id=publication["video_id"]
            )
        return mid

    # -- reads -------------------------------------------------------------------------
    def latest_per_publication(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT m.* FROM metrics m JOIN (SELECT publication_id, MAX(captured_at) AS mx FROM metrics GROUP BY publication_id) l "
            "ON l.publication_id = m.publication_id AND l.mx = m.captured_at"
        )
        return [dict(r) for r in rows]

    def scoring_snapshot(self, latest: dict[str, Any]) -> dict[str, Any] | None:
        """The snapshot a publication is scored on, or None while it is too young to compare.

        The first API snapshot at least SCORE_AT_HOURS old, so every video is judged at the same age.
        On YouTube that snapshot must also carry Analytics rows (``avg_percent_viewed``); without them the
        score is mostly reach. If none has arrived by REACH_ONLY_AFTER_HOURS, the first snapshot past that
        age is used and marked ``reach_only``. Owner-entered (manual) numbers and snapshots with no known
        age are used as they are.
        """
        if latest.get("source") == "manual" or latest.get("hours_since_post") is None:
            return latest
        if latest.get("platform") not in ANALYTICS_LAG_PLATFORMS:
            row = self.db.one(
                "SELECT * FROM metrics WHERE publication_id=? AND hours_since_post >= ? "
                "ORDER BY hours_since_post LIMIT 1",
                [latest["publication_id"], SCORE_AT_HOURS],
            )
            return dict(row) if row else None
        row = self.db.one(
            "SELECT * FROM metrics WHERE publication_id=? AND hours_since_post >= ? "
            "AND avg_percent_viewed IS NOT NULL ORDER BY hours_since_post LIMIT 1",
            [latest["publication_id"], SCORE_AT_HOURS],
        )
        if row:
            return dict(row)
        row = self.db.one(
            "SELECT * FROM metrics WHERE publication_id=? AND hours_since_post >= ? "
            "ORDER BY hours_since_post LIMIT 1",
            [latest["publication_id"], REACH_ONLY_AFTER_HOURS],
        )
        return {**dict(row), "reach_only": True} if row else None

    def video_performance(self) -> list[dict[str, Any]]:
        """One row per published video with its latest metrics, idea attributes, and composite score.

        The metric columns are the latest snapshot; ``score`` comes from :meth:`scoring_snapshot` and is
        None until the video is old enough to compare fairly.
        """
        out: list[dict[str, Any]] = []
        for m in self.latest_per_publication():
            v = self.db.get("videos", m["video_id"])
            if not v:
                continue
            idea = self.db.get("ideas", v["idea_id"])
            pub = self.db.get("publications", m["publication_id"])
            snap = self.scoring_snapshot(m)
            score = performance_score(snap, self.weights) if snap else None
            age = snap.get("hours_since_post") if snap else None
            out.append(
                {
                    **m,
                    # The snapshot the score comes from, and how old the video was then. A video whose
                    # 72-hour window was missed (an outage) is scored later; that is flagged, not hidden.
                    "scored_metrics": snap,
                    "score_age_h": age,
                    # Reach-only scores are taken at 120 h by design, so they are not "late".
                    "score_late": bool(
                        score is not None
                        and age is not None
                        and float(age) > LATE_SCORE_HOURS
                        and not (snap or {}).get("reach_only")
                    ),
                    "score_reach_only": bool(score is not None and (snap or {}).get("reach_only")),
                    "title": v["title"],
                    "duration_s": v["duration_s"],
                    "content_family": idea["content_family"] if idea else None,
                    "angle": idea["angle"] if idea else None,
                    "hook_type": idea["hook_type"] if idea else None,
                    "experiment_id": idea["experiment_id"] if idea else None,
                    "experiment_arm": idea["experiment_arm"] if idea else None,
                    "allocation_mode": idea["allocation_mode"] if idea else None,
                    "source_item_ids_json": idea["source_item_ids_json"] if idea else "[]",
                    "posted_at": pub["posted_at"] if pub else None,
                    "score": score,
                    "score_parts": score_parts(snap) if snap else None,
                }
            )
        # Niches the strategist merged count as one (the label the idea was written under is kept).
        from aimz.experiments.niches import aliases, canonicalize_rows

        canonicalize_rows(out, aliases(self.db))
        return out
