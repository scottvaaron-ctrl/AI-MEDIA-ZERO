"""Idea Generation agent: turns fresh leads into scored candidate videos."""

from __future__ import annotations

from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import IdeaBatch, IdeaDraft, SourceItemView
from aimz.util import clamp, dumps, new_id, now_iso

SCORE_WEIGHTS = {
    "hook_strength": 1.5,
    "curiosity": 1.2,
    "audience_relevance": 1.0,
    "trend_velocity": 0.6,
    "competition_gap": 0.8,
    "source_quality": 1.0,
    "originality": 1.0,
    "evergreen_potential": 0.8,
    "repeatability": 0.5,
    "monetization_potential": 0.4,
    "production_feasibility": 0.8,
    "zero_budget_feasibility": 0.8,
}


def opportunity_score(draft: IdeaDraft, family_state: dict[str, Any] | None) -> float:
    s = draft.scores.model_dump()
    total_w = sum(SCORE_WEIGHTS.values())
    base = sum(SCORE_WEIGHTS[k] * s[k] for k in SCORE_WEIGHTS) / (total_w * 10) * 100
    adj = 0.0
    status = (family_state or {}).get("status", "hypothesis")
    adj += {"winning": 8, "testing": 2, "hypothesis": 3, "new": 3, "losing": -10, "retired": -100}.get(
        status, 0
    )
    if draft.production_difficulty == "high":
        adj -= 6
    return round(clamp(base + adj, 0, 100), 2)


class IdeationAgent(Agent):
    name = "ideation"

    def pick_leads(self, k: int) -> list[SourceItemView]:
        rows = self.svc.db.query(
            "SELECT si.*, s.name AS source_name FROM source_items si JOIN sources s ON s.id=si.source_id "
            "WHERE si.status='new' ORDER BY (COALESCE(si.freshness_score,0.4)*0.6 + COALESCE(si.credibility,0.5)*0.4) DESC, si.ingested_at DESC LIMIT ?",
            [k * 3],
        )
        # spread across sources so one noisy feed does not dominate
        per_source: dict[str, int] = {}
        out: list[SourceItemView] = []
        for r in rows:
            c = per_source.get(r["source_id"], 0)
            if c >= max(2, k // 4):
                continue
            per_source[r["source_id"]] = c + 1
            out.append(
                SourceItemView(
                    id=r["id"],
                    source_name=r["source_name"],
                    url=r["url"],
                    title=r["title"],
                    summary=r["summary"] or "",
                    category=r["category"],
                    published_at=r["published_at"],
                    credibility=float(r["credibility"] or 0.5),
                    freshness_score=float(r["freshness_score"] or 0.4),
                )
            )
            if len(out) >= k:
                break
        return out

    def run(
        self,
        run: RunContext,
        strategy_state: dict[str, Any],
        n_ideas: int | None = None,
        leads: list[SourceItemView] | None = None,
    ) -> list[str]:
        cfg = self.svc.config
        n_ideas = n_ideas or int(cfg.get("pipeline.ideas_per_cycle", 8))
        leads = (
            leads
            if leads is not None
            else self.pick_leads(int(cfg.get("pipeline.research_items_per_cycle", 24)))
        )
        if not leads:
            run.note("ideation", {"ideas": 0, "reason": "no fresh leads"})
            return []
        families = strategy_state["families"]
        active = {k: v for k, v in families.items() if v.get("status") != "retired"}
        fam_lines = "\n".join(
            f"- {k}: {v.get('description') or v.get('label')} [status={v.get('status')}]"
            for k, v in active.items()
        )
        hooks = ", ".join(cfg.hook_types)
        sf = cfg.short_form
        user = (
            f"{self.strategy.prompt_summary(strategy_state)}\n\n"
            f"Generate {n_ideas} candidate short-form video ideas ({sf.get('min_seconds', 20)}-{sf.get('max_seconds', 90)} seconds, vertical) from the leads below. "
            "Each idea must be built on at least one lead (cite its id in source_refs) and must be about what that lead actually reports; do not invent an angle, cause, or consequence the lead does not state. "
            "Prefer ideas with a concrete mechanism, a surprising but well-documented fact, and a clear payoff. "
            "Spread ideas across different content families; you may propose a new family key (snake_case) if a lead clearly deserves one. "
            "Score each criterion 0-10 honestly; do not inflate. zero_budget_feasibility must consider that visuals are limited to licensed archival photos, generated cards, charts and maps.\n\n"
            f"Content families:\n{fam_lines}\n\nHook types: {hooks}\n\nLeads:\n{self.sources_block(leads, 500)}\n"
        )
        with self.svc.tracker.agent(run, self.name, "generate", {"leads": [s.id for s in leads]}) as span:
            batch = self.ask(
                self.pctx(run, span=span),
                "Idea Generation agent",
                user,
                IdeaBatch,
                "ideation",
                temperature=0.7,
                max_tokens=4000,
            )
            known = {s.id for s in leads}
            created: list[str] = []
            seen_titles: set[str] = set()
            for d in batch.ideas:
                refs = [r for r in d.source_refs if r in known]
                if not refs:
                    continue
                key = d.title.strip().lower()
                if key in seen_titles or self.svc.db.one("SELECT 1 FROM ideas WHERE lower(title)=?", [key]):
                    continue
                seen_titles.add(key)
                if d.scores.zero_budget_feasibility < 7 or d.scores.production_feasibility < 5:
                    continue
                fam_key = d.content_family.strip().lower().replace(" ", "_").replace("-", "_")
                fam_state = families.get(fam_key)
                if fam_state and fam_state.get("status") == "retired":
                    continue
                score = opportunity_score(d, fam_state)
                idea_id = new_id("idea")
                self.svc.db.insert(
                    "ideas",
                    {
                        "id": idea_id,
                        "run_id": run.id,
                        "title": d.title.strip()[:120],
                        "premise": d.premise.strip(),
                        "hook": d.hook.strip(),
                        "hook_type": d.hook_type.strip().lower(),
                        "content_family": fam_key,
                        "target_platform": d.target_platform,
                        "format": "short",
                        "suggested_runtime_s": int(
                            clamp(d.suggested_runtime_s, sf.get("min_seconds", 20), sf.get("max_seconds", 90))
                        ),
                        "source_item_ids_json": dumps(refs),
                        "production_difficulty": d.production_difficulty,
                        "expected_cost_usd": 0.0,
                        "originality_notes": d.originality_assessment,
                        "scores_json": dumps(d.scores.model_dump()),
                        "opportunity_score": score,
                        "rationale": d.rationale,
                        "status": "candidate",
                        "created_at": now_iso(),
                        "updated_at": now_iso(),
                    },
                )
                created.append(idea_id)
                for r in refs:
                    self.svc.db.update("source_items", r, {"status": "used"})
                    self.svc.db.execute(
                        "UPDATE sources SET ideas_yielded = ideas_yielded + 1 WHERE id = (SELECT source_id FROM source_items WHERE id=?)",
                        [r],
                    )
            span.output_refs["ideas"] = created
        # leads that were considered but not used are left 'new' for a later cycle; the least fresh get ignored
        run.note("ideation", {"ideas": len(created), "leads": len(leads)})
        return created
