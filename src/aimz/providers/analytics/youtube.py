"""YouTubeAnalyticsProvider: read-only metrics and comments through the official APIs. Cost: $0 (quota only).

* ``videos.list(part=statistics)`` -> views, likes, comments (1 quota unit).
* YouTube Analytics API v2 ``reports.query`` -> averageViewDuration, averageViewPercentage, shares,
  subscribersGained, estimatedMinutesWatched (per video, lifetime).
* ``commentThreads.list`` -> top-level comments (1 quota unit).
* Revenue (``estimatedRevenue``, ``estimatedAdRevenue``, ``cpm``, ``playbackBasedCpm``), only when the
  token has the monetary scope. A channel outside the Partner Program gets an error or no rows; that is
  recorded as "no revenue data", never allowed to drop the other metrics.

Not available through any API (must be entered manually from YouTube Studio): thumbnail
impressions, impressions CTR, and a literal "3-second retention" (the Analytics API exposes an
audience-retention curve by *ratio of video length*, not seconds). The owner can enter those
with ``aimz metric add``.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from aimz.core.errors import ProviderUnavailable
from aimz.domain.models import FetchedComment, MetricsSnapshot
from aimz.providers.base import AnalyticsProvider, HealthStatus, ProviderContext
from aimz.providers.publishers.youtube import (
    ALL_SCOPES,
    SCOPE_MONETARY,
    build_youtube,
    has_scope,
    load_credentials,
)

log = logging.getLogger("aimz.analytics.youtube")

REVENUE_METRICS = "estimatedRevenue,estimatedAdRevenue,cpm,playbackBasedCpm"


class YouTubeAnalyticsProvider(AnalyticsProvider):
    name = "YouTubeAnalyticsProvider"
    platform = "youtube"
    is_paid = False

    def __init__(self, enabled: bool, token_file: Path):
        self.enabled = enabled
        self.token_file = token_file

    def health(self) -> HealthStatus:
        if not self.enabled:
            return HealthStatus(True, "YouTube analytics disabled (manual metrics only)")
        try:
            creds = load_credentials(self.token_file, ALL_SCOPES)
        except ProviderUnavailable as exc:
            return HealthStatus(False, str(exc))
        except Exception as exc:
            return HealthStatus(False, f"token error: {exc}", "run `aimz youtube auth`")
        return HealthStatus(
            creds is not None,
            "YouTube analytics token valid" if creds else "no token",
            "run `aimz youtube auth`",
        )

    def _creds(self) -> Any:
        creds = load_credentials(self.token_file, ALL_SCOPES)
        if creds is None:
            raise ProviderUnavailable("YouTube OAuth token missing; run `aimz youtube auth`")
        return creds

    def fetch_metrics(self, ctx: ProviderContext, publication: dict[str, Any]) -> MetricsSnapshot | None:
        if not self.enabled or not publication.get("platform_video_id"):
            return None
        vid = publication["platform_video_id"]
        creds = self._creds()
        snap = MetricsSnapshot()
        with self.authorized(ctx, "youtube_metrics") as rec:
            yt = build_youtube(creds)
            stats = yt.videos().list(part="statistics", id=vid).execute()
            items = stats.get("items") or []
            if items:
                s = items[0].get("statistics", {})
                snap.views = int(s.get("viewCount", 0) or 0)
                snap.likes = int(s.get("likeCount", 0) or 0)
                snap.comments = int(s.get("commentCount", 0) or 0)
                snap.raw["statistics"] = s
            try:
                from googleapiclient.discovery import build

                yta = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
                start = (publication.get("posted_at") or "2020-01-01")[:10]
                rep = (
                    yta.reports()
                    .query(
                        ids="channel==MINE",
                        startDate=start,
                        endDate=date.today().isoformat(),
                        metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained",
                        filters=f"video=={vid}",
                    )
                    .execute()
                )
                rows = rep.get("rows") or []
                if rows:
                    headers = [h["name"] for h in rep.get("columnHeaders", [])]
                    row = dict(zip(headers, rows[0], strict=False))
                    snap.watch_time_minutes = float(row.get("estimatedMinutesWatched", 0) or 0)
                    snap.avg_watch_time_s = float(row.get("averageViewDuration", 0) or 0)
                    snap.avg_percent_viewed = float(row.get("averageViewPercentage", 0) or 0)
                    snap.shares = int(row.get("shares", 0) or 0)
                    snap.subscribers_gained = int(row.get("subscribersGained", 0) or 0)
                    snap.raw["analytics"] = row
                if has_scope(creds, SCOPE_MONETARY):
                    self._fetch_revenue(yta, vid, start, snap)
            except Exception as exc:  # analytics scope may not be granted yet
                log.warning("YouTube Analytics query failed for %s: %s", vid, exc)
                snap.raw["analytics_error"] = str(exc)[:300]
            rec.extra["quota_units"] = 2
        return snap

    @staticmethod
    def _fetch_revenue(yta: Any, vid: str, start: str, snap: MetricsSnapshot) -> None:
        """Lifetime revenue for one video. Not monetized (403 or no rows) leaves ``revenue_usd`` as None."""
        try:
            rep = (
                yta.reports()
                .query(
                    ids="channel==MINE",
                    startDate=start,
                    endDate=date.today().isoformat(),
                    metrics=REVENUE_METRICS,
                    filters=f"video=={vid}",
                )
                .execute()
            )
        except Exception as exc:  # 403 until the channel is in the Partner Program
            log.info("YouTube revenue unavailable for %s: %s", vid, exc)
            snap.raw["revenue_error"] = str(exc)[:300]
            return
        rows = rep.get("rows") or []
        if not rows:
            snap.raw["revenue"] = None
            return
        headers = [h["name"] for h in rep.get("columnHeaders", [])]
        row = dict(zip(headers, rows[0], strict=False))
        snap.raw["revenue"] = row
        if row.get("estimatedRevenue") is not None:
            snap.revenue_usd = float(row["estimatedRevenue"])

    def channel_statistics(self, ctx: ProviderContext) -> dict[str, Any] | None:
        """Subscribers, total views and video count of the authorised channel (1 quota unit)."""
        if not self.enabled:
            return None
        creds = self._creds()
        with self.authorized(ctx, "youtube_channel_stats") as rec:
            resp = build_youtube(creds).channels().list(part="statistics", mine=True).execute()
            rec.extra["quota_units"] = 1
        items = resp.get("items") or []
        return dict(items[0].get("statistics", {})) if items else None

    def fetch_comments(self, ctx: ProviderContext, publication: dict[str, Any]) -> list[FetchedComment]:
        if not self.enabled or not publication.get("platform_video_id"):
            return []
        creds = self._creds()
        out: list[FetchedComment] = []
        with self.authorized(ctx, "youtube_comments"):
            yt = build_youtube(creds)
            try:
                resp = (
                    yt.commentThreads()
                    .list(
                        part="snippet",
                        videoId=publication["platform_video_id"],
                        maxResults=100,
                        textFormat="plainText",
                    )
                    .execute()
                )
            except Exception as exc:  # comments disabled -> 403
                log.info("comments unavailable for %s: %s", publication["platform_video_id"], exc)
                return []
            for item in resp.get("items", []):
                top = item.get("snippet", {}).get("topLevelComment", {})
                sn = top.get("snippet", {})
                out.append(
                    FetchedComment(
                        platform_comment_id=top.get("id", ""),
                        author=sn.get("authorDisplayName", ""),
                        text=sn.get("textDisplay", "") or sn.get("textOriginal", ""),
                        posted_at=sn.get("publishedAt"),
                    )
                )
        return out
