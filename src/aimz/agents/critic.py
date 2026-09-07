"""Critic / QA agent: adversarial review with deterministic overrides the model cannot talk its way past."""

from __future__ import annotations

import re
from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import CriticResult, FactCheckResult, ScriptDraft, SourceItemView
from aimz.util import dumps, now_iso

ELEVATED_KEYWORDS = {
    "medical claims": ["cure", "treatment", "diagnos", "vaccine", "dosage", "symptom", "disease", "cancer"],
    "legal claims": ["lawsuit", "illegal", "convicted", "guilty", "indicted", "fraud", "crime"],
    "financial advice": [
        "invest",
        "buy the stock",
        "should buy",
        "guaranteed return",
        "crypto",
        "trading strategy",
    ],
    "politics or persuasion": [
        "election",
        "vote for",
        "senator",
        "president",
        "party",
        "left-wing",
        "right-wing",
    ],
    "emergencies or disasters in progress": [
        "breaking",
        "ongoing",
        "right now",
        "evacuat",
        "hurricane",
        "earthquake",
    ],
    "allegations involving real, living people": ["alleged", "accused", "allegedly", "scandal"],
}

AI_SLOP_PATTERNS = [
    r"\bdelve\b",
    r"in the realm of",
    r"it'?s important to note",
    r"\bgame[- ]changer\b",
    r"\bunlock\b",
    r"\bjourney\b",
    r"tapestry",
    r"in conclusion",
]


class CriticAgent(Agent):
    name = "critic"

    def review(
        self,
        run: RunContext,
        script_id: str,
        draft: ScriptDraft,
        idea: dict[str, Any],
        factcheck: FactCheckResult,
        sources: list[SourceItemView],
    ) -> CriticResult:
        cfg = self.svc.config
        threshold = int(cfg.get("pipeline.qa_pass_threshold", 70))
        elevated = "\n".join(f"- {t}" for t in cfg.elevated_review_topics)
        narration = " ".join(b.narration for b in draft.beats)
        user = (
            "You are adversarial. Assume the script is mediocre until proven otherwise. Evaluate: hook strength, originality, factual support, source quality, "
            "pacing, payoff, clarity, duplication of common content, copyright risk, policy risk, deceptive-media risk, whether it feels like generic mass-produced AI content, "
            "and whether the topic actually deserves a video. Required revisions must be fixable using ONLY the listed sources (never demand facts the sources do not contain; if a fact is missing, the fix is to cut or soften, not to add). "
            "'Realistic depictions of real people' means synthetic or altered imagery/voice of a real person, not merely naming a historical figure. Score 0-100; below "
            f"{threshold} fails. Every point below 80 must be explained by an entry in problems. List concrete required revisions (things that MUST change) separately from optional improvements.\n"
            f"Flag elevated_review_required if the script touches any of:\n{elevated}\n\n"
            f"Idea: {idea['title']} | family {idea['content_family']} | hook_type {idea.get('hook_type')}\n"
            f"Fact-check outcome: {factcheck.overall} ({factcheck.notes[:200]})\n\n"
            f"Script title: {draft.title}\nHook line: {draft.hook_line}\nBeats:\n"
            + "\n".join(
                f"{i}. [{b.visual_type}: {b.visual_query[:60]}] {b.narration} (caption: {b.caption})"
                for i, b in enumerate(draft.beats)
            )
            + f"\n\nSources:\n{self.sources_block(sources, 400)}\n"
        )
        with self.svc.tracker.agent(run, self.name, "review", {"script_id": script_id}) as span:
            try:
                result = self.ask(
                    self.pctx(run, script_id, span),
                    "Critic / QA agent",
                    user,
                    CriticResult,
                    "critic_review",
                    temperature=0.2,
                    max_tokens=2000,
                )
            except Exception as exc:
                self.log.warning("critic model call failed: %s; failing closed", exc)
                result = CriticResult.model_validate(
                    {
                        "pass": False,
                        "score": 0,
                        "problems": [f"critic unavailable: {exc}"],
                        "required_revisions": ["Critic unavailable; retry"],
                        "hook_strength": 0,
                        "originality": 0,
                        "factual_support": 0,
                        "pacing": 0,
                        "payoff": 0,
                        "clarity": 0,
                    }
                )
            result = self._overrides(result, draft, narration, factcheck, threshold, cfg.banned_patterns)
            self.svc.db.update(
                "scripts",
                script_id,
                {
                    "qa_json": dumps(result.model_dump(by_alias=True)),
                    "status": "qa_passed" if result.passed else "qa_failed",
                    "updated_at": now_iso(),
                },
            )
            span.output_refs["pass"] = result.passed
            span.output_refs["score"] = result.score
        return result

    @staticmethod
    def _overrides(
        result: CriticResult,
        draft: ScriptDraft,
        narration: str,
        factcheck: FactCheckResult,
        threshold: int,
        banned: list[str],
    ) -> CriticResult:
        low = narration.lower()
        # The gate is computed here, not trusted from the model. Small models often emit a harsh headline
        # score with no listed problems, so the headline is blended with the model's own sub-scores.
        derived = (
            (
                result.hook_strength
                + result.originality
                + result.factual_support
                + result.pacing
                + result.payoff
                + result.clarity
            )
            / 6
            * 10
        )
        result.score = int(round(0.5 * result.score + 0.5 * derived))
        result.passed = result.score >= threshold
        for pat in banned:
            if pat in low or pat in draft.title.lower():
                result.required_revisions.append(f"Remove banned phrase: '{pat}'")
                result.passed = False
        for pat in AI_SLOP_PATTERNS:
            if re.search(pat, low):
                result.problems.append(f"generic AI phrasing: /{pat}/")
                result.feels_generic_ai = True
        if factcheck.overall == "reject":
            result.required_revisions.insert(0, "Fact-check rejected the premise.")
            result.passed = False
        # The V0 pipeline renders archival photos and generated cards only; it cannot produce synthetic
        # depictions of real people or altered footage, so those model flags are structural false positives.
        impossible = {"realistic depictions of real people", "altered real-world events"}
        reasons = {r for r in result.elevated_review_reasons if r.strip().lower() not in impossible}
        for topic, kws in ELEVATED_KEYWORDS.items():
            if any(k in low for k in kws):
                reasons.add(topic)
        if reasons:
            result.elevated_review_required = True
            result.elevated_review_reasons = sorted(reasons)
        if (
            result.policy_risk == "high"
            or result.deceptive_media_risk == "high"
            or result.copyright_risk == "high"
        ):
            result.passed = False
            result.required_revisions.append("High policy/deception/copyright risk flagged.")
        if not result.deserves_video:
            result.passed = False
        if result.score < threshold:
            result.passed = False
        if result.required_revisions:
            result.passed = False
        return result
