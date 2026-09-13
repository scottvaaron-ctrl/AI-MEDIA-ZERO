# AI Media Zero: Brief for the CEO and the CEO's AI Agent

_Snapshot as of 2026-09-13. Numbers come from `aimz status`, `aimz analytics` and
`data/reports/daily_2026-09-12.md`._

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
| Bluesky (@backhouse06.bsky.social) | Live, public, posting automatically | Yes, within hours |
| TikTok (backhouse67) | Working, but private until TikTok approves the app | No |

**Early results:** 13 publications. The first three measured YouTube videos drew **257, 199 and 119
views**, with viewers watching 24% to 48% of each video on average. Spend so far is **$0.00**.
The AI rates its own confidence at **0.10 out of 1**. That is correct: three data points tell it
almost nothing yet. It is testing three topic families and running one A/B test (number-led vs.
story-led openings). An early hint is that 35-60 second videos do better than shorter ones.

### What is honest to say
- **It works end to end.** Real videos go out on real platforms with no human in the loop and no cost.
- **It is too early to judge the learning.** Meaningful signals need dozens of videos, which means
  weeks of running.
- **Quality is limited by free tools.** A small local model writes thinner scripts than paid models.
  Of the ideas it attempted, 18 scripts failed quality checks and 10 were rejected. The filters are
  doing their job.
- **Reliability depends on the laptop.** If it is asleep or the local AI isn't running, the cycle
  fails quietly. The last run (12 Sep) reported the AI model as unreachable, with 9 errors in 24 hours.

### Decisions for the CEO
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
- **YouTube:** the API audit has cleared and uploads land public. Analytics lag 1-2 days.
- In general, mocks built from docs have passed while real calls failed. Verify against the live API
  before trusting a test.

### Adding a platform
Write two classes plus wiring: `providers/publishers/<p>.py` (`Publisher`) and
`providers/analytics/<p>.py` (`AnalyticsProvider`), registered in `providers/registry.py` behind a
`.env` switch. Add `aimz <p> auth` in `cli.py` and tests covering the happy path, consent gate, kill
switch, validation and the no-retry rule. Use `bluesky.py` as the template. Full contract is in
`docs/HANDOFF.md` section 4.

### Known open issues
- Scheduled runs fail when Ollama isn't running or the laptop sleeps (last run: model unreachable,
  9 errors in 24h). Consider starting Ollama inside `run-cycle.ps1` and adding a wake timer.
- The TikTok chunked-upload response codes for files over 64 MB and the Display API field list are
  still unverified.
- Sample size is tiny (3 measured videos). Do not draw strategy conclusions yet.

### How to report to the CEO
Lead with: publications this week, views and average % viewed by platform, dollars spent (should be
$0), strategy version and confidence, errors and skipped runs, and any decision that needs the
owner (content rejections, funding, TikTok audit).
