"""FixtureLLMProvider: deterministic, offline stand-in for the local model.

Used by the test suite, CI, and ``LLM_PROVIDER=fixture`` dry runs. It produces
schema-valid answers derived from the ids and text present in the prompt, so the
whole pipeline (research -> ideas -> script -> QA -> render -> package -> learn)
can be exercised without Ollama. It is clearly *not* intelligent, and it is never
selected unless the owner sets it explicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel

from aimz.providers.base import HealthStatus, LLMProvider, LLMResponse, ProviderContext

_SRC_RE = re.compile(r"\b(src_\d{8}T\d{6}_[0-9a-f]{8})\b")
_IDEA_RE = re.compile(r"\b(idea_\d{8}T\d{6}_[0-9a-f]{8})\b")
_TITLE_RE = re.compile(r"^\s*-\s*\[(src_[^\]]+)\]\s*\((?:[^)]*)\)\s*(.+?)\s*$", re.MULTILINE)
_FAMILY_RE = re.compile(r"^\s*-\s*([a-z_]+):", re.MULTILINE)


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


class FixtureLLMProvider(LLMProvider):
    name = "FixtureLLMProvider"
    is_paid = False

    def __init__(self, model: str = "fixture"):
        self.model = model

    def health(self) -> HealthStatus:
        return HealthStatus(True, "fixture provider (offline, deterministic)")

    def complete(
        self,
        ctx: ProviderContext,
        system: str,
        user: str,
        purpose: str,
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.4,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        with self.authorized(ctx, purpose) as rec:
            rec.tokens_in = len(user) // 4
            name = schema.__name__ if schema else "text"
            handler = getattr(self, f"_gen_{name}", None)
            payload: Any = handler(user) if handler else {"text": "fixture"}
            text = json.dumps(payload, ensure_ascii=False)
            rec.tokens_out = len(text) // 4
            return LLMResponse(
                text=text, model=self.model, tokens_in=rec.tokens_in, tokens_out=rec.tokens_out
            )

    # -- generators --------------------------------------------------------------------
    def _gen_IdeaBatch(self, user: str) -> dict[str, Any]:  # noqa: N802
        titles = _TITLE_RE.findall(user)
        srcs = _SRC_RE.findall(user)
        families = _FAMILY_RE.findall(user) or [
            "forgotten_history",
            "corporate_failures",
            "technology_history",
        ]
        families = [f for f in families if f not in {"numeric_hook", "question_hook"}] or [
            "forgotten_history"
        ]
        hooks = ["numeric_hook", "question_hook", "contrarian_hook", "narrative_hook", "stakes_hook"]
        m = re.search(r"Generate (\d+)", user)
        n = int(m.group(1)) if m else 6
        ideas = []
        pool = titles[:n] if titles else [(s, f"Lead {i}") for i, s in enumerate(srcs[:n])]
        for i, (src, title) in enumerate(pool):
            s = _seed(title)
            fam = families[(s + i) % len(families)]
            hook_type = hooks[(s + i) % len(hooks)]
            base = 5 + (s % 4)
            ideas.append(
                {
                    "title": f"{title.strip()[:70]}: the part nobody remembers",
                    "premise": f"A short, sourced retelling of '{title.strip()[:60]}' focused on one surprising mechanism and its consequence.",
                    "hook": (
                        f"In {1900 + (s % 120)}, one decision changed everything about {title.strip()[:40]}."
                        if hook_type == "numeric_hook"
                        else f"Why did {title.strip()[:50]} happen at all?"
                    ),
                    "hook_type": hook_type,
                    "content_family": fam,
                    "target_platform": "both",
                    "suggested_runtime_s": 40 + (s % 30),
                    "source_refs": [src],
                    "production_difficulty": "low",
                    "originality_assessment": "Angle is specific to the source; low duplication risk.",
                    "scores": {
                        "hook_strength": base,
                        "curiosity": min(10, base + 1),
                        "audience_relevance": base,
                        "trend_velocity": 4 + (s % 5),
                        "competition_gap": base,
                        "source_quality": 7,
                        "originality": base,
                        "evergreen_potential": 6 + (s % 4),
                        "repeatability": 6,
                        "monetization_potential": 5,
                        "production_feasibility": 9,
                        "zero_budget_feasibility": 10,
                    },
                    "rationale": "Fixture: strong single-source lead with a clear mechanism and payoff.",
                }
            )
        return {"ideas": ideas}

    def _gen_SelectionDecision(self, user: str) -> dict[str, Any]:  # noqa: N802
        ids = list(dict.fromkeys(_IDEA_RE.findall(user)))
        m = re.search(r"Select up to (\d+)", user)
        k = int(m.group(1)) if m else 2
        return {
            "selected_idea_ids": ids[:k],
            "rejected": [
                {"idea_id": i, "reason": "Fixture: weaker hook than selected ideas."} for i in ids[k : k + 2]
            ],
            "notes": "Fixture selection: highest opportunity scores that satisfy the allocation plan.",
        }

    def _gen_ScriptDraft(self, user: str) -> dict[str, Any]:  # noqa: N802
        srcs = _SRC_RE.findall(user)
        src = srcs[:1]
        tm = re.search(r"Title:\s*(.+)", user)
        title = (tm.group(1).strip() if tm else "The forgotten decision")[:90]
        hm = re.search(r"Hook:\s*(.+)", user)
        hook = hm.group(1).strip() if hm else f"Here is the part of {title} nobody talks about."
        beats = [
            {
                "narration": hook,
                "caption": "The part nobody remembers",
                "visual_type": "text_card",
                "visual_query": title,
                "source_refs": src,
            },
            {
                "narration": "It started with a plan that looked completely reasonable on paper.",
                "caption": "A reasonable plan",
                "visual_type": "image",
                "visual_query": title,
                "source_refs": src,
            },
            {
                "narration": "But one assumption inside that plan was wrong, and nobody checked it for years.",
                "caption": "One wrong assumption",
                "visual_type": "stat_card",
                "visual_query": "years unchecked",
                "source_refs": src,
            },
            {
                "narration": "When reality finally caught up, the consequences arrived all at once.",
                "caption": "Then reality arrived",
                "visual_type": "image",
                "visual_query": title,
                "source_refs": src,
            },
            {
                "narration": "The lesson is not that they were foolish. It is that the system rewarded not looking.",
                "caption": "The system rewarded not looking",
                "visual_type": "quote_card",
                "visual_query": "rewarded not looking",
                "source_refs": src,
            },
            {
                "narration": "Sources are in the description. If you want the full story, say so in the comments.",
                "caption": "Sources in description",
                "visual_type": "text_card",
                "visual_query": "sources",
                "source_refs": src,
            },
        ]
        return {
            "title": title,
            "hook_line": hook,
            "beats": beats,
            "description": f"{title}. A short, sourced explainer.\n\nAI-narrated. Sources listed below.",
            "tags": ["history", "explainer", "shorts"],
            "thumbnail_concept": "Bold three-word title over a desaturated archival image.",
            "claims": [
                {
                    "text": "It started with a plan that looked reasonable on paper.",
                    "claim_type": "general",
                    "beat_index": 1,
                    "source_refs": src,
                },
                {
                    "text": "One assumption went unchecked for years.",
                    "claim_type": "general",
                    "beat_index": 2,
                    "source_refs": src,
                },
            ],
        }

    def _gen_FactCheckResult(self, user: str) -> dict[str, Any]:  # noqa: N802
        n = len(re.findall(r"^\s*\[(\d+)\]", user, re.MULTILINE)) or 2
        verdicts = [
            {
                "claim_index": i,
                "status": "supported" if i % 3 != 2 else "weak",
                "note": "Fixture verdict.",
                "suggested_rewrite": "" if i % 3 != 2 else "reportedly",
            }
            for i in range(n)
        ]
        return {
            "verdicts": verdicts,
            "overall": "pass",
            "notes": "Fixture fact-check: claims trace to the stored lead.",
        }

    def _gen_CriticResult(self, user: str) -> dict[str, Any]:  # noqa: N802
        s = _seed(user) % 10
        return {
            "pass": True,
            "score": 76 + s,
            "problems": ["Hook could be more concrete."] if s < 5 else [],
            "required_revisions": [],
            "optional_improvements": ["Tighten beat 4 by one sentence."],
            "hook_strength": 7,
            "originality": 7,
            "factual_support": 7,
            "pacing": 8,
            "payoff": 7,
            "clarity": 8,
            "copyright_risk": "low",
            "policy_risk": "low",
            "deceptive_media_risk": "low",
            "feels_generic_ai": False,
            "deserves_video": True,
            "elevated_review_required": False,
            "elevated_review_reasons": [],
        }

    def _gen_StrategyUpdate(self, user: str) -> dict[str, Any]:  # noqa: N802
        fams = re.findall(r"family=([a-z_]+)", user)
        fams = list(dict.fromkeys(fams))[:3]
        return {
            "audience_model": "Cold start: curious general-interest viewers; no measured audience yet.",
            "family_updates": [
                {"key": f, "status": "testing", "note": "Fixture: first samples collected."} for f in fams
            ],
            "strong_hooks": [],
            "weak_hooks": [],
            "runtime_observations": "Insufficient data.",
            "source_observations": "Wikipedia feeds yield usable leads; social feeds noisier.",
            "production_bottlenecks": "Render time dominated by TTS.",
            "audience_requests": [],
            "new_experiments": [],
            "retire_experiment_ids": [],
            "explore_ratio_suggestion": 0.7,
            "confidence": 0.1,
            "change_summary": "Fixture: no evidence yet; keep exploring.",
            "notes": "",
        }

    def _gen_CommentBatchClassification(self, user: str) -> dict[str, Any]:  # noqa: N802
        lines = [ln for ln in user.splitlines() if re.match(r"^\s*\[\d+\]", ln)]
        out = []
        for ln in lines:
            low = ln.lower()
            if "?" in low:
                cls, useful, req = "question", True, ""
            elif "wrong" in low or "actually" in low:
                cls, useful, req = "correction", True, ""
            elif "do a video" in low or "please cover" in low or "next" in low:
                cls, useful, req = "content_request", True, ln.split("]", 1)[-1].strip()
            elif "http" in low or "follow me" in low:
                cls, useful, req = "spam", False, ""
            else:
                cls, useful, req = "audience_signal", False, ""
            out.append(
                {"classification": cls, "sentiment": "neutral", "useful": useful, "content_request": req}
            )
        return {"results": out}
