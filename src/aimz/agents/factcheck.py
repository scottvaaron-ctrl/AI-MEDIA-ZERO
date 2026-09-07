"""Fact-checking layer: claims vs stored source text, plus deterministic checks the model cannot skip.

1. Deterministic pass: every 4-digit year and every number >= 100 in the narration must appear in
   the source titles/summaries; otherwise the specific is flagged ``unverifiable``.
2. Model pass: each listed claim is compared to the sources and gets a verdict and, for weak or
   unverifiable claims, a softened rewrite.
3. Application: supported claims stay; weak claims are softened; contradicted/unverifiable
   specifics become revision requirements, and on the final round the offending sentence is removed.
Nothing is ever *added* to fill a gap.
"""

from __future__ import annotations

import re

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import ClaimVerdict, FactCheckResult, ScriptDraft, SourceItemView
from aimz.util import dumps, now_iso

_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{3,})(?![\w.])")
_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _source_text(sources: list[SourceItemView]) -> str:
    return " ".join(f"{s.title} {s.summary} {s.published_at or ''} {s.url}" for s in sources).lower()


def unsupported_specifics(draft: ScriptDraft, sources: list[SourceItemView]) -> list[str]:
    corpus = _source_text(sources).replace(",", "")
    flagged: list[str] = []
    narration = " ".join(b.narration for b in draft.beats)
    for tok in set(_YEAR_RE.findall(narration)) | {t.replace(",", "") for t in _NUM_RE.findall(narration)}:
        if tok not in corpus:
            flagged.append(tok)
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
        claims_block = (
            "\n".join(
                f"[{i}] (beat {c.beat_index}, {c.claim_type}) {c.text}" for i, c in enumerate(draft.claims)
            )
            or "(no claims listed)"
        )
        user = (
            "Verify each claim strictly against the sources. Mark 'supported' only when the source text clearly supports it, "
            "'weak' when only loosely implied or by a single low-credibility source, 'unverifiable' when the sources do not contain it, "
            "'contradicted' when the sources say otherwise. For weak/unverifiable claims, give a softened rewrite (e.g. 'reportedly', 'around', or drop the specific). "
            "Never add new facts. Overall: 'pass' if all supported/weak-with-rewrite, 'revise' if some need fixing, 'reject' if the core premise is contradicted or unverifiable.\n\n"
            f"Claims:\n{claims_block}\n\nScript narration:\n"
            + " ".join(b.narration for b in draft.beats)
            + f"\n\nSources:\n{self.sources_block(sources, 900)}\n"
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
                self.log.warning("fact-check model call failed: %s; treating all claims as weak", exc)
                result = FactCheckResult(
                    verdicts=[
                        ClaimVerdict(claim_index=i, status="weak", note="model unavailable")
                        for i in range(len(draft.claims))
                    ],
                    overall="revise",
                    notes=str(exc)[:200],
                )
            revisions = self._apply(script_id, draft, result, flagged)
            span.output_refs["overall"] = result.overall
            span.output_refs["flagged_specifics"] = flagged
        return result, revisions

    def _apply(
        self, script_id: str, draft: ScriptDraft, result: FactCheckResult, flagged: list[str]
    ) -> list[str]:
        revisions: list[str] = []
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
        return remove_sentences_containing(draft, flagged) if flagged else 0
