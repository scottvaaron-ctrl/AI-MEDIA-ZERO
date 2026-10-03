# AI Media Zero: Brief for the CEO and the CEO's AI Agent

_Updated 2026-10-02: pre-send recheck passed (live runs, uploads, privacy page); reply to YouTube ready to
send. Earlier (1 Oct): compliance review still open, policy-audit findings and owner decisions
(see "1 Oct: YouTube compliance"). Snapshot as of 2026-09-30 (evening); new that day: the 30 Sep upload check, the YouTube login item,
the owner-approved plan for full AI control and 3–4 channels (`docs/PLAN_FULL_CONTROL.md`), and
all 7 stages of that plan in code: stages 0-6 (measurement, per-video settings, 21 production settings,
posting limits, several channels, music/templates/stock media, revenue focus). Some parts wait for
you; see `docs/OWNER_TODO.md`.
Other numbers are from 2026-09-28 (`aimz status`, the `metrics`, `runs` and `errors` tables, and a
verification cycle run on a copy of the database after the 28 Sep fixes)._

---

## Part 1: For the CEO (a two-minute read)

### What it is
AI Media Zero is an experiment. An AI runs a short-form video channel with **no money at all**. It
finds topics, writes scripts, checks facts, makes the videos, posts them, reads the results and
changes its own strategy based on what worked.

**The question it answers:** can an AI with a $0 budget work out what people want to watch, build an
audience and get better over time without a human doing the creative work?

### How it works
```
research -> ideas -> editor picks -> script -> fact-check -> critic -> render -> publish
    ^                                                                               |
    +-------------- metrics -> experiments -> strategy memory <----------------------+
```
Twice a day (09:00 and 18:00 since 1 Oct; four times on 30 Sep-1 Oct) the laptop runs a full cycle without
anyone touching it, and posts within your limits. All the
software is free and runs on that one laptop: a local AI model, a local voice, licensed images from
Wikimedia and free video tools. The only outside services are the platforms' official posting APIs.

### Who controls what
| The AI controls | The owner controls |
|---|---|
| Topics, scripts, titles, hooks, visuals, how often it tries new ideas, its strategy notes | Money, logins, legal authority, whether posting is allowed, the **kill switch** |

The budget is hard-capped at **$0.00/month** below the AI. The AI cannot see or change that setting.
Any call that would cost money is refused before it runs.

### Where it is today
| Platform | Status | Gives learning data? |
|---|---|---|
| YouTube Shorts ("Backhouse Explainers") | Live, public, posting automatically | Yes, 1-2 days late |
| Bluesky (@backhouse06.bsky.social) | Live, public, posting automatically | Engagement only (Bluesky has no view counts) |
| TikTok (backhouse67) | Working, but private until TikTok approves the app | No |

**Results so far:** 20 videos published (19 on YouTube), **2,357 YouTube views** in total. The best
are "Mariano Rivera's Historic Save Record" (409) and "The Mystery Behind Ashton-under-Lyne" (393),
both posted in the last 4 days. The first few likes have appeared; still no comments or subscribers.
Spend so far is **$0.00**.

**What happened 23-28 Sep:** YouTube came back after its login was fixed, and 14 videos went out.
Then the supply ran dry. For 7 runs in a row the AI approved **no new scripts**; it was only
posting the backlog the owner approved on 23 Sep. On 28 Sep it published nothing.

**Why it ran dry, and what was fixed on 28 Sep** (engineering only; none of it tells the AI what to make):
1. **The critic failed scripts without saying why.** Scripts scored just under the pass mark with no
   problem named, so the rewrites came back identical and were thrown away. Now the critic is asked
   once more for its reasons; if it still gives none, the score no longer blocks the script (owner
   decision). On a test run the same evening, **2 scripts were approved** (previous 7 runs: 0).
2. **Two of the AI's idea slots were wasted every run** on the same stale ideas it kept turning down.
   Those ideas are now rejected once, and the slot goes to another idea.
3. **Two scheduled runs could start at once** after the laptop woke late, and one died without a
   trace. Now there is one scheduled task, and a second copy cannot start.
4. **A failed upload used to stay failed for good.** It is now retried automatically, but only after
   the platform confirms the video is not already posted, so nothing is posted twice. After 3 tries
   it is closed out and you get an alert (owner decision; this replaces "never retried", D-010).
5. **Nothing technical loses a video any more.** A model outage, a crash or a shutdown sends the work
   back to the queue instead of rejecting it. Runs cut off by a shutdown are cleaned up and reported.
6. **Bluesky could post while the kill switch was on**, and could post the same video twice after a
   timeout. Both fixed.
7. **The AI's own experiment could never collect data** (it named its measure in a way the code did
   not recognise). Fixed; it now counts.
8. Videos older than 14 days are now marked **measured** and stop using API calls.

### What is honest to say
- **It works end to end.** Real videos go out on real platforms with no human in the loop and no cost.
- **Views are growing** (the last 4 days produced the two best videos), but that is still a handful of
  data points. It is too early to call it learning.
- **Quality is limited by free tools.** The small local model writes thin scripts; many are still
  rejected for stating facts their sources do not contain. That filter is doing its job.
- **The critic is now looser in one way:** a script it scores just under the bar without naming a
  problem is published. The two approved on the test run both passed this way (scored 59 of 60).
  Fact-checking and safety checks are unchanged.
- **Reliability still depends on the laptop.** If it is off, the cycle is skipped (it was off on
  26 Sep).

### Upload check, 23-30 Sep
57 uploads, 1 failure. YouTube 18 of 19 public; TikTok 19 of 19 (private, as expected); Bluesky 19 of 19.
- **30 Sep 09:05, YouTube, "Space Station Crew"**: YouTube returned a server error (HTTP 500) mid-upload.
  Confirmed not on the channel, then re-sent by the owner at 13:59 on 30 Sep: now public at https://www.youtube.com/watch?v=gl6mCNvCPdQ.
- **The YouTube login expired again on 30 Sep**, exactly 7 days after it was renewed on 23 Sep (it
  also expired 18 Sep). The owner logged in again on 30 Sep, so uploads work. Very likely cause: the
  Google Cloud OAuth consent screen is in "Testing", where logins last only 7 days.

### New plan approved 30 Sep: full AI control, faster learning, 3–4 channels
The owner chose to let the AI control the whole video and to run **3 or 4 channels** in different
niches, aiming for a niche that earns **ad revenue**, still at **$0**, still learning, and **never
posting many videos at once**. Videos keep publishing on their own. Free-to-reuse media (Pexels,
Pixabay, CC0 music) is allowed once the constitution is edited. Full plan and progress:
`docs/PLAN_FULL_CONTROL.md`.

What the code review found (30 Sep, nothing changed yet):
- The AI only chooses the words, captions, image searches and titles. Voice, speaking speed,
  captions, colours, motion and thumbnails are fixed; there is no music.
- It tests one thing at a time and needs 8 scored videos per side, so the one experiment running has
  no results yet (first data about 2 Oct).
- Only YouTube can teach it anything: TikTok is private and Bluesky has no view counts.
- Each run posts its 2 videos within about a minute of each other. Posting time is not chosen.
- Revenue is not collected; it needs one extra YouTube permission.

Stages: 0 measurement fixes and revenue permission → 1 every video tests many settings at once →
2 easy settings (voice, speed, captions, colours, motion) → 3 posting queue with limits →
4 extra channels → 5 stock photos, video clips, music, templates → 6 revenue-driven niche choice.

**Stage 0 is done (30 Sep).** What changed:
- The AI can now read **ad revenue** from YouTube, once you log in again and allow the new permission.
  Until the channel is in YouTube's Partner Program it will show "no revenue", which is expected.
- A video is no longer judged on views alone just because YouTube's watch-time numbers are late. It
  waits up to 5 days for them; if they never come, the score is marked "views only".
- A video posted to three platforms now counts as one result, not three.
- Experiments stay balanced on videos that actually get made, and can only use measures the
  platforms really report.
- The option to test "which platform" is switched off until each video can be sent to one platform
  only (stage 4). It was never used.
Checked against the live YouTube API: your current login still works, and the AI's numbers now show
17 scored videos out of 25.

**Stage 1 is done in code (30 Sep): every video can now test several settings at once.**
- Each video records the settings it was made with. For each setting the AI chooses to test, the value
  is picked per video by chance and by what has scored best, never by the topic, so its effect can be
  measured fairly.
- The AI decides which settings to test, which values to try, and when to settle on one. Engineering
  sets only safe limits (for example, speaking speed within about 25% of normal).
- Three settings exist so far: **speaking speed**, the **pause between sentences** and the **pause
  between scenes**. The rest (voice, captions, colours, motion, thumbnails) come in stage 2.
- Once 30 videos are scored, the AI also gets an estimate of each setting's effect, with the topic and
  the video length taken into account.
- The AI can now name a video's broad **niche** separately from its **angle**, and merge two niche names
  it decides mean the same thing. This fixes the scattered labels (three kinds of "history").
- On its first live look at the new controls the AI chose **not to test anything yet**. That is its call.
  From the next run, each video still records its settings (all at the current defaults).

**Stage 2 is done in code (30 Sep): the AI can now test 15 production settings.**
- Voice, caption font, size, position, colour and outline, words per caption, camera motion, longest
  shot length, background colour, headline on or off over photos, and a scene counter. Speaking speed
  and the two pauses came with stage 1.
- Every setting at every limit was rendered and checked automatically: **43 of 43 samples passed**.
  Fonts, colours, the counter and caption sizes were also checked by eye.
- **Bug fixed along the way:** the short pause after each scene was never actually in the videos, so
  captions ran up to about 3 seconds behind the voice by the end of a video. Every published video so
  far has this. From the next run, videos are about 0.35 s per scene longer and captions stay in sync.
- Large captions are now kept inside the space reserved for them, so they can never cover the
  on-screen text.
- **Not built:** a thumbnail setting. Thumbnails are never uploaded (the Shorts API does not take
  them), so it would change nothing a viewer sees.
- **Voice licences.** Many free Piper voices forbid commercial use. Since 1 Oct (owner decision) there is no
  default voice and only 5 cleared voices (public-domain recordings) can be spoken. Every video before
  1 Oct used the research-only lessac voice; those videos stay up.

**Stage 3 is done (30 Sep): your posting limits are enforced in code.**
- At most **2 videos per platform in one run**; a run posts only **2.5 hours after the previous one**;
  at most **2 posting runs a day** (4 videos per platform; was 4 runs until 1 Oct). The AI cannot change these; they live in
  `config.yaml`, which only you edit.
- Anything not allowed to post waits, oldest first, for the next run. If more than 2 days of videos
  pile up, the AI stops making new ones until the queue drains.
- Your own re-sends (`aimz publish retry`) follow the same limits and say so when they have to wait.
- With your approval the schedule is now **09:00 and 18:00** (since 1 Oct).
- Each video records the hour it went out, so the AI can learn which times work.
- `aimz status` now shows posting runs today and whether the next run may post.

**Stage 4 is done in code (30 Sep): the system can run several channels.** You are adding 3 more YouTube
channels on 1 Oct (4 in total).
- Each channel is a separate copy of the system: its own login, videos, data, strategy and
  posting limits. They share the code, your constitution, the voices and the Google app.
- Channels take turns on the laptop's GPU, and a channel never touches another's data or schedule.
- **Niches:** once a channel has 3 or more measured videos in a niche (and more than any other
  channel), the other channels stop making ideas in it. Each channel's AI still picks its own niches.
- **Shared learning:** what works for voice, captions and pacing is learned from all channels together.
  Topics and hooks are learned per channel.
- **Your steps for each new channel, about 5 minutes** (details in `docs/AUTONOMOUS_SETUP.md`, Part F):
  create it on YouTube → `python -m aimz instance create <name> --channel-name "..."` →
  `python -m aimz --instance <name> youtube auth` (pick the new channel) → `... youtube whoami` to
  check → `... schedule install --times ...` (the create command prints the times).
- New channels start with TikTok and Bluesky off.
- `python -m aimz status --all` shows every channel on one screen.

**Stages 5 and 6 are done in code (30 Sep):**
- **Music and sound:** a small original music library (calm, tense, bright) and a whoosh at cuts.
  Generated by our own code, so there are no copyright or Content ID risks. The music drops in volume
  whenever the voice speaks. The AI decides whether to use it.
- **Looks:** three layouts (card, full-screen photo, numbered list) and a looping ending, which ends on the
  opening frame so replays feel seamless.
- **Stock photos and video clips (Pexels, Pixabay):** built but **switched off**. They need your
  constitution edit, the free API keys in `.env`, and one switch in `config.yaml`.
- **Money:** `python -m aimz money` shows progress toward YouTube's Partner Program (today 2 of 1,000
  subscribers; 2,697 of 10 million Shorts views in 90 days) and ranks niches by expected value.
- **What counts as success:** the score now includes watch time and revenue, but at weight 0 until you
  choose (decision 11).
- **Fixed after your screenshot (30 Sep evening):**
  - Cards printed the same sentence as the subtitles, cut mid-word by a 60-character limit that dated
    from the first version.
  - Subtitles and image credits sat under YouTube's own buttons and labels.
  - Now a card never repeats the spoken words and text is never cut mid-word. All text stays in the
    part of the screen YouTube leaves visible. Videos already posted keep the old layout.
- **The 18:00 run on 30 Sep, the first on the new code, was clean:** 2 videos made, 2 posts per platform,
  captions in sync, and every setting recorded.

### 1 Oct: YouTube compliance (new)
- **Google's API compliance review is not finished.** This replaces the earlier "audit cleared". Google's third and final notice (1 Oct) asks for:
  - a description or screen recording of the upload process;
  - all channel links;
  - due within 7 business days, about **12 Oct**.

  The description is drafted in `data/audit/upload-process-script.md`.
- **A policy check against Google's live pages found gaps:**
  - no deletion of YouTube data when access is revoked;
  - comments kept longer than 30 days;
  - an out-of-date privacy page;
  - new channels would inherit auto-publish consent;
  - the default voice (Lessac) is licensed for research only, which is not allowed on a monetized channel;
  - some titles are inaccurate;
  - one video with negative claims about a named living person (Unitree) published without review;
  - high "inauthentic content" risk for monetization.
- **Owner decisions (1 Oct):**
  - Leave the Unitree video up for now.
  - No default voice; the AI picks only from voices cleared for commercial use.
  - 2 posts per run, 2 runs a day, per channel.
  - Every claim needs a source, titles included.
  - Each channel is distinct in voice, look and genre.
  - Longer videos are deferred until compliance is sorted.
- **Plan:** `docs/PLAN_FULL_CONTROL.md` stages C and L.
- **Done the same evening (stage C, in code, 277 tests).** Checked on a copy of the database and against the live YouTube login; the first real runs are the 18:00 cycle on 1 Oct.
  - Revoked or failing access: YouTube data is deleted within 7 days (`youtube revoke` deletes it at once).
  - Comments and saved upload records: limited to 30 days.
  - Auto-publishing: consent is given per channel with `instance consent`.
  - Voices: only the five cleared voices can be used, with no default.
  - Posting: at most 2 posts per run, 2 runs a day (schedule 09:00 and 18:00).
  - Titles and hooks: fact-checked like the narration.
  - Negative claims about named people: sent to the owner for review.
  - Descriptions: internal IDs removed.
  - Channel identity: each channel's AI picks its own genre, voice and look, different from the other channels, starting from the owner's list in `config.yaml`. The same story is never used on two channels.
- **Privacy page published** 1 Oct, with public contact backhousegroupnj@gmail.com.
- **Owner decision 1 Oct:** no new channels until YouTube has been contacted.
- **Still open (owner):**
  - Send the Google reply (a Gmail draft is ready) by about 12 Oct. It lists Backhouse Explainers and says up to three more channels may follow.
  - After that, create the new channels and send Google their links before any upload to them.
  - The asset-relevance check (no unrelated images) is not built yet.

### Open loop for 1 Oct (owner)
- **Long-term fix for the weekly YouTube login expiry.** In Google Cloud Console, open APIs & Services
  → OAuth consent screen and check the publishing status. If it says Testing, set it to **In
  production**. **Then** log in again (`.\.venv\Scripts\python.exe -m aimz youtube auth`): stage 0
  has landed, so this one login also grants the revenue permission. Tick every box Google shows. A
  "Google hasn't verified this app" warning is expected for your own app. Do it before about **7 Oct**,
  when the current login runs out. **Verify:** YouTube still uploads after 7 Oct.

### Decisions for the CEO
0. **Now:** the 19 Sep Borneo video never reached YouTube (checked on the channel). The topic was
   covered by a different video on 23 Sep (154 views), so the recommendation is to **let it go**.
   Uploads that failed before 28 Sep are not retried automatically; one command re-sends it if you
   want it anyway (see Part 2).
1. **Record the retry decision in the vault:** failed uploads are now retried after a check
   (replaces D-010's "never retried").
2. **Keep running at $0** to finish the experiment as designed, or **fund a V1**. Best return per
   dollar, in order: a better script-writing model, a better voice, stock footage. See `docs/FUNDED_V1.md`.
3. **Move it off the laptop** (a small always-on machine) so scheduled runs stop getting skipped.
4. **TikTok:** approve the time to film the demo video TikTok's app review requires.
5. **Record the 30 Sep decisions in the vault:** plan B+D (full control + 3–4 channels, $0);
   mission adds ad revenue but keeps learning and no bulk posting; free-to-reuse media allowed.
6. **Edit the constitution** (owner-only file): draft wording for the mission, media licences and
   creative autonomy is in `docs/PLAN_FULL_CONTROL.md` section 8.
7. **Posting limits:** 2 per platform per run, 2.5 h apart, **2 runs a day** (changed 1 Oct) and in force; held on 1-2 Oct.
8. **Channels:** decided 30 Sep (3 more, 4 in total). **On hold (owner, 1 Oct) until the reply to YouTube
   is sent**; then add them (`docs/AUTONOMOUS_SETUP.md` Part F) and send Google their links first.
9. **Seven scripts are waiting for sensitive-topic review** (one, a 2006 air crash, was a false 'financial advice' match, fixed 2 Oct) (dashboard or `aimz approve script <id>`).
10. **Narration voice licence:** settled 1 Oct (no default voice; only cleared voices). Optional tidy-up:
    delete the ignored `PIPER_VOICE=en_US-lessac-medium` line from `.env`.
11. **What counts as success:** set `scoring.weights` in `config.yaml` (watch_time, revenue). Both are 0
    today; see `docs/OWNER_TODO.md`.
12. **Stock media:** amend the constitution, add Pexels/Pixabay keys, then set `assets.stock.enabled: true`.

---

## Part 2: For the CEO's AI agent

### Identity
- Repo: https://github.com/scottvaaron-ctrl/AI-MEDIA-ZERO (branch `main`). Python package `aimz`.
- Runs on the owner's Windows 11 laptop (RTX 4060) at `C:\Users\scott\Documents\Agentic_Youtube`,
  Python 3.14, venv `.venv`.
- Stack: Ollama (`qwen3:8b`), Piper TTS, Pillow + Wikimedia assets, FFmpeg (1080x1920 H.264),
  RSS research, SQLite with migrations (latest `0005_posting_runs`), FastAPI dashboard on `127.0.0.1:8420`.
- 251 tests (`httpx.MockTransport` for platform APIs); ruff and mypy clean.

### Hard rules (from `config/constitution.md` and owner decisions). Do not violate these.
1. **$0.00 usage-based spend.** No paid APIs or hosting unless the owner raises `MONTHLY_BUDGET_USD`
   in `.env`. Agents never write `.env`.
2. **Official platform APIs only.** No browser automation, no scraping behind logins, no paid
   auto-posting services.
3. Every provider call goes through `Provider.authorized()` (budget check, usage row; the kill switch
   for paid providers). Every platform write also checks the kill switch, including retries and
   finishing a post that was still encoding.
4. API writes need owner consent: per-video approval or `AUTOPUBLISH_CONSENT=true` (currently on).
5. **Failed uploads are never retried blindly** (owner decision 2026-09-28, replacing "never
   retried"). A retry first asks the platform whether the earlier attempt posted
   (`Publisher.find_existing`, verified live on YouTube and Bluesky 2026-09-28); at most
   `publishing.max_attempts` (3), then `abandoned` with an alert.
6. Only PD / CC0 / CC BY / CC BY-SA media. AI-generated labels are applied where platforms require them.
7. A technical failure never rejects content: it is re-queued, and parked for the owner after
   repeated failures.

### Operating commands
Launch through the interpreter. Windows Smart App Control blocks the `aimz.exe` shim, and under
Task Scheduler that block fails silently.
```powershell
python -m aimz doctor            # health of every dependency and platform connection
python -m aimz status            # budget, counts, last run, errors in 24h, needs_attention
python -m aimz publish list      # what has gone out
python -m aimz publish retry <publication_id>   # re-send a failed/closed-out upload now
python -m aimz requeue idea|script <id>          # put a parked item back in the queue
python -m aimz analytics         # per-video views / retention / score
python -m aimz kill --reason "..."   # blocks all outbound writes; `aimz resume` undoes it
python -m aimz approve video <id> --reject
```
Scheduler: **one** Windows task, "AI Media Zero", with triggers at 09:00 and 18:00
(since 2026-10-01), runs
`scripts/run-cycle.ps1` (log: `data\logs\scheduled.log`; a copy that starts while a cycle is running
exits and notes it in `data\logs\scheduled-skipped.log`). The old tasks "AI Media Zero 0900"/"1800"
were removed on 2026-09-28.

### Key files
| Path | Purpose |
|---|---|
| `config/constitution.md` | Owner rules the AI cannot edit |
| `config/strategy.md` | AI-editable strategy memory (v31, confidence 0.10) |
| `config/config.yaml`, `config/feeds.yaml` | Pipeline, retry and measurement settings; safety patterns; research feeds |
| `data/reports/daily_YYYY-MM-DD.md` | Daily learning summary |
| `docs/ARCHITECTURE.md`, `docs/COMPLIANCE.md` | System design; verified platform requirements |
| `docs/PLAN_FULL_CONTROL.md` | **Current work.** Owner-approved staged plan (30 Sep): audit findings, stages 0–6 with done-criteria, owner checklist, constitution drafts, session log |
| `docs/HANDOFF.md` | Platform pattern, reliability model (4a); points to the plan for current work |
| `docs/CHECK_IN.md` | Returning-owner checklist |
| `docs/FUNDED_V1.md` | How to add paid providers without a redesign |

### Platform facts that differ from the docs (verified live)
- **TikTok (unaudited):** needs `SELF_ONLY` **and** a private account, otherwise you get HTTP 403.
  `publicaly_available_post_id` is never returned, so TikTok yields no metrics until the audit passes.
- **Bluesky:** no audit. Read the AT Protocol lexicon JSON on GitHub; the docs site returns empty.
  `com.atproto.repo.listRecords` is used to look for an earlier attempt's post before a retry.
- **YouTube:** uploads land public, but the **API compliance review / quota-increase request is NOT
  complete**. Google's third and final notice came 1 Oct 2026 (thread "YouTube API Services: Thank
  you for your submission"), asking for an upload-process script or screencast plus channel links
  within 7 business days, i.e. by about **12 Oct**. Draft answer: `data/audit/upload-process-script.md`.
  If it lapses, the quota increase is refused; current default quota already covers 4 channels.
  Analytics lag 1-2 days. The uploads
  playlist (`channels.list mine` then `playlistItems.list`, 2 quota units) is used before a retry.
- In general, mocks built from docs have passed while real calls failed. Verify against the live API
  before trusting a test.

### Adding a platform
Write two classes plus wiring: `providers/publishers/<p>.py` (`Publisher`, including
`find_existing`) and `providers/analytics/<p>.py` (`AnalyticsProvider`), registered in
`providers/registry.py` behind a `.env` switch. Add `aimz <p> auth` in `cli.py` and tests covering the
happy path, consent gate, kill switch, validation and `find_existing`. Use `bluesky.py` as the
template. Full contract is in `docs/HANDOFF.md` section 4.

### Known open issues
- **Plan in progress (30 Sep): `docs/PLAN_FULL_CONTROL.md`.** Stage 0 done 2026-09-30:
  - `yt-analytics-monetary.readonly` is requested at `aimz youtube auth` but optional. Tokens now load
    with their **granted** scopes (a refresh asking for an ungranted scope fails `invalid_scope`).
    Revenue is fetched only when granted; "not monetized" is stored as `revenue_usd=None`. The live
    monetary query is **unverified until the owner re-auths**;
  - YouTube scores wait for Analytics rows; after 120 h without them the score is `reach_only`;
  - stats count one sample per video (`allocation.per_video`);
  - arms balance on ideas that are not rejected;
  - `retention_3s`/`completion_rate` KPIs were removed;
  - the hook is written from the assigned hook type;
  - ages count from a scheduled publish time.

  Stage 1 done 2026-09-30 (migration `0004_video_settings`; DB backup `data/backups/aimz_2026-09-30_pre-stage1.sqlite3`):
  - `setting_space` holds the dials, defined with bounds in `experiments/settings.py` `CATALOG`;
  - `video_settings` holds every video's values: open settings are assigned per video (exploration
    floor plus Thompson sampling), and niche/angle/hook are recorded as controls;
  - `StrategyUpdate.setting_changes` and `niche_merges`, with refusals fed back via `last_rejected_setting`;
  - the regression runs from 30 scored videos;
  - `aimz settings` shows the dials and their evidence.
  - Live dials: `speech_length_scale`, `sentence_pause_s`, `beat_pause_s` (pulled forward from stage 2).
    The strategist has not opened one yet.
  Stage 2 done 2026-09-30:
  - 12 more dials: `voice`, `caption_*` (font, size, position, color, outline, max_words), `motion_style`,
    `max_scene_s`, `palette_hue`, `headline_on_cards`, `progress_counter`; plus `Beat.zoom`.
  - Voices are limited to `COMMERCIAL_SAFE_VOICES` (7 downloaded to `data/voices`, 530 MB). The default
    lessac voice is research-only (see `docs/COMPLIANCE.md`).
  - 4 OFL fonts are in `assets/fonts` with `licenses.json`.
  - Scene clips no longer use `-shortest` (audio padded, `apad`): the beat pause is now rendered and
    captions no longer drift late.
  - Captions are limited to the bottom 32% band (`caption_char_limit`).
  - `scripts/render_matrix.py` rendered 43/43 samples OK.
  - `thumbnail_style` dropped (thumbnail never uploaded).
  Stage 3 done 2026-09-30 (migration `0005_posting_runs`; DB backup `data/backups/aimz_2026-09-30_pre-stage3.sqlite3`):
  - `pipeline/posting.py` enforces `config.yaml` `publishing.limits`: 2 posts per platform per run,
    2.5 h between posting runs, 2 posting runs per local day (4 until 1 Oct).
  - Deferred platforms wait (`status: deferred`); the backlog guard pauses production over 2 days.
  - `aimz status` has a `posting` block, and `schedule install` validates times.
  - `post_hour` is recorded per video.
  Stage 4 done 2026-09-30:
  - one instance per channel in `instances/<name>/` (git-ignored), selected with `--instance`;
    `aimz/instances.py`, `aimz/experiments/shared.py`;
  - per-instance task and skip-mutex, and a shared GPU mutex that waits up to 2 h;
  - niche claims and pooled production evidence;
  - `instance create|list`, `status --all`, `youtube whoami`.
  - The owner creates the 3 channels on 1 Oct.
  Stages 5 and 6 done 2026-09-30:
  - `providers/assets/stock.py` is off (config `assets.stock.enabled`; `PEXELS_API_KEY` /
    `PIXABAY_API_KEY`) and not live-verified.
  - Clip scenes, `assets/music` and `assets/sfx` (generated, CC0) with a ducked mix; `template`,
    `music`, `music_volume_db`, `sfx`, `ending`, and `clip_scenes` (hidden without stock video).
  - Score parts `watch_time` / `revenue` at weight 0 (`config.yaml` `scoring`); `experiments/value.py`;
    `aimz money`.
  - Owner steps are in `docs/OWNER_TODO.md`.

  Still open (later stages):
  - only YouTube yields a score;
- `config.yaml` `require_owner_approval: true` is overridden by `AUTOPUBLISH_CONSENT=true`: every
  video publishes on its own (consistent with vault D-014).
- **YouTube OAuth token expires every 7 days** (`invalid_grant` on 18 Sep and 30 Sep; the token was
  re-issued 23 Sep and 30 Sep). This is consistent with the consent screen being in Testing. Owner action is
  due 1 Oct (Part 1, "Open loop"). While the token is dead, uploads fail as `RefreshError` →
  `transient` and are retried, but each attempt counts, so after 3 cycles they are `abandoned`.
  `retry_due` checks do not use attempts. Check live with a `channels.list mine` call; `aimz doctor`
  may not catch an expired refresh token until it tries to refresh.
- **30 Sep YouTube HTTP 500** (`pub_20260930T130519_63b438db`, `failure_kind=uncertain`): verified
  absent from the uploads playlist, then re-sent with `aimz publish retry` at 30 Sep 13:59 local (`gl6mCNvCPdQ`, public).
- **29 Sep 09:00: Smart App Control blocked Piper's `espeakbridge` DLL** (2 renders failed). It has not recurred in the 3 runs since, and no code changed.
- **Fixed 2026-09-28 (owner-approved; engineering and measurement only):**
  - Critic: a failing score with no stated reason is re-asked once, then not gated (owner decision).
  - Editor: a shortlist it rejects outright is marked `rejected` and the slot falls back to other
    ideas; slots only go to families with a candidate; videos in progress count as tried.
  - Retries: checked automatic retry for failed uploads (`retry_due`), `abandoned` close-out,
    `failure_kind`, `next_attempt_at`; Bluesky kill-switch and double-post bugs fixed.
  - Recovery: a file lock per cycle; `Orchestrator.recover()` closes dead runs (`abandoned`, alerts)
    and re-queues their ideas, renders and uploads; Ctrl+C closes a run as `interrupted`;
    `TechnicalFailure` for model outages; parking after repeated technical failures.
  - Scheduler: one task with two triggers, a mutex in the runner.
  - Timeouts: ffmpeg 30 min, ffprobe 60 s; Ollama timeouts no longer retried (10 min max per call).
  - Measurement: 14-day window then `measured`; experiments accept `mean_score` and reject unknown
    KPIs; one sample per video; the least-filled experiment gets the next idea; retiring an experiment
    frees unwritten ideas; late scores (after 120 h) are flagged; published counts are distinct videos.
  - Timestamps are compared as ISO text (`iso_ago`), which also makes the 21-day idea expiry work.
- **Legacy failed upload:** the 19 Sep Borneo video (`pub_20260919T153400_2e871c36`) is not on
  YouTube; its topic was published on 23 Sep. Recommendation: leave it. It is listed under
  `needs_attention` until the owner decides.
- **The analyst repeats itself.** It re-retires experiments that are already retired, and its explore-ratio
  cuts are held at 0.7 by design until 12 videos are measured while the change_summary reports them.
- **Human creative direction was removed on 2026-09-23** (see the 23 Sep entry in git history). The renderer's
  visual design is still human-made; the AI has no control over it yet.
- **Bluesky reports no view counts**, so its posts get no score and do not feed learning.
- **Topic families are often mislabelled** by the ideation model (a baseball record filed as
  `local_history`), which makes per-family learning noisy. This is the AI's creative area and was
  left alone.
- `target_platform` is **no longer assignable** (removed 2026-09-30, plan stage 0): every video still goes
  to every platform, so such an experiment compared nothing. The owner had kept it for future
  per-platform routing; it returns when stage 4 routes videos. None was ever run.
- Scheduled runs are skipped when the laptop is off (26 Sep). An always-on machine is decision 3.
- The TikTok chunked-upload response codes for files over 64 MB and the Display API field list are
  still unverified.

### How to report to the CEO
Lead with: publications this week, views and average % viewed by platform, dollars spent (should be
$0), strategy version and confidence, errors and skipped runs, anything under `needs_attention`, and
any decision that needs the owner (content rejections, funding, TikTok audit).
