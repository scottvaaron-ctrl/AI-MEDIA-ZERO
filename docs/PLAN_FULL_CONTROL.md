# Plan: full AI control, faster learning, 3–4 channels

_Approved by the owner 2026-09-30 (option "B+D"). This is the working plan for every stage. Any agent
picking this up: read this file top to bottom, then `docs/HANDOFF.md`, then start at the first stage
whose status is not **Done**. Update the status table and the session log at the bottom before you
finish a session._

## 0. The goal, and the owner's decisions behind it

**Goal.** Give the AI control over every part of a video it could plausibly learn from, make it learn
many things per video instead of one thing per month, and run **3 or 4 channels** (owner: "either 3
or 4") in different niches so it finds a niche that earns **passive ad revenue**, at **$0**.

Owner decisions of 2026-09-30:

| # | Decision | Consequence for the code |
|---|---|---|
| 1 | Option **B+D**: full control at $0, plus several channels | This plan |
| 2 | Mission becomes "find an ad-revenue niche", **but it still learns and never pushes a lot of videos at once** | Hard posting limits the AI cannot exceed (stage 3). Volume grows mainly through more channels, not more posts per channel |
| 3 | **Videos publish on their own** | Already true: `AUTOPUBLISH_CONSENT=true` in `.env` overrides `publishing.require_owner_approval` (see §1). No change; sensitive topics still go to the owner at script stage |
| 4 | **"If it's free, it's allowed"** for media | Read as: free *to reuse commercially* (Pexels, Pixabay, CC0 music) in addition to PD/CC0/CC BY/CC BY-SA. Free-to-view is not free-to-reuse. Needs the owner's constitution edit (§8) |
| 5 | Total channels: **3 or 4** | Stage 4 builds for N instances; nothing hard-codes 3 |

Owner decisions of 2026-10-01, after the YouTube policy audit (stage C below):

| # | Decision | Consequence for the code |
|---|---|---|
| 6 | Unitree video (`xH63gV36DCE`, negative claims about a named living person) **stays up for now** | No takedown. The elevated-review gap that let it through is still fixed (C9) |
| 7 | **No default voice**; the AI chooses only from licence-cleared voices | Remove `PIPER_VOICE` default/inheritance; AI picks per channel from a commercial-safe list (no lessac, no `joe`) |
| 8 | Posting limits: **2 posts per run, 2 posting runs a day** (per platform per channel) | Replaces decision of 30 Sep (4 runs/day). `publishing.limits.max_runs_per_day: 2`; reinstall schedule |
| 9 | **Sources required for claims** (every factual claim, including titles, traces to a stored source) | Fact-check titles and hooks, not just narration. Not a minimum source count |
| 10 | **Each channel is distinct in voice, look and genre** (owner's examples: AI movie summaries, Minecraft explainers; examples only) | Per-channel identity locked at channel level; cross-channel dedupe; genre must be producible with licensed media (see C11) |

Standing rules that still apply (from `config/constitution.md`, `CLAUDE.md`, owner memory):
- $0 usage-based spend; no paid APIs, no browser automation, official platform APIs only.
- **The AI owns creative choices.** Engineering adds dials and measurement; it never picks values,
  niches, hooks or styles. Bounds exist only for platform limits, legibility, or the owner's posting
  limits.
- Honesty rules, licensed media with attribution, AI disclosure, elevated review.
- Verify every platform integration against the live API, not just mocks.
- Launch with `python -m aimz` (Smart App Control blocks the `aimz.exe` shim).

## 1. What the code does today (audit 2026-09-30, read-only)

The facts this plan is built on. File references are to `src/aimz/` unless stated.

**Publishing**
- Approval: `agents/publisher.py:160-164,213`. `AUTOPUBLISH_CONSENT=true` (`.env`) makes every video
  approved; each publisher re-checks (`youtube.py:195`, `tiktok_direct.py:336`, `bluesky.py:565`).
- Elevated review only at script stage (`agents/critic.py:203-222` → `needs_owner_review`,
  `pipeline/orchestrator.py:295-297`). 3 scripts waiting on 2026-09-30.
- **Posts go out in bursts**: publish runs right after produce (`orchestrator.py:94-95,359-371`); a
  cycle's 2 videos go out 30–100 s apart on every platform. `recommended_post_time` is a fixed
  "tomorrow 17:00" note (`publisher.py:78-81`). YouTube `scheduled` mode never gets a `publish_at`
  (`youtube.py:158-159`). Column `publications.scheduled_for` exists (0001:196) but is unused. No gap,
  no daily cap.
- `target_platform` is never honored: every video goes to every publisher (`publisher.py:168`).

**Throughput**
- Two scheduled cycles/day (09:00, 18:00 local), 4–13 min each. Limits `config.yaml:10-16`: 8 ideas,
  2 selections, 2 productions. Steady state since 24 Sep: 4 videos/day. LLM (qwen3:8b) is the main
  time cost (script 36 s/call, ideation 89 s/call); render 16–98 s per video.
- Scripts: 54 `qa_failed`, 28 `rejected` overall, but only **1 failure since the 28 Sep fix**.

**Measurement**
- Score (`providers/analytics/store.py:21,41-70`): weighted retention 0.35, shares/1k 0.25, subs/1k
  0.20, completion 0.20, reach 0.20; taken from the first snapshot ≥72 h old (`store.py:32,144-157`).
- **Only YouTube produces a score.** TikTok posts are SELF_ONLY and `platform_video_id` is never
  stored, so TikTok analytics never run; Bluesky has no views, so its score is `None`.
- At 72 h YouTube Analytics rows are often still empty, so the score is almost all reach (views).
- **Revenue is never fetched** (`revenue_usd` is manual-entry only). YouTube scopes are
  `youtube.readonly` + `yt-analytics.readonly` (`providers/publishers/youtube.py:36-39`); revenue
  needs `yt-analytics-monetary.readonly`.
- `retention_3s` and `completion_rate` are accepted as experiment KPIs (`experiments/engine.py`) but no
  API provides them, so such an experiment would stay at n=0 forever.
- Running experiment `exp_20260923T220649_d519d660` (hook type, `mean_score`) shows n=0 because no
  video is 72 h old yet; first samples ≈ 2026-10-02.

**Experiments and allocation**
- Only `hook_type`, `runtime`, `target_platform` are assignable (`engine.py:118-122`); two arms; one
  experiment per idea (`ideas.experiment_id/experiment_arm`, 0001:81-82); max 2 running.
- Arm is forced **after** the idea's hook line was written (`agents/editor.py:127-137`), so the script
  prompt can contradict itself. `format` branch at `editor.py:132` is dead.
- Arm balance counts rejected ideas (`engine.py:244-260`): 12 assigned, 5 published.
- Family and hook stats count **per publication**, so a video on two platforms counts twice
  (`experiments/allocation.py:59-65`, `agents/analyst.py:34-58`).
- Family labels are free text and fragment (`history`, `forgotten_history`,
  `unusual_historical_events`), keeping n per family at 1–2.

**Production dials — almost everything is fixed**

| Dial | Today | Where |
|---|---|---|
| Speaking speed | env `PIPER_LENGTH_SCALE`, per provider instance | `settings.py:140`, `providers/tts/piper.py:137` |
| Voice | env `PIPER_VOICE` = en_US-lessac-medium, cached once | `piper.py:65,117-122` |
| Pauses | 0.25 s between sentences, 0.35 s between beats | `piper.py:69,154`, `agents/producer.py:15,83` |
| Music, sound effects | none; `docs/COMPLIANCE.md:60` says no music | `providers/video/ffmpeg_renderer.py:252-259` |
| Captions | Arial, `h*0.036`, bottom-centre, white/black outline; sentence chunks ≤14 words; Piper gives sentence timing only | `ffmpeg_renderer.py:90,164-172`, `providers/tts/text_prep.py:17` |
| Scene length | one scene per beat, duration = TTS + 0.35 s | `producer.py:83` |
| Zoom | alternates in/out by index | `producer.py:123` |
| Layout | one Pillow card layout | `providers/image/cards.py:140-239` |
| Colour | hash of `video_id` | `cards.py:44-53` |
| Thumbnail | fixed card + "EXPLAINER"; AI's `thumbnail_concept` is stored and ignored | `cards.py:241-257`, `producer.py:148` |
| Ending, CTA, progress counter | none | — |
| Runtime | AI-chosen, 2.6 words/s; binding only in a runtime experiment | `agents/script.py:13,31-43,148-168` |

**Media**: owner library + Wikimedia Commons only (`providers/registry.py:140-145`); licence gate
`providers/assets/wikimedia.py:31-49` with `config.yaml:114`. Stills only, no video clips.

**Multi-channel**: one DB with no channel column, one strategy memory, one token per platform, one
scheduled task named "AI Media Zero" (`scheduler.py:13,141-143`) whose `install()` **deletes any other
task with that prefix** (`scheduler.py:215-245`). But `AIMZ_PROJECT_ROOT` (`settings.py:118`) already
relocates `.env`, data, db, config and secrets, so a separate instance per channel is close.
`run-cycle.ps1` takes a machine-wide mutex `Local\AIMediaZeroCycle`: keep it, so channels share the one
GPU/Ollama in turn. YouTube quota is per Google Cloud project and shared by all channels (100 uploads
+ 10,000 units/day: ample).

## 2. Status

| Stage | Name | Status | Needs owner? |
|---|---|---|---|
| 0 | Measurement fixes + revenue scope | **Done in code** (30 Sep); live revenue check waits for the owner's re-auth | Re-auth YouTube now (after the 1 Oct production switch) |
| 1 | Every video tests everything | **Done in code** (30 Sep); waiting on live evidence: first videos with recorded settings, and the strategist opening a setting on its own | No |
| 2 | Easy dials | **Done in code** (30 Sep): 15 dials, render matrix 43/43; `thumbnail_style` dropped (see stage 2). Live: first cycle on the new code pending | Voice licence decision (see §5) |
| 3 | Posting limits | **Done in code** (30 Sep) with the owner's rules (2 per platform per run, 2.5 h gap, 4 runs/day); schedule 09/12/15/18 installed. Live check: first runs on the new code | No |
| 4 | Multi-channel instances | **Done in code** (30 Sep); owner adds 3 channels 1 Oct (4 in total) | Create 3 channels, `instance create`, `youtube auth`, `schedule install` |
| 5 | Media, music, templates | **Done in code** (30 Sep): music, sfx, templates, loop ending live; stock photo/video providers built but **off** | Constitution edit, Pexels/Pixabay keys, `assets.stock.enabled: true`; then a live API check |
| 6 | Revenue-driven niche choice | **Done in code** (30 Sep); new score parts at weight 0 | Owner sets `scoring.weights` (watch_time, revenue) |
| C | YouTube policy compliance (audit 1 Oct) | **Done in code** (1 Oct): C1–C11, 277 tests, migration 0006 on the live DB, schedule 09:00/18:00. Privacy page published 1 Oct (8b1e650, contact backhousegroupnj@gmail.com). Owner: no new channels until YouTube is contacted. Open: first live runs on the code (1 Oct 18:00); first 30-day trims on 8 Oct; asset relevance check not built | Send the Google reply (draft ready); only then create channels and send Google their links before their first upload |
| L | Long-form videos | **Deferred** until stage C is done and Shorts have ~2 weeks on the new code | Decide when to start |

Order is deliberate: 0 must land before the owner's YouTube re-auth; 1 before any dial so every new
dial is measured from its first video; 3 before 4 so new channels never burst-post.

## 3. Stages

Every stage ends with: `pytest -q`, `ruff check src tests`, `ruff format src tests`, `mypy`, one real
`python -m aimz run` (or the relevant stages) on the live machine, and a live-API check for anything
that talks to a platform. Take a DB backup to `data/backups/` before any migration. Update this
file's status table and session log, `docs/CEO_BRIEF.md`, and `docs/HANDOFF.md` §1.

### Stage 0 — Measurement fixes and revenue scope

Small, independent fixes. Target: done before the owner re-authorises YouTube (the current token,
renewed 30 Sep, expires about 7 Oct).

1. **Revenue scope.** Add `https://www.googleapis.com/auth/yt-analytics-monetary.readonly` to the
   YouTube scopes (`providers/publishers/youtube.py:36-39`). Request `estimatedRevenue`,
   `estimatedAdRevenue`, `cpm`, `playbackBasedCpm` in `providers/analytics/youtube.py:86` **only when
   the token has the scope**, and treat "not monetized"/403 on those metrics as `None`, never as an
   error that drops the other metrics. Store in `revenue_usd` (exists) plus the raw payload.
   Verify live after the owner's re-auth: a monetary query returns rows or a clean "not monetized".
2. **Score only real data.** A YouTube score needs Analytics rows (`avg_percent_viewed` not NULL).
   If they are missing at 72 h, wait for the next snapshot, up to 120 h, then score with a
   `reach_only` flag (`providers/analytics/store.py:144-157`).
3. **One sample per video everywhere.** Aggregate per video, not per publication, in
   `experiments/allocation.py:59-65` and `agents/analyst.py:34-58` (and `group_stats`,
   `runtime_buckets`, `measured`). `engine.evaluate` already does it: reuse that.
4. **Balance arms on published videos**, not all assigned ideas (`engine.py:236-260`); `retire`
   should also clear the label on rejected ideas (`engine.py:224-228`).
5. **Drop unmeasurable KPIs** from `KPI_FIELDS`/`MEASURABLE_KPIS` (`retention_3s`, `completion_rate`)
   unless a platform actually returns them; retire any running experiment that uses one.
6. **`target_platform`**: remove it from `ASSIGNABLE_VARIABLES` until stage 4 gives real per-platform
   routing (the CEO brief records the owner kept it for future routing: note the removal there).
   Delete the dead `format` branch at `editor.py:132`.
7. **Hook type before the hook line.** When an experiment forces a hook type, regenerate the hook
   line for that type (or let the script agent write the hook from the type) so the prompt never
   contradicts itself (`editor.py:127-137`, `agents/script.py:44-51`).
8. Fix the negative `hours_since_post` for metrics taken before a scheduled `publishAt`
   (`store.py:91-98`); stage 3 starts using `publishAt`.

Done when: tests cover each fix; `aimz analytics` shows one row per video; a 72 h video with empty
Analytics is not scored; the owner has re-authed and a live monetary query behaves as above.

### Stage 1 — Every video tests everything

Replace "one variable, two arms, one experiment per idea" with per-video settings that are all
recorded and all learned from.

**Schema (migration `0004_video_settings.sql`)**
- `setting_space(variable PK, kind 'production'|'content'|'distribution', values_json, bounds_json,
  status 'open'|'locked'|'off', locked_value, updated_by, updated_at)`.
- `video_settings(video_id, variable, value, source 'random'|'bandit'|'ai'|'locked', created_at,
  PK(video_id, variable))`.
- Keep `experiments` and `ideas.experiment_*` for the AI's own formal A/B tests; nothing is deleted.

**Assignment** (new `experiments/settings.py`, called by the producer before TTS/render)
- *Production* variables (voice, speed, captions, …) are chosen **independently of the idea** so
  their effect is not confounded with the topic: Thompson sampling per variable over its open values,
  with an exploration floor (each open value gets ≥ 1/(2k) of videos until it has 8 scored videos).
- *Content* variables (niche, angle, hook type, runtime) stay the AI's picks, recorded with
  `source='ai'`, and are used as controls in the analysis.
- A `locked` variable always uses `locked_value`; `off` uses the renderer default.

**Analysis** (extend `agents/analyst.py`)
- Per variable/value: n, mean score, posterior probability of being best (per-video YouTube score).
- When ≥ 30 scored videos: a main-effects regression of score on all open variables plus niche and
  runtime controls; report effect sizes with intervals. No interaction terms until n ≥ 150.
- Render it into strategy memory as a "Settings evidence" section.

**The AI's control**
- Extend `StrategyUpdate` so the strategist can open/lock/turn off a variable and change its value
  set **within `bounds_json`**. Bounds are engineering limits only (e.g. speed 0.75–1.35, caption size
  readable at 1080 px). Validate and reject out-of-bounds proposals, feeding the reason back as
  `last_rejected_experiment` does today (`analyst.py:294,253`).
- Seed `setting_space` rows with status `off` and the current defaults; **the AI decides what to
  open**. Do not choose value sets for it.

**Niche labels.** Split `content_family` into `niche` + `angle` (both AI-named). The strategist can
merge niche labels it considers the same (recorded as an alias). Allocation runs on niche.

Done when: every rendered video has a full `video_settings` row set; a fixture run shows assignment
balance and a regression report; the live strategist opens at least one variable on its own.

### Stage 2 — Easy dials

Each dial is a `setting_space` row plus plumbing from `video_settings` into the renderer. Default
value = today's behaviour, so an `off` variable changes nothing.

| Variable | Plumbing |
|---|---|
| `speech_rate` (length_scale) | **Done in stage 1** as `speech_length_scale` (`SpeechOptions` per call) |
| `voice` | **Done.** Voice cache per name, download on first use, `aimz doctor` lists them. Choices limited to `COMMERCIAL_SAFE_VOICES` (public-domain/CC0 datasets): Piper voices are *not* all free for commercial use (lessac is research-only; ryan, hfc_* are CC BY-NC-SA) |
| `sentence_pause_s`, `beat_pause_s` | **Done in stage 1** (`SpeechOptions`; beat pad in `producer.py`) |
| `caption_font`, `caption_size`, `caption_position`, `caption_color`, `caption_outline` | **Done.** `Timeline.caption_style` (`CaptionStyle`), templated ASS header; 4 OFL fonts in `assets/fonts` with `licenses.json` and licence texts, shipped to libass via `fontsdir`. Legibility guard: captions are split so they never leave the bottom 32% band the cards keep free (`caption_char_limit`) |
| `caption_chunk_words` | **Done** as `caption_max_words`, split in the renderer (not in `text_prep`, which would add TTS pauses mid-sentence) |
| `zoom` per beat | **Done.** `Beat.zoom` (script prompt mentions it) over the `motion_style` dial |
| `palette` | **Done** as `palette_hue` (off = the old per-video hash) |
| `headline_on_cards` | **Done** (photo cards only, so no card is ever blank) |
| `progress_counter` | **Done** ("3/7" beat counter on every card when on) |
| `max_scene_s` | **Done** (`split_shots`: equal shots, audio cut, captions rebased, zoom reversed per shot) |
| `thumbnail_style` + use `thumbnail_concept` | **Dropped**: `thumbnail.png` is never uploaded (only written to draft packages) and the Shorts API takes no custom thumbnail, so the dial would change nothing a viewer sees and only add noise |

Done when: a render matrix script produces one short sample per dial at each bound and a human-free
check (ffprobe duration, caption file present, frame not blank) passes; one live cycle uses them.

### Stage 3 — Posting limits (never burst-post) — owner rules of 2026-09-30

**Owner's limits** (`config.yaml` `publishing.limits`; agents never edit them):
- at most **2 posts per platform in one run**;
- a run posts only **2.5 h after the previous posting run**;
- at most **2 posting runs per day** (local calendar day), so at most **4 posts per platform a day** (4 runs / 8 posts until the owner's 1 Oct change).

Built (replaces the earlier dispatcher/`publishAt` design, which the owner's run-based rules make
unnecessary):
- `pipeline/posting.py`: `PostingLimits` (from config), `PostingGate.check()` (gap + daily cap, measured
  from real posts), `PostingSession` (per-platform count per run; the first post records a
  `posting_runs` row, migration `0005_posting_runs`). Only API-writing publishers count; draft packages do
  not. An attempt that may have reached the platform counts even if it failed; a failure that provably
  reached nothing (`transient`) does not.
- `PublishStage.publish()` defers a platform whose run quota is used or whose run may not post
  (`status: deferred`, no publication row touched). `publish_rendered` posts oldest first and stops when
  every platform is full; `retry_due` skips retries the run may not post. `postable_videos()` also
  picks up a platform held back last run and publications an owner re-send left `pending`.
- Owner re-sends (`aimz publish retry`) check the limits first and change nothing when refused.
- Backlog guard: production pauses when more than `backlog_days` (2) of posts are waiting.
- `aimz status` shows the limits, posting runs today, the last posting run and whether a run may post now.
- `aimz schedule install` refuses times that break the limits (more runs a day than allowed, or closer than
  2.5 h, midnight included). Installed 2026-10-01 by owner decision: **09:00, 18:00** (09/12/15/18 on 30 Sep).
- Timing is learned rather than chosen for now: every video records `post_hour` (source `schedule`) in
  `video_settings`, shown in the settings evidence. An AI-chosen `post_slot` would need the scheduler to
  follow the AI; not built.

Done when: a simulated day never violates the gap or cap (test); the first scheduled runs on the new code
post at most 2 per platform and respect the gap (live check, pending).

### Stage 4 — Multi-channel instances (3 or 4 channels)

Design: **one instance per channel**, same code, separate root. Chosen over a `channel_id` column in
every table because `AIMZ_PROJECT_ROOT` already relocates everything and a schema-wide change would
touch ~15 tables.

- Layout: `instances/<name>/` holding `.env`, `config/` (own `strategy.md`; `constitution.md` is
  shared, read from the repo root), `data/`, `secrets/`. The existing channel becomes instance
  `main` (move nothing until a backup is taken; or point `main` at the current root).
- `python -m aimz --instance <name> ...`: a root callback in `cli.py` that sets `AIMZ_PROJECT_ROOT`
  before `build_services()` (`cli.py:38-41`). One process = one instance (`.env` loads into
  `os.environ`, so never two instances in one process).
- Scheduler: task name `AI Media Zero - <name>`; `install()` only removes tasks of **its own**
  instance (`scheduler.py:215-245`); runner takes `-Instance`. Keep the shared mutex so channels run
  one at a time on the one GPU.
- Channel name/identity from each instance's `config.yaml` instead of the hard-coded "AI Media Zero"
  (`agents/base.py:78`, `config.yaml:5`).
- Dashboard port per instance (`DASHBOARD_PORT` in each `.env`), or an instance picker later.
- **Niche separation**: a shared read-only registry (`instances/registry.json`, written by each
  instance's strategist for itself only) lists each channel's current niches. The strategist sees the
  others' niches and a rule rejects exploring a niche another channel already exploits. The AI still
  chooses the niches.
- **Shared learning of production settings**: each instance's analyst also reads sibling DBs
  read-only for `video_settings` + scores and pools production variables (with channel as a control).
  Content and niche stay per channel.
- `aimz status --all` summarises every instance.
- Cadence: with the shared mutex and 4–13 min cycles, 4 channels × 3 full cycles/day ≈ 2–3 h of GPU
  time/day. Fine on the laptop; revisit if cycles grow.

Owner work: create 2–3 more YouTube channels (brand channels under the same Google account are fine)
and authorise each (`python -m aimz --instance <name> youtube auth`); optionally one Bluesky account
and app password per channel; TikTok stays off for new channels until the TikTok audit passes.

Built 2026-09-30:
- `aimz/instances.py`: `instances/<name>/` layout; `create()` copies machine settings only (budget
  unchanged, no account credentials) and points the constitution, the voices and the OAuth client at
  the main root; dashboard ports from 8421 up.
- Instance `config.yaml` holds only differences (`project.name`); `AIMZ_BASE_CONFIG` points at the main
  `config.yaml`, which `load_app_config` deep-merges underneath. So the owner's limits, weights and
  switches reach every channel (added 30 Sep after stage 5). Stock keys are copied into new instances.
- `aimz --instance <name> ...` (root callback) and `AIMZ_INSTANCE`. `EnvSettings.instance` and
  `constitution_file`. Agents' prompts use `project.name` from the instance's `config.yaml`.
- Scheduler: task `AI Media Zero - <name>`, runner `instances/<name>/run-cycle.ps1`. `install`/`remove`
  touch only their own instance's tasks. Each instance has its own skip-mutex
  (`Local\AIMediaZeroCycle-<name>`; main keeps `Local\AIMediaZeroCycle`). A shared
  `Local\AIMediaZeroGPU` mutex makes channels wait for each other, up to 2 h.
- `aimz/experiments/shared.py`: each analyst writes `instances/_shared/<name>.json` (niches and scored
  production settings). Ideation drops ideas in a niche another channel holds (`claimed_niches`: at
  least 3 measured videos and more than this channel; a tie goes to the name that sorts first). The
  strategist sees the other channels' niches. Production-setting evidence and assignment pool all
  channels, with `channel` as a regression control; content variables stay per channel.
- CLI: `instance create|list`, `status --all`, `youtube whoami` (verified live: main posts to
  "Backhouse Explainers").
- Not built: a dashboard instance picker (each instance has its own port: `--instance <name> dashboard`).


Done when: two instances run on schedule for 48 h without touching each other's data or tasks, each
uploads to its own channel (verified live with `channels.list mine`), and posting limits hold per
channel.

### Stage 5 — Media, music, templates

- **Pexels and Pixabay providers** (photos and **video clips**), official APIs, free keys from the
  owner in `.env`. Record licence name, URL, author per asset. Extend the licence gate to accept
  `pexels`/`pixabay` licences **only after** the owner edits the constitution (§8). Respect each API's
  attribution/rate-limit terms; verify live.
- Video-clip scenes in the renderer (trim/scale/crop to 1080x1920, fall back to stills).
- **Music**: `assets/music/` with `licenses.json` (track, licence, source URL, mood, bpm); only CC0 or
  licences allowing commercial reuse without extra conditions YouTube would flag. Renderer mixes with
  sidechain ducking under narration. Variables: `music_on`, `music_mood`, `music_volume_db`.
  Update `docs/COMPLIANCE.md:60`. Check TikTok's music rules before enabling music there.
- **Sound effects** on cuts (same library pattern), variable `sfx_on`.
- **Visual templates** (`template` variable): current card, photo/clip full-bleed, map/timeline,
  ranking-with-counter. Legibility bounds only.
- **Ending** variable: loop-to-start, cliffhanger, part-2 tease, plain end. CTA stays bound by the
  banned-phrase list and constitution §5 (no engagement bait).

Built 2026-09-30 (stock media is off until the owner's constitution edit, keys and config switch):
- `providers/assets/stock.py`: `PexelsAssetProvider`, `PixabayAssetProvider` (photos and `search_videos`),
  24 h search cache, licence, author and page recorded, attribution in the description. Written from the
  docs and Pexels' official client (videos are at `https://api.pexels.com/videos/search`, *not* `/v1/`).
  Mock-tested; **live check pending keys**.
- Registered only when `config.yaml` `assets.stock.enabled: true` and the key is set (`PEXELS_API_KEY`,
  `PIXABAY_API_KEY`). The `clip_scenes` dial needs capability `stock_video`; it stays hidden until then.
- Renderer: a scene with `clip_path` loops, scales and crops the clip to 1080x1920 under a transparent
  text layer (`cards._render_layered`, `overlay=True`). It falls back to the still when no clip matches.
- Music: `assets/music` (calm, tense, bright: 32 s loops) and `assets/sfx/whoosh`, generated by
  `scripts/make_audio_library.py` (original, CC0). Final mix: music looped, faded, side-chain ducked under
  the narration; whoosh at each cut (`adelay`). Measured over silence: music gain -30 gives -43 dB,
  -14 gives -27 dB, -6 gives -19 dB (narration is about -16 dB).
- Dials: `template` (card, full_bleed, ranking), `clip_scenes`, `music` (off plus moods),
  `music_volume_db` (-30 to -6), `sfx`, `ending` (plain; loop ends on the opening frame). Map/timeline
  templates were not built (no map data). A CTA ending was not built: engagement bait is banned
  (constitution §5).
- Render matrix: every new dial at every bound OK, frames and loudness checked. `tests/test_media.py` (7)
  includes a real render with a clip, music, whooshes and a loop.


Done when: sample renders for each template/music setting pass the automated checks; attribution
appears in every description; one live cycle uses a Pexels or Pixabay asset.

### Stage 6 — Revenue-driven niche choice

- Score gains a revenue term once any `revenue_usd` exists: revenue per 1,000 views, per niche.
- Before monetization, weight what gets the channel into YouTube's Partner Program: watch time
  (`estimatedMinutesWatched`) and subscribers per 1,000 views. Keep weights in `config.yaml`;
  changes to the weights are owner decisions (they define what "success" is).
- Strategist sees per-niche expected value = views × retention × (revenue per 1k when known).
- Report per channel: YPP progress (subscribers, Shorts views in 90 days, watch hours).

Built 2026-09-30:
- Score parts `watch_time` (watch minutes per view) and `revenue` (revenue per 1,000 views) in
  `providers/analytics/store.py`, with weights in `config.yaml` `scoring.weights`. **The owner decides the
  weights**; both are 0, so scores are unchanged until the owner raises them. `aimz analytics` shows each
  video's parts.
- `experiments/value.py`: niche value per video (average views x fraction watched; views x revenue per
  view once any revenue exists), ranked; the strategist's prompt and the daily report include it.
- Partner Program progress from `channels.list(statistics)` (collected with metrics, 1 unit) and Shorts
  views in 90 days. Thresholds checked 2026-09-30: 1,000 subscribers and 4,000 watch hours in 12 months,
  or 10M Shorts views in 90 days. Live: 2 subscribers, 2,697 Shorts views in 90 days.
- `aimz money` prints all of the above. `tests/test_money.py` (5).


Done when: `aimz analytics` shows the new score parts; the strategist's niche report ranks by the
new score.

### Stage C — YouTube policy compliance (audit 2026-10-01)

Source: two read-only audits against Google's live pages on 1 Oct (Developer Policies, RMF, API
ToS, Branding; YPP monetization incl. "inauthentic content", spam/deceptive practices, synthetic
content disclosure, made for kids; Pexels/Pixabay licences; Piper voice model cards). Verified by
hand: the lessac licence excludes "any commercial purpose"; the Unitree video is live.

**C1–C5: before replying to Google (target 8 Oct; Google deadline ≈ 12 Oct)**

| # | Gap (policy) | Fix |
|---|---|---|
| C1 | No deletion after revocation (Dev Policies III.D: delete within 7 days) | `aimz youtube revoke`: POST oauth2.googleapis.com/revoke, delete token, purge this instance's YouTube rows (`metrics` platform=youtube, YouTube `comments`, `youtube_response.json`, `system_state.youtube_channel_stats`). Purge automatically on `invalid_grant`, and when no successful refresh for 30 days (III.E.4.b) |
| C2 | Non-statistics API data kept > 30 days (III.E.4) | Expire YouTube comments (author, text) and comment-derived lead text after 30 days; keep only `id` from `youtube_response.json` or delete it after 30 days. **Oldest file crosses 30 days on 8 Oct.** Statistics/analytics may stay |
| C3 | Privacy page incomplete/inaccurate (III.A, III.E.3.a) | Add monetary scope; describe every `youtube.readonly` use; state commenter names/text stored for ≤30 days; real retention; deletion within 7 days of revocation; direct contact email; several channels; new date. Publishing to GitHub Pages needs owner approval |
| C4 | New instances inherit `AUTOPUBLISH_CONSENT` (III.I prior specific consent) | Remove from `SHARED_ENV_KEYS` (`instances.py`); `instance create` sets it false; owner turns it on after `youtube whoami` confirms the channel |
| C5 | Upload description overstated (III.E.3.a) | Fix `data/audit/upload-process-script.md`: all channel URLs + shared client; stock media possible; metrics/comments read each run; comment classification and storage; `embeddable`, `defaultLanguage`, Shorts tag; retention/revocation; owner pre-authorized AI metadata; derived score is internal only |

**C6–C11: before the new channels post / before applying to YPP**

| # | Gap | Fix |
|---|---|---|
| C6 | Default voice lessac is research-only; `joe` is a lessac derivative | Decision 7: no default. Commercial-safe list = `ljspeech`, `kristin`, `en_GB-cori-high` (public domain), `john`; drop `joe`; `norman`/`bryce` excluded until base checkpoint is known. `PIPER_VOICE` out of `SHARED_ENV_KEYS`. Fix docs/OWNER_TODO A5 |
| C7 | Posting volume (spam / inauthentic content) | Decision 8: `max_runs_per_day: 2`, `posts_per_run_per_platform: 2`; reinstall schedule with 2 times |
| C8 | Inaccurate titles (spam: misleading metadata) | Decision 9: fact-check title and hook claims (numbers, dates, actors) like narration; critic rejects unsupported title claims |
| C9 | Elevated review missed named living person + negative claims | Classifier/backstop flags allegations about named living people → `needs_owner_review` |
| C10 | Internal IDs `[src_…]` in descriptions; family labels in tags | Strip in `agents/publisher.py` `build_metadata`; require a real summary line |
| C11 | Coordinated-network risk; shared feeds; duplicate topics | Decision 10: each instance has a locked channel identity (genre, voice, look) chosen once, then fixed; own `feeds.yaml`; source URLs deduped across instances; no automated cross-promotion. Genre must be producible with licensed media: e.g. movie summaries cannot use film clips, posters or stills (copyright); Minecraft needs gameplay footage the pipeline cannot capture without game automation |

Also: asset relevance check (no unrelated images; no identifiable stock people in crime/disaster
stories); update `docs/COMPLIANCE.md` (retention, revocation, posting limits). Already compliant:
AI disclosure (more than required), made-for-kids false, branding, one project for all channels
(never one per channel), Wikimedia/generated-music licences, no secrets in git, quota.

Done when: tests for C1, C2, C4, C8, C9, C10; a purge dry-run on a DB copy; privacy page live; the
reply to Google sent by the owner with all channel links.

### Stage L — Long-form videos (deferred)

Only a config placeholder exists (`content.long_form`, 240–900 s, 1920x1080, `enabled: false`); no
code reads it. Needed:
1. Landscape render profile: 1920x1080 cards, captions and templates (`providers/image/cards.py`,
   `ffmpeg_renderer.py`).
2. Script structure for 4–15 min: chapters of beats (today 3–14 beats max, `domain/models.py:134`),
   chunked writing and fact-checking so qwen3:8b (8k context) can cope.
3. Assets for 60–150 scenes per video, which needs stock video (stage 5 stock enabled) or it becomes
   a slideshow, the "inauthentic content" pattern.
4. Publisher: no "Shorts" tag, chapter timestamps in the description, custom thumbnail
   (`thumbnails.set`, needs a verified channel).
5. Separate scoring and settings learning for long vs short (different retention baselines).
6. Posting limits per format; render time ~10x Shorts on the laptop.

Why defer: YPP's long-form route (4,000 watch hours) is the realistic path to revenue, but long
narrated slideshows carry the highest inauthentic-content risk, and the compliance and identity work
in stage C applies to both formats. Start when C is done and each channel has ~2 weeks of Shorts on
the new code. Estimate: 3–4 sessions.

## 4. Owner checklist

| When | Task |
|---|---|
| 1 Oct | Google Cloud Console → OAuth consent screen → set **In production**. **Do not re-auth yet.** |
| After stage 0 lands (before ~7 Oct) | `.\.venv\Scripts\python.exe -m aimz youtube auth` once (adds the revenue permission; a Google "unverified app" warning is expected for your own app) |
| Done 30 Sep | Posting limits set: 2 posts per platform per run, 2.5 h between posting runs, 4 runs a day; schedule 09:00/12:00/15:00/18:00 |
| Stage 4 | Create 2 or 3 more YouTube channels; authorise each; optional Bluesky accounts |
| Stage 5 | Free API keys: pexels.com/api and pixabay.com/api/docs → put in `.env` (agents never write `.env`) |
| Any time | Edit `config/constitution.md` (§8 drafts) |
| Now | Review the scripts in `needs_owner_review` (7 on 2 Oct) (dashboard or `aimz approve script <id>`) |

## 5. Risks

- **Narration voice licence (found 2026-09-30, resolved 2026-10-01).** No default voice; only the five
  cleared voices can be spoken (stage C6). The ~30 videos posted before 1 Oct used the research-only
  lessac voice and stay up (owner has not asked to remove them); revisit before applying to the YPP.
- **Learning is still slow per channel**: about 4 YouTube videos/day scored 3–4 days after posting.
  3–4 channels roughly triples the data. Expect large effects in weeks, small ones not at all.
- **Monetization gate**: no real revenue data until a channel joins YPP. Until then revenue is a
  stand-in (watch time, subscribers).
- **YouTube "inauthentic content" policy**: templated mass production can make a channel ineligible
  for ads. More variety (stages 2 and 5) and fewer, better videos (stage 3 limits) reduce this.
- **Shared Google Cloud project**: one quota and one consent screen for every channel. A problem
  there stops all channels at once.
- **One laptop**: all channels stop when it is off (CEO decision 3, always-on machine).
- **Confounding**: content variables are AI-chosen, not random; treat their effects as correlations.
- **Many variables, few videos**: keep interactions out of the analysis until n is large; let the AI
  lock settings it is confident about so the rest learn faster.

## 6. Open questions

- Channels: decided 30 Sep. 3 more, 4 in total, added by the owner on 1 Oct.
- Does the owner want Bluesky and TikTok on the new channels at all? They add no score today.

## 7. Conflicts noticed

- YouTube audit: on 2026-09-30 this plan wrongly concluded it had cleared because uploads land
  public. Google's final notice of 2026-10-01 shows the compliance review / quota-increase request
  is still open (upload-process script or screencast + channel links due ≈ 12 Oct). All channels
  added in stage 4 use the same API client, so tell Google about them.
- `config.yaml` `require_owner_approval: true` reads as if approval were required; it is overridden
  by `AUTOPUBLISH_CONSENT=true`. Consistent with vault decision D-014 (autopublish on).
- Constitution §1 ("the product is learning, not upload volume") vs the new revenue goal: the owner
  keeps "still learn, don't push many videos", so the drafts below keep both.

## 8. Constitution drafts (owner edits the file; agents may not)

Replace §1 Mission:

> Run an experiment: can an AI starting with $0 discover what people want to watch, find a niche
> that earns passive ad revenue, and keep improving from performance data? Learning comes first:
> the AI tests deliberately, publishes at a measured pace within the owner's posting limits, and
> never floods a platform with videos.

Replace the media sentence in §5:

> Use only media the AI may reuse commercially at no cost: public domain, CC0, CC BY, CC BY-SA
> (with attribution), the Pexels and Pixabay licences, other licences that are free for commercial
> reuse, or owner-provided. Music and sound effects must be original to the channel, public domain/CC0,
> or free for commercial reuse. "Free to view" is not "free to reuse". Record source and licence for
> every asset, and credit authors where the licence asks for it.

Add to §8 Creative autonomy:

> This includes voice, speaking speed, captions, visuals, music, editing, posting times and
> frequency within the owner's posting limits, and which variables to test or lock. Where the
> owner runs several channels, each channel's AI chooses its own niches and does not exploit a niche
> another channel already exploits.

## 9. Session log

| Date | Session | What changed | Next |
|---|---|---|---|
| 2026-09-30 | Planning | Read-only audit of the code; this plan written; HANDOFF and CEO brief updated. No code changed | Stage 0 |
| 2026-10-01 | Compliance audit | Google's final notice on the API compliance review found (audit had NOT cleared); upload description drafted; policy audits run; owner decisions 6–10; stages C and L added. No code changed | Stage C after owner go-ahead |
| 2026-10-01 | Stage C | **C1/C2** `aimz/youtube_data.py` (expire, purge, enforce, revoke), migration `0006` (`comments.refreshed_at`), comment refetch refreshes, `youtube revoke` command, `youtube_login_failing` in `aimz status`, enforce at the end of the metrics stage (purge after 7 days of failed auth or 30 without success; unknown/offline never counts). **C3** `PRIVACY.md` + `docs/privacy/index.html` rewritten (not pushed: needs owner OK, lists the owner's email). **C4** `AUTOPUBLISH_CONSENT`/`PIPER_VOICE` out of `SHARED_ENV_KEYS`, new instances `AUTOPUBLISH_CONSENT=false`, `aimz instance consent` (shows channel, records `YOUTUBE_CONSENTED_CHANNEL_ID`, publisher refuses another channel). **C5** `data/audit/upload-process-script.md` corrected against the code. **C6** voices: kristin, john, norman, ljspeech, cori (model cards re-read: norman *is* trained from scratch, contrary to the audit agent; joe = lessac derivative, bryce = unknown base); no default; provider refuses others; `voice` dial `always_on`; stale locked values re-checked. **C7** `max_runs_per_day: 2`, schedule reinstalled 09:00/18:00. **C8** title + hook fact-checked (deterministic specifics must be in sources *and* narration; model verdicts on title/hook as claims); `enforce_final` keeps the hook in step. **C9** `allegation_sentences` backstop (full name + conduct term → owner review). **C10** `public_summary`/`public_tags` strip `[src_…]` ids and internal labels. **C11** `aimz/identity.py`: AI-chosen genre/voice/look, distinct across channels, 10-video genre dwell, `channels.starting_genres` owner guidance in config.yaml, identity voice/look fixed per video, other channels' voices excluded, cross-channel source dedupe (`used_sources` in shared files). Dry run on a DB copy: 0 YouTube comments, 29 responses (oldest 8 Sep → first trim 8 Oct), live token check OK. Tests 277, ruff + mypy clean | Watch the 1 Oct 18:00 run: identity set, cleared voice used, no QA starvation from the title rule. Then: privacy push (owner), channels (owner), Google reply (owner sends) |
| 2026-10-02 | Pre-send recheck | Owner ran `instance consent main` (channel id recorded). Live runs 1 Oct 18:00, 2 Oct 09:00 and 18:00: all `ok`, 0 errors; posting runs held at 2/day (the 1 Oct 18:00 run correctly posted nothing, 2 runs already used); the consented-channel check passed on the 2 Oct uploads; videos used cleared voices (cori, kristin); the strategist chose identity "maps and geography oddities", kristin, card, hue 0.6; title rule caused no starvation (4 approved, 1 qa_failed). Live API check of 2 uploads: public, Education, disclosure + sources + credits in description, no internal ids; `containsSyntheticMedia: true` echoed by `videos.insert` (videos.list omits the field). Fixed: "invest" keyword matched "investigation" (air-crash script sent to the owner as financial advice). Stale schedule/voice statements corrected in CEO_BRIEF, CHECK_IN, HANDOFF, this plan. 278 tests, ruff + mypy clean | Owner: push the code before sending (the privacy page links the public repo); send the Google reply |
| 2026-09-30 | Stage 0 | All 8 items. (1) `yt-analytics-monetary.readonly` requested at `aimz youtube auth` (`AUTH_SCOPES`) but optional: `load_credentials` now loads the token with the scopes it was **granted** (a refresh that asks for an ungranted scope fails `invalid_scope`, which would have stopped uploads), `run_oauth_flow` stores only granted scopes, `has_scope()` gates a separate revenue query (`_fetch_revenue`); 403/no rows → `revenue_usd=None`; lifetime revenue is ledgered as increases only. (2) YouTube scores need `avg_percent_viewed`; none by 120 h → first snapshot ≥120 h, `score_reach_only` (not `late`). (3) `allocation.per_video()` used by `family_stats`, `group_stats`, `runtime_buckets`, allocation's measured count, top videos, review, milestone, `aimz analytics` (one row per video + note column). (4) Arm balance and `next_for_assignment` ignore `rejected`/`killed` ideas; `retire` frees those too. (5) `retention_3s`, `completion_rate` removed from KPIs (none running used them). (6) `target_platform` not assignable (running ones auto-retire; none existed); `format` branch removed. (7) Under a hook-type experiment the script prompt drops the ideation hook line and writes the opening from the assigned type, both arms alike. (8) `hours_live()` counts from `max(posted_at, scheduled_for)`, never negative. Tests: `tests/test_measurement_stage0.py` (20 new, 174 total), ruff, mypy clean. Live: `aimz analytics --collect` refreshed the existing token and stored 39 snapshots; 49 publication rows → 25 videos; 17 scored, 0 reach-only, 2 late; 0 negative ages. No migration | Owner: set consent screen In production (1 Oct), then `aimz youtube auth`; next agent: verify a live monetary query (rows or a clean "not monetized" in `metrics.raw_json`), then Stage 1 |
| 2026-09-30 | Stage 1 | Migration `0004_video_settings` (`setting_space`, `video_settings`, `niche_aliases`, `ideas.angle`); DB backed up to `data/backups/aimz_2026-09-30_pre-stage1.sqlite3`. `experiments/settings.py`: code-defined `CATALOG` (bounds only) synced into `setting_space` as `off`; `SettingsEngine.assign` picks each open production setting per video independently of the idea (floor: a value with < 8 scored videos is picked at random with probability \|under\|/(2k); otherwise Thompson sampling on per-video score), records every catalog setting plus niche/angle/hook_type (`source='ai'`) before TTS. Analysis: `value_stats` (n, mean, P(best)), `regression` (OLS main effects, categorical vs most common value, niche + runtime controls, from 30 scored videos), `evidence_text` → strategist prompt, `strategy.md` "Settings evidence", `aimz settings`. `StrategyUpdate.setting_changes` (open 2-4 values / lock / off, validated against bounds; refusals in `last_rejected_setting`) and `niche_merges` (`experiments/niches.py`: alias table, live ideas relabelled, history kept and canonicalised on read). Ideation now asks for niche (`content_family`) + `angle`. **Pulled forward from stage 2** so the strategist has something real to open: `speech_length_scale` (0.75-1.35), `sentence_pause_s` (0-1.0), `beat_pause_s` (0-1.2) via `SpeechOptions` on every TTS provider. Tests: `tests/test_video_settings.py` (19 new, 193 total, includes an end-to-end fixture cycle and a regression that recovers a planted effect); ruff, mypy clean. Live: migration applied; real Piper honours the options (0.85 → 5.21 s, default 5.75 s, 1.15 → 5.84 s, 0.8 s pause → 6.79 s); `aimz strategy learn` (v36) saw the new controls (schema 3.4k chars, under the 6k prompt cap) and **chose not to open a setting yet**. Not forced (the AI owns the choice) | Next agent: after the next scheduled cycle, confirm each new video has a full `video_settings` row set (`aimz settings` shows the count); watch for the strategist's first `setting_changes`; then stage 2 (remaining dials) |
| 2026-09-30 | Stage 2 | 12 new dials in `CATALOG` (15 total): `voice`, `caption_font`, `caption_size`, `caption_position`, `caption_color`, `caption_outline`, `caption_max_words`, `motion_style`, `max_scene_s`, `palette_hue`, `headline_on_cards`, `progress_counter`; `Beat.zoom`; `SpeechOptions.voice`; `ProducerAgent.assemble()` (shared with the matrix) and `default_settings()`. **Bug fixed:** each scene clip used `-shortest`, so it stopped when the narration did: the beat pause was never rendered and burned captions ran up to ~3 s late by the end of a video (e.g. 54.1 s rendered vs 56.9 s timeline). Now the WAV is padded to the scene length and the renderer uses `apad`; rendered length matches the timeline within 0.06 s. **Licence finding:** default voice `en_US-lessac-medium` is research-only (Blizzard 2013 Lessac licence); owner chose (30 Sep) to keep it and let the AI pick among safe voices. Downloads (owner-approved): 4 OFL fonts (0.5 MB) into `assets/fonts`, 7 safe voices (530 MB) into `data/voices` (git-ignored). `scripts/render_matrix.py` + `providers/video/render_check.py`: 43/43 samples OK (every number at min and max, every choice, every font and voice); frames checked by eye for fonts, colour, counter, size. Tests: `tests/test_dials.py` (15), 208 total; ruff, mypy clean | Next agent: check the first scheduled cycle on this code (videos ~0.35 s/beat longer now the pause is heard; `aimz settings` shows 15 dials and the video count); then stage 3 once the owner confirms posting limits |
| 2026-09-30 | Stage 3 | Owner set the limits: 2 posts per platform per run, a run posts 2.5 h after the previous one, at most 4 runs a day (8 videos). Built `pipeline/posting.py` + migration `0005_posting_runs` (DB backup `data/backups/aimz_2026-09-30_pre-stage3.sqlite3`); gate in `PublishStage.publish`/`publish_rendered`/`retry_due`; backlog guard in `produce_approved`; `aimz status` posting block; `publish retry` checks limits first; `schedule install` validates times; `post_hour` recorded per video. The plan's dispatcher/`publishAt`/hourly-run design was replaced by the owner's run-based rules. Three older retry tests opt out via `relax_posting_limits`. Tests: `tests/test_posting_limits.py` (15, incl. a simulated day of hourly runs), 223 total; ruff, mypy clean. Live: status shows the limits; owner approved and I installed the 4-run schedule (09:00, 12:00, 15:00, 18:00; next 18:00 today). Posts made before today's migration are not in `posting_runs` (harmless: next run is >2.5 h after the last post) | Next agent: check the 18:00 run posted at most 2 per platform and wrote a `posting_runs` row, and that 12:00/15:00 runs tomorrow respect the gap; then stage 4 (owner: create channels) |
| 2026-09-30 | Stage 4 | Owner: 3 more YouTube channels (4 in total), created by the owner on 1 Oct. Built `instances.py`, `experiments/shared.py`, `--instance`, instance-aware scheduler (own task and skip-mutex, shared GPU mutex that waits), niche claims, pooled production evidence with a channel control, `instance create|list`, `status --all`, `youtube whoami`, a constitution shared through `AIMZ_CONSTITUTION_FILE`, and the channel name in prompts from `project.name`. `instances/` git-ignored; tests use a temp `AIMZ_INSTANCES_DIR`. Tests: `tests/test_instances.py` (16), 239 total; ruff, mypy clean. Live: `youtube whoami` for main; a scratch instance created, checked with `doctor`, `settings` and `status --all` in separate processes; generated runners parse in PowerShell | Owner (1 Oct): for each channel, `instance create`, `youtube auth`, `youtube whoami`, `schedule install` (docs/AUTONOMOUS_SETUP.md Part F). Next agent: after the first runs, check each instance's `posting_runs`, `whoami` and `instances/_shared/*.json`; then stage 5 |
| 2026-09-30 | Stages 5 and 6 | Owner cannot edit `.env` or get keys today, so this session built everything else. Stage 6: score parts `watch_time`/`revenue` (weights 0 in `config.yaml` `scoring`), niche value ranking, Partner Program progress (live: 2 subs, 2,697 Shorts views/90 d), `aimz money`. Stage 5: Pexels/Pixabay providers (off; mock-tested; Pexels video endpoint confirmed from the official client), clip scenes, a generated CC0 music and sfx library with a ducked mix, templates, loop ending; dials `template`, `clip_scenes` (hidden without stock video), `music`, `music_volume_db`, `sfx`, `ending`. Fixed: the loop tail was shorter than the renderer's 0.8 s minimum; `write_silence` treated an empty path as a file. Live 18:00 cycle on the stage 0-4 code: ok, posted 2 per platform, one `posting_runs` row, rendered length matched the timeline within 0.05 s, 18 settings rows per video. Tests 251; ruff, mypy clean. `docs/OWNER_TODO.md` lists every remaining owner step | Owner: `docs/OWNER_TODO.md`. Next agent: section B of that file (checks that need the owner's steps first) |
| 2026-09-30 | Screenshot fix | Owner's phone screenshot of the 18:00 CSDA Short showed the same sentence twice, cut mid-word ("valida"), and our text under YouTube's overlay. Causes: `script._normalize` cut every caption at 60 characters (since V0); the model copied narration into captions and nothing stopped a repeat (18 of 182 beats); the caption and credit positions sat under the Shorts overlay (captions 71-83% down, credits 89-94%; channel row from ~80%, buttons on the right 54-90%). Fixed: `_clip_words` (word boundary, 120 chars); `producer.repeats_narration` drops card text that only repeats the narration (falls back to visual_query or title, else none); the script prompt now says the narration is shown as subtitles; `providers/video/layout.py` safe zones (labels 13%, card text 19-50%, credits 50-58%, captions 58-78% with 170 px sides); caption positions are now bottom 22%, low 26%, mid_low 30%; headlines shrink to fit instead of losing lines. Re-rendered the CSDA beats and checked them against the measured overlay. Tests 255 | Published videos keep the old layout. Next agent: check a new video on a phone (B4) |
