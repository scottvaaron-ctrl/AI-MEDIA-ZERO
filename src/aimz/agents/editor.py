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
from aimz.domain.models import GenreFit, SelectionDecision, SelectionDecisionWithBreaks
from aimz.experiments import niches
from aimz.experiments.allocation import AllocationSlot, plan_allocation
from aimz.experiments.engine import ExperimentEngine
from aimz.guideline import Guideline
from aimz.util import dumps, iso_ago, loads, now_iso

GOALS = (
    "Optimization goals in priority order: 1) viewer retention, 2) shares per 1,000 views, 3) followers/subscribers gained per 1,000 views, "
    "4) returning viewers, 5) completion rate, 6) production efficiency, 7) long-term audience trust, 8) future monetization potential. "
    "Number of uploads is NOT a goal. Reject ideas that do not deserve a video."
)
# The genre judge asks at most this many ideas per select() call, to bound local-model time.
GENRE_JUDGE_CAP = 12
SHORTLIST_SIZE = 4


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
        g = self.experiments.guideline  # loaded once per process, with the engine
        if g is not None:
            # The explore share is 0 under a guideline: every slot follows it, and the one experiment's
            # arms ride on these slots (spec 2026-10-07 section 3).
            slots = [
                AllocationSlot("guideline", None, f"follows the channel guideline v{g.version}")
                for _ in range(max(0, k))
            ]
        else:
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
        # Genre judge calls left in this select() call (spec 2026-10-08 section 4.2).
        budget = [GENRE_JUDGE_CAP]
        gate_rejected: set[str] = set()
        gate_skipped: set[str] = set()
        gate_ran = [False]

        def shortlist(ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if g is None or not g.genre.strip():
                return ideas[:SHORTLIST_SIZE]
            gate_ran[0] = True
            passing = self._genre_gate(run, g, ideas, budget)
            gate_rejected.update(c["id"] for c in ideas if c.get("status") == "rejected")
            gate_skipped.update(c["id"] for c in ideas if c.get("genre_skipped"))
            # Off-genre ideas are rejected and unreadable answers skipped: neither is offered again this call.
            used.update(c["id"] for c in ideas if c.get("status") == "rejected" or c.get("genre_skipped"))
            return passing

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
            topic_exp = self.experiments.topic_experiment()
            if topic_exp is not None:
                # A topic-rule experiment: ideation labelled each idea's arm. Offer the arm with fewer
                # ideas in production first, so the arms stay balanced.
                made = self.experiments.topic_arm_counts(
                    topic_exp, ("selected", "scripted", "produced", "published")
                )
                arm = "treatment" if made["treatment"] < made["control"] else "control"
                if made["treatment"] == made["control"]:
                    arm = self.rng.choice(["control", "treatment"])
                on_arm = [
                    c
                    for c in pool
                    if c.get("experiment_id") == topic_exp["id"] and c.get("experiment_arm") == arm
                ]
                pool = on_arm or pool
            mode, reason = slot.mode, slot.reason
            choice: dict[str, Any] | None = None
            short = shortlist(pool)
            if short:  # an empty gated shortlist is no choice; the editor is not asked
                choice, turned_down = self._choose(run, strategy_state, short, mode, reason)
                used.update(turned_down)
            if choice is None:
                # The editor judged no idea in this family worth a video. Offer the slot to the rest.
                rest = [c for c in cands if c["id"] not in used and c["content_family"] != slot.family]
                if not rest:
                    continue
                mode, reason = "fallback", f"{slot.family} had no idea worth making; open shortlist"
                short = shortlist(rest)
                if not short:
                    continue
                choice, turned_down = self._choose(run, strategy_state, short, mode, reason)
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
            if exp is not None and topic_exp is not None and exp["id"] == topic_exp["id"]:
                # The arm was set at ideation (the idea was written under it); an idea from before the
                # experiment stays outside it.
                exp = None
            if exp is not None:
                arm, value = self.experiments.assign_arm(exp, self.rng)
                updates["experiment_id"] = exp["id"]
                updates["experiment_arm"] = arm
                if exp["variable"] == "hook_type":
                    # The idea's hook line was written for its old type; the script agent writes the
                    # opening from this type instead (ScriptAgent.hook_type_under_test).
                    updates["hook_type"] = value
                elif exp["variable"] == "runtime" or (exp["variable"] == "G-runtime" and arm == "treatment"):
                    # A G-runtime control idea follows the guideline range; its treatment is made at the
                    # tested runtime. G-<setting> treatments reach the render through the producer
                    # (ExperimentEngine.setting_override); rule variants through the prompts (variant_for).
                    with contextlib.suppress(ValueError):
                        updates["suggested_runtime_s"] = int(value)
            self.svc.db.update("ideas", choice["id"], updates)
        note: dict[str, Any] = {"selected": len(selected), "slots": [f"{s.mode}:{s.family}" for s in slots]}
        if gate_ran[0]:
            note["genre"] = {
                "judged": GENRE_JUDGE_CAP - budget[0],
                "rejected": len(gate_rejected),
                "skipped": len(gate_skipped),
            }
        run.note("selection", note)
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
        amap = niches.aliases(self.svc.db)
        for r in self.svc.db.query(
            "SELECT v.id, i.content_family FROM videos v JOIN ideas i ON i.id = v.idea_id WHERE v.status='published'"
        ):
            if r["id"] not in scored:
                fam = str(niches.canonical(r["content_family"], amap))
                out[fam] = out.get(fam, 0) + 1
        return out

    def _genre_gate(
        self, run: RunContext, g: Guideline, pool: list[dict[str, Any]], budget: list[int]
    ) -> list[dict[str, Any]]:
        """Up to SHORTLIST_SIZE ideas from ``pool``, in order, that fit the guideline genre.

        Each idea not yet judged under ``g.version`` costs one judge call from ``budget`` (a one-element
        counter shared across one select() call). Off-genre ideas are rejected for good; on-genre ideas are
        marked with the version so they are not asked again until it changes; an unreadable answer skips the
        idea this cycle. The pool's dicts are updated in place to match (spec 2026-10-08 section 4.2).
        """
        passing: list[dict[str, Any]] = []
        for c in pool:
            if len(passing) >= SHORTLIST_SIZE:
                break
            if c.get("status") == "rejected" or c.get("genre_skipped"):
                continue
            if c.get("genre_checked_version") == g.version:
                passing.append(c)
                continue
            if budget[0] <= 0:
                continue  # out of judge calls: only ideas already judged under this version can pass
            budget[0] -= 1
            user = (
                f"Channel genre: {g.genre}\n\n"
                f"Idea: {c['title']}\nPremise: {(c.get('premise') or '')[:300]}\nHook: {(c.get('hook') or '')[:200]}\n\n"
                "Does this idea fit the channel genre? Answer fits true or false and a one-line reason."
            )
            with self.svc.tracker.agent(run, self.name, "genre_fit", {"idea": c["id"]}) as span:
                try:
                    verdict = self.ask(
                        self.pctx(run, span=span),
                        "Editor-in-Chief",
                        user,
                        GenreFit,
                        "eic_genre_fit",
                        temperature=0.0,
                        max_tokens=200,
                    )
                except Exception as exc:  # the LLM layer already retried; skip the idea this cycle
                    self.log.warning("genre judge unreadable for %s (%s); skipped this cycle", c["id"], exc)
                    c["genre_skipped"] = True
                    span.output_refs["fits"] = None
                    continue
                span.output_refs["fits"] = verdict.fits
            if verdict.fits:
                self.svc.db.update(
                    "ideas", c["id"], {"genre_checked_version": g.version, "updated_at": now_iso()}
                )
                c["genre_checked_version"] = g.version
                passing.append(c)
                continue
            prev = loads(c.get("guideline_breaks"), [])
            breaks = [b for b in (prev if isinstance(prev, list) else []) if b != "genre"] + ["genre"]
            notes = f"off genre: {verdict.reason}"[:300]
            self.svc.db.update(
                "ideas",
                c["id"],
                {
                    "status": "rejected",
                    "guideline_breaks": dumps(breaks),
                    "eic_notes": notes,
                    "updated_at": now_iso(),
                },
            )
            c.update(status="rejected", guideline_breaks=dumps(breaks), eic_notes=notes)
        return passing

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
        # Under a topic-rule experiment, treatment ideas were written under the variant on purpose: they
        # are judged by the variant text, never rejected for breaking the rule under test.
        texp = self.experiments.topic_experiment()
        tested_rule = str(texp["variable"]) if texp else ""
        variant_ideas = {
            c["id"]
            for c in shortlist
            if texp and c.get("experiment_id") == texp["id"] and c.get("experiment_arm") == "treatment"
        }
        lines = []
        for c in shortlist:
            line = f"- {c['id']} | score {c['opportunity_score']} | family {c['content_family']} | hook_type {c['hook_type']} | {c['title']}\n  Premise: {c['premise'][:300]}\n  Hook: {c['hook'][:200]}"
            if c["id"] in variant_ideas and texp:
                line += (
                    f"\n  Written under a test variant of {tested_rule}; judge it by this text instead of "
                    f"{tested_rule}: {texp['treatment']}"
                )
            lines.append(line)
        g = self.experiments.guideline  # loaded once per process, with the engine
        topic_rules = g.rules_for("topic") if g else []
        gtext = self.guideline_text(("topic",), g=g)
        if gtext:
            gtext += "In breaks, list one 'idea_id:rule_id' entry for every shortlist idea that breaks a topic rule above.\n\n"
        user = (
            f"{GOALS}\n\n{self.strategy.prompt_summary(strategy_state)}\n\n"
            f"Allocation for this slot: {mode.upper()} ({reason}).\n"
            "Select up to 1 idea from the shortlist that best serves the goals and the allocation. Reject the rest with a one-line reason. "
            "If none deserves a video, select nothing.\n\n" + gtext + "Shortlist:\n" + "\n".join(lines)
        )
        schema = SelectionDecisionWithBreaks if topic_rules else SelectionDecision
        with self.svc.tracker.agent(
            run, self.name, "select", {"shortlist": [c["id"] for c in shortlist]}
        ) as span:
            try:
                decision = self.ask(
                    self.pctx(run, span=span),
                    "Editor-in-Chief",
                    user,
                    schema,
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
            broken = self._topic_breaks(decision, by_id, {r.id for r in topic_rules})
            for cid in variant_ideas & set(broken):
                broken[cid] = [rid for rid in broken[cid] if rid != tested_rule]
            broken = {k: v for k, v in broken.items() if v}
            turned_down: set[str] = set()
            for cid, ids in broken.items():
                self.svc.db.update(
                    "ideas",
                    cid,
                    {
                        "status": "rejected",
                        "guideline_breaks": dumps(ids),
                        "eic_notes": f"breaks guideline {', '.join(ids)}",
                        "updated_at": now_iso(),
                    },
                )
                turned_down.add(cid)
            by_id = {k: v for k, v in by_id.items() if k not in broken}
            named = [s for s in decision.selected_idea_ids if s not in broken]
            chosen = next((by_id[s] for s in named if s in by_id), None)
            if chosen is None and named:
                chosen = next(
                    iter(by_id.values()), None
                )  # the model named an id that is not on the shortlist
            # Selecting nothing is the editor's verdict on the whole shortlist; a pick that broke a rule is not.
            nothing_selected = not decision.selected_idea_ids
            reasons = {r.idea_id: r.reason for r in decision.rejected if r.idea_id in by_id}
            for cid in by_id:
                if chosen is not None and cid == chosen["id"]:
                    continue
                if chosen is None and nothing_selected:
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

    @staticmethod
    def _topic_breaks(decision: Any, by_id: dict[str, Any], topic_ids: set[str]) -> dict[str, list[str]]:
        """Shortlist ideas the editor says break a topic rule: ``{idea_id: [rule ids]}``."""
        out: dict[str, list[str]] = {}
        for entry in getattr(decision, "breaks", []) or []:
            idea_id, _, rule_id = str(entry).partition(":")
            idea_id, rule_id = idea_id.strip(), rule_id.strip()
            if idea_id in by_id and rule_id in topic_ids and rule_id not in out.setdefault(idea_id, []):
                out[idea_id].append(rule_id)
        return {k: v for k, v in out.items() if v}
