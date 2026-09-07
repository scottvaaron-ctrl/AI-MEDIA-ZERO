"""SQLiteAnalyticsProvider: the local metrics store, manual entry, and the performance model. Cost: $0.

Every platform metric snapshot (from an API or typed in by the owner) lands in ``metrics``.
The learning loop reads *this* table only, so API-fed and manually-entered numbers are treated
identically. ``performance_score`` turns a snapshot into a single 0..1 number that the
allocation and experiment engines compare across videos.
"""

from __future__ import annotations

from typing import Any

from aimz.db import Database
from aimz.domain.models import MetricsSnapshot
from aimz.providers.base import AnalyticsProvider, HealthStatus, ProviderContext
from aimz.util import clamp, dumps, new_id, now_iso

# Weights follow the Editor-in-Chief's optimization goals: retention > shares > subs > completion.
WEIGHTS = {"retention": 0.35, "shares": 0.25, "subs": 0.20, "completion": 0.20}


def performance_score(m: dict[str, Any]) -> float | None:
    """0..1 composite from a metrics row. Returns None when nothing usable is present."""
    views = m.get("views") or 0
    parts: list[tuple[float, float]] = []
    if m.get("avg_percent_viewed") is not None:
        parts.append((WEIGHTS["retention"], clamp(float(m["avg_percent_viewed"]) / 100.0, 0, 1)))
    elif m.get("retention_3s") is not None:
        parts.append((WEIGHTS["retention"], clamp(float(m["retention_3s"]) / 100.0, 0, 1)))
    if views >= 50:
        if m.get("shares") is not None:
            parts.append(
                (WEIGHTS["shares"], clamp((m["shares"] / views * 1000) / 15.0, 0, 1))
            )  # 15 shares/1k = max
        gained = m.get("subscribers_gained")
        if gained is None:
            gained = m.get("followers_gained")
        if gained is not None:
            parts.append((WEIGHTS["subs"], clamp((gained / views * 1000) / 10.0, 0, 1)))  # 10 subs/1k = max
    if m.get("completion_rate") is not None:
        parts.append((WEIGHTS["completion"], clamp(float(m["completion_rate"]) / 100.0, 0, 1)))
    if not parts:
        if views:
            return clamp(views / 5000.0, 0, 0.3)  # views alone are a weak signal; cap it
        return None
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

    def video_performance(self) -> list[dict[str, Any]]:
        """One row per published video with its latest metrics, idea attributes, and composite score."""
        out: list[dict[str, Any]] = []
        for m in self.latest_per_publication():
            v = self.db.get("videos", m["video_id"])
            if not v:
                continue
            idea = self.db.get("ideas", v["idea_id"])
            pub = self.db.get("publications", m["publication_id"])
            score = performance_score(m)
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
