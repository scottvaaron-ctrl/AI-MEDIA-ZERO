"""Shared agent scaffolding: constitution-aware system prompts, LLM helpers, source loading."""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from aimz.core.runs import RunContext
from aimz.domain.models import SourceItemView
from aimz.providers.base import ProviderContext
from aimz.providers.registry import Services
from aimz.settings import read_text
from aimz.strategy.memory import StrategyMemory
from aimz.util import loads

T = TypeVar("T", bound=BaseModel)

STYLE_RULES = (
    "Writing rules: plain, concrete, specific. No engagement bait, no fake urgency, no 'you won't believe', "
    "no 'like and subscribe', no rhetorical filler, no repetitive AI phrasing ('delve', 'in the realm of', "
    "'it's important to note'). Never invent facts, quotes, numbers, names, or dates that are not in the sources. "
    "If a detail is not in the sources, leave it out or say it is uncertain."
)


class Agent:
    name = "agent"

    def __init__(self, svc: Services, strategy: StrategyMemory):
        self.svc = svc
        self.strategy = strategy
        self.log = logging.getLogger(f"aimz.agent.{self.name}")
        self._constitution: str | None = None

    # -- prompts ----------------------------------------------------------------------
    def constitution(self) -> str:
        if self._constitution is None:
            self._constitution = (
                read_text(self.svc.env.config_dir / "constitution.md") or "(no constitution file)"
            )
        return self._constitution

    def system_prompt(self, role: str, include_constitution: bool = True) -> str:
        parts = [
            f"You are the {role} of 'AI Media Zero', an autonomous, zero-budget media channel run as an experiment.",
            STYLE_RULES,
        ]
        if include_constitution:
            parts.append("CHANNEL CONSTITUTION (owner rules; you must obey):\n" + self.constitution()[:6000])
        parts.append(
            "Respond with a single JSON object that matches the requested schema exactly. No prose outside the JSON."
        )
        return "\n\n".join(parts)

    def ask(
        self,
        ctx: ProviderContext,
        role: str,
        user: str,
        schema: type[T],
        purpose: str,
        *,
        temperature: float = 0.4,
        max_tokens: int = 3000,
    ) -> T:
        system = self.system_prompt(role)
        user = (
            user + "\n\nJSON schema:\n" + json.dumps(schema.model_json_schema(), separators=(",", ":"))[:6000]
        )
        return self.svc.llm.complete_model(
            ctx, system, user, schema, purpose, temperature=temperature, max_tokens=max_tokens
        )

    def pctx(self, run: RunContext, content_id: str | None = None, span: Any = None) -> ProviderContext:
        return self.svc.ctx(run.id, content_id, span)

    # -- data helpers -----------------------------------------------------------------
    def load_sources(self, ids: list[str]) -> list[SourceItemView]:
        out: list[SourceItemView] = []
        for sid in ids:
            row = self.svc.db.get("source_items", sid)
            if not row:
                continue
            src = self.svc.db.get("sources", row["source_id"])
            out.append(
                SourceItemView(
                    id=row["id"],
                    source_name=src["name"] if src else row["source_id"],
                    url=row["url"],
                    title=row["title"],
                    summary=row["summary"] or "",
                    category=row["category"],
                    published_at=row["published_at"],
                    credibility=float(row["credibility"] or 0.5),
                    freshness_score=float(row["freshness_score"] or 0.5),
                )
            )
        return out

    @staticmethod
    def sources_block(sources: list[SourceItemView], max_summary: int = 700) -> str:
        lines = []
        for s in sources:
            lines.append(
                f"- [{s.id}] ({s.source_name}, {s.published_at or 'undated'}, credibility {s.credibility:.2f}) {s.title}\n  URL: {s.url}\n  Summary: {s.summary[:max_summary]}"
            )
        return "\n".join(lines) if lines else "(no sources)"

    @staticmethod
    def idea_source_ids(idea: dict[str, Any]) -> list[str]:
        return list(loads(idea.get("source_item_ids_json"), []) or [])
