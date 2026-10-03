"""Script agent: writes short-form scripts with explicit claims tied to sources."""

from __future__ import annotations

import re
from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import ScriptDraft, SourceItemView
from aimz.util import dumps, estimate_speech_seconds, new_id, now_iso, words

WORDS_PER_SECOND = 2.6


def _clip_words(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut at a word boundary. (A cut at exactly 60 characters used to end
    on-screen captions mid-word, e.g. "calibration and valida".) The card shrinks its font to fit."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")


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
        min_s, max_s = int(sf.get("min_seconds", 10)), int(sf.get("max_seconds", 180))
        target_s = int(idea.get("suggested_runtime_s") or sf.get("target_seconds", 45))
        target_s = max(min_s, min(max_s, target_s))
        if self.runtime_under_test(idea):
            runtime_line = (
                f"Runtime: {target_s}s (this idea is in a runtime experiment) -> "
                f"{int(target_s * WORDS_PER_SECOND * 0.6)}-{int(target_s * WORDS_PER_SECOND * 1.4)} spoken words; outside this range is rejected."
            )
        else:
            runtime_line = (
                f"Runtime: your ideation estimate was {target_s}s. The script may be any length from {min_s}s to {max_s}s "
                f"({int(min_s * WORDS_PER_SECOND)}-{int(max_s * WORDS_PER_SECOND)} spoken words); let the facts in the sources decide."
            )
        hook_type = (idea.get("hook_type") or "").lower()
        if hook_type and self.hook_type_under_test(idea):
            # The ideation hook line may be of the other arm's type; both arms write the opening from the
            # assigned type alone, so the prompt never contradicts itself and the arms are treated alike.
            hook_lines = (
                f"Hook type: {hook_type} (this idea is in a hook-type experiment). Write the opening line "
                f"yourself as a {hook_type} hook, from the premise and the sources.\n"
            )
        else:
            hook_lines = f"Hook: {idea['hook']}\n" + (
                f"Hook type chosen for this idea: {hook_type}.\n" if hook_type else ""
            )
        banned = ", ".join(f"'{b}'" for b in cfg.banned_patterns)
        # Only the channel's own choices (from ideation, the editor and its experiments), the constitution,
        # and what the renderer can physically do go in here. Structure, pacing and style are the AI's call.
        user = (
            f"Write a vertical short-form video script.\n\n"
            f"Title: {idea['title']}\nPremise: {idea['premise']}\n"
            + hook_lines
            + f"Content family: {idea['content_family']}\n{runtime_line} Narration is read at about {WORDS_PER_SECOND} words per second.\n\n"
            f"How the video is built: the script is a sequence of {ScriptDraft.MIN_BEATS}-{ScriptDraft.MAX_BEATS} beats; the renderer shows one visual per beat while its narration plays. "
            "Each beat has a caption (on-screen text; more than about 8 words will not fit) and a visual: 'image' with a visual_query "
            "(a search phrase for a licensed archival photo, so it must name something photographable), or 'text_card' / 'stat_card' / 'quote_card' "
            "with the card text in visual_query. The narration is also shown as subtitles below the card, so a caption "
            "that repeats the narration would appear twice and is left off the card. A beat may set zoom (in, out or none) for the camera move on its visual; "
            "leave it empty for the channel's default.\n"
            f"Banned phrases: {banned}. Do not address the viewer with engagement bait.\n"
            "List every factual claim (dates, numbers, names, events) in `claims` with the beat index and the source ids that support it. "
            "Only use facts present in the sources below; if the sources do not say it, do not say it.\n\n"
            f"Sources:\n{self.sources_block(sources, text_budget=5000)}\n\n"
            f"Channel strategy memory (your own notes on what has worked):\n{self.strategy.prompt_summary(strategy_state, 1500)}\n"
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
            b.caption = _clip_words(b.caption.strip(), 120)
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

    def runtime_under_test(self, idea: dict[str, Any]) -> bool:
        """True when the idea is an arm of a running experiment on runtime, so its runtime is binding."""
        if not idea.get("experiment_id"):
            return False
        exp = self.svc.db.get("experiments", idea["experiment_id"])
        return bool(exp and exp["variable"] == "runtime")

    def hook_type_under_test(self, idea: dict[str, Any]) -> bool:
        """True when the idea is an arm of a running experiment on hook type."""
        if not idea.get("experiment_id"):
            return False
        exp = self.svc.db.get("experiments", idea["experiment_id"])
        return bool(exp and exp["variable"] == "hook_type" and exp["status"] == "running")

    @staticmethod
    def word_budget_ok(
        draft: ScriptDraft, target_s: int, min_s: int = 10, max_s: int = 180, enforce_target: bool = False
    ) -> tuple[bool, str]:
        """Hard bounds come from the platform. The idea's runtime is the AI's own estimate and binds only when
        runtime is the variable under test: forcing a script up to an estimate the sources cannot fill is
        what pushed the writer to pad with invented detail."""
        n = sum(words(b.narration) for b in draft.beats)
        lo = int((max(min_s, target_s * 0.6) if enforce_target else min_s) * WORDS_PER_SECOND)
        hi = int((min(max_s, target_s * 1.4) if enforce_target else max_s) * WORDS_PER_SECOND)
        if n < lo:
            return (
                False,
                f"Script is too short: {n} spoken words; the minimum is {lo}. Add {lo - n} or more words using only facts in the sources.",
            )
        if n > hi:
            return (
                False,
                f"Script is too long: {n} spoken words; the maximum is {hi}. Cut at least {n - hi} words.",
            )
        return True, ""
