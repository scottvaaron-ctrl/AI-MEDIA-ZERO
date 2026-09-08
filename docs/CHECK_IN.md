# Check-in: coming back to look at the data

_Written 2026-09-08, the day all three platforms went live and the scheduler was installed._
_For adding platforms or changing code, read `docs/HANDOFF.md` instead._

## The system is running unattended right now

Windows Task Scheduler runs `scripts/run-cycle.ps1` at **09:00 and 18:00** daily. With
`AUTOPUBLISH_CONSENT=true`, each cycle researches, writes, renders **and publishes** with no
human in the loop. YouTube and Bluesky posts are **public immediately**. Nothing waits for review.

The laptop must be awake at those times. Asleep means the run is skipped silently, and the first
sign is an empty `data\logs\scheduled.log` and no new videos.

To stop everything at any time:

```powershell
aimz kill --reason "pausing"     # blocks every outbound write; aimz resume undoes it
```

## Decide this before the next 09:00 run

`vid_20260907T205623_523777fc` — *"The AI Sandbox Escape Plan That Broke the Internet"* — is
still at status `rendered`, so **the next cycle will publish it**. Its title contains
`broke the internet`, which is on the channel's own `safety.banned_patterns` list. It got through
because `critic.py` only checks those patterns against `narration.lower()`, never the title
(see `docs/HANDOFF.md` section 2).

Either reject it:

```powershell
aimz approve video vid_20260907T205623_523777fc --reject
```

or accept that it goes out. Doing nothing publishes it.

## What was published on 2026-09-08

| Platform | URL | Metrics? |
|---|---|---|
| YouTube | https://www.youtube.com/watch?v=xkquD2FA8KI | Yes, lagging 1-2 days |
| Bluesky | https://bsky.app/profile/backhouse06.bsky.social/post/3muzvmqorox2x | Yes, within hours |
| TikTok | `SELF_ONLY` on `backhouse67`, no public URL exists | **No, none at all** |

Both are the same video: *Islands of New Zealand: The Huhu Beetle*.

## First commands when you come back

```powershell
cd $HOME\Documents\Agentic_Youtube; .\.venv\Scripts\Activate.ps1
aimz doctor                    # every platform should still be connected
aimz status                    # counts, last run, errors in 24h
aimz publish list              # what went out while you were away
aimz run --stages metrics      # pull fresh numbers from YouTube + Bluesky
aimz analytics                 # the table that actually answers "is this working"
```

Then, once there are at least a handful of published videos with metrics:

```powershell
aimz strategy learn            # let the loop rewrite its own strategy from the data
aimz strategy show
```

## What the numbers will and will not tell you

- **Bluesky** reports likes, replies, reposts + quotes, bookmarks and follower delta, within
  hours. No view count exists on Bluesky for anyone, so engagement is the only signal.
- **YouTube** reports views, average percent viewed, shares and subscribers gained, lagging one to
  two days. Impressions and CTR are not in any API; enter them by hand from YouTube Studio with
  `aimz metric add` if you want them.
- **TikTok** reports nothing. `publicaly_available_post_id` is never returned for a `SELF_ONLY`
  post and the Display API only covers public videos. TikTok contributes **zero** signal to the
  learning loop until its app audit clears.

So for now the experiment is really running on YouTube and Bluesky. With a handful of videos,
treat everything as directional: two data points are not a trend, and the strategy loop will
happily over-fit if you let it draw conclusions early.

## Red flags worth a look

- `aimz status` showing `errors_24h` climbing. Reddit `429` responses were the old recurring one;
  the user agent has since been fixed, so new 429s would mean something changed.
- `aimz publish list` showing `failed` rows. Failures are never retried automatically, by design.
  Re-queue one deliberately with `aimz publish retry <publication_id> --approved`.
- Rows stuck at `uploading`. Bluesky was still encoding when the cycle ended; `aimz publish poll`
  finishes them.
- `data\logs\scheduled.log` not growing. The machine was asleep, or the tasks were removed
  (`aimz schedule status` lists them).
- Videos citing anything other than a real article URL. Wikipedia citations were fixed on
  2026-09-08 and verified against the live feeds; a regression there quietly undermines the
  channel's whole claim to be checkable.

## Open items, in the order I would do them

1. **Title/hook banned-pattern check** (above). Small fix, prevents an unattended repeat.
2. **The ban list itself is an unexamined human prior.** It suppresses eight clickbait phrases, so
   the channel can never learn whether they work. Defensible as an owner brand constraint, but it
   should be labelled as one in `config/constitution.md` rather than sitting in `safety` as though
   it were a quality heuristic the agent derived.
3. **TikTok app audit**, if TikTok is to be worth anything. Needs a demo video filmed in the
   sandbox showing the full flow. `docs/HANDOFF.md` section 3 has the shot list and the verified
   platform behaviour.
4. **Posting time is a fixed 17:00 heuristic.** Once two platforms report metrics, make it an
   experiment variable.
5. **Mastodon** is the cheapest next platform: app-level token, direct media upload, no review.
   Closest sibling to the Bluesky code that just landed.

## Things that have bitten before

Platform APIs here have repeatedly contradicted their own documentation, and mocks written from
the docs passed while the real calls failed. Three separate cases in one day: Bluesky's
`uploadVideo` response shape, TikTok's `username` scope, and Wikipedia's "first link". Before
trusting any integration, make one real call and look at what came back, not just the exit code.
