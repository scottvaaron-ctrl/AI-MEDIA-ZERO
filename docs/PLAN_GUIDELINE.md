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
| 1 | The guideline system: file, enforcement, one experiment per channel, proposals and owner approval, CLI, dashboard page, new-channel gate, docs | **Done** (branch `feat/guideline-system`, 2026-10-07) |
| 2 | Production abilities the guideline needs: full-bleed default layout, SigLIP image-to-narration relevance gate, Kokoro TTS, attribution in the description, -14 LUFS mastering, stock-video providers | Not started |
| 3 | The guideline text for Backhouse Explainers, drafted from `docs/VIDEO_QUALITY_RESEARCH.md`; the owner approves it | Not started |

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
* Outcomes: the variant wins at 95% confidence and 10 videos per arm (a proposal is written); the guideline
  wins at 95% (closed, logged); safety stop at 95% against the variant with 6 per arm; inconclusive at 30 per arm.
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
  guideline path uses engine constants `GUIDELINE_MIN_SAMPLE = 10`, `GUIDELINE_MIN_CONFIDENCE = 0.95` and one
  experiment per channel. The new keys `safety_min_sample`, `max_sample`, `cooldown_videos` are in config.
* **R8.** Approving a `G-runtime` proposal sets `runtime_s` to round(0.6N) to round(1.4N), clamped to 10-180.
* **R9.** Settings the guideline does not lock stay the AI's to open.
* **R10.** `docs/CEO_BRIEF.md` and `docs/OWNER_TODO.md` are not edited by the build branch (the owner's
  uncommitted edits live in the main checkout). The controller updates them after merge.
* **R11.** The 20-video cooldown also follows an experiment the guideline won or safety-stopped, not only an
  inconclusive one or a rejected proposal.

## 4. Constitution wording awaiting owner approval

Not applied. `config/constitution.md` is owner-controlled; edit it only after the owner says yes in chat.

**Section 1, replace the first sentence with:**

> Run an experiment: can an AI, starting from an owner-approved production guideline for each channel, improve on it by testing one change at a time and learning from performance data? The product is learning and quality, not upload volume.

**Section 8, replace the section with:**

> Each channel follows its owner-approved guideline (`config/guideline.md`), which sets topics, format, hooks, tone, length, look, sound, titles and covers. The AI may run one experiment per channel, changing one guideline item. A change that performs better becomes a proposal; it takes effect only when the owner approves it. Where the guideline is silent, the AI decides. The AI never edits the guideline.

After approval: edit the two sections, then run `pytest` (tests that read the constitution must still pass).

## 5. Known gaps (deferred, from reviews)

* With a guideline the KPI is always the composite score; the guideline is loaded once per process.
* Dashboard POSTs have no CSRF protection (same as the existing controls).
* The dry-run test relaxes the sample's 60-character title check to 100 in its temporary copy, because the
  fixture LLM writes long titles.
* See the build ledger (`.superpowers/sdd/2026-10-07-guideline-system/progress.md`) for the full list of
  minor deferrals.

## 6. Session log

| Date | Part | What was done | Next |
|---|---|---|---|
| 2026-10-07 | Rescope | Owner rescoped the project; research and design in `docs/VIDEO_QUALITY_RESEARCH.md` and the spec; owner approved the spec and scoped it to Backhouse Explainers. | Build part 1 |
| 2026-10-07 | Part 1 | Built the guideline system in eight tasks on `feat/guideline-system`: loader and versions; locked-value enforcement; craft rules in prompts and critic; one-experiment engine; proposals and owner approval with CLI and dashboard; strategist under a guideline and switch-over; new-channel gate; these docs and the offline dry-run test. Every task was reviewed. | Merge; owner approves the constitution wording (section 4); then the part 2 spec |
| 2026-10-07 | Part 1 | Final whole-branch review fixes: loader survives a stored version that stops validating; cover-rule experiments refused; unavailable locked items in `aimz status`; critic gates script/title rule IDs only; whole-word constitution screen; cooldown after Guideline won / Safety stop (R11); proposal sweep in learn; owner edit supersedes a pending proposal. | Merge |
