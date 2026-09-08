"""BlueskyPublisher: official AT Protocol video post. Cost: $0, no app review, no browser bot.

Verified against the atproto lexicons and the Bluesky client source (2026-09-08):

* Auth: ``com.atproto.server.createSession`` with the owner's handle and an **app password**
  (Bluesky settings -> Privacy and Security -> App Passwords), then
  ``com.atproto.server.refreshSession``. App passwords are the officially sanctioned way for a
  program to act for an account, so no browser automation is involved.
* Video: the bytes do not go to the PDS directly. The account asks its PDS for a service-auth
  token (``com.atproto.server.getServiceAuth`` with ``aud=did:web:<pds host>`` and
  ``lxm=com.atproto.repo.uploadBlob``) and hands it to the video service:
  ``POST https://video.bsky.app/xrpc/app.bsky.video.uploadVideo?did=..&name=..`` starts a job;
  ``app.bsky.video.getJobStatus`` is polled until ``JOB_STATE_COMPLETED``, which carries the
  finished ``blob``. ``app.bsky.video.getUploadLimits`` (service auth with
  ``aud=did:web:video.bsky.app``) reports the account's remaining daily video allowance.

  **The two job endpoints do not share a response shape, whatever the lexicon says.** Verified
  against the live service 2026-09-08: ``getJobStatus`` wraps the job in ``jobStatus`` and is the
  only place the ``blob`` ever appears, while ``uploadVideo`` returns the job's fields flat at
  the top level with no blob -- including on the ``409 already_exists`` reply for a file that a
  previous attempt already uploaded, which is a reusable result and not an error.
  ``_job_from_body`` accepts both shapes; do not "simplify" it back to the declared one.
* Post: ``com.atproto.repo.createRecord`` for ``app.bsky.feed.post`` with an
  ``app.bsky.embed.video`` embed (video blob, ``alt``, ``aspectRatio``, optional WebVTT
  ``captions``). Text is capped at 300 graphemes / 3000 bytes, and hashtags and links only
  become live links if the record carries matching ``app.bsky.richtext.facet`` byte ranges.

Platform limits honoured in code: 300 MB and 10 minutes per video (we send 2-6 MB / 20-90 s),
caption files up to 20 kB, and the account's own daily video allowance, which is checked before
any byte is sent so a quota block costs nothing. The kill switch and owner consent are checked
first; a failed post is reported and never retried automatically.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from aimz.core.errors import OwnerApprovalRequired, ProviderError, ProviderUnavailable, PublishError
from aimz.domain.models import PublishResult
from aimz.providers.base import HealthStatus, ProviderContext, Publisher
from aimz.providers.publishers.captions import grapheme_len, hashtags, trim_graphemes
from aimz.providers.publishers.packages import write_common_package
from aimz.util import now_iso

log = logging.getLogger("aimz.publish.bluesky")

DEFAULT_PDS = "https://bsky.social"
VIDEO_SERVICE = "https://video.bsky.app"
VIDEO_SERVICE_DID = "did:web:video.bsky.app"
POST_COLLECTION = "app.bsky.feed.post"

MAX_POST_GRAPHEMES = 300
MAX_POST_BYTES = 3000
MAX_VIDEO_BYTES = 300_000_000
MAX_VIDEO_SECONDS = 600
MAX_CAPTION_BYTES = 20_000
SERVICE_AUTH_TTL_S = 30 * 60
# The file is held in memory for the upload; our renderer emits 2-6 MB. Anything far larger
# means something is wrong upstream, and would need the video service's multipart endpoints.
UPLOAD_MEMORY_LIMIT = 64 * 1024 * 1024

_URL_RE = re.compile(r"https?://[^\s<>\[\]()]+")
_TAG_RE = re.compile(r"(?:^|\s)(#[^\s#]+)")


# --------------------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------------------
def facets_for(text: str) -> list[dict[str, Any]]:
    """Byte-range facets so hashtags and URLs render as links (AT Protocol indexes UTF-8 bytes)."""
    out: list[dict[str, Any]] = []

    def byte_start(char_index: int) -> int:
        return len(text[:char_index].encode("utf-8"))

    for m in _URL_RE.finditer(text):
        uri = m.group(0).rstrip(".,;:!?")
        start = byte_start(m.start())
        out.append(
            {
                "index": {"byteStart": start, "byteEnd": start + len(uri.encode("utf-8"))},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": uri}],
            }
        )
    for m in _TAG_RE.finditer(text):
        tag = m.group(1).lstrip("#").rstrip(".,;:!?")
        if not tag or len(tag) > 64:
            continue
        start = byte_start(m.start(1))
        out.append(
            {
                "index": {"byteStart": start, "byteEnd": start + len(("#" + tag).encode("utf-8"))},
                "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}],
            }
        )
    return sorted(out, key=lambda f: int(f["index"]["byteStart"]))


def build_post_text(title: str, tags: list[str], note: str, max_tags: int = 3) -> str:
    """Title + one-line AI disclosure + hashtags, trimmed to Bluesky's 300-grapheme post limit."""
    tag_line = " ".join(hashtags(tags, max_tags))
    tail = "\n\n".join(part for part in (note.strip(), tag_line) if part)
    # The disclosure and tags are fixed; the title absorbs whatever room is left, minus the blank line.
    room = MAX_POST_GRAPHEMES - grapheme_len(tail) - 2
    head = trim_graphemes(title.strip(), max(20, room)).rstrip()
    text = f"{head}\n\n{tail}" if tail else head
    text = trim_graphemes(text, MAX_POST_GRAPHEMES)
    while len(text.encode("utf-8")) > MAX_POST_BYTES:  # graphemes fit but bytes do not
        # Count in clusters, never code points: a limit above the cluster count is a no-op and
        # would spin here forever on text made of multi-code-point emoji.
        text = trim_graphemes(text, grapheme_len(text) - 1)
    return text


def build_sources_reply(sources: list[dict[str, Any]], limit: int = 2) -> str:
    """A self-reply carrying the source links, so the claims in the video stay checkable."""
    seen: list[str] = []
    for source in sources:
        url = str(source.get("url") or "").strip()
        if url and url not in seen:
            seen.append(url)
    if not seen:
        return ""
    return trim_graphemes("Sources:\n" + "\n".join(seen[:limit]), MAX_POST_GRAPHEMES)


def srt_to_vtt(srt_text: str) -> str:
    """SRT -> WebVTT. The only difference that matters here is ``,mmm`` -> ``.mmm`` in timestamps."""
    body = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt_text.strip().replace("\r\n", "\n"))
    return "WEBVTT\n\n" + body + "\n"


def aspect_ratio(video: dict[str, Any]) -> dict[str, int] | None:
    m = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", str(video.get("resolution") or ""))
    return {"width": int(m.group(1)), "height": int(m.group(2))} if m else None


def post_url(handle: str, at_uri: str) -> str | None:
    """``at://did/app.bsky.feed.post/3k2a`` -> ``https://bsky.app/profile/<handle>/post/3k2a``."""
    rkey = at_uri.rstrip("/").rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else None


def _jwt_expiry(token: str) -> float | None:
    """Read ``exp`` out of a JWT payload without verifying it (the server is the only verifier)."""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (ValueError, TypeError, KeyError):
        return None


def _pds_from_did_doc(doc: Any) -> str:
    for service in (doc or {}).get("service", []) or []:
        if str(service.get("id", "")).endswith("#atproto_pds"):
            return str(service.get("serviceEndpoint") or "").rstrip("/")
    return ""


# --------------------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------------------
@dataclass
class BlueskySession:
    access_jwt: str
    refresh_jwt: str
    did: str
    handle: str
    pds_url: str
    access_expires_at: float = 0.0
    refresh_expires_at: float = 0.0

    @classmethod
    def from_response(cls, data: dict[str, Any], fallback_pds: str) -> BlueskySession:
        now = time.time()
        return cls(
            access_jwt=data["accessJwt"],
            refresh_jwt=data["refreshJwt"],
            did=data.get("did", ""),
            handle=data.get("handle", ""),
            pds_url=_pds_from_did_doc(data.get("didDoc")) or fallback_pds,
            access_expires_at=_jwt_expiry(data["accessJwt"]) or now + 3600,
            refresh_expires_at=_jwt_expiry(data["refreshJwt"]) or now + 30 * 86400,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> BlueskySession | None:
        if not path.exists():
            return None
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, TypeError):
            return None


class BlueskyClient:
    """XRPC client for the owner's PDS and the Bluesky video service. The app password stays in ``.env``."""

    def __init__(
        self,
        handle: str,
        app_password: str,
        pds_url: str = DEFAULT_PDS,
        session_file: Path | None = None,
        http: httpx.Client | None = None,
    ):
        self.handle = handle.strip().lstrip("@")
        self.app_password = app_password.strip()
        self.pds_url = (pds_url or DEFAULT_PDS).rstrip("/")
        self.session_file = session_file or Path("secrets/bluesky_session.json")
        self.http = http or httpx.Client(timeout=120)
        self._session: BlueskySession | None = None

    # -- transport ---------------------------------------------------------------------
    @staticmethod
    def _check(r: httpx.Response, what: str) -> dict[str, Any]:
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        if r.status_code >= 400:
            detail = body.get("message") or body.get("error") or body if isinstance(body, dict) else body
            raise ProviderError(f"Bluesky {what} failed: HTTP {r.status_code} {detail}")
        return body if isinstance(body, dict) else {}

    def _get(
        self, base: str, nsid: str, params: dict[str, Any] | None = None, token: str | None = None
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self._check(self.http.get(f"{base}/xrpc/{nsid}", params=params or {}, headers=headers), nsid)

    def _post(
        self,
        base: str,
        nsid: str,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        content: bytes | None = None,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        headers = {"Content-Type": content_type}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = content if content is not None else json.dumps(body or {}).encode("utf-8")
        return self._check(self.http.post(f"{base}/xrpc/{nsid}", headers=headers, content=payload), nsid)

    # -- auth --------------------------------------------------------------------------
    def login(self) -> BlueskySession:
        """Exchange the owner's handle + app password for a session and store it."""
        if not self.handle or not self.app_password:
            raise ProviderUnavailable("set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD in .env")
        data = self._post(
            self.pds_url,
            "com.atproto.server.createSession",
            {"identifier": self.handle, "password": self.app_password},
        )
        session = BlueskySession.from_response(data, self.pds_url)
        session.save(self.session_file)
        self.pds_url = session.pds_url or self.pds_url
        self._session = session
        return session

    def _refresh(self, session: BlueskySession) -> BlueskySession:
        data = self._post(
            session.pds_url or self.pds_url, "com.atproto.server.refreshSession", token=session.refresh_jwt
        )
        new = BlueskySession.from_response(data, session.pds_url or self.pds_url)
        new.save(self.session_file)
        return new

    def session(self) -> BlueskySession:
        session = self._session or BlueskySession.load(self.session_file)
        if session is None:
            return self.login()
        if session.access_expires_at - time.time() < 300:
            try:
                session = self._refresh(session)
            except ProviderError as exc:
                # Refresh tokens expire after about two months. The owner's app password is
                # already in .env, so signing in again here is what keeps the channel hands-off.
                log.warning("Bluesky session refresh failed (%s); creating a new session", exc)
                return self.login()
        self._session = session
        self.pds_url = session.pds_url or self.pds_url
        return session

    def service_auth(self, aud: str, lxm: str, ttl_s: int = SERVICE_AUTH_TTL_S) -> str:
        session = self.session()
        data = self._get(
            session.pds_url,
            "com.atproto.server.getServiceAuth",
            {"aud": aud, "lxm": lxm, "exp": int(time.time()) + int(ttl_s)},
            token=session.access_jwt,
        )
        token = str(data.get("token") or "")
        if not token:
            raise ProviderError(f"Bluesky getServiceAuth returned no token for {lxm}")
        return token

    # -- video service -----------------------------------------------------------------
    def upload_limits(self) -> dict[str, Any]:
        token = self.service_auth(VIDEO_SERVICE_DID, "app.bsky.video.getUploadLimits")
        return self._get(VIDEO_SERVICE, "app.bsky.video.getUploadLimits", token=token)

    @staticmethod
    def _job_from_body(body: Any) -> dict[str, Any]:
        """Pull a jobStatus out of either shape the video service actually uses.

        The lexicon declares both endpoints as ``{"jobStatus": {...}}``, and ``getJobStatus``
        does that. ``uploadVideo`` does not: it returns the job's fields flat at the top level
        (verified against the live service 2026-09-08), including on the ``409 already_exists``
        answer you get for a file that was uploaded on an earlier attempt.
        """
        if not isinstance(body, dict):
            return {}
        inner = body.get("jobStatus")
        if isinstance(inner, dict) and inner:
            return dict(inner)
        return dict(body) if body.get("jobId") else {}

    def upload_video(self, path: Path, name: str) -> dict[str, Any]:
        session = self.session()
        size = path.stat().st_size
        if size > UPLOAD_MEMORY_LIMIT:
            raise PublishError(
                f"video is {size} bytes; this publisher uploads in a single request "
                f"(limit {UPLOAD_MEMORY_LIMIT}). Files this large need the multipart endpoints."
            )
        aud = f"did:web:{urlparse(session.pds_url).hostname}"
        token = self.service_auth(aud, "com.atproto.repo.uploadBlob")
        r = self.http.post(
            f"{VIDEO_SERVICE}/xrpc/app.bsky.video.uploadVideo",
            params={"did": session.did, "name": name},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
            content=path.read_bytes(),
        )
        try:
            body = r.json()
        except ValueError:
            body = {}
        # Re-sending an identical file answers 409 already_exists but still carries the job, so a
        # body with a jobId is a usable result whatever the status code says.
        job = self._job_from_body(body)
        if job.get("jobId"):
            return job
        return self._job_from_body(self._check(r, "uploadVideo"))

    def job_status(self, job_id: str) -> dict[str, Any]:
        # Public on the video service: reading a job's progress needs no service-auth token.
        return self._job_from_body(self._get(VIDEO_SERVICE, "app.bsky.video.getJobStatus", {"jobId": job_id}))

    # -- repo --------------------------------------------------------------------------
    def upload_blob(self, data: bytes, mime: str) -> dict[str, Any]:
        session = self.session()
        body = self._post(
            session.pds_url,
            "com.atproto.repo.uploadBlob",
            token=session.access_jwt,
            content=data,
            content_type=mime,
        )
        return dict(body.get("blob") or {})

    def create_post(self, record: dict[str, Any]) -> dict[str, Any]:
        session = self.session()
        return self._post(
            session.pds_url,
            "com.atproto.repo.createRecord",
            {"repo": session.did, "collection": POST_COLLECTION, "record": record},
            token=session.access_jwt,
        )

    # -- reads used by analytics -------------------------------------------------------
    def get_posts(self, uris: list[str]) -> list[dict[str, Any]]:
        session = self.session()
        data = self._get(
            session.pds_url, "app.bsky.feed.getPosts", {"uris": uris[:25]}, token=session.access_jwt
        )
        return list(data.get("posts") or [])

    def get_profile(self, actor: str = "") -> dict[str, Any]:
        session = self.session()
        return self._get(
            session.pds_url,
            "app.bsky.actor.getProfile",
            {"actor": actor or session.did},
            token=session.access_jwt,
        )

    def get_post_thread(self, uri: str, depth: int = 1) -> dict[str, Any]:
        session = self.session()
        data = self._get(
            session.pds_url,
            "app.bsky.feed.getPostThread",
            {"uri": uri, "depth": depth, "parentHeight": 0},
            token=session.access_jwt,
        )
        return dict(data.get("thread") or {})


# --------------------------------------------------------------------------------------
# publisher
# --------------------------------------------------------------------------------------
class BlueskyPublisher(Publisher):
    name = "BlueskyPublisher"
    platform = "bluesky"
    mode = "api"
    is_paid = False
    performs_api_writes = True

    def __init__(
        self,
        client: BlueskyClient,
        *,
        lang: str = "en",
        max_hashtags: int = 3,
        upload_captions: bool = True,
        sources_reply: bool = True,
        poll_seconds: int = 180,
    ):
        self.client = client
        self.lang = lang or "en"
        self.max_hashtags = max_hashtags
        self.upload_captions = upload_captions
        self.sources_reply = sources_reply
        self.poll_seconds = poll_seconds

    def health(self) -> HealthStatus:
        if not self.client.handle or not self.client.app_password:
            return HealthStatus(
                False,
                "BLUESKY_HANDLE / BLUESKY_APP_PASSWORD not set",
                "create an app password in Bluesky settings; see docs/AUTONOMOUS_SETUP.md",
            )
        session = BlueskySession.load(self.client.session_file)
        if session is None:
            return HealthStatus(True, f"Bluesky ready for {self.client.handle} (first post will sign in)")
        if session.refresh_expires_at < time.time():
            return HealthStatus(
                True,
                f"Bluesky session for {session.handle} expired; the app password will sign in again",
            )
        return HealthStatus(True, f"Bluesky connected as {session.handle}")

    # -- record construction -----------------------------------------------------------
    def _caption_blob(self, package_dir: Path) -> dict[str, Any] | None:
        srt = package_dir / "captions.srt"
        if not self.upload_captions or not srt.exists():
            return None
        vtt = srt_to_vtt(srt.read_text(encoding="utf-8")).encode("utf-8")
        if len(vtt) > MAX_CAPTION_BYTES:
            log.info("captions are %d bytes; Bluesky allows %d, skipping", len(vtt), MAX_CAPTION_BYTES)
            return None
        try:
            blob = self.client.upload_blob(vtt, "text/vtt")
        except ProviderError as exc:  # an accessibility extra, never a reason to lose the post
            log.warning("Bluesky caption upload failed: %s", exc)
            return None
        return {"lang": self.lang, "file": blob} if blob else None

    def _build_record(
        self, video: dict[str, Any], metadata: dict[str, Any], package_dir: Path
    ) -> dict[str, Any]:
        title = str(metadata.get("title") or video["title"])
        note = str(metadata.get("bluesky_note") or "AI-narrated, sourced explainer.")
        text = build_post_text(title, list(metadata.get("tags") or []), note, self.max_hashtags)
        embed: dict[str, Any] = {
            "$type": "app.bsky.embed.video",
            "alt": trim_graphemes(f"{title}. AI-narrated explainer video with on-screen captions.", 900),
        }
        ratio = aspect_ratio(video)
        if ratio:
            embed["aspectRatio"] = ratio
        caption = self._caption_blob(package_dir)
        if caption:
            embed["captions"] = [caption]
        record: dict[str, Any] = {
            "$type": POST_COLLECTION,
            "text": text,
            "createdAt": now_iso(),
            "langs": [self.lang],
            "embed": embed,
        }
        facets = facets_for(text)
        if facets:
            record["facets"] = facets
        return record

    def _finish(
        self, record: dict[str, Any], blob: dict[str, Any], reply_text: str
    ) -> tuple[dict[str, Any], str]:
        """Attach the finished video blob, create the post, then best-effort add the sources reply."""
        record = {**record, "embed": {**record["embed"], "video": blob}}
        created = self.client.create_post(record)
        uri, cid = str(created.get("uri") or ""), str(created.get("cid") or "")
        if not uri:
            raise PublishError(f"Bluesky createRecord returned no uri: {created}")
        note = ""
        if reply_text and self.sources_reply:
            root = {"uri": uri, "cid": cid}
            try:
                self.client.create_post(
                    {
                        "$type": POST_COLLECTION,
                        "text": reply_text,
                        "createdAt": now_iso(),
                        "langs": [self.lang],
                        "facets": facets_for(reply_text),
                        "reply": {"root": root, "parent": root},
                    }
                )
            except ProviderError as exc:  # the video is already public, so do not retry
                log.warning("Bluesky sources reply failed: %s", exc)
                note = " (sources reply failed; add it by hand)"
        return created, note

    # -- publish -----------------------------------------------------------------------
    def publish(
        self,
        ctx: ProviderContext,
        video: dict[str, Any],
        script: dict[str, Any],
        metadata: dict[str, Any],
        package_dir: Path,
    ) -> PublishResult:
        write_common_package(package_dir, video, script, metadata)
        ctx.killswitch.guard("Bluesky post")
        if not metadata.get("owner_approved"):
            raise OwnerApprovalRequired("Bluesky post requires owner approval or AUTOPUBLISH_CONSENT=true")

        path = Path(video["file_path"])
        size = path.stat().st_size
        if size > MAX_VIDEO_BYTES:
            raise PublishError(f"video is {size} bytes; Bluesky accepts up to {MAX_VIDEO_BYTES}")
        duration = float(video.get("duration_s") or 0)
        if duration > MAX_VIDEO_SECONDS:
            raise PublishError(f"video is {duration}s; Bluesky accepts up to {MAX_VIDEO_SECONDS}s")

        with self.authorized(ctx, "bluesky_post") as rec:
            limits = self.client.upload_limits()
            if not limits.get("canUpload", False):
                raise PublishError(
                    "Bluesky will not accept a video right now: "
                    + str(limits.get("message") or limits.get("error") or "daily video limit reached")
                )
            record = self._build_record(video, metadata, package_dir)
            reply_text = (
                build_sources_reply(list(metadata.get("sources") or [])) if self.sources_reply else ""
            )
            job = self.client.upload_video(path, f"{video['id']}.mp4")
            job_id = str(job.get("jobId") or "")
            if not job_id:
                raise PublishError(f"Bluesky video service returned no jobId: {job}")
            rec.extra["job_id"] = job_id
            # The record is written before the post exists, so `poll` can finish an upload that
            # is still encoding and the package holds exactly what was sent.
            (package_dir / "bluesky_request.json").write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "record": record,
                        "sources_reply": reply_text,
                        "remaining_daily_videos": limits.get("remainingDailyVideos"),
                        "at": now_iso(),
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            # `uploadVideo` never carries the blob, not even when it reports the job already
            # complete, so the blob is always resolved through `getJobStatus`.
            deadline = time.time() + self.poll_seconds
            polled = False
            while True:
                state = str(job.get("state") or "")
                if state == "JOB_STATE_FAILED":
                    raise PublishError(
                        f"Bluesky video processing failed ({job.get('failureCode')}): "
                        f"{job.get('error') or job.get('message')}"
                    )
                if state == "JOB_STATE_COMPLETED" and job.get("blob"):
                    break
                if time.time() > deadline:
                    return PublishResult(
                        status="uploading",
                        package_dir=str(package_dir),
                        privacy="public",
                        message=f"Bluesky is still encoding ({state}); will be polled next cycle",
                        publish_id=job_id,
                    )
                if polled:  # the first re-read is immediate; a short file is often already done
                    time.sleep(5)
                polled = True
                job = self.client.job_status(job_id)
            created, note = self._finish(record, dict(job["blob"]), reply_text)
            handle = self.client.session().handle
        uri = str(created.get("uri") or "")
        return PublishResult(
            status="published",
            platform_video_id=uri,
            url=post_url(handle, uri),
            package_dir=str(package_dir),
            privacy="public",
            message="Posted to Bluesky" + note,
            publish_id=job_id,
        )

    def poll(self, ctx: ProviderContext, publication: dict[str, Any]) -> PublishResult | None:
        """Finish a post whose video was still encoding when the cycle ended."""
        request_file = Path(publication.get("package_dir") or "") / "bluesky_request.json"
        if not request_file.exists():
            return None
        try:
            saved = json.loads(request_file.read_text(encoding="utf-8"))
        except ValueError:
            return None
        job_id = str(saved.get("job_id") or "")
        record = saved.get("record")
        if not job_id or not isinstance(record, dict):
            return None
        with self.authorized(ctx, "bluesky_job_poll"):
            job = self.client.job_status(job_id)
            state = str(job.get("state") or "")
            if state == "JOB_STATE_FAILED":
                return PublishResult(
                    status="failed",
                    message=f"Bluesky video processing failed ({job.get('failureCode')}): "
                    f"{job.get('error') or job.get('message')}",
                    publish_id=job_id,
                )
            if state != "JOB_STATE_COMPLETED" or not job.get("blob"):
                return None
            created, note = self._finish(record, dict(job["blob"]), str(saved.get("sources_reply") or ""))
            handle = self.client.session().handle
        uri = str(created.get("uri") or "")
        return PublishResult(
            status="published",
            platform_video_id=uri,
            url=post_url(handle, uri),
            privacy="public",
            message="Posted to Bluesky after encoding finished" + note,
            publish_id=job_id,
        )
