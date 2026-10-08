# Spec: guideline hook checks and genre gate (plan part 2, piece A)

_Owner-approved design, 2026-10-08. Part 2 of `docs/PLAN_GUIDELINE.md` is split into four pieces, built in this
order (owner, 2026-10-08): **A. script checks (this spec)**, B. picture and layout (SigLIP relevance gate,
full-bleed default, credits in the description), C. sound (Kokoro voice, -14 LUFS), D. stock-video providers._

## 1. Why

The 2026-10-07 test video (`data/tests/2026-10-07-guideline-test/README.md`) followed every locked setting but:

- opened with "The zodiacal light is a faint glow...", which breaks G-hook ("under 15 words ... no definition
  opening"). The local critic model (qwen3:8b) did not name the broken rule; nothing else checks it;
- ignored the guideline genre ("maps and geography oddities"). `Guideline.genre` is parsed but only the
  strategist prompt reads it; ideation still reads the older identity genre (`identity.genre_line`), and the
  editor sees no genre at all.

Success: a script whose hook breaks the guideline's hook settings never passes the critic, and an idea outside
the guideline genre is never selected while a guideline is active.

## 2. Owner decisions (2026-10-08)

1. Genre: a strict AI judge (option 1 of 3). Off-genre ideas are rejected and logged.
2. Hook limits live in the guideline's `checks:` block (owner-approved per channel), not fixed in code.

## 3. Hook checks (deterministic)

### 3.1 New `checks:` keys

| Key | Type | Meaning |
|---|---|---|
| `hook_max_words` | positive int | Most words allowed in the hook |
| `hook_no_question` | bool | The hook may not contain `?` |
| `hook_no_definition` | bool | The hook may not be a definition opening (3.3) |

All optional; absent means not checked. Existing keys stay: `banned_openings` (list of strings),
`title_max_chars` (positive int).

### 3.2 Validation

`guideline.py` validates the five known keys. A wrong type (e.g. `hook_max_words: "fifteen"`, a non-list
`banned_openings`, a non-bool flag) is a `GuidelineError` naming the key, so a bad guideline fails to load the
same way other invalid fields do. Unknown keys stay allowed (forward compatibility), unchanged from today.

### 3.3 What is checked

"The hook" means two texts, each checked: `draft.hook_line` and the first sentence of the narration (split with
the critic's existing `_SENTENCES`). They are normally the same; a duplicate text is checked once and reported once.

- **Word limit:** words = whitespace-separated tokens. Over the limit gives
  `Hook is N words; the guideline allows M`.
- **No question:** any `?` gives `Hook asks a question; the guideline forbids it`.
- **No definition:** case-insensitive match of
  `^\s*(?:\S+\s+){0,6}?(?:is|are|was|were)\s+(?:a|an)\b` — up to six words, a form of "to be", then "a"/"an".
  Matches "The zodiacal light is a faint glow". Does **not** match "This town is the only place where...":
  "the only/first/last..." openings are usually the surprise itself. Also not a definition: hooks whose first
  word is "there" or "here" ("There is a village that sits inside another country"). Message:
  `Hook opens with a definition ("<first 8 words>..."); state the surprise instead`.
- **Banned openings** now also apply to `hook_line`, not only the narration's first sentence.

### 3.4 Where

`CriticAgent._guideline_revisions` (`src/aimz/agents/critic.py`). Messages join `required_revisions`, so the
existing rewrite loop (`pipeline/orchestrator.py`, rewrite rounds) feeds them back to the script agent and the
script fails if a later round still breaks them. No change to the loop.

Experiments: the hook checks read `g.checks`, which proposals can change once the owner approves. They are
not themselves experiment variables in this piece.

## 4. Genre gate (AI judge)

### 4.1 One genre

When a guideline is active, `identity.genre_line` returns the guideline genre
(`This channel's genre is '<g.genre>' (owner guideline vN). Every idea must fit it.`) instead of the identity
genre. Without a guideline, behaviour is unchanged.

### 4.2 The judge

A new method in `EditorAgent` (`src/aimz/agents/editor.py`), run inside `select()` only when a guideline is
active, before each `_choose` call:

- Walk the slot's pool in order. For each idea **not yet judged under the current guideline version**, ask the
  LLM one structured question: does this idea (title, premise, hook) fit the genre `<g.genre>`? Schema
  `GenreFit {fits: bool, reason: str}` in `domain/models.py`.
- `fits = false`: the idea gets `status='rejected'`, `guideline_breaks=["genre"]` (merged with any existing),
  `eic_notes="off genre: <reason>"`. It is never offered again.
- `fits = true`: the idea's new column `genre_checked_version` is set to `g.version`. It is not asked again
  until the guideline version changes (an approved proposal or a new owner edit).
- Unreadable output after one retry: the idea is skipped this cycle, neither rejected nor marked.
- Stop once 4 passing ideas are collected (the `_choose` shortlist size) or after judging 12 ideas in one
  `select()` call, to bound local-model time. The shortlist given to `_choose` holds only passing ideas.
- The judge call goes through the usual tracker span (`self.svc.tracker.agent(...)`) so it appears in the
  run's agent log.

Topic-rule experiments: genre is not a rule, so treatment ideas are judged on genre like any other.

### 4.3 Storage

Migration `0008_genre_check.sql`: `ALTER TABLE ideas ADD COLUMN genre_checked_version INTEGER;`

## 5. Tests

Fixture LLM (`tests/conftest.py`), extending `tests/test_guideline.py` and `tests/test_guideline_prompts.py`;
`tests/fixtures/guideline_sample.md` gains the three hook keys.

- Parser: each new key accepted; each wrong type rejected with the key named.
- Hook: over-length, question, definition opening each fail with their message; "X is the only..." passes; a
  clean hook passes; banned opening caught in `hook_line`; identical hook_line and first sentence reported once.
- Genre: off-genre idea rejected with `guideline_breaks` and reason; on-genre idea marked with the version and
  not asked again; a version bump re-asks; unreadable output skips without rejecting; the 12-idea cap holds;
  no guideline means no judge call.
- `genre_line` returns the guideline genre when a guideline is active, the identity genre otherwise.

## 6. Out of scope

Pieces B-D; LLM-based hook quality scoring; making the hook keys experiment variables; changing the critic
model.
