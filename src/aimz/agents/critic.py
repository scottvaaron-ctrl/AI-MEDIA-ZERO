"""Critic / QA agent: adversarial review with deterministic overrides the model cannot talk its way past."""

from __future__ import annotations

import re
from typing import Any

from aimz import guideline
from aimz.agents.base import Agent
from aimz.agents.script import WORDS_PER_SECOND
from aimz.core.errors import TechnicalFailure
from aimz.core.runs import RunContext
from aimz.domain.models import CriticResult, FactCheckResult, ScriptDraft, SourceItemView
from aimz.guideline import Guideline
from aimz.util import dumps, now_iso

ELEVATED_KEYWORDS = {
    "medical claims": ["cure", "treatment", "diagnos", "vaccine", "dosage", "symptom", "disease", "cancer"],
    "legal claims": ["lawsuit", "illegal", "convicted", "guilty", "indicted", "fraud", "crime"],
    "financial advice": [
        # Not bare "invest": it matched "investigation" and sent air-crash stories to the owner (2 Oct).
        "invest in",
        "invest your",
        "investing in",
        "investment advice",
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


# Negative claims about how a person behaves or what they did wrong. A sentence holding one of these and
# a capitalised full name is routed to the owner (constitution section 6: allegations involving real,
# living people). Added 2026-10-01 after "Founder Wang Xingxing's extreme micromanagement ..." auto-published.
ALLEGATION_TERMS = (
    "micromanag",
    "mismanag",
    "accus",
    "alleg",
    "scandal",
    "misconduct",
    "harass",
    "abus",
    "toxic",
    "negligen",
    "incompeten",
    "lied",
    "lying",
    "cover-up",
    "covered up",
    "blamed",
    "exploit",
    "cheat",
    "plagiar",
    "bully",
    "corrupt",
    "embezzl",
    "dishonest",
    "reckless",
)
_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
# "<up to 6 words> is/are/was/were a/an": the shape of a dictionary-style opening line.
_DEFINITION_RE = re.compile(r"^(?:\S+\s+){0,6}?(?:is|are|was|were)\s+(?:a|an)\b", re.I)
_QUOTES = "\"'“”‘’"
# Guideline stages whose rules the critic is shown and may report as broken.
GATED_STAGES = ("script", "title")


def allegation_sentences(text: str) -> list[str]:
    """Sentences pairing a full name with a negative claim about conduct (living or not: the owner decides)."""
    text = text.replace("’", "'")
    out = []
    for sentence in _SENTENCES.split(text):
        low = sentence.lower()
        if _NAME_RE.search(sentence) and any(re.search(r"\b" + re.escape(t), low) for t in ALLEGATION_TERMS):
            out.append(sentence.strip())
    return out


def hook_revisions(texts: list[str], checks: dict[str, Any]) -> list[str]:
    """Guideline hook checks on the hook line and the first narration sentence.

    Each distinct text is checked for banned openings, the word limit, a question and a definition;
    identical messages are reported once."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        text = raw.strip().strip(_QUOTES).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        low = text.lower()
        for opening in checks.get("banned_openings") or []:
            if str(opening).strip() and low.startswith(str(opening).strip().lower()):
                out.append(f"Opening '{opening}' is banned by the guideline")
        words = text.split()
        limit = checks.get("hook_max_words")
        if limit is not None and len(words) > int(limit):
            out.append(f"Hook is {len(words)} words; the guideline allows {int(limit)}")
        if checks.get("hook_no_question") and "?" in text:
            out.append("Hook asks a question; the guideline forbids it")
        if (
            checks.get("hook_no_definition")
            and words[0].lower().strip(",.;:!?") not in ("there", "here")
            and _DEFINITION_RE.match(text)
        ):
            out.append(
                f'Hook opens with a definition ("{" ".join(words[:8])}..."); state the surprise instead'
            )
    return list(dict.fromkeys(out))


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
        g = guideline.for_services(self.svc)
        replace = guideline.variant_for(idea, self.svc.db) if g else {}
        tested_runtime = guideline.runtime_variant(idea, self.svc.db) if g else None
        gtext = self.guideline_text(GATED_STAGES, replace, g)
        if gtext:
            gtext += "List in broken_rules the ID of every guideline rule the script breaks.\n\n"
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
            + gtext
            + f"Channel strategy memory:\n{self.strategy.prompt_summary(self.strategy.current(), 1200)}\n\n"
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
                    guideline=g,
                    replace=replace,
                    runtime_override=tested_runtime,
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
    def _guideline_revisions(
        result: CriticResult,
        draft: ScriptDraft,
        narration: str,
        g: Guideline,
        replace: dict[str, str] | None,
        runtime_override: int | None = None,
    ) -> list[str]:
        """Required revisions from the owner's guideline: broken rules and the code checks.

        ``runtime_override`` is the tested runtime of a G-runtime treatment idea; its runtime is not
        gated on the guideline range (controller ruling R6)."""
        out: list[str] = []
        # Only rules the critic was shown can be broken by the script: script and title rules, with a
        # tested variant's text in place of its rule. A topic or cover rule ID is ignored.
        shown = {r.id: r for stage in GATED_STAGES for r in g.rules_for(stage, replace)}
        for rid in dict.fromkeys(result.broken_rules):
            rule = shown.get(rid)
            if rule is not None:
                out.append(f"Breaks {rid} ({rule.title}): {rule.text}")
        seconds = len(narration.split()) / WORDS_PER_SECOND
        if runtime_override is None and not g.runtime_min <= seconds <= g.runtime_max:
            out.append(f"Runtime {seconds:.0f}s is outside the guideline's {g.runtime_min}-{g.runtime_max}s")
        first = _SENTENCES.split(narration.strip(), maxsplit=1)[0]
        out += hook_revisions([draft.hook_line, first], g.checks)
        limit = g.checks.get("title_max_chars")
        if limit is not None and len(draft.title) > int(limit):
            out.append(f"Title is {len(draft.title)} characters; the guideline allows {int(limit)}")
        return out

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
        guideline: Guideline | None = None,
        replace: dict[str, str] | None = None,
        runtime_override: int | None = None,
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
        if guideline is not None:
            result.required_revisions.extend(
                CriticAgent._guideline_revisions(
                    result, draft, narration, guideline, replace, runtime_override
                )
            )
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
        alleged = allegation_sentences(" ".join([draft.title + ".", draft.hook_line, narration]))
        if alleged:
            reasons.add("allegations involving real, living people")
            result.elevated_review_evidence = [*result.elevated_review_evidence, *alleged[:3]]
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
