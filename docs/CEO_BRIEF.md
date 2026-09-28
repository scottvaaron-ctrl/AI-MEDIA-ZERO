# AI Media Zero: Brief for the CEO and the CEO's AI Agent

_Snapshot as of 2026-09-28 (evening). Numbers come from `aimz status`, the `metrics`, `runs` and
`errors` tables, and a verification cycle run on a copy of the database after the 28 Sep fixes._

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
Twice a day (09:00 and 18:00) the laptop runs a full cycle without anyone touching it. All the
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

---

## Part 2: For the CEO's AI agent

### Identity
- Repo: https://github.com/scottvaaron-ctrl/AI-MEDIA-ZERO (branch `main`). Python package `aimz`.
- Runs on the owner's Windows 11 laptop (RTX 4060) at `C:\Users\scott\Documents\Agentic_Youtube`,
  Python 3.14, venv `.venv`.
- Stack: Ollama (`qwen3:8b`), Piper TTS, Pillow + Wikimedia assets, FFmpeg (1080x1920 H.264),
  RSS research, SQLite with migrations (latest `0003_reliability`), FastAPI dashboard on `127.0.0.1:8420`.
- 154 tests (`httpx.MockTransport` for platform APIs); ruff and mypy clean.

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
Scheduler: **one** Windows task, "AI Media Zero", with triggers at 09:00 and 18:00, runs
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
| `docs/HANDOFF.md` | Ranked engineering work, the reliability model (4a), the pattern for adding a platform |
| `docs/CHECK_IN.md` | Returning-owner checklist |
| `docs/FUNDED_V1.md` | How to add paid providers without a redesign |

### Platform facts that differ from the docs (verified live)
- **TikTok (unaudited):** needs `SELF_ONLY` **and** a private account, otherwise you get HTTP 403.
  `publicaly_available_post_id` is never returned, so TikTok yields no metrics until the audit passes.
- **Bluesky:** no audit. Read the AT Protocol lexicon JSON on GitHub; the docs site returns empty.
  `com.atproto.repo.listRecords` is used to look for an earlier attempt's post before a retry.
- **YouTube:** the API audit has cleared and uploads land public. Analytics lag 1-2 days. The uploads
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
- `target_platform` experiments do not change where a video is posted yet (kept by owner decision
  for future per-platform routing).
- Scheduled runs are skipped when the laptop is off (26 Sep). An always-on machine is decision 3.
- The TikTok chunked-upload response codes for files over 64 MB and the Display API field list are
  still unverified.

### How to report to the CEO
Lead with: publications this week, views and average % viewed by platform, dollars spent (should be
$0), strategy version and confidence, errors and skipped runs, anything under `needs_attention`, and
any decision that needs the owner (content rejections, funding, TikTok audit).
