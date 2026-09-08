"""TikTokAnalyticsProvider: read-only Display API metrics for the owner's own public videos. Cost: $0.

``POST /v2/video/query/?fields=...`` (scope ``video.list``) returns view, like, comment and share
counts. ``GET /v2/user/info/?fields=follower_count`` (scope ``user.info.stats``) gives the account's
follower count; ``followers_gained`` is the delta from the previous snapshot of the same publication
(attribution is approximate: it is the account-level change since last check). TikTok exposes no
watch-time, retention or impressions to normal apps; those stay manual.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aimz.db import Database
from aimz.domain.models import MetricsSnapshot
from aimz.providers.analytics.youtube import AnalyticsProvider
from aimz.providers.base import HealthStatus, ProviderContext
from aimz.providers.publishers.tiktok_direct import TikTokClient, TikTokToken

log = logging.getLogger("aimz.analytics.tiktok")


class TikTokAnalyticsProvider(AnalyticsProvider):
    name = "TikTokAnalyticsProvider"
    platform = "tiktok"
    is_paid = False

    def __init__(self, client: TikTokClient, db: Database):
        self.client = client
        self.db = db

    def health(self) -> HealthStatus:
        tok = TikTokToken.load(self.client.token_file)
        if tok is None:
            return HealthStatus(False, "no TikTok token", "run `aimz tiktok auth`")
        return HealthStatus(True, "TikTok Display API analytics ready")

    def _previous_followers(self, publication_id: str) -> int | None:
        row = self.db.one(
            "SELECT raw_json FROM metrics WHERE publication_id=? ORDER BY captured_at DESC LIMIT 1",
            [publication_id],
        )
        if not row or not row["raw_json"]:
            return None
        raw = json.loads(row["raw_json"])
        val = raw.get("follower_count")
        return int(val) if val is not None else None

    def fetch_metrics(self, ctx: ProviderContext, publication: dict[str, Any]) -> MetricsSnapshot | None:
        vid = publication.get("platform_video_id")
        if not vid:
            return None
        with self.authorized(ctx, "tiktok_metrics"):
            videos = self.client.query_videos([str(vid)])
            row = next((v for v in videos if str(v.get("id")) == str(vid)), None)
            if row is None:
                log.info("TikTok video %s not returned (private or removed)", vid)
                return None
            snap = MetricsSnapshot(
                views=int(row.get("view_count") or 0),
                likes=int(row.get("like_count") or 0),
                comments=int(row.get("comment_count") or 0),
                shares=int(row.get("share_count") or 0),
                raw={"video": row},
            )
            try:
                user = self.client.user_info()
                followers = int(user.get("follower_count") or 0)
                snap.raw["follower_count"] = followers
                prev = self._previous_followers(publication["id"])
                if prev is not None:
                    snap.followers_gained = max(0, followers - prev)
            except Exception as exc:  # stats scope may be missing
                log.warning("TikTok user info failed: %s", exc)
        return snap
