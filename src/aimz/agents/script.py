"""Script agent: writes short-form scripts with explicit claims tied to sources."""

from __future__ import annotations

import re
from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import ScriptDraft, SourceItemView
from aimz.util import dumps, estimate_speech_seconds, new_id, now_iso, words

WORDS_PER_SECOND = 2.6

HOOK_GUIDE = {
    "numeric_hook": "open with a specific, sourced number or date in the first sentence",
    "question_hook": "open with a sharp, concrete question the video will answer",
    "contrarian_hook": "open by contradicting a common belief, then prove it",
    "narrative_hook": "open in the middle of a specific scene or moment",
    "stakes_hook": "open with what was at stake or what went wrong",
    "visual_hook": "open by describing something the viewer is looking at on screen",
}


class ScriptAgent(Agent):
    name = "script"

    def write(
        self,
        run: RunContext,
        idea: dict[str, Any],
        sources: list[SourceItemView],
        strategy_state: dict[str, Any],
        feedback: list[str] | None = None,
        previous: ScriptDraft | None = None,
        version: int = 1,
    ) -> tuple[str, ScriptDraft]:
        cfg = self.svc.config
        sf = cfg.short_form
        target_s = int(idea.get("suggested_runtime_s") or sf.get("target_seconds", 45))
        target_s = max(int(sf.get("min_seconds", 20)), min(int(sf.get("max_seconds", 90)), target_s))
        min_words = int(target_s * WORDS_PER_SECOND * 0.75)
        max_words = int(target_s * WORDS_PER_SECOND * 1.15)
        hook_type = (idea.get("hook_type") or "narrative_hook").lower()
        banned = ", ".join(f"'{b}'" for b in cfg.banned_patterns)
        user = (
            f"Write a short-form vertical video script.\n\n"
            f"Title: {idea['title']}\nPremise: {idea['premise']}\nHook: {idea['hook']}\n"
            f"Required hook type: {hook_type} ({HOOK_GUIDE.get(hook_type, 'strong, specific opening')}).\n"
            f"Content family: {idea['content_family']}\nTarget runtime: {target_s}s -> {min_words}-{max_words} spoken words total.\n\n"
            "Structure: immediate hook (beat 1, one sentence, no preamble), clear tension/mechanism, a payoff the viewer can repeat to a friend, "
            f"and a final beat that points to sources in the description. Minimal filler. 6-8 beats of 18-30 spoken words each (about {min_words}-{max_words} words in total; scripts under {min_words} words are rejected). "
            "Each beat needs a short caption (max 8 words) and a visual: 'image' with a visual_query (a concrete search phrase for a licensed archival photo, e.g. a place, object, or person), "
            "or 'text_card' / 'stat_card' / 'quote_card' with the card text in visual_query. At least two beats must be 'image' beats with a concrete, photographable visual_query (a named place, object, machine, document, or landscape).\n"
            f"Banned phrases: {banned}. Do not address the viewer with generic engagement bait.\n"
            "List every factual claim (dates, numbers, names, events) in `claims` with the beat index and the source ids that support it. "
            "Only use facts present in the sources below; if the sources do not say it, do not say it.\n\n"
            f"Sources:\n{self.sources_block(sources)}\n\n"
            f"Strategy context (for tone and format decisions only):\n{self.strategy.prompt_summary(strategy_state, 1500)}\n"
        )
        if feedback:
            user += "\n\nREVISION REQUIRED. Fix all of the following before returning:\n" + "\n".join(
                f"- {f}" for f in feedback
            )
        if previous is not None:
            user += "\n\nPrevious draft (revise it, keep what works):\n" + previous.model_dump_json()[:5000]

        with self.svc.tracker.agent(run, self.name, f"write:v{version}", {"idea_id": idea["id"]}) as span:
            draft = self.ask(
                self.pctx(run, idea["id"], span),
                "Script agent",
                user,
                ScriptDraft,
                "script_write",
                temperature=0.6,
                max_tokens=4000,
            )
            draft = self._normalize(draft, sources)
            script_id = self._store(run, idea, draft, version)
            span.output_refs["script_id"] = script_id
        return script_id, draft

    # -- helpers ----------------------------------------------------------------------
    @staticmethod
    def _normalize(draft: ScriptDraft, sources: list[SourceItemView]) -> ScriptDraft:
        known = {s.id for s in sources}
        for b in draft.beats:
            b.narration = re.sub(r"\s+", " ", b.narration).strip()
            b.caption = b.caption.strip()[:60]
            b.source_refs = [r for r in b.source_refs if r in known]
        for c in draft.claims:
            c.source_refs = [r for r in c.source_refs if r in known]
            c.beat_index = max(0, min(len(draft.beats) - 1, c.beat_index))
        if not draft.hook_line.strip():
            draft.hook_line = draft.beats[0].narration
        return draft

    def _store(self, run: RunContext, idea: dict[str, Any], draft: ScriptDraft, version: int) -> str:
        narration = " ".join(b.narration for b in draft.beats)
        script_id = new_id("scr")
        self.svc.db.insert(
            "scripts",
            {
                "id": script_id,
                "idea_id": idea["id"],
                "run_id": run.id,
                "version": version,
                "title": draft.title[:100],
                "hook_line": draft.hook_line,
                "beats_json": dumps([b.model_dump() for b in draft.beats]),
                "narration_text": narration,
                "estimated_runtime_s": round(estimate_speech_seconds(narration), 1),
                "word_count": words(narration),
                "description": draft.description,
                "tags_json": dumps(draft.tags),
                "thumbnail_concept": draft.thumbnail_concept,
                "status": "draft",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
        src_by_id = {s.id: s for s in self.load_sources(self.idea_source_ids(idea))}
        for c in draft.claims:
            self.svc.db.insert(
                "claims",
                {
                    "id": new_id("clm"),
                    "script_id": script_id,
                    "text": c.text[:500],
                    "claim_type": c.claim_type,
                    "source_urls_json": dumps([src_by_id[r].url for r in c.source_refs if r in src_by_id]),
                    "verification_status": "unverified",
                    "created_at": now_iso(),
                },
            )
        self.svc.db.update("ideas", idea["id"], {"status": "scripted", "updated_at": now_iso()})
        return script_id

    @staticmethod
    def word_budget_ok(
        draft: ScriptDraft, target_s: int, min_s: int = 20, max_s: int = 90
    ) -> tuple[bool, str]:
        """Hard bounds come from the platform (20-90s); the suggested runtime is only guidance."""
        n = sum(words(b.narration) for b in draft.beats)
        lo = int(max(min_s, target_s * 0.6) * WORDS_PER_SECOND)
        hi = int(max_s * WORDS_PER_SECOND)
        if n < lo:
            return (
                False,
                f"Script is too short: {n} spoken words. Add at least {lo - n} more words of sourced detail (target about {int(target_s * WORDS_PER_SECOND)} words for ~{target_s}s).",
            )
        if n > hi:
            return (
                False,
                f"Script is too long: {n} spoken words. Cut at least {n - hi} words (target about {int(target_s * WORDS_PER_SECOND)} words).",
            )
        return True, ""
