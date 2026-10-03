"""Which niches could earn, and how far the channel is from YouTube's Partner Program (plan stage 6).

Before monetization there is no revenue data, so a niche's expected value per video is its reach times its
retention (average views x average fraction watched), the closest stand-in for watch time and ad
impressions. Once revenue arrives (monetary scope, Partner Program) the value is views x revenue per view.

Partner Program thresholds (support.google.com/youtube/answer/72851, checked 2026-09-30): 1,000
subscribers, and either 4,000 public watch hours in the last 12 months or 10 million Shorts views in the
last 90 days. Shorts views do not count toward watch hours.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aimz.db import Database

YPP_SUBSCRIBERS = 1_000
YPP_WATCH_HOURS_12M = 4_000
YPP_SHORTS_VIEWS_90D = 10_000_000


@dataclass
class NicheValue:
    niche: str
    n: int
    mean_views: float
    mean_retention: float | None
    revenue_per_1000: float | None
    watch_value: float  # expected views x fraction watched, per video
    revenue_value: float | None  # expected revenue per video (USD), once known
    mean_score: float | None


def niche_values(rows: list[dict[str, Any]]) -> list[NicheValue]:
    """Per niche, from per-video rows with a score (their ``scored_metrics`` snapshot). Best first."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        snap = r.get("scored_metrics") or {}
        if r.get("score") is None or snap.get("views") is None or not r.get("content_family"):
            continue
        groups.setdefault(str(r["content_family"]), []).append({**snap, "score": r["score"]})
    out: list[NicheValue] = []
    for niche, items in groups.items():
        n = len(items)
        views = [float(i["views"]) for i in items]
        rets = [
            float(i["avg_percent_viewed"]) / 100 for i in items if i.get("avg_percent_viewed") is not None
        ]
        paid = [i for i in items if i.get("revenue_usd") is not None]
        mean_views = sum(views) / n
        mean_ret = sum(rets) / len(rets) if rets else None
        rpm = None
        if paid and sum(float(i["views"]) for i in paid):
            rpm = sum(float(i["revenue_usd"]) for i in paid) * 1000 / sum(float(i["views"]) for i in paid)
        out.append(
            NicheValue(
                niche=niche,
                n=n,
                mean_views=round(mean_views, 1),
                mean_retention=round(mean_ret, 3) if mean_ret is not None else None,
                revenue_per_1000=round(rpm, 4) if rpm is not None else None,
                watch_value=round(mean_views * (mean_ret if mean_ret is not None else 0.0), 2),
                revenue_value=round(mean_views * rpm / 1000, 4) if rpm is not None else None,
                mean_score=round(sum(float(i["score"]) for i in items) / n, 3),
            )
        )
    by_revenue = bool(out) and all(v.revenue_value is not None for v in out)
    return sorted(out, key=lambda v: -((v.revenue_value or 0.0) if by_revenue else v.watch_value))


def niche_value_text(values: list[NicheValue], limit: int = 10) -> str:
    if not values:
        return "- no scored niches yet"
    by_revenue = all(v.revenue_value is not None for v in values)
    head = (
        "ranked by expected revenue per video (views x revenue per view)"
        if by_revenue
        else "ranked by expected watch per video (average views x fraction watched; no revenue data yet)"
    )
    lines = [f"({head})"]
    for v in values[:limit]:
        rev = f" revenue/1k views=${v.revenue_per_1000}" if v.revenue_per_1000 is not None else ""
        lines.append(
            f"- {v.niche}: n={v.n} views={v.mean_views:g} watched={v.mean_retention} value={v.watch_value:g}{rev} score={v.mean_score}"
        )
    return "\n".join(lines)


def ypp_progress(db: Database, now: datetime | None = None) -> dict[str, Any]:
    """Partner Program progress for this channel's YouTube account."""
    now = now or datetime.now(UTC)
    stats = json.loads(db.get_state("youtube_channel_stats") or "{}")
    since = (now - timedelta(days=90)).replace(microsecond=0).isoformat()
    row = db.one(
        "SELECT COALESCE(SUM(m.views), 0) AS v FROM metrics m JOIN ("
        "  SELECT publication_id, MAX(captured_at) AS mx FROM metrics WHERE platform='youtube' GROUP BY publication_id"
        ") l ON l.publication_id = m.publication_id AND l.mx = m.captured_at "
        "JOIN publications p ON p.id = m.publication_id WHERE p.posted_at >= ?",
        [since],
    )
    shorts_views = int(row["v"]) if row else 0
    subs = stats.get("subscriberCount")
    subs_n = int(subs) if subs is not None and not stats.get("hiddenSubscriberCount") else None
    return {
        "subscribers": subs_n,
        "subscribers_needed": YPP_SUBSCRIBERS,
        "shorts_views_90d": shorts_views,
        "shorts_views_needed_90d": YPP_SHORTS_VIEWS_90D,
        "watch_hours_12m_needed": YPP_WATCH_HOURS_12M,
        "watch_hours_note": "Shorts views do not count toward watch hours; this channel posts Shorts only",
        "channel_total_views": int(stats["viewCount"]) if stats.get("viewCount") is not None else None,
        "stats_as_of": stats.get("captured_at"),
        "eligible": bool(
            subs_n is not None and subs_n >= YPP_SUBSCRIBERS and shorts_views >= YPP_SHORTS_VIEWS_90D
        ),
    }


def ypp_text(p: dict[str, Any]) -> str:
    subs = "unknown" if p["subscribers"] is None else f"{p['subscribers']:,}"
    return (
        f"Partner Program: subscribers {subs}/{p['subscribers_needed']:,}; "
        f"Shorts views in 90 days {p['shorts_views_90d']:,}/{p['shorts_views_needed_90d']:,} "
        f"(or {p['watch_hours_12m_needed']:,} long-form watch hours in 12 months)"
    )
