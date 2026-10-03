# Platform compliance notes

Verified against official documentation on 2026-09-07; the Bluesky section on 2026-09-08; YouTube
policies (developer, monetization, spam, disclosure) re-checked 2026-10-01 (`docs/PLAN_FULL_CONTROL.md` stage C).
Re-verify before changing publishing modes.

## YouTube Data API v3

| Requirement | Source | How AI Media Zero complies |
|---|---|---|
| `videos.insert` needs `youtube.upload` (or broader) scope | developers.google.com/youtube/v3/docs/videos/insert | `YouTubePublisher` requests `youtube.upload` + read-only scopes via the installed-app OAuth flow, run by the owner (`aimz youtube auth`) |
| Uploads from unverified API projects created after 28 Jul 2020 are restricted to **private** until the project passes a compliance audit | same page | Default `YOUTUBE_MODE=draft` (no API writes); `private` is the only automated upload mode recommended in V0; `public` is owner-opt-in and documented as ineffective until audited |
| Quota (June 2026): 100 `videos.insert` calls/day (1 unit each) plus 10,000 units/day for other endpoints, per Google Cloud project | developers.google.com/youtube/v3/getting-started | All channels share one project and one OAuth client (Developer Policies III.D.1.c: one project per API client; never create a project per channel for quota). Owner posting limits (`publishing.limits`, 2026-10-01): at most 2 posts per run and 2 posting runs a day per channel, so at most 16 uploads/day across 4 channels |
| `status.containsSyntheticMedia` is the official altered/synthetic disclosure | videos resource docs (added Oct 2024) | Set `true` by default (`publishing.youtube.contains_synthetic_media`); disclosure text also goes in the description |
| `status.selfDeclaredMadeForKids` (COPPA) | videos resource docs | Set from config, default `false` |
| `status.publishAt` requires `privacyStatus=private` | videos resource docs | `scheduled` mode always uploads private with `publishAt` |
| Developer Policy III.E.3.d: users must expressly consent before write actions; III.C.3: users have final control over published data; III.I.2: no automated uploads without prior specific consent | developers.google.com/youtube/terms/developer-policies | `publishing.require_owner_approval=true`; `PublishStage` raises `OwnerApprovalRequired` unless the video was approved in the dashboard/CLI; the kill switch blocks all writes, retries included; a failed upload is retried only under the same consent, only after `YouTubePublisher.find_existing` (channels.list + playlistItems.list on the uploads playlist, 2 read units, verified live 2026-09-28) confirms the earlier attempt did not post, and at most `publishing.max_attempts` times |
| Analytics: `averageViewDuration`, `averageViewPercentage`, `shares`, `subscribersGained`, `estimatedMinutesWatched` available; thumbnail impressions / CTR and a literal "3-second retention" are **not** exposed by the API | developers.google.com/youtube/analytics/metrics | `YouTubeAnalyticsProvider` pulls what exists; owner enters impressions/CTR/3s retention manually from YouTube Studio |
| `commentThreads.list` (1 unit) for reading comments | commentThreads docs | Read-only; the comment agent never replies |
| Developer Policies III.E.4 (checked 2026-10-01): non-statistics API data no longer than 30 days unless refreshed; statistics may be kept while authorized, re-checking authorization every 30 days | developers.google.com/youtube/terms/developer-policies | `aimz/youtube_data.py`, run every cycle: comments are refreshed on each fetch (`comments.refreshed_at`) and deleted after 30 days without a refresh, with their text removed from leads; `youtube_response.json` is cut to the id after 30 days; authorization is checked every cycle |
| Developer Policies III.D: delete authorized data within 7 days of revocation; API ToS 24.3 on termination | same | `aimz youtube revoke` revokes the token and purges at once (metrics, comments, responses, video ids/links, channel stats, token). Automatic purge when authorization has failed for 7 days or not succeeded for 30; `aimz status` lists the failure meanwhile |
| Developer Policies III.I: prior, specific consent before automated uploads; III.E.3: clearly identify the channel | same | Consent is per channel: `aimz instance consent <name>` shows the channel and records its id; a new instance starts with `AUTOPUBLISH_CONSENT=false`; uploads refuse if the login points at another channel |
| Developer Policies III.A: privacy policy contents | same | `PRIVACY.md` / `docs/privacy/` (updated 2026-10-01): all four scopes and their uses, commenter data, retention, deletion on revocation, several channels, contact email |

## TikTok Content Posting API

| Requirement | Source | How AI Media Zero complies |
|---|---|---|
| Unaudited API clients "can only post contents in SELF_ONLY viewership" and the account must be private; public posting requires an app audit | developers.tiktok.com/doc/content-sharing-guidelines, content-posting-api-get-started | Default `TIKTOK_MODE=package` never calls the API. `TIKTOK_MODE=direct` uses `TikTokDirectPostPublisher`, which validates the chosen privacy level against `creator_info.privacy_level_options` (so an unaudited app can only post SELF_ONLY) and checks `max_video_post_duration_sec` |
| Creator must preview the content, be able to edit the caption, and manually select privacy from `creator_info` options with **no default**; consent before bytes are sent; "Music Usage Confirmation" declaration | content-sharing-guidelines | Package mode: `posting_notes.md` walks the owner through these steps. Direct mode: the dashboard one-tap page shows the creator name, a privacy dropdown with no default, and an explicit consent checkbox; the hands-off path requires the owner to set `TIKTOK_PRIVACY_LEVEL` and `AUTOPUBLISH_CONSENT=true` in `.env` for their own account |
| `post_info.is_aigc` labels AI-generated content | direct-post reference | Package recommends `is_aigc: true`; posting notes tell the owner to enable the AI label |
| Rate limits: 6 `init` calls/min per token; chunk 5–64 MB; MP4/H.264 recommended; 23–60 fps; ≤ 4 GB | media-transfer guide | Renderer outputs 1080x1920 H.264 MP4 at 30 fps, well inside limits; `TikTokDirectPostPublisher` uploads in 5-64 MB chunks (single chunk under 64 MB) and never retries inside a cycle; a later retry first asks `publish/status/fetch` about the saved `publish_id`, so a post that went through is never sent twice |
| Automation of tiktok.com with bots violates the Terms of Service | tiktok.com/legal | No browser automation anywhere in the codebase; posting goes through the official API only |
| Display API `video.list` / `video.query` expose like/comment/share/view counts for **public** videos only | developers.tiktok.com/doc/tiktok-api-v2-video-list | `TikTokAnalyticsProvider` (`TIKTOK_ANALYTICS_ENABLED=true`) reads them for public posts; SELF_ONLY posts return nothing, so metrics stay manual until the app is audited |

## Bluesky / AT Protocol

Verified 2026-09-08 against the `bluesky-social/atproto` lexicons and the official client source.

| Requirement | Source | How AI Media Zero complies |
|---|---|---|
| A program acting for an account should use an **app password**, not the account password | bsky.app settings -> Privacy and Security -> App Passwords | `BLUESKY_APP_PASSWORD` holds a revocable app password; `.env.example` says in as many words never to put the account password there. `aimz bluesky auth` runs `com.atproto.server.createSession` once and stores only the returned JWTs |
| Video bytes go to the video service, not the PDS: `app.bsky.video.uploadVideo` authorized by a `com.atproto.server.getServiceAuth` token with `aud=did:web:<pds host>`, `lxm=com.atproto.repo.uploadBlob` | lexicons `app/bsky/video/*`, `social-app/src/lib/media/video/upload.shared.ts` | `BlueskyClient.upload_video` derives the audience from the PDS in the account's DID document and requests a token bound to that one method, valid 30 minutes |
| Processing is asynchronous: poll `app.bsky.video.getJobStatus` until `JOB_STATE_COMPLETED`, which carries the blob | `app.bsky.video.defs#jobStatus` | Polled inside the cycle for up to `poll_seconds`; if it is still encoding the publication stays `uploading` and the next cycle finishes it (not while the kill switch is engaged). A failure is retried only after `com.atproto.repo.listRecords` (verified live 2026-09-28) shows no post with the same text since the first attempt, at most `publishing.max_attempts` times |
| Per-account daily video allowance; Bluesky-hosted accounts must have a confirmed email before uploading video | `app.bsky.video.getUploadLimits` | Checked before a single byte is sent, so hitting the limit costs nothing and surfaces the platform's own message |
| Video embed limits: MP4, up to 300 MB and 10 minutes; captions are WebVTT blobs up to 20 kB, max 20 | lexicon `app.bsky.embed.video` | Enforced in `bluesky.py` (`MAX_VIDEO_BYTES`, `MAX_VIDEO_SECONDS`, `MAX_CAPTION_BYTES`). Our renderer emits 1080x1920 H.264 MP4, 10-180 s (runtime chosen by the AI), far inside them |
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
- **Music and sound effects (2026-09-30).** Off unless the AI opens the `music` / `sfx` settings. The
  library in `assets/music` and `assets/sfx` is generated by `scripts/make_audio_library.py` from sine
  waves and noise: original to this project and listed as CC0 in each `licenses.json`. Nothing is
  third-party, so there is no Content ID or licence risk, and TikTok's commercial-music restriction
  (which concerns copyrighted recordings) does not apply. The owner may add tracks. Only licences in
  `FREE_AUDIO_LICENSES` are offered, and a CC0 track from a public site can still be Content-ID claimed
  when someone else registered it, so prefer original or verified sources.
- **Stock photos and video (Pexels, Pixabay; built 2026-09-30, off).** Their licences allow free
  commercial use but are not in the constitution's original list, so the providers are registered only
  when the owner has amended the constitution, set `assets.stock.enabled: true` in `config.yaml` and put
  `PEXELS_API_KEY` / `PIXABAY_API_KEY` in `.env`. Each asset records licence, page URL and author, and
  the credit ("Photo by X on Pexels", "Image by X from Pixabay") goes into the video description. Pixabay
  terms: cache searches 24 h (done), no hotlinking (files are downloaded). Pexels: 200 requests per hour.
  **Not yet verified against the live APIs.**
- **Narration voices (checked 2026-10-01).** Piper's code and voice repository are MIT, but each voice
  is trained on a dataset with its own licence. **There is no default voice** (owner decision
  2026-10-01): every video's `voice` setting picks from `COMMERCIAL_SAFE_VOICES`
  (`providers/tts/piper.py`), and `PiperTTSProvider` refuses any other voice, so a stale `PIPER_VOICE`
  in an `.env` is ignored. Allowed: `kristin`, `norman`, `ljspeech`, `cori` (public-domain data,
  trained from scratch) and `john` (public-domain data, fine-tuned from kristin), per each voice's
  `MODEL_CARD`. Excluded: `lessac` (Blizzard 2013 licence, "Research Purposes" only, excludes "any
  commercial purpose"; used by every video up to 1 Oct 2026), `joe` (CC0 data but fine-tuned from
  lessac), `bryce` (fine-tuned from an unreleased voice), `ryan`, `hfc_female`, `hfc_male`
  (CC BY-NC-SA).
- **Caption fonts.** Only fonts listed in `assets/fonts/licenses.json` with a free licence (OFL,
  Apache, UFL, CC0) are offered; each font ships with its licence text. The renderer's own default
  font (`FONT_FILE`, Arial on Windows) is used from the system and not redistributed.

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
