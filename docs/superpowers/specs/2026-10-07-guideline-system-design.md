# Design: owner guideline per channel, one experiment at a time (part 1 of the 2026-10-07 rescope)

_Status: the owner approved the written spec on 2026-10-07. This spec covers part 1._

**Scope:** the `main` instance only, which is the **Backhouse Explainers** YouTube channel that aimz uploads to. The owner's other YouTube projects (Breakdown, and the Monmouth County & Politics channel) are separate and owner-run. They are not aimz instances, and nothing in this work touches them.

## 0. Context and decision

The owner looked at the Shorts and called them "AI slop". The audit and research are in `docs/VIDEO_QUALITY_RESEARCH.md`. On 2026-10-07 the owner rescoped the project:

> Rather than the AI having full creative control we give it guidelines to follow, and then it is
> allowed to choose one to experiment with. If it performs better than the guidelines we gave it then it
> may change. No longer does the AI start from zero.

This replaces the earlier rules:
- the "AI owns creative choices" rule in constitution §8, `docs/PLAN_FULL_CONTROL.md` and the agent memory;
- the 2026-10-01 `starting_genres` exception.

Owner decisions made during the design:

| # | Question | Decision |
|---|---|---|
| D1 | Who changes the guideline when a variant wins? | The AI proposes; the owner approves |
| D2 | How many experiments at once? | One per channel |
| D3 | What does the guideline cover? | Everything: production look and sound, script craft, topic selection, packaging |
| D4 | How do channels stay distinct? | One full guideline per channel |
| D5 | What is "better" measured on? | The composite score (average % viewed is already its largest part, weight 0.35), with average % viewed shown alongside |
| D6 | Approach | A: hard values plus a written playbook, with locked settings and craft rules |
| D7 | Bar for proposing a change | ≥90% confidence and ≥10 scored videos per arm (owner lowered it from 95% on 2026-10-07, after the build) |
| D8 | Constitution wording | The agent drafts it; after owner approval the agent edits `config/constitution.md` directly |

The rescope has three parts, built in this order:
1. **The guideline system** (this spec).
2. **Production abilities the guideline needs:**
   - full-bleed default layout;
   - a SigLIP image–narration relevance gate;
   - Kokoro TTS;
   - attribution moved to the description;
   - −14 LUFS mastering;
   - stock-video providers.
3. **Backhouse Explainers' guideline content.** Drafted from the research; the owner approves it.

Parts 2 and 3 get their own specs.

## 1. The guideline file

**Location.**
- `config/guideline.md` for `main`.
- `instances/<name>/config/guideline.md` for every other channel.

**Ownership.**
- The owner owns the file. The AI reads it and never writes it.
- Each change is stored as a version in a new table `guideline_versions` (migration `0007`), with these columns:
  - `id`, `instance`, `version` (an integer that increases);
  - `created_at`;
  - `source`, which is `owner_edit`, `proposal:<id>` or `initial`;
  - `reason`;
  - `content` (the full file text).
- The file on disk is the current version. The loader compares the file's hash with the latest row; on a mismatch, which means the owner edited the file directly, it saves a new `owner_edit` version.

**Format.** A Markdown file that starts with one fenced YAML block, followed by the craft rules.

```yaml
guideline:
  channel: Backhouse Explainers
  genre: "..."
locked:            # existing CATALOG setting names -> value; every key must be in CATALOG and in bounds
  template: full_bleed
  caption_max_words: 3
  ...
runtime_s: {min: 20, max: 45}
checks:            # deterministic, optional
  banned_openings: ["Did you know", "X is a"]
  title_max_chars: 60
```

**Craft rules.**
- Every rule is a heading of the form `### G-<id>: <title>`, followed by its text.
- Each rule carries a `stage` tag (`topic`, `script`, `title`, `cover`), which says where it is applied.
- **Locked values have IDs too:** each one is addressed as `G-<setting>`, for example `G-caption_max_words`.

**Validation.** `aimz.guideline.load(instance)` parses the file and returns a `Guideline` object. A bad file is refused and production falls back to the last valid version. The file is bad when:
- a key is unknown;
- a value is out of bounds;
- an ID is duplicated;
- the YAML is missing or doesn't parse.

The refusal shows in `aimz status`.

**What the guideline replaces.** It takes over three existing mechanisms:
- `channels.starting_genres`;
- the strategist's `setting_changes` on any setting the guideline covers;
- `ChannelIdentityProposal`.

The channel identity (genre, voice, look) is now part of each guideline. `identity.py` keeps its cross-channel uniqueness check, which now checks guidelines when they are saved. `strategy.md` stays as the AI's learning notebook; it has no authority over guideline items. Items the guideline does not mention stay the AI's choice.

**Activation.** A channel without `guideline.md` behaves exactly as it does today. Part 1 can therefore ship before parts 2 and 3.

## 2. Enforcement

**Locked values.**
- `SettingsEngine.assign` takes the first value that applies, in this order:
  1. the experiment's variant value, for a treatment-arm video when the experiment targets this setting (`source="experiment"`);
  2. the guideline's locked value (`source="guideline"`);
  3. identity, open, locked, always-on and default, as today.

  Steps 1 and 2 are new.
- If the strategist sends `setting_changes` for a guideline-covered setting, the change is refused and recorded in `last_rejected_setting` with the text "covered by the guideline; propose an experiment instead".

**Runtime.**
- Ideation and script may only choose a runtime inside `runtime_s`.
- If a script's estimated runtime falls outside the range, `_overrides` adds a required revision.

**Craft rules in prompts.**
- The craft rules for each stage are added to the user prompts of these agents:
  - **topic rules:** `IdeationAgent.run` (`agents/ideation.py:131`) and `EditorAgent._choose` (`agents/editor.py:181`);
  - **script and title rules:** `ScriptAgent.write` (`agents/script.py:67`) and `CriticAgent.review` (`agents/critic.py:132`).
- The script and critic prompts currently carry no channel context; the guideline fixes that.

**Critic.**
- `CriticResult` gains `broken_rules: list[str]`, the IDs of the rules the script breaks.
- In `_overrides`, any broken rule becomes a required revision with the rule's text. The existing loop applies: 2 revisions plus a final round, then `rejected`.
- For a video on an experiment's variant arm, the critic checks the variant rule instead of the guideline rule.

**Code-only checks** (in `_overrides`, no LLM judgement):
- runtime inside the range;
- caption chunk word count, using the renderer's chunker;
- `banned_openings` against the first sentence;
- `title_max_chars`.

**Topic and title rules.**
- Ideation is told the topic rules.
- The editor drops ideas that break them before any script is written, and records the rule ID on the dropped idea.

**Missing abilities.**
- If a locked value needs an ability that is not installed yet (for example a Kokoro voice before part 2), the guideline loader marks that item `unavailable`.
- The video then uses the closest valid value, which is the renderer default, and records `source="guideline_fallback"`.
- `aimz status` warns. Production never stops for this.

## 3. One experiment per channel

**Proposal.**
- `ExperimentProposal` gains two fields:
  - `guideline_item`, a rule or setting ID;
  - `variant`, either a value or the replacement rule text.
- Code refuses a proposal when:
  - the item does not exist in the current guideline;
  - a setting variant is outside `CATALOG` bounds;
  - the channel already has an active experiment;
  - the same item closed as inconclusive or was rejected in the last 20 published videos;
  - the variant text contains instructions that contradict the constitution (a keyword screen, plus the critic's existing policy checks on every script).
- Refusals are fed back through `last_rejected_experiment`, as today.
- Engine settings:
  - `max_concurrent=1` per channel;
  - `ASSIGNABLE_VARIABLES` becomes "any guideline item".

**Assignment.**
- `EditorAgent.select` alternates arms strictly per selected idea, using the existing `assign_arm`. The arms are `control` (the guideline) and `treatment` (the variant).
- A setting variant is applied by the producer.
- A craft variant replaces that single rule in the script and critic prompts for that idea.
- For a `topic`-stage item, ideation writes half its batch under each version and labels each idea's arm. The editor keeps the two arms balanced.
- With no experiment running, every video follows the guideline. The `Exploration ratio` field is retired.

**Evaluation.**
- One sample per scored video (the composite score, at the existing 72 h rule).
- Confidence comes from the existing `welch_confidence`.
- Outcomes:

  | Outcome | When | What happens |
  |---|---|---|
  | **Variant wins** | P(variant better) ≥ 0.90 and n ≥ 10 per arm | Proposal (section 4); the experiment closes |
  | **Guideline wins** | P(variant better) ≤ 0.10 and n ≥ 10 per arm | Closes; logged in `strategy.md` |
  | **Safety stop** | P ≤ 0.10 and n ≥ 6 per arm | Closes early |
  | **Inconclusive** | n ≥ 30 per arm with no decision | Closes |

- Each result reports the mean composite and mean average % viewed for each arm.
- Config: `experiments.min_sample=10`, `experiments.min_confidence=0.90`, `experiments.safety_min_sample=6`, `experiments.max_sample=30`.

## 4. Proposals and owner approval

**Storage.** A new table `guideline_proposals` (migration `0007`) with these columns:

| Column | Holds |
|---|---|
| `id`, `instance`, `experiment_id` | identity and link to the experiment |
| `item` | the guideline item ID |
| `before`, `after` | the old and new text or value |
| `evidence_json` | per-arm n, mean composite, mean average % viewed, confidence, and video IDs |
| `summary` | the AI's plain-English summary |
| `status` | `pending`, `approved` or `rejected` |
| `owner_note`, `created_at`, `decided_at` | the owner's decision record |

**Summary check.** The AI's `summary` is checked by code: every number in it must match `evidence_json`. If it fails, the summary is replaced by a template built from the evidence.

**Where the owner sees proposals.**
- `core/attention.py` adds the line "N guideline change(s) waiting", with the command to fix it. A proposal pending for more than 14 days is marked overdue.
- On the dashboard, a guideline page shows the current version, its history and the pending proposals, with Approve and Reject buttons.
- CLI commands:
  - `python -m aimz guideline show`
  - `python -m aimz guideline history`
  - `python -m aimz guideline proposals`
  - `python -m aimz guideline approve <id>`
  - `python -m aimz guideline reject <id> --note "..."`
- When proposals are pending, agents updating `docs/CEO_BRIEF.md` list them under the owner's decisions.

**Approve.**
1. Rewrite that single item in `guideline.md`.
2. Save the new version with `source="proposal:<id>"`.
3. Every later video uses it.

**Reject.**
- The guideline is unchanged.
- The owner's note goes into the strategist's next prompt.
- The item is blocked for 20 videos.

**While a proposal is pending.**
- Videos follow the current guideline.
- The channel may start an experiment on a *different* item.
- An item can have at most one pending proposal.

**Owner edits.** A direct edit to the guideline creates a version. Any active experiment whose item changed is closed with the reason "baseline changed by owner".

## 5. Rollout and documentation

**Transition**, run on the first cycle that finds a guideline:
- Retire the active experiment `exp_20261006T214417_0a30ab72` with the reason "rescope 2026-10-07".
- Set every guideline-covered setting in `setting_space` to `off`, so the guideline takes over. Its evidence history is kept and stays visible to the strategist.
- Retire `starting_genres` from the strategist prompt.

**New channels.**
- `instance create` writes a guideline template with a header that says it is not yet approved.
- Uploads stay disabled until the owner approves the channel's guideline.

**Documents to change.**

| Document | Change |
|---|---|
| `config/constitution.md` §1 and §8 | The agent drafts new wording, shows it to the owner, and edits the file after approval (D8). Intended meaning: the AI follows the owner-approved channel guideline, may test one change at a time per channel, and changes take effect only with owner approval. Honesty, licensing and elevated review are unchanged. |
| `docs/PLAN_FULL_CONTROL.md` | Add a "superseded by the 2026-10-07 rescope" banner |
| `docs/PLAN_GUIDELINE.md` | New working plan, with the status table for parts 1–3 |
| `docs/HANDOFF.md`, `docs/CEO_BRIEF.md`, `docs/OWNER_TODO.md` | Update |
| `CLAUDE.md` | The read-first list points to `PLAN_GUIDELINE.md` |
| Agent memory | Replace `feedback-ai-owns-creative-design` |

**Vault.** CEO_BRIEF Part 2 asks the vault session to log this decision in the vault's decision log. The agent does not write to the vault.

## 6. Testing

**Unit tests** (pytest, fixture LLM):
- guideline parsing and validation, including bad files and fallback to the last valid version;
- version on hash change;
- `assign` precedence and `guideline_fallback`;
- refusal of `setting_changes` on covered settings;
- proposal validation: unknown item, out of bounds, concurrent, cooldown;
- arm alternation, and craft-variant prompt substitution;
- the evaluation outcomes: win, loss, safety stop, inconclusive, and the boundaries 0.90 / 10 / 6 / 30;
- approve/reject versioning and the cooldown;
- the summary number check;
- `broken_rules` → required revision;
- the code-only checks;
- the transition step.

**Full suite.** `pytest` must pass as CI runs it, with `.[dev,youtube]`.

**Dry run.** One `DRY_RUN` cycle with a sample guideline. Check two things:
- `video_settings` rows show `source="guideline"`;
- a forced experiment produces one control and one treatment video, with the right overrides.

## 7. Out of scope (parts 2 and 3)

- The renderer abilities.
- The guideline text for Backhouse Explainers.
- Guidelines for any new channel.
