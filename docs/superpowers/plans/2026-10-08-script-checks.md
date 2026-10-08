# Guideline Hook Checks and Genre Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scripts whose hook breaks the guideline's hook settings never pass the critic, and ideas outside the
guideline genre are never selected while a guideline is active.

**Architecture:** Three new optional `checks:` keys are validated in `guideline.py` and enforced as code checks
in `CriticAgent._guideline_revisions`, so the existing rewrite loop handles failures. A per-idea LLM judge in
`EditorAgent` (with a `GenreFit` schema) filters each slot's pool before `_choose`; verdicts are stored on the
idea (`guideline_breaks`, new `genre_checked_version` column). `identity.genre_line` prefers the guideline genre.

**Tech Stack:** Python 3.14, pydantic, SQLite migrations, pytest with the fixture LLM.

**Spec:** `docs/superpowers/specs/2026-10-08-script-checks-design.md`

## Global Constraints

- Run Python through `.venv/Scripts/python.exe` (`python -m pytest`, `python -m aimz`); never the `aimz.exe` shim.
- $0 runtime: no new paid services or dependencies.
- Without `config/guideline.md`, behaviour must be byte-for-byte unchanged (no judge calls, same prompts).
- Edit Python with the Edit tool, not shell heredocs (they mangle backslashes in regexes).
- `ruff check`, `ruff format --check` and `mypy src` stay clean; full `pytest` stays green.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A hook with leading spaces or curly quotes (`“The light is a glow”`) should still be judged on its words → test in Task 2.
- `hook_max_words: 0` or negative or `true` (YAML bool is an int subclass) must be rejected → test in Task 1.
- An empty `hook_line` and an empty narration must not crash the checks → test in Task 2.
- A pool where every idea is off-genre must leave `_choose` with nothing and the slot unfilled, not crash → test in Task 4.
- An idea rejected earlier for a topic rule keeps that break when the genre judge also rejects it (merge, no duplicate) → test in Task 4.

---

### Task 1: Validate the guideline's `checks:` keys

**Files:**
- Modify: `src/aimz/guideline.py` (after `if not isinstance(checks, dict)`, ~line 147)
- Modify: `tests/fixtures/guideline_sample.md` (add `hook_max_words: 15`, `hook_no_question: true`, `hook_no_definition: true` under `checks:`)
- Test: `tests/test_guideline.py`

**Interfaces:**
- Produces: `Guideline.checks` guaranteed typed for known keys: `banned_openings: list[str]`, `title_max_chars: int > 0`, `hook_max_words: int > 0`, `hook_no_question: bool`, `hook_no_definition: bool`.

- [ ] **Step 1: Write failing tests** `test_checks_hook_keys_accepted` (sample parses; `g.checks["hook_max_words"] == 15` and both flags `is True`) and a parametrized `test_checks_wrong_type_rejected` over `("hook_max_words", '"fifteen"')`, `("hook_max_words", "0")`, `("hook_max_words", "true")`, `("hook_no_question", '"yes"')`, `("hook_no_definition", "1")`, `("banned_openings", '"Did you know"')`, `("title_max_chars", "-5")`: each raises `GuidelineError` whose message contains the key name. Build the text by substituting into the sample's YAML block.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/test_guideline.py -k checks -v` → the wrong-type cases FAIL (no error raised).
- [ ] **Step 3: Implement** `_validate_checks(checks: dict[str, Any]) -> None` in `guideline.py`, called right after the mapping check. Positive-int keys reject `bool` explicitly; bool keys require `isinstance(v, bool)`; `banned_openings` must be a list of strings. Error text: `f"checks: {key} must be <a positive whole number|true or false|a list of strings>"`. Unknown keys pass.
- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/test_guideline.py tests/test_guideline_prompts.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: validate guideline checks keys, add hook settings`.

### Task 2: Hook checks in the critic

**Files:**
- Modify: `src/aimz/agents/critic.py` (`_guideline_revisions`, ~lines 234-264; new module-level helpers)
- Test: `tests/test_guideline_prompts.py`

**Interfaces:**
- Consumes: Task 1's typed `g.checks`.
- Produces: `hook_revisions(texts: list[str], checks: dict[str, Any]) -> list[str]` (module-level in `critic.py`), and `_DEFINITION_RE`.

- [ ] **Step 1: Write failing tests** using the existing `_gate`/`_draft` helpers and the `g` fixture (sample now has limit 15 and both flags):
  - `test_hook_too_long`: 20-word opening → `"Hook is 20 words; the guideline allows 15" in out.required_revisions`, `passed is False`.
  - `test_hook_question`: `"Why did Amalthea hide for centuries?"` → `"Hook asks a question; the guideline forbids it"` present.
  - `test_hook_definition`: `"The zodiacal light is a faint glow in the sky."` → a revision starting `'Hook opens with a definition ("The zodiacal light is a faint glow in the'`.
  - `test_hook_definition_curly_quote`: `"“The zodiacal light is a faint glow.”"` → definition revision present.
  - parametrized `test_hook_not_definition` over `"This town is the only place where clocks stop."`, `"There is a village that sits inside another country."`, `"Here is a border nobody can cross."` → no revision starting `"Hook opens with a definition"`.
  - `test_banned_opening_in_hook_line`: draft whose `hook_line="Did you know this?"` but first beat narration is clean → `"Opening 'Did you know' is banned by the guideline"` present exactly once.
  - `test_hook_same_text_reported_once`: 20-word opening (hook_line == first sentence) → the "Hook is 20 words" message appears once.
  - `test_hook_empty_texts`: `hook_revisions(["", "   "], g.checks) == []`.
  - Update `test_banned_opening` only if it now breaks (it uses `in`, so it should not).
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/test_guideline_prompts.py -k hook -v` → FAIL.
- [ ] **Step 3: Implement.** `_DEFINITION_RE = re.compile(r"^(?:\S+\s+){0,6}?(?:is|are|was|were)\s+(?:a|an)\b", re.I)`. `hook_revisions` strips each text plus leading quote characters (`"'“”‘’`), drops empty and duplicate (case-insensitive) texts, and for each applies, in order: banned openings (existing message), word limit, question, definition (skipped when the first word lowercased is `there` or `here`; message quotes the first 8 words joined, then `...`). De-duplicate the combined messages preserving order. In `_guideline_revisions`, replace the inline banned-openings loop with `out += hook_revisions([draft.hook_line, first_sentence], g.checks)` where `first_sentence` keeps its original case.
- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/test_guideline_prompts.py tests/test_guideline.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: code checks for the guideline hook settings`.

### Task 3: One genre — the guideline's

**Files:**
- Modify: `src/aimz/identity.py:157-162` (`genre_line`)
- Modify: `src/aimz/agents/ideation.py:141` (pass the guideline)
- Test: `tests/test_guideline_prompts.py`

**Interfaces:**
- Produces: `genre_line(db: Database, g: Guideline | None = None) -> str`. With `g` and a non-empty `g.genre`: `f"This channel's genre is '{g.genre}' (owner guideline v{g.version}). Every idea must fit it.\n"`; otherwise unchanged.

- [ ] **Step 1: Write failing tests** `test_genre_line_prefers_guideline` (with `_activate(svc)`, `genre_line(svc.db, g)` contains `"'explainer' (owner guideline v"`) and `test_genre_line_without_guideline_unchanged` (identity genre set via the identity module's existing setter/state; `genre_line(svc.db)` equals the old text). Plus `test_ideation_prompt_uses_guideline_genre` capturing the ideation prompt with a monkeypatched `ask` (same pattern as `_script_prompt`) → contains `"owner guideline v"`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the signature above; in ideation pass `guideline.for_services(self.svc)`.
- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest tests/test_guideline_prompts.py tests/test_identity*.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: the guideline genre replaces the identity genre in ideation`.

### Task 4: Genre judge in the editor

**Files:**
- Create: `src/aimz/db/migrations/0008_genre_check.sql` (`ALTER TABLE ideas ADD COLUMN genre_checked_version INTEGER;`)
- Modify: `src/aimz/domain/models.py` (after `SelectionDecisionWithBreaks`): `class GenreFit(BaseModel): fits: bool; reason: str = ""`
- Modify: `src/aimz/providers/llm/fixture.py`: `_gen_GenreFit(self, user) -> {"fits": True, "reason": "fixture"}`
- Modify: `src/aimz/agents/editor.py` (`select()` slot loop, new method)
- Test: `tests/test_guideline_prompts.py`

**Interfaces:**
- Consumes: `Guideline.version`, `Guideline.genre`; `self.ask(...)` (raises on unreadable output after the provider's retries).
- Produces: `EditorAgent._genre_gate(self, run: RunContext, g: Guideline, pool: list[dict[str, Any]], budget: list[int]) -> list[dict[str, Any]]` — returns up to 4 passing ideas in pool order; `budget` is a one-element counter of judge calls left for this `select()` call (starts at 12), decremented per call.

- [ ] **Step 1: Write failing tests** (monkeypatch `orch.editor.ask`; dispatch on `schema.__name__ == "GenreFit"`):
  - `test_genre_gate_rejects_off_genre`: judge returns `fits=False, reason="about cooking"` → idea `status == "rejected"`, `json.loads(guideline_breaks) == ["genre"]`, `eic_notes == "off genre: about cooking"`, not in result.
  - `test_genre_gate_merges_breaks`: idea already has `guideline_breaks='["G-topic"]'` → `["G-topic", "genre"]`.
  - `test_genre_gate_marks_and_skips_rechecks`: `fits=True` → `genre_checked_version == g.version`; a second call makes no judge call for it.
  - `test_genre_gate_rechecks_after_version_change`: set the idea's `genre_checked_version` to `g.version - 1` → judged again.
  - `test_genre_gate_unreadable_skips`: `ask` raises `ValidationError` → idea still `candidate`, `genre_checked_version` NULL, not in result.
  - `test_genre_gate_cap`: 15 unjudged ideas, all `fits=False` → exactly 12 judge calls; 3 ideas remain `candidate`.
  - `test_genre_gate_stops_at_four`: 6 unjudged ideas all fit → 4 judge calls, 4 returned.
  - `test_select_all_off_genre_selects_nothing`: `_activate`, two ideas, judge rejects both → `select(...) == []`, no exception, `_choose` never called with an empty shortlist.
  - `test_select_without_guideline_never_judges`: no guideline → no `GenreFit` call.
  - `test_fixture_llm_answers_genre_fit`: the fixture provider validates into `GenreFit(fits=True)`.
- [ ] **Step 2: Run** `.venv/Scripts/python.exe -m pytest tests/test_guideline_prompts.py -k genre -v` → FAIL.
- [ ] **Step 3: Implement** the migration, model, fixture handler and `_genre_gate`. Prompt (user text): genre, then the idea's title, premise (first 300 chars) and hook (first 200), then `"Does this idea fit the channel genre? Answer fits true or false and a one-line reason."`; role `"Editor-in-Chief"`, purpose `"eic_genre_fit"`, `temperature=0.0`, `max_tokens=200`, inside `self.svc.tracker.agent(run, self.name, "genre_fit", {"idea": id})`. Ideas whose `genre_checked_version == g.version` pass without a call. In `select()`, when `g is not None`, create `budget = [12]` before the slot loop and replace `pool[:4]` with `self._genre_gate(run, g, pool, budget)` for both `_choose` calls (the fallback uses `rest`); add rejected ids to `used`; if the gated list is empty, skip `_choose` and treat it as no choice.
- [ ] **Step 4: Run** `.venv/Scripts/python.exe -m pytest -q` (full suite, migrations included) → PASS.
- [ ] **Step 5: Commit** `feat: strict genre judge before the editor chooses`.

### Task 5: Docs and live check

**Files:**
- Modify: `docs/PLAN_GUIDELINE.md` (status table: part 2 piece A done; section 6; session log), `docs/CEO_BRIEF.md` (status line), `data/tests/2026-10-07-guideline-test/guideline-TEST-DRAFT.md` (add the three hook keys so part 3 starts from them)

- [ ] **Step 1:** `.venv/Scripts/python.exe -m ruff check src tests`, `ruff format --check src tests`, `.venv/Scripts/python.exe -m mypy src` → clean.
- [ ] **Step 2:** Apply the migration to the live DB through the normal path (`python -m aimz status` runs migrations) after backing up `data/aimz.sqlite3` to `data/backups/`; confirm `PRAGMA table_info(ideas)` lists `genre_checked_version`.
- [ ] **Step 3:** Update the docs above; commit `docs: part 2 piece A done`.
