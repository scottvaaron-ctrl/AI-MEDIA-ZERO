# AI Media Zero: Brief for the CEO and the CEO's AI Agent

_Snapshot as of 2026-09-23 (evening). Numbers come from `aimz status`, `aimz analytics`, the `metrics` and
`errors` tables and `data/reports/daily_2026-09-23.md`._

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
| YouTube Shorts ("Backhouse Explainers") | Reconnected 23 Sep after its login expired 19 Sep | Yes, 1-2 days late |
| Bluesky (@backhouse06.bsky.social) | Live, public, posting automatically | Yes, within hours |
| TikTok (backhouse67) | Working, but private until TikTok approves the app | No |

**Results so far:** 5 videos on YouTube (last one 14 Sep), **650 views** in total as of 23 Sep:
252, 197, 123, 59 and 19. Viewers watched 24% to 85% of each video. No likes, comments or
subscribers yet. Views stop growing about 2 days after posting. Spend so far is **$0.00**.

**What the AI has learned: almost nothing new since 13 Sep.** Its strategy notes went from version 12
to 22, but every update re-read the same 5 measurements, because YouTube data stopped arriving on
14 Sep. Confidence is still **0.10 out of 1**. Its only two leads are still hints from one or two
videos each: 35-60 second videos beat shorter ones, and a number-led opening (1 video) beat
story-led ones (4 videos). The A/B test has no videos in the number-led arm yet.

**Why it stalled:**
1. **YouTube login expired on 19 Sep.** The 19 Sep video ("The 1945 Borneo POW Camp Rescue") failed
   to upload, and every metrics read since has failed. Runs still report "ok", so nothing raised an
   alarm. Google expires these logins after 7 days while the Google Cloud app is in "Testing" mode.
2. **Almost no new videos.** Only one video was made after 14 Sep. Scripts keep failing the quality
   checks (41 failed QA, 21 rejected in total), and today's run approved 0 scripts.
3. **Skipped days.** No runs on 15-17 or 20-21 Sep, most likely because the laptop was off or asleep.

**Fixed on 23 Sep:** YouTube is reconnected. Failed runs now pop up a Windows alert. The writer now
gets the full article instead of a one-line summary, which should cut the invented-fact rejections.
The scoring no longer rates a video 19 people saw above one 250 people saw. None of these
changes tell the AI what content to make.

### What is honest to say
- **It works end to end.** Real videos go out on real platforms with no human in the loop and no cost.
- **It is too early to judge the learning.** Meaningful signals need dozens of videos, which means
  weeks of running.
- **Quality is limited by free tools.** A small local model writes thinner scripts than paid models.
  So far 41 scripts failed quality checks and 21 were rejected, mostly for stating facts their
  sources did not contain. The filters are doing their job.
- **Reliability depends on the laptop.** If it is asleep, the cycle is skipped. A lost login also
  fails quietly: runs report "ok" while YouTube errors pile up (15 in the last 24 hours).

### Decisions for the CEO
0. **Now:** YouTube was reconnected on 23 Sep. To stop the login expiring again every 7 days, set
   the Google Cloud OAuth app's publishing status to **In production**. The owner approved the 9
   scripts that were waiting in `needs_owner_review` on 23 Sep.
1. **Keep running at $0** to finish the experiment as designed, or **fund a V1**. Adding money is a
   settings change plus new plug-ins, not a rewrite. Best return per dollar, in order: a better
   script-writing model, a better voice, stock footage. See `docs/FUNDED_V1.md`.
2. **Move it off the laptop** (a small always-on machine) so scheduled runs stop getting skipped.
3. **TikTok:** approve the time to film the demo video TikTok's app review requires. Until then
   TikTok adds nothing.
4. **Risk appetite:** posts go public with no human review. Safety filters and a banned-phrase list
   exist, and one bad title has already slipped through (since fixed). The kill switch stops
   everything in one command.

---

## Part 2: For the CEO's AI agent

### Identity
- Repo: https://github.com/scottvaaron-ctrl/AI-MEDIA-ZERO (branch `main`). Python package `aimz`.
- Runs on the owner's Windows 11 laptop (RTX 4060) at `C:\Users\scott\Documents\Agentic_Youtube`,
  Python 3.14, venv `.venv`.
- Stack: Ollama (`qwen3:8b`), Piper TTS, Pillow + Wikimedia assets, FFmpeg (1080x1920 H.264),
  RSS research, SQLite with migrations, FastAPI dashboard on `127.0.0.1:8420`.
- 94 tests (`httpx.MockTransport` for platform APIs); ruff and mypy clean.

### Hard rules (from `config/constitution.md`). Do not violate these.
1. **$0.00 usage-based spend.** No paid APIs or hosting unless the owner raises `MONTHLY_BUDGET_USD`
   in `.env`. Agents never write `.env`.
2. **Official platform APIs only.** No browser automation, no scraping behind logins, no paid
   auto-posting services.
3. Every provider call goes through `Provider.authorized()` (budget check, kill switch, usage row).
4. API writes need owner consent: per-video approval or `AUTOPUBLISH_CONSENT=true` (currently on).
5. **Failed uploads are never retried automatically.**
6. Only PD / CC0 / CC BY / CC BY-SA media. AI-generated labels are applied where platforms require them.

### Operating commands
Launch through the interpreter. Windows Smart App Control blocks the `aimz.exe` shim, and under
Task Scheduler that block fails silently.
```powershell
python -m aimz doctor            # health of every dependency and platform connection
python -m aimz status            # budget, counts, last run, errors in 24h
python -m aimz publish list      # what has gone out
python -m aimz run --stages metrics
python -m aimz analytics         # per-video views / retention / score
python -m aimz kill --reason "..."   # blocks all outbound writes; `aimz resume` undoes it
python -m aimz approve video <id> --reject
```
Scheduler: Windows tasks "AI Media Zero 0900" and "AI Media Zero 1800" run `scripts/run-cycle.ps1`,
which logs to `data\logs\scheduled.log`.

### Key files
| Path | Purpose |
|---|---|
| `config/constitution.md` | Owner rules the AI cannot edit |
| `config/strategy.md` | AI-editable strategy memory (v11, confidence 0.10) |
| `config/config.yaml`, `config/feeds.yaml` | Content profiles, safety patterns, research feeds |
| `data/reports/daily_YYYY-MM-DD.md` | Daily learning summary |
| `docs/ARCHITECTURE.md`, `docs/COMPLIANCE.md` | System design; verified platform requirements |
| `docs/HANDOFF.md` | Ranked engineering work and the pattern for adding a platform |
| `docs/CHECK_IN.md` | Returning-owner checklist |
| `docs/FUNDED_V1.md` | How to add paid providers without a redesign |

### Platform facts that differ from the docs (verified live)
- **TikTok (unaudited):** needs `SELF_ONLY` **and** a private account, otherwise you get HTTP 403.
  `publicaly_available_post_id` is never returned, so TikTok yields no metrics until the audit passes.
- **Bluesky:** no audit. Read the AT Protocol lexicon JSON on GitHub; the docs site returns empty.
- **YouTube:** the API audit has cleared and uploads land public. Analytics lag 1-2 days. The OAuth
  refresh token died with `invalid_grant` on 2026-09-19 (the last good call was 2026-09-14). This
  matches the 7-day limit for Google OAuth apps in "Testing" status. Fix: `aimz youtube auth`, and
  publish the OAuth consent screen to Production.
- In general, mocks built from docs have passed while real calls failed. Verify against the live API
  before trusting a test.

### Adding a platform
Write two classes plus wiring: `providers/publishers/<p>.py` (`Publisher`) and
`providers/analytics/<p>.py` (`AnalyticsProvider`), registered in `providers/registry.py` behind a
`.env` switch. Add `aimz <p> auth` in `cli.py` and tests covering the happy path, consent gate, kill
switch, validation and the no-retry rule. Use `bluesky.py` as the template. Full contract is in
`docs/HANDOFF.md` section 4.

### Known open issues
- **Fixed 2026-09-23 (owner-approved; engineering only, no creative direction added):**
  - Runs that record errors now finish `degraded` instead of `ok`, `aimz run` exits 2, and
    `run-cycle.ps1` raises a Windows notification. The YouTube outage went 4 days unseen.
  - Elevated-review flags from the critic count only if it quotes the script. The small model was
    echoing the whole topic list, which sent 6 of 9 queued scripts to the owner (one was about a moon of Jupiter).
    The keyword backstop still applies and now matches at word starts.
  - The writer, fact-checker and critic now get the full article behind each lead
    (`ResearchAgent.enrich`, migration 0002). QA failures were mostly the writer padding thin feed
    summaries with invented facts.
  - The score treats every view count the same way: percentages are shrunk toward a prior, share and
    subscriber rates are smoothed, a reach term (log views) is added, and each video is scored at its
    first snapshot at least 72 hours after posting. The old formula scored 19 views at 85% watched as
    0.85 and 252 views at 24% as 0.11. The new one gives 0.24 and 0.21.
- **The 19 Sep Borneo video failed to upload** and is not retried automatically. The owner re-uploads it or lets it go.
- **The analyst repeats itself.** Every version retires an experiment that is already retired and
  proposes experiments that `ExperimentEngine.validate` silently drops. Its explore-ratio cuts are held
  at 0.7 by design until 12 videos are measured, but the change_summary still reports them as applied.
- **Human creative direction removed (owner decision, 2026-09-23).** Constitution section 8 gives the AI
  control of topics, formats, hooks and length, and the owner confirmed it. Removed:
  - the eleven starting content families and six hook types in `config.yaml` (the 8 never tried were
    also dropped from strategy memory, v23)
  - the hook-type style guide and the fixed script structure (6-8 beats, hook/tension/payoff, at least 2 image beats)
  - the "prefer a mechanism, surprising fact, payoff" and "spread across families" ideation nudges
  - the uneven idea-scoring weights (now equal)
  - the auto-seeded "numeric vs narrative hook" experiment (retired)
  - the critic's pass/fail gate on hook, pacing, payoff and clarity; it now gates only on factual
    support and originality, which the constitution requires
  - the human 20-90 s length range; the range is now 10-180 s, the platform limits
  - the rule that padded scripts up to the ideation runtime estimate, which forced the writer to invent detail
  Kept: constitution rules (honesty, no engagement bait, no AI-slop phrasing, safety review), the
  owner's goal ranking for the editor, renderer limits, and the owner-controlled feed list. The
  renderer's visual design (card layouts, fonts) is still human-made; the AI has no control over it yet.
- **Bluesky reports no view counts**, so its posts get no score and do not feed learning.
- Scheduled runs are skipped when the laptop is off. The owner confirmed it was off on 15-17 and
  20-21 Sep. Wake-to-run covers sleep, not shutdown, and an always-on machine is still decision 2.
- The TikTok chunked-upload response codes for files over 64 MB and the Display API field list are
  still unverified.
- Sample size is tiny (5 measured videos, 4 families). Do not draw strategy conclusions yet.

### How to report to the CEO
Lead with: publications this week, views and average % viewed by platform, dollars spent (should be
$0), strategy version and confidence, errors and skipped runs, and any decision that needs the
owner (content rejections, funding, TikTok audit).
