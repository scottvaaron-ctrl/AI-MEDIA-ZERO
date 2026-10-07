# Guideline System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Backhouse Explainers video follow an owner-approved, versioned guideline. The AI may test one guideline item at a time. A variant that wins becomes a proposal that waits for the owner's yes.

**Architecture:** A new module `aimz/guideline.py` parses and validates `config/guideline.md` and stores its versions. Existing code reads that `Guideline` object:
- `SettingsEngine.assign` applies its locked values;
- the four LLM prompts get its craft rules;
- the critic gate checks it;
- `ExperimentEngine` validates against its item IDs.

A second new module, `aimz/guideline_proposals.py`, turns winning experiments into proposals and applies the owner's decision. With no `guideline.md`, every code path behaves as it does today.

**Tech Stack:** Python 3.12, SQLite migrations, pydantic models, PyYAML, typer CLI, FastAPI + Jinja dashboard, pytest with the `fixture` LLM provider.

**Spec:** `docs/superpowers/specs/2026-10-07-guideline-system-design.md`

## Global Constraints

- Scope is the `main` instance only (Backhouse Explainers). The owner's Breakdown and Monmouth County & Politics channels are out of scope.
- A channel without `guideline.md` behaves exactly as it does today. Every new code path is a no-op when `guideline.load(...)` returns `None`.
- The AI never writes `guideline.md`. Only the owner's direct edits and `guideline approve` change it.
- Proposal bar: P(variant better) ≥ **0.95** and n ≥ **10** scored videos per arm.
- Guideline wins at P ≤ 0.05 with n ≥ 10. Safety stop at P ≤ 0.05 with n ≥ **6**. Inconclusive at n ≥ **30** per arm.
- One running experiment per channel. An item is blocked for **20** published videos after an inconclusive result or a rejected proposal.
- A pending proposal is flagged overdue after **14** days.
- Metric: the composite score from `analytics/store.py` (one sample per video, at the 72 h rule). Mean `avg_percent_viewed` per arm is reported alongside it.
- $0 budget, no new paid dependencies; PyYAML is already a dependency.
- Launch through the interpreter: `python -m aimz ...`
- CI runs plain `pytest` with `.[dev,youtube]`, and that run must stay green.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not edit `config/constitution.md` until the owner approves the drafted wording in chat (Task 8).

## Review Focus

1. **Bad guideline edit:** a YAML typo, an unknown setting, or an out-of-bounds value made by the owner by hand. Production keeps running on the last valid version, and `aimz status` names the error. Tested in Task 1.
2. **Owner edits mid-experiment:** the file is edited while an experiment on the changed item is running. The experiment closes with "baseline changed by owner"; experiments on other items keep running. Tested in Task 5.
3. **AI summary with wrong numbers:** the AI's proposal summary quotes numbers that are not in the evidence. The summary is replaced with the evidence template. Tested in Task 5.
4. **Variant that needs a missing ability:** a setting variant needs something not installed (e.g. a Kokoro voice). The proposal is refused at validation rather than producing silently unchanged videos. Tested in Task 4.
5. **Guideline removed:** `guideline.md` is deleted after activation. Behaviour falls back to the last stored version, not to the pre-guideline AI freedom, because the owner never asked to drop it. Tested in Task 1.

---

### Task 1: Guideline file, parser, validation and version store

**Files:**
- Create: `src/aimz/guideline.py`
- Create: `src/aimz/db/migrations/0007_guideline.sql`
- Create: `tests/test_guideline.py`
- Create: `tests/fixtures/guideline_sample.md`, a valid sample with 3 locked values, `runtime_s`, `checks`, and four rules with stages topic, script, title and cover.

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Rule: id: str; title: str; stage: str; text: str`. `stage` is one of `topic|script|title|cover`.
  - `@dataclass(frozen=True) class Guideline` with these fields:
    - `version: int`
    - `channel: str`
    - `genre: str`
    - `locked: dict[str, Any]`
    - `runtime_min: int`, `runtime_max: int`
    - `checks: dict[str, Any]`
    - `rules: dict[str, Rule]`
    - `unavailable: dict[str, str]` (setting → reason)
    - `content: str`
  - `Guideline` methods:
    - `.item_ids() -> set[str]`: rule IDs, plus `G-<setting>` for each locked key, plus `G-runtime`.
    - `.rules_for(stage: str, replace: dict[str, str] | None = None) -> list[Rule]`. `replace` maps a rule ID to variant text.
    - `.prompt_block(stage: str, replace: dict[str, str] | None = None) -> str`
  - `class GuidelineError(ValueError)`
  - `parse(text: str, catalog: dict[str, SettingDef], caps: set[str]) -> Guideline`. Raises `GuidelineError`.
  - `load(db: Database, config_dir: Path, catalog, caps) -> Guideline | None`
  - `path(config_dir: Path) -> Path`. Returns `config_dir / "guideline.md"`.
  - `save_version(db, content: str, source: str, reason: str) -> int`
  - `write(db, config_dir, content: str, source: str, reason: str) -> int`. Validates, writes the file, saves the version, and returns the version number.
  - State key `guideline_error` (str), read by `aimz status`.
- Migration `0007_guideline.sql`:
  - Table `guideline_versions(id TEXT PK, version INTEGER NOT NULL, source TEXT NOT NULL, reason TEXT, content TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL)`.
  - Table `guideline_proposals(id TEXT PK, experiment_id TEXT, item TEXT NOT NULL, before TEXT, after TEXT NOT NULL, evidence_json TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL, owner_note TEXT, created_at TEXT NOT NULL, decided_at TEXT)`.
  - `ALTER TABLE experiments ADD COLUMN guideline_version INTEGER;`
  - `ALTER TABLE ideas ADD COLUMN guideline_breaks TEXT;`

**File format** (the parser contract):
- The file opens with one fenced YAML block (`` ```yaml ``) containing these keys:
  - `guideline: {channel, genre}`
  - `locked: {setting: value}`
  - `runtime_s: {min, max}`
  - `checks: {banned_openings: [...], title_max_chars: int}`. Optional.
- After the YAML block, each rule is a heading `### G-<id>: <title>`. The next line is `stage: <stage>`, and the rest up to the next heading is the rule's text.

**Validation errors** (each raises `GuidelineError` with a message naming the problem):
- no YAML block;
- an unknown `locked` key;
- `SettingDef.check` fails;
- `runtime_s` outside `content.short_form` limits of 10–180, or min > max;
- a duplicate rule ID;
- a bad stage;
- an empty rule text.

A locked value whose `SettingDef.requires` is not in `caps` is not an error. It goes into `unavailable` instead.

- [ ] **Step 1: Write the failing tests** in `tests/test_guideline.py`:
  - `test_parse_sample`: `g.locked["caption_max_words"] == 3`, `g.runtime_min == 20`, and `{"G-hook", "G-caption_max_words", "G-runtime"} <= g.item_ids()`.
  - `test_rejects_unknown_setting`, `test_rejects_out_of_bounds`, `test_rejects_duplicate_rule_id`, `test_rejects_missing_yaml`: each uses `pytest.raises(GuidelineError)`.
  - `test_unavailable_capability`: a locked `clip_scenes: on` with `caps=set()` gives `"clip_scenes" in g.unavailable` and no error.
  - `test_load_none_without_file(svc)`: `load(...) is None`, and no `guideline_versions` rows exist.
  - `test_load_saves_initial_version(svc)`: write the sample file and call `load`. Version 1 is stored with `source="initial"`. A second `load` adds no row.
  - `test_owner_edit_creates_version(svc)`: change the file text and call `load`. Version 2 has `source="owner_edit"`.
  - `test_bad_edit_keeps_last_valid(svc)`: break the YAML and call `load`. The result is `g.version == 1` and `db.get_state("guideline_error")` contains `"YAML"`.
  - `test_deleted_file_uses_last_version(svc)`: after activation, delete the file. `load` returns the last stored version.
  - `test_prompt_block_replace`: `g.prompt_block("script", {"G-hook": "Open on a question."})` contains `"Open on a question."` and not the original G-hook text.
- [ ] **Step 2:** Run `pytest tests/test_guideline.py -v`. Expected: FAIL with `ModuleNotFoundError: aimz.guideline`.
- [ ] **Step 3:** Implement the migration and `guideline.py`.
  - `load` hashes the file text with sha256 and compares it with the latest row.
  - A parse failure sets `guideline_error` and returns the last valid stored version, parsed. A success clears `guideline_error`.
- [ ] **Step 4:** Run `pytest tests/test_guideline.py tests/test_db.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit `feat: guideline file parser, validation and version store`.

---

### Task 2: Guideline values in per-video settings

**Files:**
- Modify: `src/aimz/experiments/settings.py`, in `SettingsEngine.assign` (`:353`) and `change` (`:310`).
- Modify: `src/aimz/agents/producer.py:188` (`choose_settings`).
- Modify: `src/aimz/agents/analyst.py:415` (`apply_setting_changes`).
- Test: `tests/test_video_settings.py`

**Interfaces:**
- Consumes: `guideline.load`, `Guideline.locked`, `Guideline.unavailable` (Task 1).
- Produces:
  - `SettingsEngine.assign(..., guideline: dict[str, Any] | None = None, unavailable: set[str] | None = None, experiment: dict[str, Any] | None = None)`. `experiment` is `{variable, value}`, applied only on a treatment arm.
  - Precedence per variable:
    1. `experiment` → `source="experiment"`
    2. `guideline` → `"guideline"`
    3. a variable in `unavailable` → the renderer default with `"guideline_fallback"`
    4. today's chain (identity, open, locked, always-on, default)
  - `SettingsEngine.change(..., covered: set[str] | None = None)`. Refuses with `"{var}: covered by the guideline; propose an experiment instead"`.
  - `ProducerAgent.guideline() -> Guideline | None`. Cached per cycle.

- [ ] **Step 1: Write the failing tests:**
  - `test_guideline_value_wins_over_open`: open `caption_max_words` with [4, 8] and set guideline {caption_max_words: 3}. The assigned value is 3 with source `guideline`.
  - `test_experiment_value_wins_over_guideline`: experiment {variable: caption_max_words, value: 5} gives 5 with source `experiment`.
  - `test_unavailable_falls_back_to_default`: source is `guideline_fallback`, and the value equals `defaults[var]`.
  - `test_change_refused_when_covered`: `change("template", "lock", locked_value="card", covered={"template"})` returns a string containing `"covered by the guideline"`.
  - `test_no_guideline_unchanged`: called without the new kwargs, the result equals today's output.
- [ ] **Step 2:** Run `pytest tests/test_video_settings.py -v`. Expected: the new tests FAIL with `TypeError: unexpected keyword`.
- [ ] **Step 3:** Implement the new kwargs and precedence.
  - `ProducerAgent.choose_settings` passes `g.locked`, `set(g.unavailable)`, and the idea's experiment override. Task 4 provides that through `ExperimentEngine.setting_override(idea)`; until Task 4 lands, pass `None`.
  - `apply_setting_changes` passes `covered=set(g.locked)`.
- [ ] **Step 4:** Run `pytest tests/test_video_settings.py tests/test_dials.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit `feat: guideline locked values outrank AI-chosen settings`.

---

### Task 3: Craft rules in prompts, critic rule checks and code checks

**Files:**
- Modify: `src/aimz/domain/models.py:169`. Add `broken_rules: list[str] = Field(default_factory=list)` to `CriticResult`.
- Modify: `src/aimz/agents/ideation.py:131`, `src/aimz/agents/editor.py:181`, `src/aimz/agents/script.py:67`, `src/aimz/agents/critic.py:132` and `:219`.
- Test: `tests/test_guideline_prompts.py`, a new file.

**Interfaces:**
- Consumes: `Guideline.prompt_block(stage, replace)` and `Guideline.checks` (Task 1).
- Produces:
  - `aimz.guideline.variant_for(idea: dict, db) -> dict[str, str]`. Returns `{rule_id: variant_text}` when the idea is on the treatment arm of a craft-rule experiment, otherwise `{}`.
  - `CriticAgent._overrides(..., guideline: Guideline | None = None, replace: dict[str, str] | None = None)` adds these required revisions:
    - each ID in `broken_rules` that exists in the guideline → `"Breaks {id} ({title}): {text}"`;
    - spoken runtime outside `runtime_min`–`runtime_max` → `"Runtime {s}s is outside the guideline's {min}-{max}s"`. Use `len(narration.split()) / WORDS_PER_SECOND`.
    - first sentence starts with a `banned_openings` entry (case-insensitive) → `"Opening '{x}' is banned by the guideline"`;
    - title longer than `title_max_chars` → `"Title is {n} characters; the guideline allows {max}"`.
- Prompt wiring:
  - The ideation and editor prompts get `prompt_block("topic")`.
  - The script and critic prompts get `prompt_block("script")` + `prompt_block("title")`.
  - Each block is headed "Channel guideline (owner-approved; follow it):".
  - The critic prompt adds: "List in broken_rules the ID of every guideline rule the script breaks."
  - The script runtime line uses the guideline's range in place of `min_s`/`max_s`.
- Editor: an idea whose `guideline_breaks` (written by the editor's LLM call; extend `_choose`'s schema with `breaks: list[str]`) contains a topic rule ID is rejected with `eic_notes="breaks guideline {ids}"`.

- [ ] **Step 1: Write the failing tests:**
  - `test_script_prompt_contains_rules(svc)`: with the sample guideline active, the captured fixture-LLM prompt for `ScriptAgent.write` contains the G-hook text and `"20-45"`.
  - `test_critic_broken_rule_requires_revision`: `_overrides` with `result.broken_rules=["G-hook"]` gives `passed is False` and a revision starting `"Breaks G-hook"`.
  - `test_runtime_out_of_range`: 200 words gives a revision containing `"outside the guideline's 20-45s"`.
  - `test_banned_opening` and `test_title_too_long`.
  - `test_variant_replaces_rule_in_critic_prompt`: on the treatment arm, the prompt contains the variant and not the original text.
  - `test_no_guideline_prompts_unchanged(svc)`: without a guideline, the prompts contain no `"Channel guideline"`.

  For capturing prompts, follow the existing pattern in `tests/test_learning_inputs.py`.
- [ ] **Step 2:** Run `pytest tests/test_guideline_prompts.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the wiring and checks. `CriticAgent.review` loads the guideline once and passes it to `gate`.
- [ ] **Step 4:** Run `pytest tests/test_guideline_prompts.py tests/test_pipeline_offline.py tests/test_title_claims.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit `feat: guideline craft rules in prompts and critic gate`.

---

### Task 4: One experiment per channel on a guideline item

**Files:**
- Modify: `src/aimz/domain/models.py:197` (`ExperimentProposal`).
- Modify: `src/aimz/experiments/engine.py`: `validate`, `propose`, `evaluate`, `evaluate_all`, plus the new methods below.
- Modify: `src/aimz/agents/editor.py:126-140` (arm application) and `src/aimz/agents/ideation.py` (topic-stage arms).
- Modify: `src/aimz/pipeline/orchestrator.py:48` (engine construction).
- Modify: `config/config.yaml` `experiments:`
- Test: `tests/test_experiments.py`

**Interfaces:**
- Consumes: `Guideline.item_ids()`, `Guideline.rules`, `Guideline.locked` and `Guideline.unavailable` (Task 1); `SettingsEngine.catalog` (Task 2).
- Produces:
  - `ExperimentProposal` gains `guideline_item: str = ""` and `variant: str = ""`.
  - When a guideline is active, the engine stores these values:
    - `variable = guideline_item`;
    - `control =` the current value or rule text;
    - `treatment = variant`;
    - `guideline_version = g.version`;
    - `min_sample = 10`.
  - `ExperimentEngine.__init__(..., guideline: Guideline | None = None, safety_min_sample: int = 6, max_sample: int = 30, cooldown_videos: int = 20)`.
  - `ExperimentEngine.validate(p)` refusals when a guideline is active:
    - `"unknown guideline item"`;
    - `"variant out of bounds: {why}"` (via `SettingDef.check`);
    - `"variant needs {cap}, which is not installed"`;
    - `"an experiment is already running on this channel"`;
    - `"{item} is cooling down for {k} more videos"`;
    - `"variant contradicts the constitution"`. Keyword screen: `"fabricat", "invent", "no source", "unsourced", "clickbait", "fake"`.
  - `ExperimentEngine.cooldown_left(item: str) -> int`: 20 minus the videos published since that item's last inconclusive experiment or rejected proposal.
  - `ExperimentEngine.setting_override(idea: dict) -> dict | None`. Returns `{variable, value}` for a treatment-arm idea whose experiment item is `G-<setting>`.
  - `ExperimentEngine.topic_arm_for_new_idea() -> tuple[str, str] | None`. Gives ideation the arm to label the next idea with, alternating.
  - `ExperimentEngine.evaluate` outcomes when a guideline is active:
    - status `"variant_won"` (P≥0.95, n≥10 each);
    - `"concluded"`, with the conclusion text starting `"Guideline won"` (P≤0.05, n≥10) or `"Safety stop"` (P≤0.05, n≥6);
    - `"inconclusive"` (n≥30 each).

    The result also carries a per-arm `mean_avg_percent_viewed`.
  - `ExperimentEngine.close_for_baseline_change(changed_items: set[str]) -> list[str]`
- Editor:
  - A `G-runtime` treatment sets `suggested_runtime_s`.
  - A `G-<setting>` treatment is applied by the producer through `setting_override`.
  - A rule treatment is applied by the prompts through `variant_for` (Task 3).
  - A topic-stage rule's arm comes from the idea's existing `experiment_arm`, which ideation labelled. The editor picks the next idea from the arm with fewer ideas.
- Config: `experiments.default_min_sample_per_arm: 10`, `max_concurrent: 1`, `min_confidence: 0.95`, `safety_min_sample: 6`, `max_sample: 30`, `cooldown_videos: 20`.

- [ ] **Step 1: Write the failing tests:**
  - `test_validate_unknown_item`, `test_validate_out_of_bounds_variant`, `test_validate_missing_capability`, `test_validate_one_running`, `test_validate_cooldown`, `test_validate_constitution_screen`.
  - `test_variant_wins_at_95_with_10`: synthetic perf rows of 10 vs 10, with a clear treatment lift, give `status == "variant_won"`.
  - `test_not_decided_at_9`: 9 vs 9 with the same lift stays `running`.
  - `test_guideline_wins`.
  - `test_safety_stop_at_6`: 6 vs 6, with treatment clearly worse, gives a conclusion starting `"Safety stop"`.
  - `test_inconclusive_at_30`.
  - `test_reports_avg_percent_viewed`.
  - `test_setting_override_only_on_treatment`.
  - `test_close_for_baseline_change`.
  - `test_legacy_path_without_guideline`: the existing hook_type/runtime tests still pass unchanged.
- [ ] **Step 2:** Run `pytest tests/test_experiments.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement.
  - With no guideline, the engine keeps today's `ASSIGNABLE_VARIABLES` path untouched.
  - Wire `ProducerAgent.choose_settings` to `setting_override` (Task 2's placeholder).
- [ ] **Step 4:** Run `pytest tests/test_experiments.py tests/test_allocation.py tests/test_pipeline_offline.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit `feat: one guideline-item experiment per channel with 95% / n>=10 bar`.

---

### Task 5: Proposals, owner decisions, CLI, status and dashboard

**Files:**
- Create: `src/aimz/guideline_proposals.py`
- Modify: `src/aimz/core/attention.py` (new entries), `src/aimz/cli.py` (new `guideline` typer group, next to `strategy_app`), `src/aimz/dashboard/app.py`.
- Create: `src/aimz/dashboard/templates/guideline.html`, and link it in `base.html`.
- Test: `tests/test_guideline_proposals.py`

**Interfaces:**
- Consumes:
  - `guideline.write` and `guideline.load` (Task 1);
  - `ExperimentEngine.evaluate`'s `"variant_won"` result and `close_for_baseline_change` (Task 4).
- Produces:
  - `create_from_experiment(db, experiment: dict, result: dict, ai_summary: str) -> str`. Returns the proposal ID. Returns the existing ID if one is already pending for that item.
  - `check_summary(summary: str, evidence: dict) -> bool`. Every number in `summary` (regex `\d+(?:\.\d+)?%?`) must equal, after rounding to the shown precision, a number in `evidence`. A percent is compared ×100.
  - `template_summary(evidence: dict) -> str`. Format: `"{item}: variant '{after}' scored {t_mean} vs guideline {c_mean} (n={tn}/{cn}), {p}% likely better; average viewed {t_apv}% vs {c_apv}%."`
  - `approve(db, config_dir, proposal_id: str) -> int`: returns the new guideline version. The rewrite depends on the item:
    - a `G-<setting>` item edits the YAML `locked` value;
    - `G-runtime` edits `runtime_s`;
    - a rule edits that rule's body text.

    The version's source is `f"proposal:{id}"`.
  - `reject(db, proposal_id: str, note: str) -> None`. Stores `owner_note` and sets state `last_rejected_guideline_proposal`.
  - `pending(db) -> list[dict]`
  - `on_version_change(db, old: Guideline, new: Guideline, engine) -> None`. Computes the changed item IDs and calls `close_for_baseline_change` with them. `guideline.load` calls it for `owner_edit` versions.
  - `attention()` gains:
    - `"guideline_changes_waiting"`, with fix `python -m aimz guideline approve <id>` and a note `"overdue"` when older than 14 days;
    - `"guideline_invalid"`, from state `guideline_error`, with fix `"edit config/guideline.md"`.
  - CLI: `guideline show`, `guideline history`, `guideline proposals`, `guideline approve <id>`, `guideline reject <id> --note TEXT`.
  - Dashboard:
    - `GET /guideline` shows the current text, the version table and pending proposals with their evidence;
    - `POST /guideline/proposals/{id}/approve` and `POST /guideline/proposals/{id}/reject` (form field `note`).

- [ ] **Step 1: Write the failing tests:**
  - `test_create_dedupes_pending_item`.
  - `test_summary_with_wrong_number_is_replaced`: an AI summary quoting `71%` when the evidence holds 62 gives a stored summary equal to `template_summary(evidence)`.
  - `test_approve_setting_rewrites_yaml`: the file has `caption_max_words: 5`, a new version exists with `source="proposal:<id>"`, and `load` returns 5.
  - `test_approve_rule_rewrites_body`.
  - `test_reject_sets_note_and_cooldown`: `engine.cooldown_left(item) == 20`.
  - `test_owner_edit_closes_only_changed_item_experiment`.
  - `test_attention_lists_pending_and_overdue`.
  - `test_cli_approve(svc)`, using `typer.testing.CliRunner`.
- [ ] **Step 2:** Run `pytest tests/test_guideline_proposals.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. `AnalystAgent.learn` calls `create_from_experiment` for each `"variant_won"` result. The AI summary comes from the strategist output (Task 6).
- [ ] **Step 4:** Run `pytest tests/test_guideline_proposals.py tests/test_autopublish.py -v`, plus a dashboard smoke run: `python -m aimz dashboard`, open `/guideline`, check for HTTP 200.
- [ ] **Step 5:** Commit `feat: guideline change proposals with owner approve/reject`.

---

### Task 6: Strategist under a guideline, and the switch-over

**Files:**
- Modify: `src/aimz/agents/analyst.py:249-412` (`learn` prompt and apply).
- Modify: `src/aimz/domain/models.py` (`StrategyUpdate`).
- Modify: `src/aimz/strategy/memory.py` (explore ratio display).
- Modify: `src/aimz/agents/editor.py` (allocation explore share).
- Test: `tests/test_learning_inputs.py`, `tests/test_strategy_memory.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `StrategyUpdate` gains `proposal_summaries: dict[str, str] = {}`, keyed by experiment ID: the AI's plain-English summary for each experiment the evaluation marked `variant_won`.
  - When a guideline is active, the strategist prompt:
    - **drops** the "You decide which settings to test" paragraph, the channel-identity paragraph and `starting_genres`;
    - **adds** the full guideline text with item IDs, the running experiment and its result, cooldowns, pending and rejected proposals with owner notes, and this instruction: "You may propose one experiment: name one guideline_item and a variant. Everything else follows the guideline."
  - `setting_changes` on covered settings are refused (Task 2). `channel_identity` is ignored, and state is set to `"identity is set by the guideline"`.
  - `migrate_to_guideline(db, settings_engine, experiment_engine, g: Guideline) -> bool`. Runs once; state key `guideline_migrated`. It does three things:
    - retires running experiments, including `exp_20261006T214417_0a30ab72`, with `"rescope 2026-10-07"`;
    - sets every `setting_space` row whose variable is in `g.locked` to `off`, keeping its evidence;
    - records the run.

    The orchestrator calls it at cycle start when a guideline loads.
  - With a guideline, the editor sets the explore share to 0: all slots are guideline slots, and experiment arms ride on them. The strategy markdown shows "Exploration ratio: replaced by the guideline (one experiment at a time)".

- [ ] **Step 1: Write the failing tests:**
  - `test_strategist_prompt_has_guideline_not_identity(svc)`: the captured prompt contains `"guideline_item"` and the G-hook text, and does not contain `"Channel identity:"` or `"open one with 2-4 values"`.
  - `test_identity_ignored_with_guideline`.
  - `test_migrate_once`: the experiment is retired, covered settings are `off`, and a second call returns False.
  - `test_variant_won_creates_proposal_with_ai_summary`.
  - `test_without_guideline_prompt_unchanged`.
- [ ] **Step 2:** Run `pytest tests/test_learning_inputs.py tests/test_strategy_memory.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full `pytest` suite. Expected: all PASS.
- [ ] **Step 5:** Commit `feat: strategist works within the guideline; one-time switch-over`.

---

### Task 7: New-channel template and upload gate

**Files:**
- Modify: `src/aimz/instances.py:110-158` (`create`), and the autopublish/consent check in `src/aimz/agents/publisher.py:160-164`.
- Create: `src/aimz/templates/guideline_template.md`. It holds the YAML block with an empty `locked:` and a first line `<!-- NOT APPROVED: the owner must remove this line to approve -->`.
- Test: `tests/test_instances.py`

**Interfaces:**
- Produces:
  - `guideline.is_approved(config_dir) -> bool`: the file exists and lacks the `NOT APPROVED` marker.
  - For a non-`main` instance, the publisher treats an unapproved guideline like missing consent. Uploads are blocked, and the reason `"channel guideline not approved"` shows in `attention`.

- [ ] **Step 1: Write the failing tests:** `test_create_writes_unapproved_template` and `test_unapproved_guideline_blocks_upload`.
- [ ] **Step 2:** Run `pytest tests/test_instances.py tests/test_autopublish.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. The marker check is ignored for `main`, so the main channel's existing consent path stays unchanged until its guideline is written in part 3.
- [ ] **Step 4:** Run the same command. Expected: PASS.
- [ ] **Step 5:** Commit `feat: new channels need an approved guideline before uploading`.

---

### Task 8: Constitution, plan docs, briefing and dry run

**Files:**
- Modify: `config/constitution.md` §1 and §8, but only after the owner approves the wording in chat.
- Create: `docs/PLAN_GUIDELINE.md`, with a status table: part 1 Done, part 2 Not started, part 3 Not started. Include the session log.
- Modify:
  - `docs/PLAN_FULL_CONTROL.md`: add a top banner `> Superseded on 2026-10-07 by docs/PLAN_GUIDELINE.md (owner rescope).`
  - `docs/HANDOFF.md`
  - `docs/CEO_BRIEF.md`: date on top. Part 1 gets the rescope and the guideline waiting to be written. Part 2 asks the vault session to log the decision.
  - `docs/OWNER_TODO.md`
  - `CLAUDE.md`: read-first item 6 points to `docs/PLAN_GUIDELINE.md`.
  - `config/config.yaml`: remove `channels.starting_genres`, with a comment noting it moved into the guideline.

**Constitution wording** to present to the owner for approval:
- **§1, replacement for the first sentence:** "Run an experiment: can an AI, starting from an owner-approved production guideline for each channel, improve on it by testing one change at a time and learning from performance data? The product is learning and quality, not upload volume."
- **§8, replacement:** "Each channel follows its owner-approved guideline (`config/guideline.md`), which sets topics, format, hooks, tone, length, look, sound, titles and covers. The AI may run one experiment per channel, changing one guideline item. A change that performs better becomes a proposal; it takes effect only when the owner approves it. Where the guideline is silent, the AI decides. The AI never edits the guideline."

- [ ] **Step 1:** Show the owner the constitution wording above, and wait for an explicit yes or edits.
- [ ] **Step 2:** Apply the approved wording to `config/constitution.md`. Then run `pytest` to confirm that tests which read the constitution still pass.
- [ ] **Step 3:** Write and update the docs listed above.
- [ ] **Step 4:** Dry run with `tests/fixtures/guideline_sample.md` copied to a temporary project's `config/guideline.md`.
  - **What to run:** add a test `test_dry_run_cycle_follows_guideline` in `tests/test_pipeline_offline.py` that runs `Orchestrator.cycle()` offline.
  - **First check:** every produced video's `video_settings` rows for locked keys have `source="guideline"`.
  - **Second check:** start a forced `G-caption_max_words` experiment (variant 5) and run two productions. That must give one `control` video with value 3 and one `treatment` video with value 5 and source `experiment`.
  - **Then:** run `pytest`. Expected: all PASS.
- [ ] **Step 5:** Commit `docs: guideline rescope plan, constitution and brief; dry-run test`.
