"""Critic / QA agent: adversarial review with deterministic overrides the model cannot talk its way past."""

from __future__ import annotations

import re
from typing import Any

from aimz.agents.base import Agent
from aimz.core.errors import TechnicalFailure
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


REASK = (
    "\n\nYour review scored this script {score}, below the pass mark of {threshold}, but listed no problems "
    "and no required revisions. List the concrete problems that cost it points, and any change that MUST be "
    "made (fixable using only the sources). If you find no concrete problem, return empty lists."
)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s$%.]", " ", s.lower()).split())


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
            "You are adversarial. Assume the script is mediocre until proven otherwise. The constitution requires you to check: factual support against the sources, "
            "originality (not generic mass-produced AI content, not a duplicate of common content), copyright risk, policy risk, deceptive-media risk, "
            "and whether the topic deserves a video at all. Judge craft (opening, structure, pacing, ending) by the channel's own evidence in the strategy memory below, "
            "not by a fixed formula; while that evidence is thin, do not fail a script for a creative choice. "
            "Required revisions must be fixable using ONLY the listed sources (never demand facts the sources do not contain; if a fact is missing, the fix is to cut or soften, not to add). "
            "'Realistic depictions of real people' means synthetic or altered imagery/voice of a real person, not merely naming a historical figure. Score 0-100; below "
            f"{threshold} fails. Every point below 80 must be explained by an entry in problems. List concrete required revisions (things that MUST change) separately from optional improvements.\n"
            f"Flag elevated_review_required if the script touches any of:\n{elevated}\n"
            "Only list a topic in elevated_review_reasons if the script actually touches it, and put the exact words from the script "
            "that touch it in elevated_review_evidence. If you cannot quote the script, do not list the topic.\n\n"
            f"Channel strategy memory:\n{self.strategy.prompt_summary(self.strategy.current(), 1200)}\n\n"
            f"Idea: {idea['title']} | family {idea['content_family']} | hook_type {idea.get('hook_type')}\n"
            f"Fact-check outcome: {factcheck.overall} ({factcheck.notes[:200]})\n\n"
            f"Script title: {draft.title}\nHook line: {draft.hook_line}\nBeats:\n"
            + "\n".join(
                f"{i}. [{b.visual_type}: {b.visual_query[:60]}] {b.narration} (caption: {b.caption})"
                for i, b in enumerate(draft.beats)
            )
            + f"\n\nSources:\n{self.sources_block(sources, 400, text_budget=3000)}\n"
        )
        with self.svc.tracker.agent(run, self.name, "review", {"script_id": script_id}) as span:

            def ask(prompt: str) -> CriticResult:
                try:
                    return self.ask(
                        self.pctx(run, script_id, span),
                        "Critic / QA agent",
                        prompt,
                        CriticResult,
                        "critic_review",
                        temperature=0.2,
                        max_tokens=2000,
                    )
                except Exception as exc:
                    # Still fails closed (nothing is approved), but as a technical failure: the idea is
                    # retried later instead of being rejected as if the script were bad.
                    raise TechnicalFailure(f"critic model call failed: {type(exc).__name__}: {exc}") from exc

            def gate(r: CriticResult, waive: bool = False) -> CriticResult:
                return self._overrides(
                    r.model_copy(deep=True),
                    draft,
                    narration,
                    factcheck,
                    threshold,
                    cfg.banned_patterns,
                    waive_unexplained=waive,
                )

            raw = ask(user)
            result = gate(raw)
            if not result.passed and self._unexplained(raw) and gate(raw, waive=True).passed:
                # The only thing failing the script is a score or verdict the critic gave no reason for,
                # and the rewrite loop cannot act on no feedback. Ask once for the reasons (owner decision
                # 2026-09-28); if it still names none, the unexplained score does not gate.
                span.output_refs["reasked"] = True
                second = ask(user + REASK.format(score=result.score, threshold=threshold))
                if self._unexplained(second):
                    result = gate(second, waive=True)
                    result.problems.append(
                        f"critic scored {result.score} (below {threshold}) twice without giving a reason; "
                        "not gated"
                    )
                else:
                    result = gate(second)
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
    def _unexplained(result: CriticResult) -> bool:
        """The model named no problem and no required change (its own output, before any override)."""
        return not [p for p in result.problems if p.strip()] and not [
            r for r in result.required_revisions if r.strip()
        ]

    @staticmethod
    def _overrides(
        result: CriticResult,
        draft: ScriptDraft,
        narration: str,
        factcheck: FactCheckResult,
        threshold: int,
        banned: list[str],
        *,
        waive_unexplained: bool = False,
    ) -> CriticResult:
        """Apply the deterministic gate. ``waive_unexplained`` drops the two gates that need no stated
        reason (the score threshold and the deserves-video verdict); every other gate still applies."""
        low = narration.lower()
        # The gate is computed here, not trusted from the model. Small models often emit a harsh headline
        # score with no listed problems, so the headline is blended with the model's own sub-scores. Only the
        # constitution-backed ones count toward the gate; hook/pacing/payoff/clarity are recorded, not gated.
        derived = (result.originality + result.factual_support) / 2 * 10
        result.score = int(round(0.5 * result.score + 0.5 * derived))
        result.passed = result.score >= threshold or waive_unexplained
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
        # Small models echo the whole topic list back (six of nine queued scripts, one about a moon of
        # Jupiter, were flagged for every topic). A model flag counts only when it quotes the script.
        text = _norm(" ".join([draft.title, draft.hook_line, narration]))
        quoted = any(len(_norm(q)) >= 4 and _norm(q) in text for q in result.elevated_review_evidence)
        reasons = (
            {r for r in result.elevated_review_reasons if r.strip().lower() not in impossible}
            if quoted
            else set()
        )
        if result.elevated_review_reasons and not quoted:
            result.problems.append("elevated-review flags dropped: no supporting quote from the script")
        # The keyword backstop needs no quote. Matching at word starts keeps "cure" out of "secure".
        for topic, kws in ELEVATED_KEYWORDS.items():
            if any(re.search(r"\b" + re.escape(k), low) for k in kws):
                reasons.add(topic)
        result.elevated_review_required = False
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
        if not result.deserves_video and not waive_unexplained:
            result.passed = False
        if result.score < threshold and not waive_unexplained:
            result.passed = False
        if result.required_revisions:
            result.passed = False
        return result
