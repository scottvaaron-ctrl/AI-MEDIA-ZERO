"""Editor-in-Chief agent: the central decision-maker.

Selects what gets produced (using the allocation plan and strategy memory), assigns experiment
arms, rejects weak ideas, and expires stale candidates. Its optimization goals, in order:
viewer retention, shares/1k, subscribers/1k, returning viewers, completion, production
efficiency, long-term trust, future monetization. Upload count is not a goal.
"""

from __future__ import annotations

import contextlib
import random
from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import SelectionDecision
from aimz.experiments.allocation import plan_allocation
from aimz.experiments.engine import ExperimentEngine
from aimz.util import now_iso

GOALS = (
    "Optimization goals in priority order: 1) viewer retention, 2) shares per 1,000 views, 3) followers/subscribers gained per 1,000 views, "
    "4) returning viewers, 5) completion rate, 6) production efficiency, 7) long-term audience trust, 8) future monetization potential. "
    "Number of uploads is NOT a goal. Reject ideas that do not deserve a video."
)


class EditorInChief(Agent):
    name = "editor_in_chief"

    def __init__(
        self, svc: Any, strategy: Any, experiments: ExperimentEngine, rng: random.Random | None = None
    ):
        super().__init__(svc, strategy)
        self.experiments = experiments
        self.rng = rng or random.Random()

    def expire_stale(self, max_age_days: int = 21) -> int:
        cur = self.svc.db.execute(
            "UPDATE ideas SET status='killed', eic_notes='expired: not selected within window', updated_at=? "
            "WHERE status='candidate' AND created_at < datetime('now', ?)",
            [now_iso(), f"-{max_age_days} days"],
        )
        return cur.rowcount or 0

    def candidates(self, limit: int = 40) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.svc.db.query(
                "SELECT * FROM ideas WHERE status='candidate' ORDER BY opportunity_score DESC LIMIT ?",
                [limit],
            )
        ]

    def select(
        self,
        run: RunContext,
        strategy_state: dict[str, Any],
        perf_rows: list[dict[str, Any]],
        k: int | None = None,
    ) -> list[str]:
        cfg = self.svc.config
        k = k or int(cfg.get("pipeline.selections_per_cycle", 2))
        self.expire_stale()
        cands = self.candidates()
        if not cands:
            run.note("selection", {"selected": 0, "reason": "no candidates"})
            return []
        alloc_cfg = dict(cfg.get("allocation", {}) or {})
        slots = plan_allocation(
            perf_rows, strategy_state["families"], k, alloc_cfg, self.rng, strategy_state.get("explore_ratio")
        )
        running = self.experiments.running()

        selected: list[str] = []
        used: set[str] = set()
        for slot in slots:
            pool = [
                c
                for c in cands
                if c["id"] not in used and (slot.family is None or c["content_family"] == slot.family)
            ]
            if not pool:
                pool = [c for c in cands if c["id"] not in used]
            if not pool:
                break
            shortlist = pool[:4]
            choice = self._choose(run, strategy_state, shortlist, slot.mode, slot.reason)
            if choice is None:
                continue
            used.add(choice["id"])
            selected.append(choice["id"])
            updates: dict[str, Any] = {
                "status": "selected",
                "allocation_mode": slot.mode,
                "eic_notes": f"{slot.mode}: {slot.reason}",
                "updated_at": now_iso(),
            }
            # experiment arm assignment: force the variable's value onto the idea
            for exp in running:
                arm, value = self.experiments.assign_arm(exp, self.rng)
                updates["experiment_id"] = exp["id"]
                updates["experiment_arm"] = arm
                if exp["variable"] in {"hook_type", "target_platform", "format"}:
                    updates[exp["variable"]] = value
                elif exp["variable"] == "runtime":
                    with contextlib.suppress(ValueError):
                        updates["suggested_runtime_s"] = int(value)
                break  # one experiment per idea keeps attribution clean
            self.svc.db.update("ideas", choice["id"], updates)
        run.note("selection", {"selected": len(selected), "slots": [f"{s.mode}:{s.family}" for s in slots]})
        return selected

    def _choose(
        self,
        run: RunContext,
        strategy_state: dict[str, Any],
        shortlist: list[dict[str, Any]],
        mode: str,
        reason: str,
    ) -> dict[str, Any] | None:
        lines = []
        for c in shortlist:
            lines.append(
                f"- {c['id']} | score {c['opportunity_score']} | family {c['content_family']} | hook_type {c['hook_type']} | {c['title']}\n  Premise: {c['premise'][:300]}\n  Hook: {c['hook'][:200]}"
            )
        user = (
            f"{GOALS}\n\n{self.strategy.prompt_summary(strategy_state)}\n\n"
            f"Allocation for this slot: {mode.upper()} ({reason}).\n"
            "Select up to 1 idea from the shortlist that best serves the goals and the allocation. Reject the rest with a one-line reason. "
            "If none deserves a video, select nothing.\n\nShortlist:\n" + "\n".join(lines)
        )
        with self.svc.tracker.agent(
            run, self.name, "select", {"shortlist": [c["id"] for c in shortlist]}
        ) as span:
            try:
                decision = self.ask(
                    self.pctx(run, span=span),
                    "Editor-in-Chief",
                    user,
                    SelectionDecision,
                    "eic_select",
                    temperature=0.3,
                    max_tokens=1200,
                )
            except Exception as exc:
                self.log.warning("EIC LLM selection failed (%s); falling back to top score", exc)
                decision = SelectionDecision(
                    selected_idea_ids=[shortlist[0]["id"]], notes="fallback: top opportunity score"
                )
            by_id = {c["id"]: c for c in shortlist}
            for rej in decision.rejected:
                if rej.idea_id in by_id and rej.idea_id not in decision.selected_idea_ids:
                    self.svc.db.update(
                        "ideas",
                        rej.idea_id,
                        {"eic_notes": f"rejected: {rej.reason[:300]}", "updated_at": now_iso()},
                    )
            for sid in decision.selected_idea_ids:
                if sid in by_id:
                    span.output_refs["selected"] = sid
                    return by_id[sid]
            if not decision.selected_idea_ids:
                span.output_refs["selected"] = None
                return None
        return shortlist[0]
