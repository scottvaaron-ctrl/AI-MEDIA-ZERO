"""YouTubePublisher: official YouTube Data API v3 ``videos.insert`` (resumable upload). Cost: $0 (quota only).

Modes (``YOUTUBE_MODE``):

* ``draft``     - never touches the API; writes a YouTube-ready package for the owner. Default.
* ``private``   - uploads with ``privacyStatus=private``.
* ``scheduled`` - uploads private with ``publishAt`` (YouTube requires private for scheduling).
* ``public``    - uploads with ``privacyStatus=public``; note that unverified API projects are
                  restricted to private viewing by YouTube until the project passes its audit.

Compliance notes (verified Sept 2026):
* ``videos.insert`` costs 1 unit from a dedicated 100-uploads/day bucket.
* ``status.containsSyntheticMedia`` is the official altered/synthetic disclosure field.
* ``status.selfDeclaredMadeForKids`` is required for COPPA; we default to ``false``.
* Developer Policy III.E.3.d: the user must expressly consent before write actions -> this
  publisher refuses unless ``metadata['owner_approved']`` is true.
* A failed upload is never retried blindly: before a retry, ``find_existing`` looks for it among the
  channel's uploads, because a timed-out resumable upload can still complete (see agents/publisher.py).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aimz.core.errors import (
    CannotVerify,
    OwnerApprovalRequired,
    PermanentPublishError,
    ProviderUnavailable,
    PublishError,
)
from aimz.domain.models import PublishResult
from aimz.providers.base import HealthStatus, ProviderContext, Publisher
from aimz.providers.publishers.packages import write_common_package

log = logging.getLogger("aimz.publish.youtube")

SCOPES_UPLOAD = ["https://www.googleapis.com/auth/youtube.upload"]
SCOPES_READ = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
ALL_SCOPES = SCOPES_UPLOAD + SCOPES_READ
# Revenue (estimatedRevenue, cpm, ...) needs its own scope. It is requested at `aimz youtube auth` but is
# optional: a token without it still uploads and reads every other metric; revenue is then just not fetched.
SCOPE_MONETARY = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
AUTH_SCOPES = ALL_SCOPES + [SCOPE_MONETARY]


def load_credentials(token_file: Path, scopes: list[str] | None = None) -> Any:
    """Load stored OAuth credentials; refresh if expired. Returns None if absent/invalid.

    The token is loaded with the scopes it was granted, not ``scopes``: a refresh that asks for a scope the
    owner never granted fails with ``invalid_scope``, so adding a scope to the code must not break the
    token already on disk. ``scopes`` is kept for callers; use :func:`has_scope` to test for one.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise ProviderUnavailable(
            "google-auth libraries missing: pip install ai-media-zero[youtube]"
        ) from exc
    if not token_file.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_file))
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds if creds and creds.valid else None


def run_oauth_flow(client_secret_file: Path, token_file: Path, scopes: list[str]) -> Any:
    """Owner-only interactive consent flow (opens a browser). Never called by agents."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise ProviderUnavailable("google-auth-oauthlib missing: pip install ai-media-zero[youtube]") from exc
    if not client_secret_file.exists():
        raise ProviderUnavailable(f"client secret file not found: {client_secret_file}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), scopes)
    creds = flow.run_local_server(port=0, prompt="consent")
    granted = list(getattr(creds, "granted_scopes", None) or [])
    if granted and set(granted) != set(creds.scopes or []):
        # Google lets the owner untick optional permissions. Store only what was granted, so a later
        # refresh does not ask for a scope the owner declined.
        from google.oauth2.credentials import Credentials

        info = json.loads(creds.to_json())
        info["scopes"] = granted
        creds = Credentials.from_authorized_user_info(info)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def has_scope(creds: Any, scope: str) -> bool:
    """True when the stored token was granted ``scope``."""
    return scope in set(getattr(creds, "granted_scopes", None) or getattr(creds, "scopes", None) or [])


def sent_title(publication: dict[str, Any]) -> str:
    """The title exactly as it was sent: from the saved request body, else the stored metadata."""
    package = Path(str(publication.get("package_dir") or ""))
    body_file = package / "youtube_request_body.json"
    if publication.get("package_dir") and body_file.exists():
        try:
            return str(json.loads(body_file.read_text(encoding="utf-8"))["snippet"]["title"])
        except (ValueError, KeyError, TypeError):
            pass
    meta = json.loads(publication.get("metadata_json") or "{}")
    return str(meta.get("title") or "")[:100]


def _parse_time(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def build_youtube(creds: Any) -> Any:
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


class YouTubePublisher(Publisher):
    name = "YouTubePublisher"
    platform = "youtube"
    is_paid = False

    def __init__(
        self,
        mode: str,
        default_privacy: str,
        enabled: bool,
        client_secret_file: Path,
        token_file: Path,
        category_id: str = "27",
        made_for_kids: bool = False,
        contains_synthetic_media: bool = True,
        consented_channel_id: str = "",
    ):
        self.consented_channel_id = consented_channel_id
        self.mode = mode
        self.default_privacy = default_privacy
        self.enabled = enabled
        self.client_secret_file = client_secret_file
        self.token_file = token_file
        self.category_id = category_id
        self.made_for_kids = made_for_kids
        self.contains_synthetic_media = contains_synthetic_media

    @property
    def performs_api_writes(self) -> bool:  # type: ignore[override]
        return self.enabled and self.mode != "draft"

    def health(self) -> HealthStatus:
        if not self.enabled or self.mode == "draft":
            return HealthStatus(True, f"YouTube in {self.mode} mode (no API writes)")
        try:
            creds = load_credentials(self.token_file, ALL_SCOPES)
        except ProviderUnavailable as exc:
            return HealthStatus(False, str(exc), "pip install ai-media-zero[youtube]")
        except Exception as exc:
            return HealthStatus(False, f"stored token invalid: {exc}", "run `aimz youtube auth`")
        if creds is None:
            return HealthStatus(False, "no YouTube OAuth token", "run `aimz youtube auth` (owner)")
        return HealthStatus(True, f"YouTube OAuth token valid; mode={self.mode}")

    def _body(self, metadata: dict[str, Any]) -> dict[str, Any]:
        privacy = self.default_privacy
        if self.mode == "public":
            privacy = "public"
        elif self.mode in {"private", "scheduled"}:
            privacy = "private"
        status: dict[str, Any] = {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": self.made_for_kids,
            "containsSyntheticMedia": self.contains_synthetic_media,
            "embeddable": True,
        }
        if self.mode == "scheduled" and metadata.get("publish_at"):
            status["publishAt"] = metadata["publish_at"]
        return {
            "snippet": {
                "title": metadata.get("title", "")[:100],
                "description": metadata.get("description", "")[:5000],
                "tags": list(dict.fromkeys([*[t[:30] for t in metadata.get("tags", [])], "Shorts"]))[:20],
                "categoryId": self.category_id,
                "defaultLanguage": "en",
            },
            "status": status,
        }

    def publish(
        self,
        ctx: ProviderContext,
        video: dict[str, Any],
        script: dict[str, Any],
        metadata: dict[str, Any],
        package_dir: Path,
    ) -> PublishResult:
        # Always produce the package (draft artifact for the owner, and a record of what was sent).
        write_common_package(package_dir, video, script, metadata)
        body = self._body(metadata)
        (package_dir / "youtube_request_body.json").write_text(json.dumps(body, indent=2), encoding="utf-8")
        (package_dir / "posting_notes.md").write_text(self._notes(metadata, body), encoding="utf-8")

        if not self.enabled or self.mode == "draft":
            return PublishResult(
                status="packaged",
                package_dir=str(package_dir),
                privacy=body["status"]["privacyStatus"],
                message="YouTube draft package written; no API call made (YOUTUBE_MODE=draft).",
            )

        # ---- real API write path: every gate below is owner-controlled ----------------
        ctx.killswitch.guard("YouTube upload")
        if not metadata.get("owner_approved"):
            raise OwnerApprovalRequired("YouTube upload requires owner approval of this video")
        creds = load_credentials(self.token_file, ALL_SCOPES)
        if creds is None:
            raise ProviderUnavailable("YouTube OAuth token missing/invalid; run `aimz youtube auth`")
        try:
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise ProviderUnavailable("google-api-python-client missing") from exc

        with self.authorized(ctx, "youtube_upload") as rec:
            yt = build_youtube(creds)
            if self.consented_channel_id:
                # Consent was given for one channel (`aimz instance consent`); never upload to another.
                mine = yt.channels().list(part="id", mine=True).execute().get("items") or []
                rec.extra["quota_units_check"] = 1
                if self.consented_channel_id not in {c.get("id") for c in mine}:
                    raise PermanentPublishError(
                        "the YouTube login no longer points at the channel the owner consented to "
                        f"({self.consented_channel_id}); run `aimz instance consent` again"
                    )
            media = MediaFileUpload(
                video["file_path"], mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
            )
            request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            try:
                while response is None:
                    _status, response = request.next_chunk()
            except Exception as exc:  # no blind retries: surface for review
                raise PublishError(f"YouTube upload failed: {exc}") from exc
            rec.extra["quota_units"] = 1
            vid = response.get("id")
        url = f"https://www.youtube.com/watch?v={vid}" if vid else None
        (package_dir / "youtube_response.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
        return PublishResult(
            status="uploaded",
            platform_video_id=vid,
            url=url,
            package_dir=str(package_dir),
            privacy=body["status"]["privacyStatus"],
            message=f"uploaded as {body['status']['privacyStatus']}",
        )

    def find_existing(self, ctx: ProviderContext, publication: dict[str, Any]) -> PublishResult | None:
        """Look for this video among the channel's recent uploads (2 quota units, read-only).

        A timed-out resumable upload can still finish on YouTube's side, so before a retry the uploads
        playlist is searched for the exact title sent, posted no earlier than the first attempt.
        """
        if not self.enabled or self.mode == "draft":
            raise CannotVerify("YouTube is in draft mode; nothing is uploaded")
        if publication.get("platform_video_id"):
            vid = str(publication["platform_video_id"])
            return PublishResult(
                status="uploaded", platform_video_id=vid, url=f"https://www.youtube.com/watch?v={vid}"
            )
        title = sent_title(publication)
        if not title:
            raise CannotVerify("no record of the title that was sent")
        creds = load_credentials(self.token_file, ALL_SCOPES)
        if creds is None:
            raise ProviderUnavailable("YouTube OAuth token missing/invalid; run `aimz youtube auth`")
        since = _parse_time(str(publication.get("created_at") or "")) - timedelta(hours=1)
        with self.authorized(ctx, "youtube_find_existing") as rec:
            yt = build_youtube(creds)
            channels = yt.channels().list(part="contentDetails", mine=True).execute().get("items") or []
            if not channels:
                raise CannotVerify("the authorised account has no channel")
            uploads = channels[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            items = (
                yt.playlistItems()
                .list(part="snippet", playlistId=uploads, maxResults=50)
                .execute()
                .get("items")
                or []
            )
            rec.extra["quota_units"] = 2
        for item in items:
            sn = item.get("snippet") or {}
            if sn.get("title") != title:
                continue
            published = _parse_time(str(sn.get("publishedAt") or ""))
            if published < since:
                continue  # an older video with the same title, not this attempt
            vid = str((sn.get("resourceId") or {}).get("videoId") or "")
            if vid:
                return PublishResult(
                    status="uploaded",
                    platform_video_id=vid,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    message="found on the channel from an earlier attempt; not uploaded again",
                )
        return None

    @staticmethod
    def _notes(metadata: dict[str, Any], body: dict[str, Any]) -> str:
        return (
            "# YouTube posting notes\n\n"
            f"- Title: {body['snippet']['title']}\n"
            f"- Privacy: {body['status']['privacyStatus']}\n"
            f"- Made for kids: {body['status']['selfDeclaredMadeForKids']}\n"
            f"- Altered/synthetic content disclosure: {body['status']['containsSyntheticMedia']}\n"
            "- Upload `video.mp4`; paste `description.txt`; add `captions.srt` if desired; use `thumbnail.png` or the concept in metadata.\n"
            "- In YouTube Studio, confirm the 'Altered content' disclosure under 'Show more' if not applied by the API.\n"
            "- Unverified API projects are locked to private until the project passes YouTube's compliance audit.\n"
            f"- Sources: see `sources.json`. Attributions: see `attributions.txt`.\n"
            f"- Recommended posting time (local): {metadata.get('recommended_post_time', 'not set')}\n"
        )
