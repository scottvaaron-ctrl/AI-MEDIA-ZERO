# Handoff: adding more platforms to AI Media Zero

_Written 2026-09-08 for the next agent; updated 2026-09-08 after Bluesky shipped. Read this,
`docs/ARCHITECTURE.md`, `docs/COMPLIANCE.md` and `docs/AUTONOMOUS_SETUP.md` before touching code._

## 1. Where things stand

| Item | State |
|---|---|
| Core pipeline (research → ideas → EIC → script → fact-check → critic → render → publish → metrics → learn) | Done, 5 real cycles run, 75 tests green, ruff + mypy clean |
| YouTube upload + analytics + comments | **Done and live** on the owner's machine: OAuth token stored, `YOUTUBE_ENABLED=true`, `YOUTUBE_MODE=public`, `YOUTUBE_ANALYTICS_ENABLED=true`, `AUTOPUBLISH_CONSENT=true` |
| YouTube API compliance audit | **Submitted by the owner (or in progress)**. Until Google approves, uploads land as private. Evidence files are in `data/audit/` (not in git). Nothing in code changes when it's approved |
| TikTok Direct Post + Display API analytics | **Implemented and unit-tested against a mock transport** (`providers/publishers/tiktok_direct.py`, `providers/analytics/tiktok.py`). **Not live**: the owner has not registered a TikTok developer app. `TIKTOK_MODE=package` still |
| Bluesky (AT Protocol) post + analytics | **Implemented and unit-tested against a mock transport** (`providers/publishers/bluesky.py`, `providers/analytics/bluesky.py`). Not yet run against the live API: needs `BLUESKY_ENABLED=true` plus a handle and app password in `.env`, then `aimz bluesky auth`. No developer app and no audit are involved, so this is a five-minute owner step with nothing to wait for |
| Scheduler | `aimz schedule install` works (tested install/status/remove). **Not installed** yet; the owner should run it once they are happy with the first uploads |
| Repo | Public at https://github.com/scottvaaron-ctrl/AI-MEDIA-ZERO (main). Work on this machine at `C:\Users\scott\Documents\Agentic_Youtube`, venv `.venv`, Python 3.14 |
| Local models | Ollama with `qwen3:8b` (default) and `qwen3:4b`; Piper voice downloaded; FFmpeg via winget and bundled imageio-ffmpeg |

Non-negotiables inherited from the owner (see `config/constitution.md`):

- $0.00 usage-based cost. No paid APIs, no paid hosting. Anything with a price tag is a
  `is_paid=True` provider template that stays unregistered.
- Official platform APIs only. No browser automation, no scraping behind logins, no third-party
  "auto-posting" services with fees.
- Every provider call goes through `Provider.authorized()` (budget + kill switch + usage row).
- API writes require owner consent: per-video approval or `AUTOPUBLISH_CONSENT=true`.
- Failed uploads are never retried automatically.

## 2. First task: switch Bluesky on (owner-dependent, five minutes)

The code is done and tested; the blocker is one credential. Walk the owner through
`docs/AUTONOMOUS_SETUP.md` Part C: create an app password in Bluesky settings, put
`BLUESKY_ENABLED=true`, `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` and
`BLUESKY_ANALYTICS_ENABLED=true` in `.env`, run `aimz bluesky auth`, then `aimz run`.

Things to confirm against the live API on the first real post (mock-tested only):

- whether `app.bsky.video.uploadVideo` answers immediately with `JOB_STATE_COMPLETED` for a 2-6 MB
  file or hands back an encoding job (both paths are implemented; the second finishes in
  `aimz publish poll`);
- the exact wording `getUploadLimits` returns when the daily allowance is used up;
- that the WebVTT caption blob is accepted (if Bluesky rejects it, the post still goes out and a
  warning is logged, so this shows up in `data/logs/aimz.jsonl` rather than as a failure).

Fix anything that differs in `bluesky.py` and keep `tests/test_bluesky.py` passing.

## 3. Second task: finish TikTok (owner-dependent)

Code is done; the blocker is account setup. Walk the owner through
`docs/AUTONOMOUS_SETUP.md` Part B:

1. Register an app at developers.tiktok.com with Login Kit + Content Posting API (Direct Post),
   scopes `user.info.basic, user.info.stats, video.publish, video.upload, video.list`, and a
   redirect URI they control (a GitHub Pages URL is fine; the page can be blank).
2. Put `TIKTOK_MODE=direct`, `TIKTOK_CLIENT_KEY/SECRET`, `TIKTOK_REDIRECT_URI`,
   `TIKTOK_PRIVACY_LEVEL=SELF_ONLY`, `TIKTOK_ANALYTICS_ENABLED=true` in `.env`.
3. `aimz tiktok auth` (paste-the-redirect-URL flow). Then `aimz tiktok creator-info` to confirm
   `privacy_level_options`.
4. Run a cycle; expect a SELF_ONLY post on a private account. Then submit TikTok's app audit.
   After approval set `TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE`.

Things to verify against the live API on first use (mock-tested only): the exact `upload_url`
PUT response codes (we accept 200/201/206), whether `publicaly_available_post_id` is returned
immediately or only after `PUBLISH_COMPLETE`, and the Display API field list. Fix in
`tiktok_direct.py`; keep the tests in `tests/test_tiktok_direct.py` passing.

## 4. How to add a platform (the pattern)

Every platform is two classes plus wiring. Copy the TikTok pair as the template.

```
providers/publishers/<platform>.py   class <Platform>Publisher(Publisher)
providers/analytics/<platform>.py    class <Platform>AnalyticsProvider(AnalyticsProvider)
providers/registry.py                wire both behind .env switches
settings.py + .env.example           new env fields (keys, token file, mode, privacy)
cli.py                               `aimz <platform> auth` (owner-only OAuth)
docs/COMPLIANCE.md                   verified requirements + how the code honours them
docs/AUTONOMOUS_SETUP.md             owner checklist
tests/test_<platform>.py             httpx.MockTransport tests: happy path, consent gate,
                                     kill switch, privacy/validation, failure not retried
```

`Publisher` contract (`providers/base.py`):

- `platform`, `mode`, `performs_api_writes=True`.
- `publish(ctx, video, script, metadata, package_dir) -> PublishResult` — always call
  `write_common_package()` first (it is the audit trail), then `ctx.killswitch.guard()`, then
  check `metadata["owner_approved"]`, then do the work inside `with self.authorized(ctx, "<purpose>")`.
- Return `status="uploading"` with `publish_id` if the platform processes asynchronously and
  implement `poll(ctx, publication)`; `PublishStage.poll_pending()` calls it every cycle.
- Raise `PublishError` on failure. Never loop/retry.

`AnalyticsProvider` contract: `fetch_metrics(ctx, publication) -> MetricsSnapshot | None`
(map to `views, likes, comments, shares, avg_percent_viewed, followers_gained…`; put the raw
payload in `snap.raw`). `AnalystAgent.collect_metrics()` stores it; the learning loop needs
nothing else. Optional `fetch_comments()` feeds the comment agent.

Video spec the renderer already produces: 1080x1920 H.264/AAC MP4, 30 fps, 20–90 s, 2–6 MB.
Most platforms accept it unchanged. If a platform needs a different aspect ratio, add a profile
in `config.yaml` `content.*` and let `ProducerAgent` render a second file; do not re-encode
inside a publisher.

## 5. Candidate platforms, ranked by "free + official + hands-off" fit

Verify every row against current docs before coding; this table is from knowledge as of
September 2026 and platforms change terms often.

| Platform | Posting API | Cost | Human gates | Analytics | Verdict |
|---|---|---|---|---|---|
| ~~**Bluesky**~~ | **Done** — `app.bsky.video.uploadVideo` on video.bsky.app (service auth) + `app.bsky.feed.post` with an `app.bsky.embed.video` embed. The first draft of this row was wrong on two counts: the limits are 300 MB / 10 min (not 50 MB / 60 s), and video bytes go to the video service, not straight to `uploadBlob` | free, no app review | none; app password from settings | likes, replies, reposts + quotes, bookmarks, follower delta. **No view count exists on Bluesky for anyone** | Shipped 2026-09-08; owner just needs the app password |
| **Mastodon** | `POST /api/v2/media` + `/api/v1/statuses`, any instance | free | register app via API; no review | favourites/boosts | Easy second; audience small |
| **Instagram Reels** | Instagram Graph API content publishing (`/media` with `media_type=REELS`, then `/media_publish`) | free | needs Facebook Page + Instagram professional account; Meta app review for `instagram_content_publish` before use on other accounts (own account works in dev mode with the owner as tester); **video must be fetched from a public HTTPS URL**, so it needs free hosting (GitHub release asset or the owner's GitHub Pages) | Insights API: plays, reach, likes, shares, saves | Worth doing after TikTok; solve the public-URL step first |
| **Facebook Reels** | Reels Publishing API on a Page (`/video_reels`, resumable upload) | free | Page + app review for `pages_manage_posts` | Page insights | Same app as Instagram; add together |
| **Threads** | Threads API (`/threads` with `media_type=VIDEO` from public URL, then `/threads_publish`) | free | Meta app + review; public URL again | limited insights | Cheap add-on once Instagram works |
| **X / Twitter** | v2 `POST /2/media/upload` (chunked) + `POST /2/tweets` | free tier exists but write caps are low and have changed repeatedly; verify current limits | developer account | free tier gives almost no metrics | Low priority; check limits before investing |
| **Pinterest** | `POST /v5/pins` with video (needs `media` upload) | free | app review ("standard access") | analytics API | Low priority for this content type |
| **LinkedIn** | Videos API (`/rest/videos` initialize/upload/finalize + `/rest/posts`) | free | app review for `w_member_social` | limited | Low priority |
| Snapchat, Reddit video, Lemon8 | no practical free posting API for individuals | — | — | — | Skip |

Recommended order from here: **Mastodon → finish TikTok (owner) → Instagram + Facebook Reels
(one Meta app) → Threads → X only if limits allow**. Mastodon is the closest sibling to the
Bluesky work that just landed (app-level token, direct media upload, no review), so it is the
cheapest next platform even though the audience is smaller.

## 6. Public-URL hosting for Meta platforms at $0

Instagram, Threads and Facebook pull the video from a URL instead of accepting bytes. Options
that cost nothing and use official APIs:

1. **GitHub Releases** on the owner's public repo: upload the MP4 as a release asset via the
   GitHub REST API (free, needs a fine-grained token stored in `.env`), publish, use the asset
   URL, delete the release after `media_publish` succeeds. Implement as an `AssetHostProvider`
   with the same `authorized()` guard.
2. Owner-controlled GitHub Pages branch: commit the MP4, push, wait for Pages, use the URL.
   Slower and bloats the repo; prefer option 1.

Do **not** use any hosting that meters bandwidth or has a paid tier that could be triggered.

## 7. Cross-platform behaviour already in place

- `PublishStage.publish()` iterates over every registered publisher for each rendered video,
  so a new platform gets every future video automatically.
- Idempotency key is `(video, platform, mode)`; a second cycle never re-uploads.
- `metrics` rows are platform-tagged; the learning loop scores every publication separately,
  so per-platform family/hook performance falls out of the existing reports.
- The comment agent works for any provider that implements `fetch_comments()`.

## 8. Open items and known gaps

- YouTube audit approval is pending; nothing to code, but re-run `aimz youtube auth` if Google
  asks for re-consent after approval.
- YouTube impressions/CTR and TikTok watch time are not exposed by any API; manual entry stays
  optional. Bluesky exposes no view count at all, so its publications carry engagement only.
- Per-platform captions: the shared hashtag formatting now lives in
  `providers/publishers/captions.py`; each publisher composes its own caption on top of it
  (`build_caption` for TikTok, `build_post_text` for Bluesky's 300-grapheme limit). Keep it that
  way rather than pushing platform formatting up into `agents/publisher.py`.
- Posting time: `recommended_post_time` is a fixed 17:00 heuristic; make it an experiment
  variable once two or more platforms report metrics. With Bluesky live this becomes worth doing,
  since it is the one platform that will report back within hours instead of days.
- The learning loop compares publications across platforms that expose different metrics. Watch
  the first few weeks of `aimz analytics` for a platform with fewer signals being scored unfairly
  against YouTube; if that shows up, normalise per platform in `experiments/allocation.py`.
- `docs/DRY_RUN.md` documents 4B-vs-8B model calibration; re-run one cycle after any prompt
  change and check `aimz analytics` before committing.

## 9. Commands the next agent will use

```powershell
cd $HOME\Documents\Agentic_Youtube; .\.venv\Scripts\Activate.ps1
aimz doctor                      # everything should be OK
pytest -q                        # 75 tests, offline
ruff check src tests; ruff format src tests; mypy
aimz run --stages publish        # re-run only publishing for rendered videos
aimz publish list; aimz publish poll
aimz bluesky auth; aimz bluesky limits
aimz status; aimz budget
```

Commit convention: small commits, message ends with the Co-Authored-By line used in `git log`.
Keep `data/`, `secrets/`, `.env` out of git (already ignored).
