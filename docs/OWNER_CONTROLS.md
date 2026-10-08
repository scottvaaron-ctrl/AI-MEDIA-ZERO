# Owner controls guide

Everything below is owner-only. The AI has no code path to any of it.

## Kill switch

```powershell
aimz kill --reason "pause while I review"
aimz status
aimz resume
```

Engaged by either the sentinel file `data\KILL` or the `kill_switch` flag in the database
(both are set by `aimz kill`; either alone is enough). While engaged, **the agent does nothing**
(owner decision 2026-10-07):

- a scheduled or manual `aimz run` starts, records the run as skipped ("kill switch engaged: <reason>")
  and exits 0, so the scheduled task does not look broken. No stage runs: no research, ideas,
  writing, rendering, publishing, metrics, comments or learning, and no LLM, TTS or network call;
- the one exception is YouTube's required data clean-up (the 30-day limits in the YouTube
  Developer Policies), which keeps running each cycle however long the switch stays on;
- commands that do agent work refuse with a message: `produce`, `research`, `ideas --generate/--select`,
  `publish run`, `publish retry`, `publish poll`, `strategy learn` and `analytics --collect`;
- owner commands still work: `status`, `kill`, `resume`, `doctor`, `approve`, `requeue`, `posting ...`,
  `guideline ...`, `youtube auth/whoami/revoke`, `instance ...`, `schedule ...` and `dashboard`.

The kill switch overrides the posting switch below. To stop only posting, use the posting switch.

Emergency without Python: create an empty file named `KILL` inside `data\`.

## Posting switch

```powershell
python -m aimz posting off --reason "reviewing the last uploads"
python -m aimz posting status
python -m aimz posting on
```

Or Dashboard → Controls → Posting switch (the home page shows its state next to the kill switch,
and `aimz status` shows it as `posting_switch`). It is **on** unless you turn it off, and each change
is recorded with who, when and the reason.

While posting is off:

- nothing new is posted: no upload, no package, no automatic retry of a failed upload.
  Rendered videos stay `rendered`; no publication row is created and no retry attempt is used;
- an upload already sent before you turned posting off is still polled to completion each cycle
  (finishing it is not new posting), and is never turned into a failure while posting is off.
  `aimz publish poll` likewise works while posting is off; only the kill switch stops it;
- everything else runs as usual: research, ideas, writing, fact-check, review, rendering, metrics,
  comments and learning;
- `aimz publish run` and `aimz publish retry` refuse and tell you to run `python -m aimz posting on`,
  and the dashboard's one-tap TikTok post and Retry buttons post nothing and show a "Not done" message.

When you turn it back on, the waiting videos post through the normal publish stage under the usual
posting limits (`publishing.limits`) and backlog rule. Posting also still needs the existing consent
and approval gates (`AUTOPUBLISH_CONSENT` or `aimz approve video`); the switch does not replace them.

## Budget

`.env`:

```
MONTHLY_BUDGET_USD=0.00
ALLOW_PAID_PROVIDERS=false
```

Or Dashboard → Controls → Budget. The change is written to `.env` and recorded in
`configuration_versions`. Raising the budget does not enable any provider by itself.
`aimz budget` shows the ledger (authorizations, denials, spend, revenue, injections).

## Publishing mode

| Setting | Effect |
|---|---|
| `YOUTUBE_MODE=draft` (default) | packages only; no API calls |
| `YOUTUBE_MODE=private` + `YOUTUBE_ENABLED=true` | upload as private after owner approval |
| `YOUTUBE_MODE=scheduled` | upload private with `publishAt` |
| `YOUTUBE_MODE=public` | owner opt-in; YouTube still forces private for unaudited projects |
| `TIKTOK_MODE=package` | the only V0 mode |

Approval gate: `publishing.require_owner_approval: true` in `config/config.yaml`. Approve a
video with `aimz approve video <id>` or the video page in the dashboard; approve elevated-review
scripts with `aimz approve script <id>`.

## Credentials

- Put the Google OAuth client JSON at `secrets/client_secret.json` (path configurable).
- Run `aimz youtube auth` once; the refresh token is stored at `secrets/youtube_token.json`.
- `secrets/`, `.env`, `*token*`, `client_secret*.json` are git-ignored.
- The dashboard only ever stores *paths* to these files, never their contents.

## Constitution and strategy

- `config/constitution.md` is injected into every agent prompt. Edit it in the dashboard
  (Strategy page) or any editor. The AI cannot write it.
- `config/strategy.md` is a rendering of the latest `strategy_versions` row. To override the
  AI's strategy, edit the JSON state on the Strategy page (saved as an `owner` version) or
  `aimz strategy edit state.json`. The AI will continue from your version.

## Feeds

Dashboard → Feeds (add / remove / toggle) or edit `config/feeds.yaml`. Removed feeds are
disabled, not deleted, so historical yield statistics survive.

## Approvals and manual posting

1. Review rendered videos at `/videos/<id>` (inline player, timeline, sources, attributions).
2. Approve or reject.
3. Post the package (TikTok app / YouTube Studio) and click **Mark posted** on `/publishing`,
   pasting the URL so metrics can be attached.
4. Enter metrics on `/metrics` or with `aimz metric add`.

## Money in

`aimz` never spends revenue. Record it with `aimz metric add <pub> --revenue-usd 1.23` (goes to
the ledger as `revenue`) or `BudgetManager.record_owner_injection()` for capital you add. The
`reinvestment` ledger type exists but nothing writes it in V0.

## Reviews

```powershell
aimz strategy review daily
aimz strategy review weekly
aimz strategy review monthly
```

Reports land in `data/reports/`. Each answers: did performance improve, which hooks/formats/
topics/sources worked, which videos gained subscribers, is decision-making improving, and
whether spending money would plausibly help.
