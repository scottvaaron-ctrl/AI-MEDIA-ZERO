# Platform compliance notes

Verified against official documentation on 2026-09-07. Re-verify before changing publishing modes.

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
| Unaudited API clients "can only post contents in SELF_ONLY viewership" and the account must be private; public posting requires an app audit | developers.tiktok.com/doc/content-sharing-guidelines, content-posting-api-get-started | V0 does not call the API at all: `TikTokPackagePublisher` writes an owner-completed package |
| Creator must preview the content, be able to edit the caption, and manually select privacy from `creator_info` options with **no default**; consent before bytes are sent; "Music Usage Confirmation" declaration | content-sharing-guidelines | `posting_notes.md` walks the owner through these steps; `tiktok_metadata.json` marks privacy as `CHOOSE_IN_APP` |
| `post_info.is_aigc` labels AI-generated content | direct-post reference | Package recommends `is_aigc: true`; posting notes tell the owner to enable the AI label |
| Rate limits: 6 `init` calls/min per token; chunk 5–64 MB; MP4/H.264 recommended; 23–60 fps; ≤ 4 GB | media-transfer guide | Renderer outputs 1080x1920 H.264 MP4 at 30 fps, well inside limits, ready for a future `TikTokDirectPostPublisher` |
| Automation of tiktok.com with bots violates the Terms of Service | tiktok.com/legal | No browser automation anywhere in the codebase; the interface is API-shaped so Direct Post can be added once the owner's app is audited |
| Display API `video.list` / `video.query` expose like/comment/share/view counts for **public** videos only | developers.tiktok.com/doc/tiktok-api-v2-video-list | Not integrated in V0 (SELF_ONLY posts would be invisible anyway); metrics are entered manually |

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

1. Create the Google Cloud project, enable YouTube Data + Analytics APIs, download the OAuth
   client JSON, run `aimz youtube auth`. (Only if you want API uploads; draft mode needs nothing.)
2. Approve each video before any API upload.
3. Post TikTok packages in the app; select privacy and the AI label yourself.
4. Enter metrics the APIs do not expose.
5. Apply for the YouTube compliance audit and the TikTok app audit if you ever want public,
   automated posting.
