"""Fact-checking layer: claims vs stored source text, plus deterministic checks the model cannot skip.

1. Deterministic pass: every 4-digit year and every number >= 100 in the narration must appear in
   the source titles/summaries; otherwise the specific is flagged ``unverifiable``.
2. Model pass: each listed claim is compared to the sources and gets a verdict and, for weak or
   unverifiable claims, a softened rewrite.
3. Application: supported claims stay; weak claims are softened; contradicted/unverifiable
   specifics become revision requirements, and on the final round the offending sentence is removed.
4. Title and hook (owner decision 2026-10-01, "sources required for claims"; YouTube's misleading-
   metadata rules): every number, year or amount in them must be in the sources *and* said in the
   narration, and the model checks them as claims (who did it, what happened). Any failure is a
   revision, so the script cannot be approved with an unsupported title.
Nothing is ever *added* to fill a gap.
"""

from __future__ import annotations

import re

from aimz.agents.base import Agent
from aimz.core.errors import TechnicalFailure
from aimz.core.runs import RunContext
from aimz.domain.models import FactCheckResult, ScriptDraft, SourceItemView
from aimz.util import dumps, now_iso

_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{3,})(?![\w.])")
_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
# Amounts the >=100 rule misses: "$3.2 billion", "40%", "2.5 million".
_AMOUNT_RE = re.compile(
    r"\$?\s?(\d+(?:\.\d+)?)\s*(?:billion|million|trillion|thousand|bn|percent|%)", re.IGNORECASE
)


def _source_text(sources: list[SourceItemView]) -> str:
    return " ".join(
        f"{s.title} {s.summary} {s.full_text} {s.published_at or ''} {s.url}" for s in sources
    ).lower()


def unsupported_specifics(draft: ScriptDraft, sources: list[SourceItemView]) -> list[str]:
    corpus = _source_text(sources).replace(",", "")
    flagged: list[str] = []
    narration = " ".join(b.narration for b in draft.beats)
    for tok in set(_YEAR_RE.findall(narration)) | {t.replace(",", "") for t in _NUM_RE.findall(narration)}:
        if tok not in corpus:
            flagged.append(tok)
    return sorted(flagged)


def _specifics(text: str) -> set[str]:
    return (
        set(_YEAR_RE.findall(text))
        | {t.replace(",", "") for t in _NUM_RE.findall(text)}
        | set(_AMOUNT_RE.findall(text))
    )


def unsupported_title_specifics(draft: ScriptDraft, sources: list[SourceItemView]) -> list[str]:
    """Numbers, years and amounts in the title or hook that the sources or the narration do not contain."""
    corpus = _source_text(sources).replace(",", "")
    narration = " ".join(b.narration for b in draft.beats).lower().replace(",", "")
    flagged = [
        tok
        for tok in _specifics(f"{draft.title} {draft.hook_line}")
        if tok.lower() not in corpus or tok.lower() not in narration
    ]
    return sorted(flagged)


def remove_sentences_containing(draft: ScriptDraft, tokens: list[str]) -> int:
    removed = 0
    for b in draft.beats:
        sents = _SENT_SPLIT.split(b.narration)
        keep = [s for s in sents if not any(t in s.replace(",", "") for t in tokens)]
        if len(keep) != len(sents):
            removed += len(sents) - len(keep)
            b.narration = " ".join(keep).strip() or b.narration  # never leave a beat empty
    return removed


class FactCheckAgent(Agent):
    name = "factcheck"

    def check(
        self, run: RunContext, script_id: str, draft: ScriptDraft, sources: list[SourceItemView]
    ) -> tuple[FactCheckResult, list[str]]:
        """Return the model's verdicts plus a list of required revisions (may be empty)."""
        flagged = unsupported_specifics(draft, sources)
        title_flagged = unsupported_title_specifics(draft, sources)
        n = len(draft.claims)
        claims_block = "\n".join(
            [f"[{i}] (beat {c.beat_index}, {c.claim_type}) {c.text}" for i, c in enumerate(draft.claims)]
            + [f"[{n}] (the video's TITLE) {draft.title}", f"[{n + 1}] (the spoken HOOK) {draft.hook_line}"]
        )
        user = (
            "Verify each claim strictly against the sources. The title and hook are claims too: check who did what, "
            "numbers, dates and what actually happened (e.g. a study NASA funded is not 'NASA finds'; a building's cost "
            "is not the cost of a fire in it), and mark them 'contradicted' when they misstate the sources. "
            "Mark 'supported' only when the source text clearly supports it, "
            "'weak' when only loosely implied or by a single low-credibility source, 'unverifiable' when the sources do not contain it, "
            "'contradicted' when the sources say otherwise. For weak/unverifiable claims, give a softened rewrite (e.g. 'reportedly', 'around', or drop the specific). "
            "Never add new facts. Overall: 'pass' if all supported/weak-with-rewrite, 'revise' if some need fixing, 'reject' if the core premise is contradicted or unverifiable.\n\n"
            f"Claims:\n{claims_block}\n\nScript narration:\n"
            + " ".join(b.narration for b in draft.beats)
            + f"\n\nSources:\n{self.sources_block(sources, 900, text_budget=5000)}\n"
        )
        with self.svc.tracker.agent(run, self.name, "verify", {"script_id": script_id}) as span:
            try:
                result = self.ask(
                    self.pctx(run, script_id, span),
                    "Fact-checking agent",
                    user,
                    FactCheckResult,
                    "factcheck",
                    temperature=0.1,
                    max_tokens=2500,
                )
            except Exception as exc:
                # A dead model says nothing about the claims. Softening every claim as "weak" turned an
                # outage into a content revision; it is a technical failure, retried later.
                raise TechnicalFailure(f"fact-check model call failed: {type(exc).__name__}: {exc}") from exc
            revisions = self._apply(script_id, draft, result, flagged, title_flagged)
            span.output_refs["overall"] = result.overall
            span.output_refs["flagged_specifics"] = flagged
            span.output_refs["title_flagged"] = title_flagged
        return result, revisions

    def _apply(
        self,
        script_id: str,
        draft: ScriptDraft,
        result: FactCheckResult,
        flagged: list[str],
        title_flagged: list[str] | None = None,
    ) -> list[str]:
        revisions: list[str] = []
        n = len(draft.claims)
        for v in result.verdicts:
            if v.claim_index in (n, n + 1) and v.status != "supported":
                what, text = ("TITLE", draft.title) if v.claim_index == n else ("HOOK", draft.hook_line)
                revisions.append(
                    f"{what} is {v.status} by the sources; rewrite it so every claim in it is stated in the "
                    f"sources and in the narration: '{text[:100]}' ({v.note[:100]})"
                )
        if title_flagged:
            revisions.append(
                "Title/hook specifics that are not both in the sources and said in the narration "
                "(remove them or say and source them): " + ", ".join(title_flagged)
            )
        claim_rows = [
            dict(r)
            for r in self.svc.db.query(
                "SELECT * FROM claims WHERE script_id=? ORDER BY created_at", [script_id]
            )
        ]
        for v in result.verdicts:
            if v.claim_index >= len(draft.claims):
                continue
            claim = draft.claims[v.claim_index]
            status: str = v.status
            if status == "weak" and v.suggested_rewrite.strip():
                b = draft.beats[claim.beat_index]
                if claim.text and claim.text in b.narration:
                    b.narration = b.narration.replace(claim.text, v.suggested_rewrite.strip())
                    status = "softened"
                else:
                    revisions.append(f"Soften claim '{claim.text[:80]}': {v.suggested_rewrite[:120]}")
            elif status in {"unverifiable", "contradicted"}:
                revisions.append(
                    f"{status.upper()} claim, remove or rewrite without the specific: '{claim.text[:100]}' ({v.note[:100]})"
                )
            if v.claim_index < len(claim_rows):
                self.svc.db.update(
                    "claims",
                    claim_rows[v.claim_index]["id"],
                    {"verification_status": status, "verification_notes": v.note[:500]},
                )
        if flagged:
            revisions.append(
                "Unsupported specifics not found in any source (remove or replace with sourced facts): "
                + ", ".join(flagged)
            )
        self.svc.db.update(
            "scripts",
            script_id,
            {
                "factcheck_json": dumps(
                    {
                        "overall": result.overall,
                        "notes": result.notes,
                        "flagged": flagged,
                        "title_flagged": title_flagged or [],
                        "verdicts": [v.model_dump() for v in result.verdicts],
                    }
                ),
                "status": "factchecked",
                "updated_at": now_iso(),
            },
        )
        if result.overall == "reject":
            revisions.insert(
                0,
                "Fact-check REJECT: the core premise is not supported by the sources. Rebuild the script only from what the sources actually say.",
            )
        return revisions

    def enforce_final(self, draft: ScriptDraft, sources: list[SourceItemView]) -> int:
        """Last-resort cleanup after the revision budget is exhausted: drop sentences with unsupported specifics."""
        flagged = unsupported_specifics(draft, sources)
        removed = remove_sentences_containing(draft, flagged) if flagged else 0
        if removed and any(t in draft.hook_line.replace(",", "") for t in flagged) and draft.beats:
            # The hook is the first thing said; keep it in step with the narration that is left.
            first = _SENT_SPLIT.split(draft.beats[0].narration.strip())[0]
            draft.hook_line = first if len(first) >= 5 else draft.hook_line
        return removed
