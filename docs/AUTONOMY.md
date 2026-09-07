# What is autonomous, what is human-controlled

## Fully autonomous (no human in the loop)

- Fetching, deduplicating and freshness-scoring leads from the configured feeds.
- Generating and scoring ideas across content families; proposing new family keys.
- Deciding what to produce: exploration/exploitation allocation, family choice, hook type,
  runtime, experiment arm assignment, rejecting weak ideas, expiring stale ones.
- Writing scripts, fact-checking claims against stored sources, adversarial QA, up to two
  revision rounds, and rejecting scripts that never pass.
- Producing videos: narration (Piper), licensed image search and attribution (Wikimedia),
  scene cards, timeline, render, thumbnail, captions.
- Writing owner packages for TikTok and YouTube (draft mode).
- Pulling YouTube metrics and comments when the read-only API is enabled; classifying comments;
  converting content requests into leads.
- Evaluating experiments, proposing new ones (bounded by concurrency and sample rules),
  rewriting strategy memory, writing daily learning summaries.
- Adapting future cycles to that memory (family status, explore ratio, hook guidance).

## Human-controlled (the AI has no code path)

| Control | Where |
|---|---|
| Budget and paid-provider switch | `.env` (`MONTHLY_BUDGET_USD`, `ALLOW_PAID_PROVIDERS`) |
| Kill switch | `aimz kill/resume`, dashboard, `data/KILL` |
| Credentials, OAuth consent | `secrets/`, `aimz youtube auth` |
| Publishing mode | `.env` (`YOUTUBE_MODE`, `YOUTUBE_ENABLED`) |
| Approval of each upload | dashboard / `aimz approve` |
| Approval of elevated-review scripts | dashboard / `aimz approve script` |
| TikTok posting (preview, caption, privacy, AI label, consent) | TikTok app |
| Feeds list | `config/feeds.yaml` / dashboard |
| Constitution | `config/constitution.md` |
| Overriding strategy | dashboard / `aimz strategy edit` |
| Metrics the APIs do not expose | `aimz metric add` / dashboard |
| Retrying a failed upload | dashboard **Retry** button |
| Account ownership, payments, contracts, sponsorships, ads | never touched by code |

## Things the AI *cannot* do even if its prompts are compromised

- Spend money: every provider call passes `BudgetManager.authorize()` and the budget is read
  from `.env`, which no agent writes.
- Bypass the kill switch: publishers and paid providers call `KillSwitch.guard()`; only the CLI
  and dashboard clear the sentinel and DB flag.
- Publish without approval: `PublishStage` raises `OwnerApprovalRequired` for API writers.
- Add feeds, change the constitution, or change publishing modes: those files are written only
  by owner-facing code paths.
- Declare a family "winning/losing/retired" with fewer than three measured videos: enforced in
  `StrategyMemory.apply_update()`.
- Conclude an experiment below its minimum sample: enforced in `ExperimentEngine.evaluate()`.
- Invent facts to fill gaps: unsupported specifics are detected deterministically and removed.
