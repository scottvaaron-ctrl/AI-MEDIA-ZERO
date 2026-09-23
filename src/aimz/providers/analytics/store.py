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


def _shrunk(pct: float, views: int) -> float:
    return (clamp(pct / 100.0, 0, 1) * views + PRIOR_PERCENT * PRIOR_VIEWS) / (views + PRIOR_VIEWS)


def performance_score(m: dict[str, Any]) -> float | None:
    """0..1 composite from a metrics row. Returns None when nothing usable is present.

    Every component is computed the same way at every view count, so a video that reached few
    people cannot outscore one that reached many just because some metrics were skipped for it.
    """
    if m.get("views") is None:
        return None  # no denominator (Bluesky reports none): rates and reach cannot be compared
    views = int(m["views"])
    parts: list[tuple[float, float]] = []
    pct = m.get("avg_percent_viewed")
    if pct is None:
        pct = m.get("retention_3s")
    if pct is not None:
        parts.append((WEIGHTS["retention"], _shrunk(float(pct), views)))
    if m.get("shares") is not None:
        # per 1,000 views, with RATE_PRIOR_VIEWS added to the denominator so 1 share on 5 views is not 200/1k
        rate = m["shares"] * 1000 / (views + RATE_PRIOR_VIEWS)
        parts.append((WEIGHTS["shares"], clamp(rate / 15.0, 0, 1)))  # 15 shares/1k = max
    gained = m.get("subscribers_gained")
    if gained is None:
        gained = m.get("followers_gained")
    if gained is not None:
        rate = gained * 1000 / (views + RATE_PRIOR_VIEWS)
        parts.append((WEIGHTS["subs"], clamp(rate / 10.0, 0, 1)))  # 10 subs/1k = max
    if m.get("completion_rate") is not None:
        parts.append((WEIGHTS["completion"], _shrunk(float(m["completion_rate"]), views)))
    parts.append((WEIGHTS["reach"], clamp(math.log10(1 + views) / 4.0, 0, 1)))  # 10,000 views = max
    wsum = sum(w for w, _ in parts)
    return sum(w * v for w, v in parts) / wsum


class SQLiteAnalyticsProvider(AnalyticsProvider):
    name = "SQLiteAnalyticsProvider"
    platform = "*"
    is_paid = False

    def __init__(self, db: Database):
        self.db = db

    def health(self) -> HealthStatus:
        n = self.db.count("metrics")
        return HealthStatus(True, f"metrics store OK ({n} snapshots)")

    def fetch_metrics(self, ctx: ProviderContext, publication: dict[str, Any]) -> MetricsSnapshot | None:
        return None  # this provider stores; remote providers fetch

    # -- writes ------------------------------------------------------------------------
    def record(self, publication: dict[str, Any], snap: MetricsSnapshot, source: str) -> str:
        hours = None
        if publication.get("posted_at"):
            from datetime import datetime

            try:
                posted = datetime.fromisoformat(publication["posted_at"])
                hours = round((datetime.fromisoformat(now_iso()) - posted).total_seconds() / 3600, 2)
            except ValueError:
                hours = None
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
        if snap.revenue_usd:
            from aimz.core.budget import BudgetManager

            BudgetManager(self.db, 0.0).record_revenue(
                snap.revenue_usd, note=f"metrics {mid}", content_id=publication["video_id"]
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
        Owner-entered (manual) numbers and snapshots with no known age are used as they are.
        """
        if latest.get("source") == "manual" or latest.get("hours_since_post") is None:
            return latest
        row = self.db.one(
            "SELECT * FROM metrics WHERE publication_id=? AND hours_since_post >= ? "
            "ORDER BY hours_since_post LIMIT 1",
            [latest["publication_id"], SCORE_AT_HOURS],
        )
        return dict(row) if row else None

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
            score = performance_score(snap) if snap else None
            out.append(
                {
                    **m,
                    "title": v["title"],
                    "duration_s": v["duration_s"],
                    "content_family": idea["content_family"] if idea else None,
                    "hook_type": idea["hook_type"] if idea else None,
                    "experiment_id": idea["experiment_id"] if idea else None,
                    "experiment_arm": idea["experiment_arm"] if idea else None,
                    "allocation_mode": idea["allocation_mode"] if idea else None,
                    "source_item_ids_json": idea["source_item_ids_json"] if idea else "[]",
                    "posted_at": pub["posted_at"] if pub else None,
                    "score": score,
                }
            )
        return out
