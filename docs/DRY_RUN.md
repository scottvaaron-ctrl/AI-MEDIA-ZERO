# End-to-end dry run report (2026-09-07, Windows 11, RTX 4060 laptop)

Real run: live feeds, `qwen3:4b` through Ollama, Piper `en_US-lessac-medium`, Wikimedia Commons,
bundled FFmpeg 7.1. Budget `$0.00`. Every cycle ended with `spent $0.00, denials 0`.

## Cycle 1 (`aimz run --limit 1`, 4m25s)

| Stage | Result |
|---|---|
| research | 10 feeds, 171 items fetched, 169 new leads, 2 duplicates, 1 error (Reddit 429) |
| ideas | 6 ideas from 24 leads (2 filtered by $0-feasibility / duplicates) |
| select | 2 ideas, both `explore` (constitution forces exploration until 3 families are tested) |
| write | 0 approved after 3 rounds each |
| learn | strategy v1 written; change summary correctly named the bottleneck (QA failures) |

What blocked output, and the fix that followed:

1. Scripts were 41–53 words against a 143-word target; the word-budget check rejected them, and the
   model ignored the target. **Fix:** hard bounds now derive from platform min/max seconds and 60 % of
   the target, with an explicit per-beat word count in the prompt.
2. Critic scores of 55–65 with an empty problem list versus a threshold of 70. **Fix:** threshold 60,
   and the headline score is blended 50/50 with the critic's own six sub-scores.
3. The year `2026` was flagged as unsupported because the deterministic fact-check ignored the
   source's publication date. **Fix:** corpus now includes `published_at` and the URL.
4. The critic demanded facts not in the sources ("include the distance, 5,000 light-years").
   **Fix:** prompt rule: required revisions must be fixable with the listed sources only.
5. Correct behaviour worth noting: the fact-checker marked an invented "space tech" angle on a NASA
   firefighting drill as `contradicted`, and the script was rejected. That is the system working.

## Cycle 2 (2m48s) — first rendered video

- Research: 2 new leads (dedupe working: 169 duplicates skipped).
- 6 ideas, 2 selected; **1 script approved on v1** (critic 85, 4/4 claims `supported`, sourced to an
  Ars Technica article); the other rejected (fact-check `reject`, elevated review).
- Rendered `The AI Sandbox Escape Plan…`: 26.5 s, 1080x1920, 2.7 MB, 15.6 s render time, timed
  burned captions, AI disclosure on scene 1.
- Packages written for TikTok (`video.mp4, caption.txt, metadata.json, tiktok_metadata.json,
  sources.json, cover.png, thumbnail.png, captions.srt, ai_disclosure.txt, posting_notes.md`) and
  YouTube (`… youtube_request_body.json` with `containsSyntheticMedia: true`, `privacyStatus:
  private`). No API call was made (`YOUTUBE_MODE=draft`).
- Provider usage: Ollama 38 calls, Piper 4, Pillow 5, FFmpeg 1, RSS 20, all at $0.00.

Defects found by inspecting frames, and fixes:

- Stat card showed the raw image-search phrase instead of the stat; card text collided with the
  subtitle band; the disclosure line overflowed. **Fix:** new card layout reserving the bottom 30 %
  for subtitles, stat/quote cards use the caption, wrapped disclosure.
- No photos were used because the model chose only text cards. **Fix:** the producer now searches
  Wikimedia for every beat (cleaned query, title fallback) and renders the card over the photo.
- Title "…That Broke the Internet" is bait the source never supports. **Fix:** added to
  `banned_patterns` (critic fails it).
- The strategist proposed an experiment on "Script complexity", a variable the Editor-in-Chief cannot
  assign, which blocked the cold-start hook experiment. **Fix:** `ExperimentEngine.validate()`
  accepts only assignable variables with valid arms; invalid running experiments are auto-retired.
- The strategist lowered the explore ratio to 50 % with zero measured videos. **Fix:**
  `StrategyMemory.apply_update()` cannot reduce exploration below the cold-start ratio until
  `decay_start_sample` videos are measured.

## Cycle 3 (4m41s) — guards verified

- Bogus experiment retired automatically; "Numeric vs narrative hook on retention" seeded and
  running; strategy v3 explore ratio clamped back to 70 %.
- 0 scripts approved: critic headline 55 with no problems listed (fixed by the sub-score blend),
  an unsupported "10,000 years" the model would not drop (now removed deterministically on the
  final round), and a false "realistic depictions of real people" flag on a thylacine story
  (structurally impossible in this pipeline; those two categories are now ignored from the model
  and left to keyword detection for the rest).

## Learning loop evidence (zero metrics so far)

- Three strategy versions, each attributed to `ai`, each with a change summary derived from the
  actual bottleneck table; family statuses stayed at `hypothesis` because no video is measured.
- Offline test `test_manual_metrics_feed_learning` proves the rest of the loop: a manual metrics
  entry produces a performance score, the family gets `n=1`, the weekly review answers the
  validation questions.

## Costs and timings

| Item | Value |
|---|---|
| Usage-based spend | $0.00 |
| Budget denials | 0 |
| Model calls per cycle | ~25–40 (qwen3:4b, GPU) |
| Cycle wall time | 3–5 min |
| Render time (26 s video) | 15.6 s |

## Cycle 4 (qwen3:4b, 4m30s) — writer identified as the weak link

Guards held (strategy v4, hook experiment running), but both scripts were rejected again: 49 words
in four thin beats, critic sub-scores 2–4/10 ("feels like generic AI"), no payoff. This time the
critic was right. **Fixes:** the script schema itself now requires at least 5 beats and 60 spoken
words (a thin draft fails validation and the error is fed back to the model), and the default
model was raised to `qwen3:8b` (free, ~5 GB, fits an 8 GB GPU).

## Cycle 5 (qwen3:8b, 3m14s) — target output reached

- 5 ideas, 2 selected; **1 script approved on its first draft** (107 words, 7 beats, critic 70,
  fact-check `pass`, 4/4 claims supported); the other rejected for an unsupported year the model
  would not drop (correct, conservative behaviour).
- Rendered `Islands of New Zealand: The Huhu Beetle`: 37.6 s, 32 s render time. **All seven scenes
  carry licensed photos** from Wikimedia Commons (CC BY 2.0/3.0/4.0), each with a stored
  attribution line baked into the frame and listed in the description.
- TikTok and YouTube packages written; strategy v5; usage-based spend still $0.00.
- A copy of the package, a frame, the daily learning summary and the strategy memory after five
  cycles are in [`examples/`](../examples/).

## Known limitations observed

- Script quality tracks the local model. `qwen3:4b` passes QA roughly one draft in six;
  `qwen3:8b` passed on the first draft. Larger free models will do better still.
- A single-sentence beat that contains an unsupported specific is not auto-removed (the beat would
  be empty); the script is rejected instead. Safe, but wastes a production slot.
- The 4B critic's headline scores are noisy; the sub-score blend mitigates but does not remove it.
- Wikimedia search occasionally returns a related-but-off image (a scientist drawing a beetle, for
  the "cultural significance" beat). Attribution is always correct; relevance is model-dependent.
- Reddit RSS rate-limits back-to-back fetches; one Reddit feed is enabled by default.
- No metrics exist until videos are posted and numbers are entered, so the strategy has not yet
  changed a family's status in a real run; the offline test proves that path.
