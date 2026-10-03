"""Idea Generation agent: turns fresh leads into scored candidate videos."""

from __future__ import annotations

from typing import Any

from aimz import identity, instances
from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import IdeaBatch, IdeaDraft, SourceItemView
from aimz.experiments import niches, shared
from aimz.util import clamp, dumps, new_id, now_iso

# Equal weights: the model rates each criterion; no human view of which matters most is layered on top.
# What actually matters is learned from performance through family status (below) and the editor.
SCORE_WEIGHTS = {
    k: 1.0
    for k in (
        "hook_strength",
        "curiosity",
        "audience_relevance",
        "trend_velocity",
        "competition_gap",
        "source_quality",
        "originality",
        "evergreen_potential",
        "repeatability",
        "monetization_potential",
        "production_feasibility",
        "zero_budget_feasibility",
    )
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
        taken = shared.sources_used_elsewhere(self.svc.env.instance)
        if taken:  # never two of the owner's channels on one story (YouTube spam policy: channel networks)
            leads = [
                s
                for s in leads
                if (row := self.svc.db.one("SELECT url_hash FROM source_items WHERE id=?", [s.id])) is None
                or row["url_hash"] not in taken
            ]
        if not leads:
            run.note("ideation", {"ideas": 0, "reason": "no fresh leads"})
            return []
        families = strategy_state["families"]
        active = {k: v for k, v in families.items() if v.get("status") != "retired"}
        fam_lines = "\n".join(
            f"- {k} [status={v.get('status')}, measured={v.get('n', 0)}, mean_score={v.get('mean_score')}]"
            for k, v in active.items()
        )
        used_hooks = sorted(
            {
                r["hook_type"]
                for r in self.svc.db.query("SELECT DISTINCT hook_type FROM ideas WHERE hook_type IS NOT NULL")
            }
        )
        used_angles = sorted(
            {
                r["angle"]
                for r in self.svc.db.query("SELECT DISTINCT angle FROM ideas WHERE angle IS NOT NULL")
            }
        )
        amap = niches.aliases(self.svc.db)
        own_counts = {k: int(v.get("n") or 0) for k, v in families.items()}
        claimed = instances.claimed_niches(self.svc.env.instance, own_counts)
        others_text = shared.other_channels_text(self.svc.env.instance)
        sf = cfg.short_form
        user = (
            f"{self.strategy.prompt_summary(strategy_state)}\n\n"
            f"{identity.genre_line(self.svc.db)}"
            f"Generate {n_ideas} candidate vertical video ideas ({sf.get('min_seconds', 10)}-{sf.get('max_seconds', 180)} seconds) from the leads below. "
            "Each idea must be built on at least one lead (cite its id in source_refs) and must be about what that lead actually reports; do not invent an angle, cause, or consequence the lead does not state. "
            "content_family is the idea's niche: the subject area a viewer would follow a channel for. angle is the kind of story "
            "within that niche. content_family, angle and hook_type are your own labels (snake_case) for grouping ideas so results "
            "can be compared; reuse a label when an idea belongs to it, or create a new one. "
            "Score each criterion 0-10 honestly; do not inflate. zero_budget_feasibility must consider that visuals are limited to licensed archival photos, generated cards, charts and maps.\n\n"
            f"Niches (content families) so far:\n{fam_lines or '- none yet'}\n\n"
            f"Angles used so far: {', '.join(used_angles) or 'none yet'}\n"
            + (
                f"Niches held by the owner's other channels; ideas in them are dropped: {', '.join(sorted(claimed))}\n"
                if claimed
                else ""
            )
            + (f"The owner's other channels:\n{others_text}\n" if others_text else "")
            + "\n"
            f"Hook types used so far: {', '.join(used_hooks) or 'none yet'}\n\nLeads:\n{self.sources_block(leads, 500)}\n"
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
                fam_key = niches.canonical(niches.norm_label(d.content_family), amap) or ""
                if fam_key in claimed:  # another channel already exploits this niche
                    continue
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
                        "hook_type": d.hook_type.strip().lower().replace(" ", "_").replace("-", "_"),
                        "content_family": fam_key,
                        "angle": niches.norm_label(d.angle) or None,
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
