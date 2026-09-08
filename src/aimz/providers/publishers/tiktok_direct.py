"""TikTokDirectPostPublisher: official Content Posting API, Direct Post with FILE_UPLOAD. Cost: $0.

Implemented against the developers.tiktok.com reference (verified 2026-09-07):

* OAuth: ``https://www.tiktok.com/v2/auth/authorize/`` -> code -> ``POST /v2/oauth/token/``
  (form-encoded; access tokens last 24 h, refresh tokens 365 d).
* ``POST /v2/post/publish/creator_info/query/`` -> ``privacy_level_options``,
  ``max_video_post_duration_sec``, nickname/username.
* ``POST /v2/post/publish/video/init/`` with ``post_info`` (title, privacy_level, is_aigc, ...) and
  ``source_info`` (``FILE_UPLOAD``, video_size, chunk_size, total_chunk_count) -> ``publish_id`` + ``upload_url``.
* ``PUT upload_url`` with ``Content-Range: bytes a-b/total`` per chunk (5-64 MB, last up to 128 MB;
  files under 5 MB go as one chunk).
* ``POST /v2/post/publish/status/fetch/`` -> PROCESSING_UPLOAD | PROCESSING_DOWNLOAD |
  SEND_TO_USER_INBOX | PUBLISH_COMPLETE | FAILED, and ``publicaly_available_post_id``.

Platform constraints honoured in code: unaudited apps may only post ``SELF_ONLY``; the privacy level
must be one of the creator's ``privacy_level_options``; the video must be shorter than the creator's
``max_video_post_duration_sec``; ``is_aigc`` is set; the kill switch and owner consent are checked
before any byte is sent; a failed post is never retried automatically.

Status: implemented to spec and unit-tested with a mock transport. It cannot be exercised against
the live API until the owner registers a TikTok developer app (see docs/AUTONOMOUS_SETUP.md).
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from aimz.core.errors import OwnerApprovalRequired, ProviderError, ProviderUnavailable, PublishError
from aimz.domain.models import PublishResult
from aimz.providers.base import HealthStatus, ProviderContext, Publisher
from aimz.providers.publishers.packages import write_common_package
from aimz.providers.publishers.tiktok import build_caption
from aimz.util import now_iso

log = logging.getLogger("aimz.publish.tiktok_direct")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
SCOPES = ["user.info.basic", "user.info.stats", "video.publish", "video.upload", "video.list"]
PRIVACY_LEVELS = {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024


def chunk_plan(size: int) -> tuple[int, int]:
    """Return (chunk_size, total_chunk_count) per TikTok's media transfer rules."""
    if size <= MAX_CHUNK:
        return size, 1
    return MAX_CHUNK, size // MAX_CHUNK


@dataclass
class TikTokToken:
    access_token: str
    refresh_token: str
    expires_at: float
    refresh_expires_at: float
    open_id: str = ""
    scope: str = ""

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> TikTokToken:
        now = time.time()
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            expires_at=now + float(data.get("expires_in", 86400)),
            refresh_expires_at=now + float(data.get("refresh_expires_in", 365 * 86400)),
            open_id=data.get("open_id", ""),
            scope=data.get("scope", ""),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> TikTokToken | None:
        if not path.exists():
            return None
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            return None


class TikTokClient:
    """Thin HTTP client for Login Kit + Content Posting + Display API. Owner credentials never leave ``.env``."""

    def __init__(
        self,
        client_key: str,
        client_secret: str,
        redirect_uri: str,
        token_file: Path,
        http: httpx.Client | None = None,
    ):
        self.client_key = client_key
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_file = token_file
        self.http = http or httpx.Client(timeout=60)
        self._token: TikTokToken | None = None

    # -- auth ---------------------------------------------------------------------------
    def authorize_url(self, state: str | None = None) -> tuple[str, str]:
        state = state or secrets.token_urlsafe(16)
        q = {
            "client_key": self.client_key,
            "scope": ",".join(SCOPES),
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(q)}", state

    @staticmethod
    def code_from_redirect(redirected_url: str) -> str:
        qs = parse_qs(urlparse(redirected_url.strip()).query)
        code = (qs.get("code") or [""])[0]
        if not code:
            raise ValueError("no ?code= parameter found in the pasted URL")
        return code

    def exchange_code(self, code: str) -> TikTokToken:
        r = self.http.post(
            f"{API}/oauth/token/",
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        data = r.json()
        if r.status_code != 200 or "access_token" not in data:
            raise ProviderError(f"TikTok token exchange failed: {data}")
        tok = TikTokToken.from_response(data)
        tok.save(self.token_file)
        self._token = tok
        return tok

    def _refresh(self, tok: TikTokToken) -> TikTokToken:
        r = self.http.post(
            f"{API}/oauth/token/",
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": tok.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        data = r.json()
        if r.status_code != 200 or "access_token" not in data:
            raise ProviderUnavailable(f"TikTok token refresh failed (re-run `aimz tiktok auth`): {data}")
        new = TikTokToken.from_response(data)
        new.save(self.token_file)
        return new

    def token(self) -> TikTokToken:
        tok = self._token or TikTokToken.load(self.token_file)
        if tok is None:
            raise ProviderUnavailable("no TikTok token; run `aimz tiktok auth`")
        if tok.expires_at - time.time() < 300:
            if tok.refresh_expires_at < time.time():
                raise ProviderUnavailable("TikTok refresh token expired; run `aimz tiktok auth`")
            tok = self._refresh(tok)
        self._token = tok
        return tok

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token().access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    @staticmethod
    def _check(r: httpx.Response, what: str) -> dict[str, Any]:
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        if r.status_code >= 400 or (err and err.get("code") not in (None, "ok")):
            raise ProviderError(f"TikTok {what} failed: HTTP {r.status_code} {err or body}")
        return body.get("data", {}) if isinstance(body, dict) else {}

    # -- content posting ---------------------------------------------------------------
    def creator_info(self) -> dict[str, Any]:
        r = self.http.post(f"{API}/post/publish/creator_info/query/", headers=self._headers(), content=b"{}")
        return self._check(r, "creator_info")

    def init_direct_post(self, post_info: dict[str, Any], video_size: int) -> dict[str, Any]:
        chunk_size, total = chunk_plan(video_size)
        body = {
            "post_info": post_info,
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total,
            },
        }
        r = self.http.post(
            f"{API}/post/publish/video/init/", headers=self._headers(), content=json.dumps(body)
        )
        data = self._check(r, "video init")
        if "publish_id" not in data or "upload_url" not in data:
            raise ProviderError(f"TikTok init returned no publish_id/upload_url: {data}")
        return {**data, "chunk_size": chunk_size, "total_chunk_count": total}

    def upload_file(self, upload_url: str, path: Path, chunk_size: int) -> None:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            offset = 0
            while offset < size:
                remaining = size - offset
                # The final chunk absorbs the remainder (TikTok allows the last chunk up to 128 MB).
                n = remaining if remaining < chunk_size * 2 else chunk_size
                data = fh.read(n)
                r = self.http.put(
                    upload_url,
                    content=data,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{size}",
                    },
                )
                if r.status_code not in (200, 201, 206):
                    raise ProviderError(f"TikTok chunk upload failed: HTTP {r.status_code} {r.text[:200]}")
                offset += len(data)

    def publish_status(self, publish_id: str) -> dict[str, Any]:
        r = self.http.post(
            f"{API}/post/publish/status/fetch/",
            headers=self._headers(),
            content=json.dumps({"publish_id": publish_id}),
        )
        return self._check(r, "status fetch")

    # -- display api -----------------------------------------------------------------------
    def query_videos(self, video_ids: list[str]) -> list[dict[str, Any]]:
        fields = "id,title,view_count,like_count,comment_count,share_count,share_url,create_time"
        r = self.http.post(
            f"{API}/video/query/?fields={fields}",
            headers=self._headers(),
            content=json.dumps({"filters": {"video_ids": video_ids[:20]}}),
        )
        return list(self._check(r, "video query").get("videos", []))

    def user_info(self) -> dict[str, Any]:
        r = self.http.get(
            # `username` deliberately not requested: it needs the extra `user.info.profile` scope,
            # and the only thing we wanted it for (the post URL) is already in creator_info's
            # `creator_username`, which costs no scope beyond the ones posting already requires.
            f"{API}/user/info/?fields=open_id,display_name,follower_count,likes_count,video_count",
            headers=self._headers(),
        )
        return dict(self._check(r, "user info").get("user", {}))


class TikTokDirectPostPublisher(Publisher):
    name = "TikTokDirectPostPublisher"
    platform = "tiktok"
    mode = "direct"
    is_paid = False
    performs_api_writes = True

    def __init__(
        self,
        client: TikTokClient,
        privacy_level: str = "",
        recommend_ai_label: bool = True,
        poll_seconds: int = 45,
    ):
        self.client = client
        self.privacy_level = privacy_level.strip().upper()
        self.is_aigc = recommend_ai_label
        self.poll_seconds = poll_seconds

    def health(self) -> HealthStatus:
        if not self.client.client_key or not self.client.client_secret:
            return HealthStatus(
                False,
                "TIKTOK_CLIENT_KEY/SECRET not set",
                "register an app at developers.tiktok.com; see docs/AUTONOMOUS_SETUP.md",
            )
        tok = TikTokToken.load(self.client.token_file)
        if tok is None:
            return HealthStatus(False, "no TikTok token", "run `aimz tiktok auth`")
        if tok.refresh_expires_at < time.time():
            return HealthStatus(False, "TikTok refresh token expired", "run `aimz tiktok auth`")
        if self.privacy_level and self.privacy_level not in PRIVACY_LEVELS:
            return HealthStatus(
                False,
                f"TIKTOK_PRIVACY_LEVEL={self.privacy_level} invalid",
                f"one of {sorted(PRIVACY_LEVELS)}",
            )
        return HealthStatus(
            True, f"TikTok direct post ready (privacy {self.privacy_level or 'chosen per post'})"
        )

    def publish(
        self,
        ctx: ProviderContext,
        video: dict[str, Any],
        script: dict[str, Any],
        metadata: dict[str, Any],
        package_dir: Path,
    ) -> PublishResult:
        write_common_package(package_dir, video, script, metadata)
        ctx.killswitch.guard("TikTok direct post")
        if not metadata.get("owner_approved"):
            raise OwnerApprovalRequired(
                "TikTok direct post requires owner approval or AUTOPUBLISH_CONSENT=true"
            )
        privacy = str(metadata.get("tiktok_privacy_level") or self.privacy_level).upper()
        if privacy not in PRIVACY_LEVELS:
            raise OwnerApprovalRequired(
                "TikTok privacy level not chosen: set TIKTOK_PRIVACY_LEVEL or pick it in the dashboard"
            )

        with self.authorized(ctx, "tiktok_direct_post") as rec:
            info = self.client.creator_info()
            options = set(info.get("privacy_level_options") or [])
            if options and privacy not in options:
                raise PublishError(
                    f"privacy {privacy} not allowed for this creator (options: {sorted(options)}); unaudited apps allow SELF_ONLY only"
                )
            max_dur = int(info.get("max_video_post_duration_sec") or 0)
            if max_dur and float(video.get("duration_s") or 0) > max_dur:
                raise PublishError(f"video is {video.get('duration_s')}s; creator limit is {max_dur}s")
            caption = build_caption(metadata.get("title", video["title"]), metadata.get("tags", []))
            post_info = {
                "title": caption[:2200],
                "privacy_level": privacy,
                "disable_duet": bool(info.get("duet_disabled", False)),
                "disable_comment": bool(info.get("comment_disabled", False)),
                "disable_stitch": bool(info.get("stitch_disabled", False)),
                "video_cover_timestamp_ms": 500,
                "is_aigc": bool(self.is_aigc),
                "brand_content_toggle": False,
                "brand_organic_toggle": False,
            }
            path = Path(video["file_path"])
            size = path.stat().st_size
            init = self.client.init_direct_post(post_info, size)
            (package_dir / "tiktok_request.json").write_text(
                json.dumps(
                    {
                        "post_info": post_info,
                        "video_size": size,
                        "publish_id": init["publish_id"],
                        "at": now_iso(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            try:
                self.client.upload_file(init["upload_url"], path, int(init["chunk_size"]))
            except Exception as exc:  # never retried automatically
                raise PublishError(f"TikTok upload failed after init {init['publish_id']}: {exc}") from exc
            rec.extra["publish_id"] = init["publish_id"]
            username = info.get("creator_username", "")
            deadline = time.time() + self.poll_seconds
            while True:
                st = self.client.publish_status(init["publish_id"])
                status = st.get("status", "")
                if status == "PUBLISH_COMPLETE":
                    ids = st.get("publicaly_available_post_id") or []
                    vid = str(ids[0]) if ids else None
                    url = f"https://www.tiktok.com/@{username}/video/{vid}" if (vid and username) else None
                    return PublishResult(
                        status="published",
                        platform_video_id=vid,
                        url=url,
                        package_dir=str(package_dir),
                        privacy=privacy,
                        message="TikTok direct post complete",
                        publish_id=init["publish_id"],
                    )
                if status == "FAILED":
                    raise PublishError(f"TikTok post failed: {st.get('fail_reason')}")
                if time.time() > deadline:
                    return PublishResult(
                        status="uploading",
                        package_dir=str(package_dir),
                        privacy=privacy,
                        message=f"TikTok processing ({status}); will be polled next cycle",
                        publish_id=init["publish_id"],
                    )
                time.sleep(5)

    def poll(self, ctx: ProviderContext, publication: dict[str, Any]) -> PublishResult | None:
        """Called for publications left in ``uploading``; returns the terminal result when TikTok is done."""
        meta = json.loads(publication.get("metadata_json") or "{}")
        publish_id = meta.get("publish_id")
        if not publish_id:
            return None
        with self.authorized(ctx, "tiktok_status_poll"):
            st = self.client.publish_status(publish_id)
            status = st.get("status", "")
            if status == "PUBLISH_COMPLETE":
                ids = st.get("publicaly_available_post_id") or []
                vid = str(ids[0]) if ids else None
                username = ""
                with contextlib.suppress(ProviderError):
                    username = self.client.creator_info().get("creator_username", "")
                url = f"https://www.tiktok.com/@{username}/video/{vid}" if (vid and username) else None
                return PublishResult(
                    status="published",
                    platform_video_id=vid,
                    url=url,
                    privacy=publication.get("privacy"),
                    message="TikTok post complete",
                    publish_id=publish_id,
                )
            if status == "FAILED":
                return PublishResult(
                    status="failed",
                    message=f"TikTok post failed: {st.get('fail_reason')}",
                    publish_id=publish_id,
                )
        return None
