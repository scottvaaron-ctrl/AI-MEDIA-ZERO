"""TikTok Direct Post + Display API against a mock transport (the live API needs an audited app)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from aimz.core.errors import OwnerApprovalRequired, PublishError
from aimz.domain.models import MetricsSnapshot
from aimz.providers.analytics.tiktok import TikTokAnalyticsProvider
from aimz.providers.publishers.tiktok_direct import (
    TikTokClient,
    TikTokDirectPostPublisher,
    TikTokToken,
    chunk_plan,
)


class FakeTikTok:
    """Records requests and plays the documented responses."""

    def __init__(self, privacy_options=("SELF_ONLY", "PUBLIC_TO_EVERYONE"), complete_after=1, fail=False):
        self.calls: list[tuple[str, str]] = []
        self.uploaded = b""
        self.status_calls = 0
        self.privacy_options = list(privacy_options)
        self.complete_after = complete_after
        self.fail = fail

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/oauth/token/"):
            return httpx.Response(
                200,
                json={
                    "access_token": "at2",
                    "refresh_token": "rt2",
                    "expires_in": 86400,
                    "refresh_expires_in": 31536000,
                    "open_id": "o",
                    "scope": "video.publish",
                },
            )
        if path.endswith("/creator_info/query/"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "creator_username": "aimz",
                        "creator_nickname": "AIMZ",
                        "privacy_level_options": self.privacy_options,
                        "comment_disabled": False,
                        "duet_disabled": False,
                        "stitch_disabled": False,
                        "max_video_post_duration_sec": 600,
                    },
                    "error": {"code": "ok", "message": ""},
                },
            )
        if path.endswith("/publish/video/init/"):
            body = json.loads(request.content)
            assert body["source_info"]["source"] == "FILE_UPLOAD"
            assert body["post_info"]["is_aigc"] is True
            self.init_body = body
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "v_pub_file~1",
                        "upload_url": "https://open-upload.tiktokapis.com/video/?upload_id=1",
                    },
                    "error": {"code": "ok"},
                },
            )
        if request.url.host == "open-upload.tiktokapis.com":
            assert request.headers["Content-Range"].startswith("bytes 0-")
            self.uploaded += request.content
            return httpx.Response(201)
        if path.endswith("/publish/status/fetch/"):
            self.status_calls += 1
            if self.fail:
                return httpx.Response(
                    200,
                    json={
                        "data": {"status": "FAILED", "fail_reason": "spam_risk_text"},
                        "error": {"code": "ok"},
                    },
                )
            if self.status_calls >= self.complete_after:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "status": "PUBLISH_COMPLETE",
                            "publicaly_available_post_id": [7351234567890123456],
                        },
                        "error": {"code": "ok"},
                    },
                )
            return httpx.Response(
                200, json={"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}}
            )
        if path.endswith("/video/query/"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "videos": [
                            {
                                "id": "7351234567890123456",
                                "view_count": 1200,
                                "like_count": 80,
                                "comment_count": 7,
                                "share_count": 9,
                            }
                        ]
                    },
                    "error": {"code": "ok"},
                },
            )
        if path.endswith("/user/info/"):
            return httpx.Response(
                200,
                json={"data": {"user": {"username": "aimz", "follower_count": 150}}, "error": {"code": "ok"}},
            )
        return httpx.Response(404, json={"error": {"code": "not_found"}})


def _client(tmp_path: Path, fake: FakeTikTok) -> TikTokClient:
    tok_file = tmp_path / "tiktok_token.json"
    TikTokToken("at", "rt", time.time() + 3600, time.time() + 1e6, "o", "video.publish").save(tok_file)
    return TikTokClient(
        "key",
        "secret",
        "https://example.com/cb",
        tok_file,
        http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )


def _video(svc, size: int = 4096) -> tuple[dict, dict, dict]:  # noqa: ANN001
    from aimz.util import new_id, now_iso

    mp4 = svc.env.data_dir / "tt.mp4"
    mp4.write_bytes(b"\x01" * size)
    video = {
        "id": new_id("vid"),
        "title": "Huhu beetle",
        "file_path": str(mp4),
        "duration_s": 37.0,
        "resolution": "1080x1920",
        "created_at": now_iso(),
    }
    script = {"id": new_id("scr")}
    meta = {
        "title": "Huhu beetle",
        "tags": ["history"],
        "sources": [],
        "attributions": [],
        "ai_disclosure": "AI",
        "owner_approved": True,
    }
    return video, script, meta


def test_chunk_plan() -> None:
    assert chunk_plan(4_000_000) == (4_000_000, 1)
    assert chunk_plan(60 * 1024 * 1024) == (60 * 1024 * 1024, 1)
    assert chunk_plan(150 * 1024 * 1024) == (64 * 1024 * 1024, 2)


def test_direct_post_happy_path(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeTikTok()
    pub = TikTokDirectPostPublisher(_client(tmp_path, fake), privacy_level="SELF_ONLY", poll_seconds=30)
    video, script, meta = _video(svc)
    res = pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert res.status == "published" and res.platform_video_id == "7351234567890123456"
    assert res.url == "https://www.tiktok.com/@aimz/video/7351234567890123456"
    assert fake.uploaded == b"\x01" * 4096
    assert fake.init_body["post_info"]["privacy_level"] == "SELF_ONLY"
    assert (svc.env.data_dir / "pkg" / "tiktok_request.json").exists()
    assert svc.db.count("provider_usage", "provider='TikTokDirectPostPublisher'") == 1


def test_direct_post_requires_consent_and_valid_privacy(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeTikTok(privacy_options=("SELF_ONLY",))
    pub = TikTokDirectPostPublisher(_client(tmp_path, fake), privacy_level="PUBLIC_TO_EVERYONE")
    video, script, meta = _video(svc)
    with pytest.raises(OwnerApprovalRequired):
        pub.publish(svc.ctx(), video, script, {**meta, "owner_approved": False}, svc.env.data_dir / "pkg")
    with pytest.raises(PublishError):  # unaudited creator: SELF_ONLY only
        pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert fake.uploaded == b""  # nothing was sent


def test_direct_post_kill_switch_blocks_before_any_request(svc, tmp_path: Path) -> None:  # noqa: ANN001
    from aimz.core.errors import KillSwitchEngaged

    fake = FakeTikTok()
    pub = TikTokDirectPostPublisher(_client(tmp_path, fake), privacy_level="SELF_ONLY")
    video, script, meta = _video(svc)
    svc.killswitch.engage("test")
    with pytest.raises(KillSwitchEngaged):
        pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert fake.calls == []


def test_processing_then_poll_completes(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeTikTok(complete_after=2)
    pub = TikTokDirectPostPublisher(_client(tmp_path, fake), privacy_level="SELF_ONLY", poll_seconds=0)
    video, script, meta = _video(svc)
    res = pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert res.status == "uploading" and res.publish_id == "v_pub_file~1"
    done = pub.poll(
        svc.ctx(), {"metadata_json": json.dumps({"publish_id": "v_pub_file~1"}), "privacy": "SELF_ONLY"}
    )
    assert done is not None and done.status == "published" and done.platform_video_id


def test_failed_post_is_reported_not_retried(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeTikTok(fail=True)
    pub = TikTokDirectPostPublisher(_client(tmp_path, fake), privacy_level="SELF_ONLY")
    video, script, meta = _video(svc)
    with pytest.raises(PublishError, match="spam_risk_text"):
        pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert sum(1 for m, p in fake.calls if p.endswith("/video/init/")) == 1


def test_token_refresh_when_expired(tmp_path: Path) -> None:
    fake = FakeTikTok()
    client = _client(tmp_path, fake)
    TikTokToken("old", "rt", time.time() - 10, time.time() + 1e6).save(client.token_file)
    client._token = None
    assert client.token().access_token == "at2"
    assert TikTokToken.load(client.token_file).access_token == "at2"


def test_requested_fields_stay_within_the_requested_scopes(tmp_path: Path) -> None:
    """Every field we ask for must be covered by SCOPES, or the live call fails at runtime.

    `username` needs `user.info.profile`, which we deliberately do not request: the post URL is
    built from creator_info's `creator_username` instead, which posting already pays for.
    """
    from aimz.providers.publishers.tiktok_direct import SCOPES

    assert "user.info.profile" not in SCOPES

    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return FakeTikTok().handler(request)

    client = TikTokClient(
        "key",
        "secret",
        "https://example.com/cb",
        tmp_path / "tok.json",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    TikTokToken("at", "rt", time.time() + 3600, time.time() + 1e6).save(client.token_file)
    client.user_info()
    user_info_url = next(u for u in captured if "/user/info/" in u)
    assert "username" not in user_info_url, f"asks for a user.info.profile field: {user_info_url}"


def test_poll_builds_the_url_without_the_profile_scope(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeTikTok(complete_after=2)
    pub = TikTokDirectPostPublisher(_client(tmp_path, fake), privacy_level="SELF_ONLY", poll_seconds=0)
    video, script, meta = _video(svc)
    pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    done = pub.poll(svc.ctx(), {"metadata_json": json.dumps({"publish_id": "v_pub_file~1"})})
    assert done is not None and done.url == "https://www.tiktok.com/@aimz/video/7351234567890123456"
    # the username came from creator_info, not from the profile-scoped user_info endpoint
    assert any(p.endswith("/creator_info/query/") for _m, p in fake.calls)


def test_display_api_metrics_and_follower_delta(svc, tmp_path: Path) -> None:  # noqa: ANN001
    from aimz.util import new_id, now_iso

    fake = FakeTikTok()
    prov = TikTokAnalyticsProvider(_client(tmp_path, fake), svc.db)
    vid_id, pub_id = new_id("vid"), new_id("pub")
    svc.db.insert(
        "ideas",
        {
            "id": "i",
            "title": "t",
            "premise": "p",
            "hook": "h",
            "content_family": "x",
            "target_platform": "both",
            "source_item_ids_json": "[]",
            "scores_json": "{}",
            "opportunity_score": 1,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    svc.db.insert(
        "scripts",
        {
            "id": "s",
            "idea_id": "i",
            "title": "t",
            "hook_line": "h",
            "beats_json": "[]",
            "narration_text": "x",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    svc.db.insert(
        "videos",
        {
            "id": vid_id,
            "script_id": "s",
            "idea_id": "i",
            "title": "t",
            "format": "short",
            "resolution": "x",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    publication = {
        "id": pub_id,
        "video_id": vid_id,
        "platform": "tiktok",
        "publisher": "x",
        "mode": "direct",
        "idempotency_key": "k",
        "status": "published",
        "platform_video_id": "7351234567890123456",
        "posted_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    svc.db.insert("publications", publication)
    snap = prov.fetch_metrics(svc.ctx(), publication)
    assert (
        isinstance(snap, MetricsSnapshot)
        and snap.views == 1200
        and snap.shares == 9
        and snap.followers_gained is None
    )
    svc.analytics_store.record(publication, snap, source="api")
    snap2 = prov.fetch_metrics(svc.ctx(), publication)
    assert snap2.followers_gained == 0  # same follower count as the stored snapshot
    assert svc.db.count("metrics") == 1
