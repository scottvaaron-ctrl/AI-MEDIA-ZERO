# Plan: owner guideline per channel, one experiment at a time

_Approved by the owner 2026-10-07 (rescope). This replaces `docs/PLAN_FULL_CONTROL.md` as the working plan.
Any agent picking this up: read this file top to bottom, then the spec
(`docs/superpowers/specs/2026-10-07-guideline-system-design.md`) and the implementation plan
(`docs/superpowers/plans/2026-10-07-guideline-system.md`), then start at the first part not marked Done.
Update the status table and the session log before you finish a session._

## 0. What changed, in plain English

The owner looked at the videos and called them "AI slop". The owner's decision:

> Rather than the AI having full creative control we give it guidelines to follow, and then it is
> allowed to choose one to experiment with. If it performs better than the guidelines we gave it then it
> may change. No longer does the AI start from zero.

So each channel now has a **guideline**: a written file the owner approves. It sets topics, format, hooks,
tone, length, look, sound, titles and covers. The AI follows it. The AI may run **one experiment at a time per
channel**, changing one guideline item. If the change wins clearly, the AI writes a **proposal**. A proposal
changes nothing until the owner approves it. Where the guideline says nothing, the AI still decides.

Scope: the `main` instance only, which is the **Backhouse Explainers** YouTube channel. The owner's other
YouTube projects (Breakdown, Monmouth County & Politics) are separate and owner-run. Nothing here touches them.

## 1. Status

| Part | What | Status |
|---|---|---|
| 1 | The guideline system: file, enforcement, one experiment per channel, proposals and owner approval, CLI, dashboard page, new-channel gate, docs | **Done**, merged to `main` 2026-10-08 (`fb57dad`), 416 tests |
| 2 | Production abilities the guideline needs: full-bleed default layout, SigLIP image-to-narration relevance gate, Kokoro TTS, attribution in the description, -14 LUFS mastering, stock-video providers | Not started |
| 3 | The guideline text for Backhouse Explainers, drafted from `docs/VIDEO_QUALITY_RESEARCH.md`; the owner approves it | Not started (a test-only draft exists: `data/tests/2026-10-07-guideline-test/guideline-TEST-DRAFT.md`) |
| Switches | Kill switch = the agent does nothing (only YouTube's required data deletion runs); new posting switch `python -m aimz posting on|off|status` pauses only posting | **Done**, merged 2026-10-08 (owner decision 2026-10-07) |
| Constitution | Sections 1, 3, 4 and 8 rewritten (owner-approved 2026-10-07) | **Done** |

Until part 3 exists there is no `config/guideline.md` on `main`, and the channel behaves exactly as before.
Parts 2 and 3 each get their own spec.

## 2. How the guideline system works (for the next agent)

### Where things are

| What | Where |
|---|---|
| The guideline (main channel) | `config/guideline.md` |
| The guideline (other channels) | `instances/<name>/config/guideline.md` |
| Loader, validation, versions | `src/aimz/guideline.py` (`load`, `for_services`) |
| Proposals, approve/reject, switch-over | `src/aimz/guideline_proposals.py` |
| One experiment per channel, evaluation | `src/aimz/experiments/engine.py` (the "guideline path") |
| Locked-value precedence | `src/aimz/experiments/settings.py` (`SettingsEngine.assign`) |
| Where rules reach the prompts | ideation, editor, script and critic agents in `src/aimz/agents/` |
| Strategist prompt | `src/aimz/agents/analyst.py` |
| Tables | `guideline_versions`, `guideline_proposals` (migration `0007`) |
| Sample file used in tests | `tests/fixtures/guideline_sample.md` |
| Dry-run test | `tests/test_pipeline_offline.py::test_dry_run_cycle_follows_guideline` |

### The file

A Markdown file that starts with one fenced YAML block (`guideline:` name and genre, `locked:` settings,
`runtime_s:` min and max, `checks:` such as `banned_openings` and `title_max_chars`), followed by craft rules.
Each rule is a heading `### G-<id>: <title>` with a `stage:` line (`topic`, `script`, `title` or `cover`) and its
text. A locked setting is addressed as `G-<setting>` (for example `G-caption_max_words`).

The AI never writes this file. A direct owner edit is detected by hash and stored as a new version. A bad file
(unknown key, value out of bounds, duplicate ID, missing or broken YAML) is refused and the last valid
version stays in use; the refusal shows in `aimz status`. A channel with no guideline file behaves as before.

### Commands

```
python -m aimz guideline show                      # active text and version
python -m aimz guideline history                   # stored versions
python -m aimz guideline proposals                 # pending changes, with the evidence
python -m aimz guideline approve <id>              # rewrite that one item, save a new version
python -m aimz guideline reject <id> --note "..."  # guideline unchanged; item blocked for 20 videos
```

The dashboard has a `/guideline` page with the same information and Approve / Reject buttons. `aimz status`
and the attention list show "N guideline change(s) waiting"; a proposal waiting over 14 days is marked overdue.

### Experiments

* One running experiment per channel, on one guideline item (a craft rule, a locked setting, or the runtime).
* Arms alternate per selected idea: `control` follows the guideline, `treatment` uses the variant.
* A setting variant is applied by the producer (`video_settings.source = 'experiment'`). A craft-rule variant
  replaces that single rule in the script and critic prompts for that idea. A topic-stage item is split at
  ideation time.
* The measure is the composite score; average % viewed is reported beside it.
* Outcomes: the variant wins at 90% confidence and 10 videos per arm (a proposal is written); the guideline
  wins at 90% (closed, logged); safety stop at 90% against the variant with 6 per arm; inconclusive at 30 per arm.
* A proposal's plain-English summary is checked by code: every number must match the evidence, else a template
  built from the evidence replaces it.
* While a proposal is pending, videos follow the current guideline, and an item can have only one pending proposal.
* An owner edit that changes an item closes any experiment on it ("baseline changed by owner") and marks any
  pending proposal on it `superseded` (same note), so approving can never overwrite the owner's newer edit.
  `approve` also re-checks the item against the file and supersedes the proposal if the owner changed it.
* A won experiment whose proposal could not be written is picked up at the next learn (a sweep of `variant_won`
  experiments that have no proposal row).
* Cooldown: 20 published videos after an inconclusive experiment, a "Guideline won" or "Safety stop" result,
  or a rejected proposal (ruling R11).
* **Cover rules are not applied yet.** No stage of the pipeline reads `cover`-stage rules (there is no cover
  generator), so an experiment on a cover rule is refused: "G-... is a cover rule, which the pipeline does not
  apply yet".
* The constitution screen on a variant matches whole words and phrases ("fabricate", "invent facts", "fake
  quote", "make up", "clickbait", "unsourced", ...), so "inventions" or "debunk fake claims" pass.

### When the guideline cannot be used as written

* A locked setting that needs an ability this machine lacks is stored in the `guideline_unavailable` state;
  `aimz status` lists it under `guideline_items_unavailable` with what to install or change.
* If a settings-catalog change makes the stored guideline invalid, `load` falls back to the newest stored
  version that still validates, or runs with no guideline, and says so in `guideline_error`; it never stops
  the cycle.

### Enforcement order for a setting

1. the experiment's variant value, for a treatment video (`source = 'experiment'`);
2. the guideline's locked value (`source = 'guideline'`);
3. if a locked value needs an ability not installed yet: the renderer default, `source = 'guideline_fallback'`;
4. otherwise identity, open, locked, always-on and default as before.

Strategist `setting_changes` on a setting the guideline covers are refused with "covered by the guideline;
propose an experiment instead". Runtime must sit inside `runtime_s`. The critic reports `broken_rules`, which
become required revisions; code-only checks in the critic cover runtime, banned openings and title length. The
critic accepts `broken_rules` IDs of script- and title-stage rules only. Caption chunk size is not a critic check:
the renderer enforces the locked `caption_max_words` when it builds the captions.

### New channels

`instance create` writes a guideline template marked NOT APPROVED. Uploads stay off until the owner approves it
(removes the marker).

### Switch-over (first cycle that finds a guideline)

The old experiments are retired, every setting the guideline locks is set to `off` in `setting_space` so the
guideline takes over (the evidence history is kept), and identity proposals are ignored. `starting_genres`
no longer exists (removed from `config/config.yaml`); with no guideline the strategist is shown "none".

## 3. Rules and rulings made during the build

Owner rules (constitution, memory) still apply: zero budget, no browser bots, and the AI never edits the
guideline or the constitution.

* **R1.** An experiment row whose variable starts with `G-`, and is neither `G-runtime` nor `G-<catalog setting>`,
  is a craft-rule experiment; its `treatment` column holds the variant text.
* **R2.** `guideline.load` only records versions. The orchestrator calls `proposals.sync_version` at cycle start
  to close experiments after an owner edit. (Avoids a circular import; an owner edit takes effect at the next cycle.)
* **R3.** The constitution is not edited by the build. The new wording is held below for the owner's approval.
* **R4.** The dashboard check is a FastAPI `TestClient` test (`GET /guideline` returns 200), not a live server.
* **R5.** `guideline.for_services(svc)` is the one shared loader for the producer, analyst, editor and critic.
* **R6.** Under a guideline, a `G-runtime` experiment's treatment value bypasses the guideline runtime clamp in the
  script agent and the critic for treatment-arm ideas.
* **R7.** The legacy experiment keys in `config.yaml` stay at 8 / 2 / 0.85 so "no guideline = unchanged" holds. The
  guideline path uses engine constants `GUIDELINE_MIN_SAMPLE = 10`, `GUIDELINE_MIN_CONFIDENCE = 0.90` (owner lowered it from 0.95 on 2026-10-07) and one
  experiment per channel. The new keys `safety_min_sample`, `max_sample`, `cooldown_videos` are in config.
* **R8.** Approving a `G-runtime` proposal sets `runtime_s` to round(0.6N) to round(1.4N), clamped to 10-180.
* **R9.** Settings the guideline does not lock stay the AI's to open.
* **R10.** `docs/CEO_BRIEF.md` and `docs/OWNER_TODO.md` are not edited by the build branch (the owner's
  uncommitted edits live in the main checkout). The controller updates them after merge.
* **R11.** The 20-video cooldown also follows an experiment the guideline won or safety-stopped, not only an
  inconclusive one or a rejected proposal.

## 4. Constitution wording (approved by the owner 2026-10-07, applied)

Applied to `config/constitution.md` on 2026-10-07 after the owner approved it. The owner also changed section 3 (videos that pass every guardrail post automatically while the posting switch is on) and section 4 (kill switch = the AI does nothing, except platform-required data deletion).

**Section 1, replace the first sentence with:**

> Run an experiment: can an AI, starting from an owner-approved production guideline for each channel, improve on it by testing one change at a time and learning from performance data? The product is learning and quality, not upload volume.

**Section 8, replace the section with:**

> Each channel follows its owner-approved guideline (`config/guideline.md`), which sets topics, format, hooks, tone, length, look, sound, titles and covers. The AI may run one experiment per channel, changing one guideline item. A change that performs better becomes a proposal; it takes effect only when the owner approves it. Where the guideline is silent, the AI decides. The AI never edits the guideline.


## 5. Known gaps (deferred, from reviews)

All are minor; none blocks parts 2 or 3. The build ledger was deleted with the worktree, so this is the full list.

* **Loader:** `runtime_s` uses `int()`, so it truncates floats and accepts bools, and number settings accept a bool.
  The YAML fence is matched anywhere in the file, not only at the top. The 10-180 s runtime limits are hard-coded
  in `guideline.py`.
* **Critic:** the old "Guideline runtime: N-Ms" suffix in the script prompt is redundant.
  `guideline.for_services` runs twice when there is no guideline.
* **Editor:** if the chosen idea breaks a topic rule, that slot is empty for the cycle, and this is undocumented.
  The editor may pick an older unlabelled idea when the needed arm has no candidates; that idea runs outside the
  experiment.
* **Experiments:**
  * With a guideline, the KPI is always the composite score (spec D5).
  * The guideline is loaded once per process.
  * `cooldown_left` compares second-precision timestamps with a strict `>`.
  * While a topic-rule experiment runs, ideation makes 2 LLM calls per batch.
* **Constitution screen:** the phrase list misses "fabricating", "fabrication", "made up" and "invent a quote", and
  "make up" can false-positive. The critic and fact-check still gate every script.
* **Proposals:**
  * A column-0 YAML comment inside `locked:`, or a heading inside a variant, makes approve impossible. This fails
    safe: nothing is written.
  * The template rounds p to "100%" at p ≥ 0.995.
  * `test_cli_reject_needs_note` is misnamed: notes are optional.
  * The learn sweep could retry forever if a `variant_won` experiment's item already has a pending proposal. The
    one-pending and one-running rules should prevent this.
* **Dashboard:** POSTs have no CSRF protection, the same as the existing controls.
* **Instances:** the `NOT APPROVED` marker is a substring match anywhere in the file.
* **Tests:**
  * The dry-run test relaxes the sample's 60-character title check to 100, because the fixture LLM writes long
    titles.
  * There is no second-cycle orchestrator test of migration idempotence (a unit test covers it).
  * `test_identity` still has "starting_genres" in a test name.
  * The suite prints a pre-existing Starlette deprecation warning.
* **Polling:** if a poll call raises an error while posting is off, the row becomes an "uncertain" failure. Its
  retry waits until posting is back on and uses no attempts. A pre-existing edge: after a kill longer than 24 h,
  `recover()` runs before publish, so a pollable `uploading` row is marked uncertain instead of being polled.

## 6. Handoff: open items (2026-10-08)

Do these in order.

1. **Owner: log in to YouTube again.** The login expired on 7 Oct, and live YouTube uploads have failed since then
   (`invalid_grant`).
   * Command: `.\.venv\Scripts\python.exe -m aimz youtube auth`.
   * Also set the OAuth consent screen to "In production", so the login stops expiring every 7 days.
   * Agents cannot do this: it is an interactive Google sign-in.
2. **Then upload the test video unlisted and send the owner the link.** Steps are in
   `data/tests/2026-10-07-guideline-test/README.md`. The owner asked for this link.
3. **Part 2 (production abilities).** Write a spec with the brainstorming process, then a plan, then build. The
   owner already approved the direction on 2026-10-07: the full list is in section 1. Add what the test video
   showed:
   * **Deterministic hook check.** The small critic model does not enforce craft rules reliably. The test opened
     with "The zodiacal light is a faint glow..." and G-hook was not flagged. Add code checks for definition
     openings ("<Name> is a/an/the ...") and for the guideline's banned openings.
   * **Genre enforcement.** The test ignored the guideline's genre. Ideation and editor topic gating need to
     enforce `guideline.genre`, not only show it.
   * **SigLIP relevance gate.** One image did not match its beat.
   * **Image credits moved into the description.** The CC licence allows it when nothing is credited on screen.
     See `docs/VIDEO_QUALITY_RESEARCH.md`.
   * **Kokoro-82M voice** (Apache-2.0, CPU). Add it as a voice option; the guideline then locks it.
   * Licence and source notes for all of these are in `docs/VIDEO_QUALITY_RESEARCH.md`.
4. **Part 3.** Draft Backhouse Explainers' `config/guideline.md` from the research and the test draft. The owner
   approves it line by line before it goes in place. The channel's current identity is "maps and geography
   oddities", voice `en_US-kristin-medium`. Writing this file activates the guideline: the first cycle retires
   the running experiment and hands the locked settings to the guideline.
5. **Owner items outside this plan:**
   * Google's compliance screencast, due Thu 15 Oct (`docs/OWNER_TODO.md` A0).
   * The constitution section 5 media sentence for stock media (A3).
6. **Uncommitted files on `main`.** As of 2026-10-08 these are not committed:
   * `config/strategy.md`: written by the AI each cycle, which is normal.
   * `docs/CEO_BRIEF.md` and `docs/OWNER_TODO.md`: edits from an earlier session about the compliance screencast,
     plus this session's rescope and YouTube-login updates.

   Commit them when the owner agrees, or leave them for the owner.

## 7. Session log

| Date | Part | What was done | Next |
|---|---|---|---|
| 2026-10-07 | Rescope | Owner rescoped the project; research and design in `docs/VIDEO_QUALITY_RESEARCH.md` and the spec; owner approved the spec and scoped it to Backhouse Explainers. | Build part 1 |
| 2026-10-07 | Part 1 | Built the guideline system in eight tasks on `feat/guideline-system`: loader and versions; locked-value enforcement; craft rules in prompts and critic; one-experiment engine; proposals and owner approval with CLI and dashboard; strategist under a guideline and switch-over; new-channel gate; these docs and the offline dry-run test. Every task was reviewed. | Merge; owner approves the constitution wording (section 4); then the part 2 spec |
| 2026-10-07 | Part 1 | Final whole-branch review fixes: loader survives a stored version that stops validating; cover-rule experiments refused; unavailable locked items in `aimz status`; critic gates script/title rule IDs only; whole-word constitution screen; cooldown after Guideline won / Safety stop (R11); proposal sweep in learn; owner edit supersedes a pending proposal. | Merge |
| 2026-10-07 | Part 1 | Owner lowered the proposal bar to 90% (guideline wins and the safety stop now at P <= 0.10). | Test video |
| 2026-10-07/08 | Test + switches | Rendered a 31 s test video in an isolated copy with a test-only guideline (`data/tests/2026-10-07-guideline-test/`). The upload failed because the YouTube login expired. Owner decided: auto-posting under guardrails, kill switch = do nothing, new posting switch; built and reviewed. Constitution sections 1/3/4/8 applied. Merged to `main` (`fb57dad`), 416 tests; live `aimz status` checked. | Section 6 (handoff) |
