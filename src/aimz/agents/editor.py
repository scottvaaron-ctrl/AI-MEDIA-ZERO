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
from aimz.util import iso_ago, now_iso

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
            "WHERE status='candidate' AND created_at < ?",
            [now_iso(), iso_ago(days=max_age_days)],
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
        # Families the AI named during ideation enter strategy memory only once a video is measured, so
        # the candidates' families are added here as new and unmeasured. Otherwise a family can never be tried.
        families = dict(strategy_state["families"])
        for c in cands:
            if c.get("content_family"):
                families.setdefault(c["content_family"], {"status": "new", "n": 0})
        # Slots only go to families that have a candidate to fill them, and a family whose video is still
        # being made or measured counts as already being tried. Otherwise the least-sampled family, with one
        # stale idea the editor keeps turning down, won the explore slot every cycle and nothing was made.
        available = {str(c["content_family"]) for c in cands if c.get("content_family")}
        slots = plan_allocation(
            perf_rows,
            families,
            k,
            alloc_cfg,
            self.rng,
            strategy_state.get("explore_ratio"),
            available=available,
            pending=self.in_flight(perf_rows),
        )

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
            mode, reason = slot.mode, slot.reason
            choice, turned_down = self._choose(run, strategy_state, pool[:4], mode, reason)
            used.update(turned_down)
            if choice is None:
                # The editor judged no idea in this family worth a video. Offer the slot to the rest.
                rest = [c for c in cands if c["id"] not in used and c["content_family"] != slot.family]
                if not rest:
                    continue
                mode, reason = "fallback", f"{slot.family} had no idea worth making; open shortlist"
                choice, turned_down = self._choose(run, strategy_state, rest[:4], mode, reason)
                used.update(turned_down)
                if choice is None:
                    continue
            used.add(choice["id"])
            selected.append(choice["id"])
            updates: dict[str, Any] = {
                "status": "selected",
                "allocation_mode": mode,
                "eic_notes": f"{mode}: {reason}",
                "updated_at": now_iso(),
            }
            # experiment arm assignment: force the variable's value onto the idea. One experiment per idea
            # keeps attribution clean; the least-filled one goes first so a second experiment is not starved.
            exp = self.experiments.next_for_assignment()
            if exp is not None:
                arm, value = self.experiments.assign_arm(exp, self.rng)
                updates["experiment_id"] = exp["id"]
                updates["experiment_arm"] = arm
                if exp["variable"] in {"hook_type", "target_platform", "format"}:
                    updates[exp["variable"]] = value
                elif exp["variable"] == "runtime":
                    with contextlib.suppress(ValueError):
                        updates["suggested_runtime_s"] = int(value)
            self.svc.db.update("ideas", choice["id"], updates)
        run.note("selection", {"selected": len(selected), "slots": [f"{s.mode}:{s.family}" for s in slots]})
        return selected

    def in_flight(self, perf_rows: list[dict[str, Any]]) -> dict[str, int]:
        """Per family: ideas being written or rendered, plus published videos not scored yet."""
        out: dict[str, int] = {}
        for r in self.svc.db.query(
            "SELECT content_family, COUNT(*) AS n FROM ideas WHERE status IN ('selected','scripted','produced') "
            "GROUP BY content_family"
        ):
            out[r["content_family"]] = out.get(r["content_family"], 0) + int(r["n"])
        scored = {r.get("video_id") for r in perf_rows if r.get("score") is not None}
        for r in self.svc.db.query(
            "SELECT v.id, i.content_family FROM videos v JOIN ideas i ON i.id = v.idea_id WHERE v.status='published'"
        ):
            if r["id"] not in scored:
                out[r["content_family"]] = out.get(r["content_family"], 0) + 1
        return out

    def _choose(
        self,
        run: RunContext,
        strategy_state: dict[str, Any],
        shortlist: list[dict[str, Any]],
        mode: str,
        reason: str,
    ) -> tuple[dict[str, Any] | None, set[str]]:
        """Return the chosen idea (or None) and the ids the editor rejected outright.

        Picking one idea means passing over the others, which says nothing absolute about them, so they
        stay candidates. Selecting *nothing* is the editor's verdict that none deserves a video: those
        ideas are marked ``rejected`` so they are not offered again every cycle.
        """
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
            chosen = next((by_id[s] for s in decision.selected_idea_ids if s in by_id), None)
            if chosen is None and decision.selected_idea_ids:
                chosen = shortlist[0]  # the model named an id that is not on the shortlist
            reasons = {r.idea_id: r.reason for r in decision.rejected if r.idea_id in by_id}
            turned_down: set[str] = set()
            for cid in by_id:
                if chosen is not None and cid == chosen["id"]:
                    continue
                if chosen is None:
                    why = reasons.get(cid) or "the editor selected nothing from this shortlist"
                    self.svc.db.update(
                        "ideas",
                        cid,
                        {
                            "status": "rejected",
                            "eic_notes": f"rejected: {why[:300]}",
                            "updated_at": now_iso(),
                        },
                    )
                    turned_down.add(cid)
                elif cid in reasons:
                    self.svc.db.update(
                        "ideas",
                        cid,
                        {"eic_notes": f"passed over: {reasons[cid][:300]}", "updated_at": now_iso()},
                    )
            span.output_refs["selected"] = chosen["id"] if chosen else None
        return chosen, turned_down
