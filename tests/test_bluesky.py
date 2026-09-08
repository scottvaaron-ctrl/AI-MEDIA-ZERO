"""Bluesky AT Protocol publishing and analytics against a mock transport (no network, no account)."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from aimz.core.errors import KillSwitchEngaged, OwnerApprovalRequired, PublishError
from aimz.domain.models import MetricsSnapshot
from aimz.providers.analytics.bluesky import BlueskyAnalyticsProvider
from aimz.providers.publishers.bluesky import (
    BlueskyClient,
    BlueskyPublisher,
    BlueskySession,
    aspect_ratio,
    build_post_text,
    build_sources_reply,
    facets_for,
    post_url,
    srt_to_vtt,
)

POST_URI = "at://did:plc:aimz/app.bsky.feed.post/3kabc123"


def _jwt(exp_offset: float) -> str:
    payload = json.dumps({"exp": int(time.time() + exp_offset)}).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"header.{body}.signature"


class FakeBluesky:
    """Records requests and plays the documented AT Protocol responses."""

    def __init__(
        self,
        *,
        can_upload: bool = True,
        states: list[str] | None = None,
        create_record_error: str = "",
        already_exists: bool = False,
    ):
        self.calls: list[tuple[str, str]] = []
        self.uploaded = b""
        self.blobs: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.can_upload = can_upload
        self.states = list(states or ["JOB_STATE_COMPLETED"])
        self.create_record_error = create_record_error
        self.already_exists = already_exists
        self.service_auth_requests: list[dict[str, Any]] = []

    @property
    def blob(self) -> dict[str, Any]:
        return {"$type": "blob", "ref": {"$link": "bafyvideo"}, "mimeType": "video/mp4", "size": 4096}

    def _next_state(self) -> str:
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        nsid = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((request.method, nsid))
        params = dict(request.url.params)

        if nsid == "com.atproto.server.createSession":
            body = json.loads(request.content)
            assert body["identifier"] and body["password"]
            return httpx.Response(
                200,
                json={
                    "accessJwt": _jwt(3600),
                    "refreshJwt": _jwt(60 * 86400),
                    "did": "did:plc:aimz",
                    "handle": "aimz.bsky.social",
                    "didDoc": {
                        "service": [
                            {
                                "id": "#atproto_pds",
                                "type": "AtprotoPersonalDataServer",
                                "serviceEndpoint": "https://pds.example",
                            }
                        ]
                    },
                },
            )
        if nsid == "com.atproto.server.refreshSession":
            assert request.headers["Authorization"].startswith("Bearer ")
            return httpx.Response(
                200,
                json={
                    "accessJwt": _jwt(3600),
                    "refreshJwt": _jwt(60 * 86400),
                    "did": "did:plc:aimz",
                    "handle": "aimz.bsky.social",
                },
            )
        if nsid == "com.atproto.server.getServiceAuth":
            self.service_auth_requests.append(params)
            return httpx.Response(200, json={"token": "service-auth-token"})
        if nsid == "app.bsky.video.getUploadLimits":
            if not self.can_upload:
                return httpx.Response(200, json={"canUpload": False, "message": "daily video limit reached"})
            return httpx.Response(
                200, json={"canUpload": True, "remainingDailyVideos": 4, "remainingDailyBytes": 10**9}
            )
        if nsid == "app.bsky.video.uploadVideo":
            assert request.url.host == "video.bsky.app"
            assert params["did"] == "did:plc:aimz" and params["name"].endswith(".mp4")
            assert request.headers["Authorization"] == "Bearer service-auth-token"
            self.uploaded += request.content
            # The live service answers uploadVideo with the job's fields FLAT (not wrapped in
            # "jobStatus" as the lexicon declares) and never includes the blob. Verified
            # 2026-09-08; a mock that wrapped this is what let the shape bug ship.
            if self.already_exists:
                return httpx.Response(
                    409,
                    json={
                        "did": "did:plc:aimz",
                        "jobId": "job-1",
                        "state": "JOB_STATE_COMPLETED",
                        "error": "already_exists",
                        "message": "Video already processed",
                    },
                )
            return httpx.Response(
                200, json={"did": "did:plc:aimz", "jobId": "job-1", "state": "JOB_STATE_CREATED"}
            )
        if nsid == "app.bsky.video.getJobStatus":
            # getJobStatus *is* wrapped, and is the only place the blob appears.
            state = self._next_state()
            job: dict[str, Any] = {"jobId": params["jobId"], "did": "did:plc:aimz", "state": state}
            if state == "JOB_STATE_COMPLETED":
                job["blob"] = self.blob
                job["progress"] = 100
            if state == "JOB_STATE_FAILED":
                job["failureCode"] = "validation_failure"
                job["error"] = "unsupported codec"
            return httpx.Response(200, json={"jobStatus": job})
        if nsid == "com.atproto.repo.uploadBlob":
            self.blobs.append(request.headers["Content-Type"])
            return httpx.Response(
                200,
                json={"blob": {"$type": "blob", "ref": {"$link": "bafycaptions"}, "mimeType": "text/vtt"}},
            )
        if nsid == "com.atproto.repo.createRecord":
            if self.create_record_error:
                return httpx.Response(400, json={"error": self.create_record_error})
            body = json.loads(request.content)
            self.records.append(body["record"])
            suffix = "" if len(self.records) == 1 else f"-reply{len(self.records)}"
            return httpx.Response(200, json={"uri": POST_URI + suffix, "cid": "bafycid"})
        if nsid == "app.bsky.feed.getPosts":
            return httpx.Response(
                200,
                json={
                    "posts": [
                        {
                            "uri": POST_URI,
                            "cid": "bafycid",
                            "likeCount": 42,
                            "repostCount": 5,
                            "quoteCount": 2,
                            "replyCount": 3,
                            "bookmarkCount": 1,
                            "indexedAt": "2026-09-08T12:00:00Z",
                        }
                    ]
                },
            )
        if nsid == "app.bsky.actor.getProfile":
            return httpx.Response(200, json={"handle": "aimz.bsky.social", "followersCount": 120})
        if nsid == "app.bsky.feed.getPostThread":
            return httpx.Response(
                200,
                json={
                    "thread": {
                        "post": {"uri": POST_URI},
                        "replies": [
                            {
                                "post": {
                                    "uri": POST_URI + "/reply",
                                    "author": {"handle": "reader.bsky.social"},
                                    "record": {
                                        "text": "Where is the beetle from?",
                                        "createdAt": "2026-09-08",
                                    },
                                }
                            },
                            {"post": {"uri": "x", "author": {"handle": "b"}, "record": {"text": "   "}}},
                        ],
                    }
                },
            )
        return httpx.Response(404, json={"error": "NotFound", "message": nsid})


def _client(tmp_path: Path, fake: FakeBluesky) -> BlueskyClient:
    return BlueskyClient(
        "aimz.bsky.social",
        "app-pass-word",
        "https://bsky.social",
        tmp_path / "bluesky_session.json",
        http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
    )


def _video(svc, size: int = 4096) -> tuple[dict, dict, dict]:  # noqa: ANN001
    from aimz.util import new_id, now_iso

    mp4 = svc.env.data_dir / "bsky.mp4"
    mp4.write_bytes(b"\x02" * size)
    video = {
        "id": new_id("vid"),
        "title": "Islands of New Zealand: the huhu beetle",
        "file_path": str(mp4),
        "duration_s": 37.0,
        "resolution": "1080x1920",
        "created_at": now_iso(),
    }
    script = {"id": new_id("scr")}
    meta = {
        "title": "Islands of New Zealand: the huhu beetle",
        "tags": ["natural history", "new zealand"],
        "sources": [{"title": "Te Papa", "url": "https://example.org/huhu"}],
        "attributions": [],
        "ai_disclosure": "AI narration",
        "owner_approved": True,
    }
    return video, script, meta


# --------------------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------------------
def test_post_text_fits_the_grapheme_limit() -> None:
    text = build_post_text("A" * 400, ["natural history", "bugs"], "AI-narrated, sourced explainer.")
    from aimz.providers.publishers.captions import grapheme_len

    assert grapheme_len(text) <= 300
    assert text.endswith("#NaturalHistory #Bugs")
    assert "AI-narrated" in text  # the disclosure is never the part that gets trimmed


def test_post_text_also_respects_the_byte_limit() -> None:
    """A title of multi-code-point emoji fits 300 graphemes but can blow the 3000-byte cap."""
    from aimz.providers.publishers.captions import grapheme_len

    family = "\U0001f468‍\U0001f469‍\U0001f467"  # ~25 bytes, one grapheme
    text = build_post_text(family * 200, [], "")
    assert grapheme_len(text) <= 300
    assert len(text.encode("utf-8")) <= 3000


def test_facets_index_utf8_bytes_not_characters() -> None:
    text = "Café story #Bugs https://example.org/x"
    facets = facets_for(text)
    encoded = text.encode("utf-8")
    for facet in facets:
        span = encoded[facet["index"]["byteStart"] : facet["index"]["byteEnd"]].decode("utf-8")
        feature = facet["features"][0]
        if feature["$type"].endswith("#tag"):
            assert span == "#Bugs" and feature["tag"] == "Bugs"
        else:
            assert span == feature["uri"] == "https://example.org/x"
    assert len(facets) == 2


def test_srt_to_vtt_and_aspect_ratio_and_post_url() -> None:
    vtt = srt_to_vtt("1\n00:00:01,000 --> 00:00:03,500\nHello\n")
    assert vtt.startswith("WEBVTT") and "00:00:01.000 --> 00:00:03.500" in vtt
    assert aspect_ratio({"resolution": "1080x1920"}) == {"width": 1080, "height": 1920}
    assert aspect_ratio({"resolution": "unknown"}) is None
    assert post_url("aimz.bsky.social", POST_URI) == "https://bsky.app/profile/aimz.bsky.social/post/3kabc123"


def test_sources_reply_dedupes_and_can_be_empty() -> None:
    sources = [{"url": "https://a.example"}, {"url": "https://a.example"}, {"url": "https://b.example"}]
    assert build_sources_reply(sources) == "Sources:\nhttps://a.example\nhttps://b.example"
    assert build_sources_reply([{"title": "no url"}]) == ""


# --------------------------------------------------------------------------------------
# publishing
# --------------------------------------------------------------------------------------
def test_publish_happy_path(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky()
    pub = BlueskyPublisher(_client(tmp_path, fake))
    video, script, meta = _video(svc)
    package = svc.env.data_dir / "pkg"
    res = pub.publish(svc.ctx(), video, script, meta, package)

    assert res.status == "published" and res.platform_video_id == POST_URI
    assert res.url == "https://bsky.app/profile/aimz.bsky.social/post/3kabc123"
    assert fake.uploaded == b"\x02" * 4096

    record = fake.records[0]
    assert record["$type"] == "app.bsky.feed.post"
    assert record["embed"]["$type"] == "app.bsky.embed.video"
    assert record["embed"]["video"] == fake.blob
    assert record["embed"]["aspectRatio"] == {"width": 1080, "height": 1920}
    assert record["embed"]["alt"]
    assert record["langs"] == ["en"]
    # the sources self-reply is threaded under the post
    assert fake.records[1]["reply"]["parent"]["uri"] == POST_URI
    assert "https://example.org/huhu" in fake.records[1]["text"]

    saved = json.loads((package / "bluesky_request.json").read_text(encoding="utf-8"))
    assert saved["job_id"] == "job-1" and saved["record"]["text"] == record["text"]
    assert svc.db.count("provider_usage", "provider='BlueskyPublisher'") == 1

    # the upload token is scoped to the PDS from the DID document, bound to uploadBlob
    upload_auth = next(p for p in fake.service_auth_requests if p["lxm"] == "com.atproto.repo.uploadBlob")
    assert upload_auth["aud"] == "did:web:pds.example"


def test_upload_video_returns_the_job_flat_and_the_blob_comes_from_get_job_status(
    svc,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """Regression: uploadVideo is NOT wrapped in `jobStatus` and never carries the blob.

    The first live post failed with "returned no jobId" because the code trusted the lexicon's
    declared shape for both endpoints. Only getJobStatus matches it.
    """
    fake = FakeBluesky()
    client = _client(tmp_path, fake)
    mp4 = svc.env.data_dir / "x.mp4"
    mp4.write_bytes(b"\x03" * 32)

    job = client.upload_video(mp4, "x.mp4")
    assert job["jobId"] == "job-1"
    assert job["state"] == "JOB_STATE_CREATED"
    assert "blob" not in job

    resolved = client.job_status("job-1")
    assert resolved["state"] == "JOB_STATE_COMPLETED" and resolved["blob"] == fake.blob


def test_already_exists_409_is_reused_rather_than_failing(svc, tmp_path: Path) -> None:  # noqa: ANN001
    """A file uploaded on an earlier attempt answers 409 with the job; reuse it, do not fail."""
    fake = FakeBluesky(already_exists=True)
    pub = BlueskyPublisher(_client(tmp_path, fake))
    video, script, meta = _video(svc)
    res = pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert res.status == "published" and res.platform_video_id == POST_URI
    # completed-without-blob still has to resolve the blob through getJobStatus
    assert ("GET", "app.bsky.video.getJobStatus") in fake.calls
    assert fake.records[0]["embed"]["video"] == fake.blob


def test_captions_are_attached_when_present(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky()
    pub = BlueskyPublisher(_client(tmp_path, fake))
    video, script, meta = _video(svc)
    srt = svc.env.data_dir / "captions.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,500\nHello\n", encoding="utf-8")
    video["captions_path"] = str(srt)
    res = pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert res.status == "published"
    assert fake.blobs == ["text/vtt"]
    assert fake.records[0]["embed"]["captions"][0]["lang"] == "en"


def test_publish_requires_owner_approval(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky()
    pub = BlueskyPublisher(_client(tmp_path, fake))
    video, script, meta = _video(svc)
    with pytest.raises(OwnerApprovalRequired):
        pub.publish(svc.ctx(), video, script, {**meta, "owner_approved": False}, svc.env.data_dir / "pkg")
    assert fake.uploaded == b""


def test_kill_switch_blocks_before_any_request(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky()
    pub = BlueskyPublisher(_client(tmp_path, fake))
    video, script, meta = _video(svc)
    svc.killswitch.engage("test")
    with pytest.raises(KillSwitchEngaged):
        pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert fake.calls == []


def test_daily_quota_block_costs_no_upload(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky(can_upload=False)
    pub = BlueskyPublisher(_client(tmp_path, fake))
    video, script, meta = _video(svc)
    with pytest.raises(PublishError, match="daily video limit"):
        pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert fake.uploaded == b"" and fake.records == []


def test_still_encoding_then_poll_completes(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky(states=["JOB_STATE_COMPLETED"])
    pub = BlueskyPublisher(_client(tmp_path, fake), poll_seconds=0)
    video, script, meta = _video(svc)
    package = svc.env.data_dir / "pkg"
    res = pub.publish(svc.ctx(), video, script, meta, package)
    # poll_seconds=0 means the cycle does not wait for encoding at all
    assert res.status == "uploading" and res.publish_id == "job-1" and fake.records == []

    done = pub.poll(svc.ctx(), {"package_dir": str(package), "metadata_json": "{}"})
    assert done is not None and done.status == "published" and done.platform_video_id == POST_URI
    assert fake.records[0]["embed"]["video"] == fake.blob


def test_processing_failure_is_reported_not_retried(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky(states=["JOB_STATE_FAILED"])
    pub = BlueskyPublisher(_client(tmp_path, fake), poll_seconds=0)
    video, script, meta = _video(svc)
    package = svc.env.data_dir / "pkg"
    assert pub.publish(svc.ctx(), video, script, meta, package).status == "uploading"

    failed = pub.poll(svc.ctx(), {"package_dir": str(package), "metadata_json": "{}"})
    assert failed is not None and failed.status == "failed" and "unsupported codec" in failed.message
    assert sum(1 for _m, nsid in fake.calls if nsid == "app.bsky.video.uploadVideo") == 1
    assert fake.records == []


def test_failed_sources_reply_does_not_lose_the_post(svc, tmp_path: Path) -> None:  # noqa: ANN001
    class ReplyFails(FakeBluesky):
        def handler(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("com.atproto.repo.createRecord") and self.records:
                self.calls.append((request.method, "com.atproto.repo.createRecord"))
                return httpx.Response(400, json={"error": "InvalidRequest", "message": "rate limited"})
            return super().handler(request)

    fake = ReplyFails()
    pub = BlueskyPublisher(_client(tmp_path, fake))
    video, script, meta = _video(svc)
    res = pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")
    assert res.status == "published" and "sources reply failed" in res.message


def test_create_record_failure_raises_publish_error(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky(create_record_error="InvalidRequest")
    pub = BlueskyPublisher(_client(tmp_path, fake))
    video, script, meta = _video(svc)
    with pytest.raises(Exception, match="createRecord"):
        pub.publish(svc.ctx(), video, script, meta, svc.env.data_dir / "pkg")


# --------------------------------------------------------------------------------------
# session handling
# --------------------------------------------------------------------------------------
def test_expired_access_token_is_refreshed(tmp_path: Path) -> None:
    fake = FakeBluesky()
    client = _client(tmp_path, fake)
    BlueskySession(
        access_jwt="old",
        refresh_jwt="rt",
        did="did:plc:aimz",
        handle="aimz.bsky.social",
        pds_url="https://pds.example",
        access_expires_at=time.time() - 10,
        refresh_expires_at=time.time() + 10**6,
    ).save(client.session_file)

    session = client.session()
    assert session.access_jwt != "old"
    assert ("POST", "com.atproto.server.refreshSession") in fake.calls
    assert BlueskySession.load(client.session_file).access_jwt == session.access_jwt


def test_dead_refresh_token_signs_in_again_with_the_app_password(tmp_path: Path) -> None:
    class RefreshRejected(FakeBluesky):
        def handler(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("com.atproto.server.refreshSession"):
                self.calls.append((request.method, "com.atproto.server.refreshSession"))
                return httpx.Response(400, json={"error": "ExpiredToken"})
            return super().handler(request)

    fake = RefreshRejected()
    client = _client(tmp_path, fake)
    BlueskySession(
        access_jwt="old",
        refresh_jwt="dead",
        did="did:plc:aimz",
        handle="aimz.bsky.social",
        pds_url="https://pds.example",
        access_expires_at=time.time() - 10,
        refresh_expires_at=time.time() - 5,
    ).save(client.session_file)

    session = client.session()
    assert session.handle == "aimz.bsky.social"
    assert ("POST", "com.atproto.server.createSession") in fake.calls


# --------------------------------------------------------------------------------------
# analytics
# --------------------------------------------------------------------------------------
def _publication(svc, uri: str = POST_URI) -> dict:  # noqa: ANN001
    from aimz.util import new_id, now_iso

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
        "platform": "bluesky",
        "publisher": "BlueskyPublisher",
        "mode": "api",
        "idempotency_key": "k",
        "status": "published",
        "platform_video_id": uri,
        "posted_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    svc.db.insert("publications", publication)
    return publication


def test_metrics_combine_reposts_and_quotes_and_track_followers(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky()
    provider = BlueskyAnalyticsProvider(_client(tmp_path, fake), svc.db)
    publication = _publication(svc)

    snap = provider.fetch_metrics(svc.ctx(), publication)
    assert isinstance(snap, MetricsSnapshot)
    assert snap.likes == 42 and snap.comments == 3
    assert snap.shares == 7  # 5 reposts + 2 quotes
    assert snap.views is None  # Bluesky publishes no view count to anyone
    assert snap.followers_gained is None  # no earlier snapshot to compare against

    svc.analytics_store.record(publication, snap, source="api")
    again = provider.fetch_metrics(svc.ctx(), publication)
    assert again.followers_gained == 0
    assert svc.db.count("metrics") == 1


def test_metrics_none_for_unpublished_or_missing_post(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky()
    provider = BlueskyAnalyticsProvider(_client(tmp_path, fake), svc.db)
    assert provider.fetch_metrics(svc.ctx(), {"id": "p", "platform_video_id": None}) is None
    other = _publication(svc, uri="at://did:plc:aimz/app.bsky.feed.post/gone")
    assert provider.fetch_metrics(svc.ctx(), other) is None


def test_comments_come_back_as_fetched_comments(svc, tmp_path: Path) -> None:  # noqa: ANN001
    fake = FakeBluesky()
    provider = BlueskyAnalyticsProvider(_client(tmp_path, fake), svc.db)
    comments = provider.fetch_comments(svc.ctx(), _publication(svc))
    assert len(comments) == 1  # the blank reply is dropped
    assert comments[0].author == "reader.bsky.social"
    assert comments[0].text == "Where is the beetle from?"
