# Platform compliance notes

Verified against official documentation on 2026-09-07; the Bluesky section on 2026-09-08.
Re-verify before changing publishing modes.

## YouTube Data API v3

| Requirement | Source | How AI Media Zero complies |
|---|---|---|
| `videos.insert` needs `youtube.upload` (or broader) scope | developers.google.com/youtube/v3/docs/videos/insert | `YouTubePublisher` requests `youtube.upload` + read-only scopes via the installed-app OAuth flow, run by the owner (`aimz youtube auth`) |
| Uploads from unverified API projects created after 28 Jul 2020 are restricted to **private** until the project passes a compliance audit | same page | Default `YOUTUBE_MODE=draft` (no API writes); `private` is the only automated upload mode recommended in V0; `public` is owner-opt-in and documented as ineffective until audited |
| Quota (June 2026): 100 `videos.insert` calls/day (1 unit each) plus 10,000 units/day for other endpoints | developers.google.com/youtube/v3/getting-started | `aimz` never uploads more than `max_productions_per_cycle` per cycle; usage is recorded in `provider_usage.extra` |
| `status.containsSyntheticMedia` is the official altered/synthetic disclosure | videos resource docs (added Oct 2024) | Set `true` by default (`publishing.youtube.contains_synthetic_media`); disclosure text also goes in the description |
| `status.selfDeclaredMadeForKids` (COPPA) | videos resource docs | Set from config, default `false` |
| `status.publishAt` requires `privacyStatus=private` | videos resource docs | `scheduled` mode always uploads private with `publishAt` |
| Developer Policy III.E.3.d: users must expressly consent before write actions; III.C.3: users have final control over published data; III.I.2: no automated uploads without prior specific consent | developers.google.com/youtube/terms/developer-policies | `publishing.require_owner_approval=true`; `PublishStage` raises `OwnerApprovalRequired` unless the video was approved in the dashboard/CLI; the kill switch blocks all writes; uploads are never retried automatically |
| Analytics: `averageViewDuration`, `averageViewPercentage`, `shares`, `subscribersGained`, `estimatedMinutesWatched` available; thumbnail impressions / CTR and a literal "3-second retention" are **not** exposed by the API | developers.google.com/youtube/analytics/metrics | `YouTubeAnalyticsProvider` pulls what exists; owner enters impressions/CTR/3s retention manually from YouTube Studio |
| `commentThreads.list` (1 unit) for reading comments | commentThreads docs | Read-only; the comment agent never replies |

## TikTok Content Posting API

| Requirement | Source | How AI Media Zero complies |
|---|---|---|
| Unaudited API clients "can only post contents in SELF_ONLY viewership" and the account must be private; public posting requires an app audit | developers.tiktok.com/doc/content-sharing-guidelines, content-posting-api-get-started | Default `TIKTOK_MODE=package` never calls the API. `TIKTOK_MODE=direct` uses `TikTokDirectPostPublisher`, which validates the chosen privacy level against `creator_info.privacy_level_options` (so an unaudited app can only post SELF_ONLY) and checks `max_video_post_duration_sec` |
| Creator must preview the content, be able to edit the caption, and manually select privacy from `creator_info` options with **no default**; consent before bytes are sent; "Music Usage Confirmation" declaration | content-sharing-guidelines | Package mode: `posting_notes.md` walks the owner through these steps. Direct mode: the dashboard one-tap page shows the creator name, a privacy dropdown with no default, and an explicit consent checkbox; the hands-off path requires the owner to set `TIKTOK_PRIVACY_LEVEL` and `AUTOPUBLISH_CONSENT=true` in `.env` for their own account |
| `post_info.is_aigc` labels AI-generated content | direct-post reference | Package recommends `is_aigc: true`; posting notes tell the owner to enable the AI label |
| Rate limits: 6 `init` calls/min per token; chunk 5–64 MB; MP4/H.264 recommended; 23–60 fps; ≤ 4 GB | media-transfer guide | Renderer outputs 1080x1920 H.264 MP4 at 30 fps, well inside limits; `TikTokDirectPostPublisher` uploads in 5-64 MB chunks (single chunk under 64 MB) and never retries a failed post |
| Automation of tiktok.com with bots violates the Terms of Service | tiktok.com/legal | No browser automation anywhere in the codebase; posting goes through the official API only |
| Display API `video.list` / `video.query` expose like/comment/share/view counts for **public** videos only | developers.tiktok.com/doc/tiktok-api-v2-video-list | `TikTokAnalyticsProvider` (`TIKTOK_ANALYTICS_ENABLED=true`) reads them for public posts; SELF_ONLY posts return nothing, so metrics stay manual until the app is audited |

## Bluesky / AT Protocol

Verified 2026-09-08 against the `bluesky-social/atproto` lexicons and the official client source.

| Requirement | Source | How AI Media Zero complies |
|---|---|---|
| A program acting for an account should use an **app password**, not the account password | bsky.app settings -> Privacy and Security -> App Passwords | `BLUESKY_APP_PASSWORD` holds a revocable app password; `.env.example` says in as many words never to put the account password there. `aimz bluesky auth` runs `com.atproto.server.createSession` once and stores only the returned JWTs |
| Video bytes go to the video service, not the PDS: `app.bsky.video.uploadVideo` authorized by a `com.atproto.server.getServiceAuth` token with `aud=did:web:<pds host>`, `lxm=com.atproto.repo.uploadBlob` | lexicons `app/bsky/video/*`, `social-app/src/lib/media/video/upload.shared.ts` | `BlueskyClient.upload_video` derives the audience from the PDS in the account's DID document and requests a token bound to that one method, valid 30 minutes |
| Processing is asynchronous: poll `app.bsky.video.getJobStatus` until `JOB_STATE_COMPLETED`, which carries the blob | `app.bsky.video.defs#jobStatus` | Polled inside the cycle for up to `poll_seconds`; if it is still encoding the publication stays `uploading` and `aimz publish poll` finishes it next cycle. `JOB_STATE_FAILED` is reported and **never** retried |
| Per-account daily video allowance; Bluesky-hosted accounts must have a confirmed email before uploading video | `app.bsky.video.getUploadLimits` | Checked before a single byte is sent, so hitting the limit costs nothing and surfaces the platform's own message |
| Video embed limits: MP4, up to 300 MB and 10 minutes; captions are WebVTT blobs up to 20 kB, max 20 | lexicon `app.bsky.embed.video` | Enforced in `bluesky.py` (`MAX_VIDEO_BYTES`, `MAX_VIDEO_SECONDS`, `MAX_CAPTION_BYTES`). Our renderer emits 1080x1920 H.264 MP4, 20-90 s, 2-6 MB, far inside them |
| Post text: 300 graphemes / 3000 bytes; hashtags and links are inert unless the record carries matching `app.bsky.richtext.facet` byte ranges | lexicons `app/bsky/feed/post`, `app/bsky/richtext/facet` | `build_post_text` trims on grapheme clusters and then on bytes; `facets_for` emits UTF-8 byte offsets for tags and URLs |
| Accessibility: `alt` on the video embed | lexicon `app.bsky.embed.video` | Always set from the title; the SRT track is converted to WebVTT and attached when it fits |
| No official API exposes view, impression or watch-time counts to anyone, including the author | `app.bsky.feed.defs#postView` | `BlueskyAnalyticsProvider` records likes, replies, reposts + quotes, bookmarks and the follower delta, and leaves `views` empty rather than inventing a proxy |
| Automating the bsky.app web client with a browser would be a workaround, not an API | project constitution | No browser automation anywhere; every call is an XRPC method against the owner's PDS or the video service |

Bluesky requires **no developer app and no platform audit**, so unlike YouTube and TikTok there is
no restricted first phase: posts are public from the first upload. That makes owner consent the
only gate, and it is the same one as everywhere else (`AUTOPUBLISH_CONSENT=true` or per-video
approval, plus the kill switch).

## Media licensing

- `WikimediaAssetProvider` accepts only `Public domain`, `PD-*`, `CC0`, `CC BY x`, `CC BY-SA x`
  license short-names; `NC`, `ND`, fair-use, GFDL-only and unlabeled files are rejected in code
  (`license_allowed`). Attribution (`title — author — license — via Wikimedia Commons`) is stored
  per asset, baked onto the scene card, listed in the video description and in `attributions.txt`.
- A descriptive `User-Agent` (`AIMZ_USER_AGENT`) is sent to Wikimedia, per its API etiquette.
- Owner-library images are the owner's responsibility (`assets/owner/credits.txt` for attribution).
- No background music is used (no licensed source at $0 was integrated); this avoids TikTok's
  commercial-music restrictions.

## Editorial safety

- Every factual claim is stored with source URLs; unsupported years/numbers are detected
  deterministically and either revised or removed. Nothing is invented to fill a gap.
- The Critic fails scripts with banned engagement-bait phrases, high policy/copyright/deception
  risk, or "does not deserve a video".
- Topics in `safety.elevated_review_topics` (real people, altered events, medical, legal,
  financial, political, emergencies, allegations) route the script to `needs_owner_review`; it
  cannot be produced until the owner approves.
- AI disclosure text is included on the first scene card, in the description, in
  `ai_disclosure.txt`, and via `containsSyntheticMedia` / `is_aigc` where the platform supports it.

## Human-required actions (V0)

See docs/AUTONOMOUS_SETUP.md for the one-time checklist. In short: create the Google Cloud OAuth
client and run `aimz youtube auth`; register a TikTok app and run `aimz tiktok auth`; submit both
platform audits (uploads stay private/SELF_ONLY until approved); create a Bluesky app password and
run `aimz bluesky auth` (no audit, no waiting); set `AUTOPUBLISH_CONSENT=true` for your own
accounts; schedule `aimz run`. Impressions/CTR (YouTube), watch time (TikTok) and views of any
kind (Bluesky) are not exposed by any API and remain optional manual entries.
