# Fully autonomous mode — one-time setup checklist

Goal: after this, you never touch the system. It researches, writes, renders, uploads, pulls the
numbers, and rewrites its strategy on a schedule. Still $0.00 in usage-based cost.

Everything below is a one-time human action that the platforms require (accounts, consent, audits).
The AI cannot do any of these for you, by design.

## What "autonomous" means per platform

| Platform | Posting | Metrics | Human steps required once |
|---|---|---|---|
| YouTube Shorts | Automatic via the official Data API | Automatic via Analytics API (views, avg % viewed, shares, subscribers gained, comments) | Google Cloud OAuth setup; YouTube API compliance audit (otherwise uploads stay private) |
| TikTok | Automatic via the Content Posting API (Direct Post) | Automatic via Display API (views, likes, comments, shares, follower delta) | TikTok developer app; app audit (otherwise posts are SELF_ONLY / private) |
| Bluesky | Automatic via the AT Protocol video API | Automatic (likes, replies, reposts + quotes, bookmarks, follower delta) | Create one app password. **No developer app, no audit, no waiting** |

Not available from any API, ever: YouTube impressions/CTR, TikTok watch time, and any Bluesky
view count. The learning loop does not need them.

Bluesky is the cheapest platform to switch on and the only one with no gatekeeper, so if you
want the shortest path to a fully hands-off channel, do Part C first.

---

## Part A — YouTube (about 30 minutes, then wait for the audit)

### A1. Google Cloud project and OAuth client

1. Go to <https://console.cloud.google.com/> and sign in with the Google account that owns the channel.
2. Create a project (name it anything, e.g. `ai-media-zero`).
3. **APIs & Services → Library**: enable **YouTube Data API v3** and **YouTube Analytics API**.
4. **APIs & Services → OAuth consent screen**: External, fill app name and your email, add the
   scopes `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`, add your own Google
   account under **Test users**, save.
5. **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app**.
   Download the JSON and save it as `secrets\client_secret.json` inside the project folder.

### A2. Connect the channel (once)

```powershell
aimz youtube auth
```

A browser opens; sign in and allow. The token is stored at `secrets\youtube_token.json` and
refreshes itself.

### A3. Apply for the API compliance audit (free)

Uploads from unaudited projects are forced to **private**. Fill in the
[YouTube API Services – Audit and Quota Extension form](https://support.google.com/youtube/contact/yt_api_form)
describing: "Personal automated channel; uploads AI-narrated educational shorts with disclosure to
my own channel; reads my own analytics." Approval typically takes 1–3 weeks. You can run everything
before approval; videos just stay private until then.

### A4. Turn it on

Edit `.env`:

```
YOUTUBE_ENABLED=true
YOUTUBE_MODE=public          # keep "private" until the audit is approved if you prefer
YOUTUBE_ANALYTICS_ENABLED=true
AUTOPUBLISH_CONSENT=true
```

`AUTOPUBLISH_CONSENT=true` is your prior, express consent (required by YouTube's Developer
Policies) for the system to upload to **your own channel** without approving each video. Set it
back to `false` at any time; `aimz kill` stops everything instantly regardless.

---

## Part B — TikTok (about 30 minutes, then wait for the audit)

### B1. Register an app

1. Go to <https://developers.tiktok.com/>, log in with the TikTok account that will post, and
   create an app.
2. Add the products **Login Kit** and **Content Posting API**; enable **Direct Post**.
3. Request scopes: `user.info.basic`, `user.info.stats`, `video.publish`, `video.upload`, `video.list`.
4. Under Login Kit, add a **Redirect URI** you control (any https page you own, or a GitHub Pages
   URL; TikTok only needs to redirect there once, the page can be blank).
5. Copy the **Client key** and **Client secret** into `.env`:

```
TIKTOK_MODE=direct
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=https://your-page.example/tiktok
TIKTOK_PRIVACY_LEVEL=SELF_ONLY     # change to PUBLIC_TO_EVERYONE once your app is audited
TIKTOK_ANALYTICS_ENABLED=true
```

### B2. Connect the account (once)

```powershell
aimz tiktok auth
```

It prints a link. Open it, log in, allow. Your browser lands on your redirect page; copy the full
address bar URL (it contains `?code=...`) and paste it back into PowerShell. The token is stored at
`secrets\tiktok_token.json` and refreshes itself for a year.

### B3. Sandbox test, then submit the app audit

While unaudited, TikTok only allows posting to your own **private** account with privacy
`SELF_ONLY` (your account must be set to private in the TikTok app). Run one cycle, confirm a
private post appears, then submit the app for review in the developer portal (Content Posting API
audit). TikTok's guidelines expect the creator to choose the privacy level; `TIKTOK_PRIVACY_LEVEL`
is you making that choice once for your own account. After approval, set
`TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE`.

If TikTok declines audit for a single-user personal app, keep `TIKTOK_MODE=package` and post the
packages by hand, or skip TikTok and let YouTube run alone. The dashboard also offers a one-tap
**Post to TikTok** button (privacy chosen from your creator options) if you prefer approving each
post.

---

## Part C — Bluesky (about 5 minutes, no audit)

Bluesky needs no developer account and no app review. An app password is a revocable credential
that is not your account password, and it is the officially supported way to let a program post.

### C1. Create the app password

1. Open Bluesky -> **Settings** -> **Privacy and Security** -> **App Passwords** -> *Add App Password*.
2. Name it `ai-media-zero` and copy the `xxxx-xxxx-xxxx-xxxx` value. It is shown once.
3. If your account is hosted by Bluesky, confirm your email first; video upload is blocked until you do.

### C2. Fill in `.env`

```
BLUESKY_ENABLED=true
BLUESKY_HANDLE=yourname.bsky.social
BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
BLUESKY_ANALYTICS_ENABLED=true
```

Leave `BLUESKY_PDS_URL=https://bsky.social` unless you self-host your PDS. Never put your account
password here; if the app password leaks, revoke it in the same settings page and nothing else is
exposed.

### C3. Connect and check the quota

```powershell
aimz bluesky auth
aimz bluesky limits
```

`auth` signs in once and stores the session in `secretsluesky_session.json`; after that the
system keeps itself signed in. `limits` prints `canUpload` and how many videos the account may
still post today.

### C4. What gets posted

Title, a one-line AI disclosure and up to three hashtags (Bluesky allows only 300 characters),
the vertical video with alt text and a WebVTT caption track, then a self-reply carrying the source
links. Set `BLUESKY_SOURCES_REPLY=false` if you would rather not have the reply.

Posts are public immediately: there is no restricted phase to wait out. The owner consent gate is
the only thing standing in front of them, so nothing is posted until `AUTOPUBLISH_CONSENT=true`
or you approve the video.

---

## Part D — Schedule (2 minutes)

```powershell
aimz schedule install --times 09:00,18:00
```

This registers two Windows Task Scheduler jobs that run `aimz run` daily at those times and log to
`data\logs\scheduled.log`. `aimz schedule status` lists them; `aimz schedule remove` deletes them.
The laptop must be on (not asleep) at those times; set Power options to "never sleep when plugged in".

The tasks are registered at the root of the Task Scheduler Library (not inside a subfolder): open
Task Scheduler, click **Task Scheduler Library** itself, and look in the middle pane for
"AI Media Zero 0900" and "AI Media Zero 1800". Both already have "Wake the computer to run this
task" and "Run task as soon as possible after a scheduled start is missed" set (see `_task_xml`).
If they are missing, run `python -m aimz schedule install --times 09:00,18:00` again.

`scripts/run-cycle.ps1` starts `ollama serve` itself if the Ollama API is not answering, and waits
up to 60 s for it before running the cycle (added 2026-09-13 after scheduled runs failed with
"model unreachable"). Ollama must be on PATH. The runner is regenerated by `aimz schedule install`,
so the guard lives in `src/aimz/scheduler.py`, not only in the script.

---

## Part E — Verify

```powershell
aimz doctor
aimz run
aimz publish list
```

`doctor` should show `YouTube OAuth token valid`, `Bluesky connected as <handle>` and (if
enabled) `TikTok direct post ready`.
After the first upload, `aimz publish list` shows `uploaded`/`published` with a URL. The next
cycles pull metrics automatically (YouTube analytics lag by 1–2 days) and the strategy starts
moving families out of `hypothesis`.

## What still needs you (rarely)

- Re-running `aimz youtube auth` / `aimz tiktok auth` if a token is revoked (about yearly).
  Bluesky is the exception: it signs itself back in from the app password, so it only needs you
  if you revoke that password.
- Responding to a platform audit question.
- Reading the weekly review in `data\reports\` if you are curious. Optional.
- Anything involving money, contracts, sponsorships, or ads (never automated).
