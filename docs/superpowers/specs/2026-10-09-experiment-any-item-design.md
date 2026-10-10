# Spec: the AI may experiment on any guideline item, including genre and the code checks

_Owner decision 2026-10-09, when approving guideline v1: "the agent should be able to experiment with any facet of the
guideline, not just text, font etc. If it wants to experiment with genre it may, or it may experiment with voice."_

## What already worked
One running experiment per channel could test: any rule text (`G-topic`, `G-hook`, ...), any locked setting
(`G-voice`, `G-template`, ...) and the runtime (`G-runtime`). A win becomes a proposal; nothing changes until the owner
approves it.

## What this adds
1. **`G-genre`**: the variant is a different genre line. It runs like a topic-rule experiment: ideation writes half of
   each batch under each arm's genre and labels the ideas; the editor's genre gate judges a treatment idea against the
   variant genre and its selection prompt says the idea was written under the variant.
2. **One item per code check** present in the guideline: `G-hook_max_words`, `G-title_max_chars` (whole numbers,
   5-40 and 30-100), `G-hook_no_question`, `G-hook_no_definition` (true/false), `G-banned_openings` (a JSON list or a
   `;`-separated list of phrases). Treatment-arm ideas are written and checked under the variant value (script prompt
   hook limits, critic hook and title checks); control ideas under the guideline's.
3. `guideline.for_idea(g, idea, db)` returns the guideline as one idea sees it (genre or check swapped on the treatment
   arm of a running experiment); the critic, script writer and editor use it.
4. Proposals: `current_value`, `rewrite_item` and `changed_items` handle `G-genre` and check items, so an approved win
   is written into `config/guideline.md` like any other item.
5. The strategist's guideline view lists `G-genre` and each check as testable items.

Unchanged: one experiment at a time, cooldowns, the constitution check on variants, owner approval for every change.
Cover rules stay untestable (the pipeline does not apply them yet).
