"""BlueskyAnalyticsProvider: read-only engagement counts from the public AT Protocol views. Cost: $0.

``app.bsky.feed.getPosts`` returns the post view for our own AT-URI with ``likeCount``,
``repostCount``, ``replyCount``, ``quoteCount`` and ``bookmarkCount``.
``app.bsky.actor.getProfile`` gives ``followersCount``, and ``followers_gained`` is the delta
from the previous snapshot of the same publication (account-level, so attribution is
approximate, exactly as for TikTok). ``app.bsky.feed.getPostThread`` supplies the replies the
comment agent reads.

Bluesky publishes **no view, impression or watch-time counts** to anyone, including the author,
so ``views``, ``avg_percent_viewed`` and friends stay empty for this platform. The learning loop
scores each platform separately, so a platform with fewer metrics simply contributes fewer
signals rather than distorting the others.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aimz.db import Database
from aimz.domain.models import FetchedComment, MetricsSnapshot
from aimz.providers.base import AnalyticsProvider, HealthStatus, ProviderContext
from aimz.providers.publishers.bluesky import BlueskyClient, BlueskySession

log = logging.getLogger("aimz.analytics.bluesky")


class BlueskyAnalyticsProvider(AnalyticsProvider):
    name = "BlueskyAnalyticsProvider"
    platform = "bluesky"
    is_paid = False

    def __init__(self, client: BlueskyClient, db: Database):
        self.client = client
        self.db = db

    def health(self) -> HealthStatus:
        if not self.client.handle or not self.client.app_password:
            return HealthStatus(
                False, "BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not set", "see docs/AUTONOMOUS_SETUP.md"
            )
        session = BlueskySession.load(self.client.session_file)
        detail = f"Bluesky analytics ready ({session.handle})" if session else "Bluesky analytics ready"
        return HealthStatus(True, detail)

    def _previous_followers(self, publication_id: str) -> int | None:
        row = self.db.one(
            "SELECT raw_json FROM metrics WHERE publication_id=? ORDER BY captured_at DESC LIMIT 1",
            [publication_id],
        )
        if not row or not row["raw_json"]:
            return None
        value = json.loads(row["raw_json"]).get("followers_count")
        return int(value) if value is not None else None

    def fetch_metrics(self, ctx: ProviderContext, publication: dict[str, Any]) -> MetricsSnapshot | None:
        uri = publication.get("platform_video_id")  # the post's at:// URI
        if not uri:
            return None
        with self.authorized(ctx, "bluesky_metrics"):
            posts = self.client.get_posts([str(uri)])
            post = next((p for p in posts if str(p.get("uri")) == str(uri)), None)
            if post is None:
                log.info("Bluesky post %s not returned (deleted or not yet indexed)", uri)
                return None
            reposts = int(post.get("repostCount") or 0)
            quotes = int(post.get("quoteCount") or 0)
            snap = MetricsSnapshot(
                likes=int(post.get("likeCount") or 0),
                comments=int(post.get("replyCount") or 0),
                # Bluesky splits sharing into reposts and quote posts; the learning loop wants
                # one "somebody passed this on" number, and both are kept in raw.
                shares=reposts + quotes,
                raw={
                    "repost_count": reposts,
                    "quote_count": quotes,
                    "bookmark_count": int(post.get("bookmarkCount") or 0),
                    "indexed_at": post.get("indexedAt"),
                },
            )
            try:
                profile = self.client.get_profile()
                followers = int(profile.get("followersCount") or 0)
                snap.raw["followers_count"] = followers
                previous = self._previous_followers(publication["id"])
                if previous is not None:
                    snap.followers_gained = max(0, followers - previous)
            except Exception as exc:  # a profile read must not lose the engagement snapshot
                log.warning("Bluesky profile read failed: %s", exc)
        return snap

    def fetch_comments(self, ctx: ProviderContext, publication: dict[str, Any]) -> list[FetchedComment]:
        uri = publication.get("platform_video_id")
        if not uri:
            return []
        out: list[FetchedComment] = []
        with self.authorized(ctx, "bluesky_comments"):
            try:
                thread = self.client.get_post_thread(str(uri))
            except Exception as exc:  # blocked or deleted thread
                log.info("Bluesky thread unavailable for %s: %s", uri, exc)
                return []
            for reply in thread.get("replies") or []:
                post = reply.get("post") or {}
                record = post.get("record") or {}
                text = str(record.get("text") or "").strip()
                if not text:
                    continue
                out.append(
                    FetchedComment(
                        platform_comment_id=str(post.get("uri") or ""),
                        author=str((post.get("author") or {}).get("handle") or ""),
                        text=text,
                        posted_at=record.get("createdAt") or post.get("indexedAt"),
                    )
                )
        return out
