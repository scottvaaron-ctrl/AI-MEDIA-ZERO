"""Orchestrator: one autonomous cycle = research -> ideas -> select -> write/QA -> produce -> publish -> metrics -> comments -> learn.

Each stage is individually runnable from the CLI. The orchestrator never bypasses the
budget manager or the kill switch: it only calls agents, which only call providers.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from aimz.agents.analyst import AnalystAgent
from aimz.agents.comments import CommentAgent
from aimz.agents.critic import CriticAgent
from aimz.agents.editor import EditorInChief
from aimz.agents.factcheck import FactCheckAgent
from aimz.agents.ideation import IdeationAgent
from aimz.agents.producer import ProducerAgent
from aimz.agents.publisher import PublishStage
from aimz.agents.research import ResearchAgent
from aimz.agents.script import ScriptAgent
from aimz.core.errors import BudgetDenied, KillSwitchEngaged
from aimz.core.runs import RunContext
from aimz.experiments.engine import ExperimentEngine
from aimz.providers.registry import Services
from aimz.strategy.memory import StrategyMemory
from aimz.util import dumps, now_iso

log = logging.getLogger("aimz.orchestrator")

STAGES = ["research", "ideas", "select", "write", "produce", "publish", "metrics", "comments", "learn"]


class Orchestrator:
    def __init__(self, svc: Services, seed: int | None = None):
        self.svc = svc
        cfg = svc.config
        self.strategy = StrategyMemory(
            svc.db,
            svc.env.config_dir,
            cfg.starting_families,
            float(cfg.get("allocation.cold_start_explore_ratio", 0.7)),
            int(cfg.get("allocation.decay_start_sample", 12)),
        )
        self.experiments = ExperimentEngine(
            svc.db,
            int(cfg.get("experiments.default_min_sample_per_arm", 8)),
            int(cfg.get("experiments.max_concurrent", 2)),
            float(cfg.get("experiments.min_confidence", 0.85)),
            hook_types=cfg.hook_types,
        )
        rng = random.Random(seed)
        self.research = ResearchAgent(svc, self.strategy)
        self.ideation = IdeationAgent(svc, self.strategy)
        self.editor = EditorInChief(svc, self.strategy, self.experiments, rng)
        self.script = ScriptAgent(svc, self.strategy)
        self.factcheck = FactCheckAgent(svc, self.strategy)
        self.critic = CriticAgent(svc, self.strategy)
        self.producer = ProducerAgent(svc, self.strategy)
        self.publisher = PublishStage(svc, self.strategy)
        self.analyst = AnalystAgent(svc, self.strategy, self.experiments)
        self.comments = CommentAgent(svc, self.strategy)

    # -- full cycle ---------------------------------------------------------------------
    def cycle(self, stages: list[str] | None = None, produce_limit: int | None = None) -> dict[str, Any]:
        stages = stages or STAGES
        with self.svc.tracker.run("cycle") as run:
            state = self.strategy.current()
            if "research" in stages:
                self._safe(run, "research", lambda: self.research.run(run))
            if "ideas" in stages:
                self._safe(run, "ideas", lambda: self.ideation.run(run, state))
            if "select" in stages:
                self._safe(
                    run,
                    "select",
                    lambda: self.editor.select(run, state, self.svc.analytics_store.video_performance()),
                )
            if "write" in stages:
                self._safe(run, "write", lambda: self.write_selected(run, state))
            if "produce" in stages:
                self._safe(run, "produce", lambda: self.produce_approved(run, produce_limit))
            if "publish" in stages:
                self._safe(run, "publish", lambda: self.publish_rendered(run))
            if "metrics" in stages:
                self._safe(run, "metrics", lambda: self.analyst.collect_metrics(run))
            if "comments" in stages:
                self._safe(run, "comments", lambda: self.comments.run(run))
            if "learn" in stages:
                self._safe(run, "learn", lambda: self.analyst.learn(run, self.strategy.current()))
            run.note("finished_at", now_iso())
            return dict(run.summary)

    def _safe(self, run: RunContext, stage: str, fn: Any) -> Any:
        try:
            return fn()
        except KillSwitchEngaged as exc:
            log.warning("stage %s blocked by kill switch: %s", stage, exc)
            run.note(f"{stage}_blocked", str(exc))
        except BudgetDenied as exc:
            log.error("stage %s denied by budget: %s", stage, exc)
            run.note(f"{stage}_denied", str(exc))
        except Exception as exc:
            log.exception("stage %s failed", stage)
            self.svc.tracker.record_error(run.id, "orchestrator", stage, exc)
            run.note(f"{stage}_error", f"{type(exc).__name__}: {exc}"[:300])
        return None

    # -- write / QA loop --------------------------------------------------------------------
    def write_selected(self, run: RunContext, state: dict[str, Any], limit: int | None = None) -> list[str]:
        ideas = [
            dict(r)
            for r in self.svc.db.query(
                "SELECT * FROM ideas WHERE status='selected' ORDER BY opportunity_score DESC"
            )
        ]
        limit = limit or int(self.svc.config.get("pipeline.max_productions_per_cycle", 2))
        approved: list[str] = []
        for idea in ideas[:limit]:
            sid = self.write_one(run, idea, state)
            if sid:
                approved.append(sid)
        run.note("write", {"approved_scripts": len(approved)})
        return approved

    def write_one(self, run: RunContext, idea: dict[str, Any], state: dict[str, Any]) -> str | None:
        max_rounds = int(self.svc.config.get("pipeline.max_revision_rounds", 2))
        sources = self.script.load_sources(self.script.idea_source_ids(idea))
        if not sources:
            self.svc.db.update(
                "ideas",
                idea["id"],
                {"status": "rejected", "eic_notes": "no stored sources", "updated_at": now_iso()},
            )
            return None
        feedback: list[str] | None = None
        previous = None
        script_id = None
        target_s = int(idea.get("suggested_runtime_s") or 45)
        for round_no in range(1, max_rounds + 2):
            script_id, draft = self.script.write(
                run, idea, sources, state, feedback, previous, version=round_no
            )
            if round_no > max_rounds:
                # Final round: unsupported specifics the model would not drop are removed deterministically.
                removed = self.factcheck.enforce_final(draft, sources)
                if removed:
                    log.info(
                        "final round: removed %d sentence(s) with unsupported specifics from %s",
                        removed,
                        script_id,
                    )
            fc_result, revisions = self.factcheck.check(run, script_id, draft, sources)
            sf = self.svc.config.short_form
            ok_len, len_msg = self.script.word_budget_ok(
                draft, target_s, int(sf.get("min_seconds", 20)), int(sf.get("max_seconds", 90))
            )
            if not ok_len:
                revisions.append(len_msg)
            critique = self.critic.review(run, script_id, draft, idea, fc_result, sources)
            required = [*revisions, *critique.required_revisions]
            if critique.passed and not required:
                status = "needs_owner_review" if critique.elevated_review_required else "approved"
                self._persist_draft(script_id, draft, status)
                return script_id if status == "approved" else None
            if round_no > max_rounds:
                break
            feedback = required + [f"Critic score {critique.score}: " + "; ".join(critique.problems[:4])]
            previous = draft
        # revision budget exhausted without a pass
        self.svc.db.update("scripts", script_id or "", {"status": "rejected", "updated_at": now_iso()})
        self.svc.db.update(
            "ideas",
            idea["id"],
            {"status": "rejected", "eic_notes": "failed QA after revisions", "updated_at": now_iso()},
        )
        return None

    def _persist_draft(self, script_id: str, draft: Any, status: str) -> None:
        narration = " ".join(b.narration for b in draft.beats)
        self.svc.db.update(
            "scripts",
            script_id,
            {
                "beats_json": dumps([b.model_dump() for b in draft.beats]),
                "narration_text": narration,
                "word_count": len(narration.split()),
                "status": status,
                "updated_at": now_iso(),
            },
        )

    # -- produce / publish ---------------------------------------------------------------------
    def produce_approved(self, run: RunContext, limit: int | None = None) -> list[str]:
        limit = limit or int(self.svc.config.get("pipeline.max_productions_per_cycle", 2))
        scripts = [
            dict(r)
            for r in self.svc.db.query(
                "SELECT s.* FROM scripts s WHERE s.status='approved' AND NOT EXISTS (SELECT 1 FROM videos v WHERE v.script_id=s.id AND v.status<>'failed') ORDER BY s.created_at LIMIT ?",
                [limit],
            )
        ]
        out: list[str] = []
        for s in scripts:
            try:
                out.append(self.producer.produce(run, s["id"]))
            except Exception as exc:
                log.exception("production failed for %s", s["id"])
                self.svc.tracker.record_error(run.id, "producer", "produce", exc)
        run.note("produce", {"rendered": len(out)})
        return out

    def publish_rendered(self, run: RunContext) -> list[dict[str, Any]]:
        videos = [
            dict(r)
            for r in self.svc.db.query(
                "SELECT * FROM videos WHERE status IN ('rendered','approved') ORDER BY created_at"
            )
        ]
        results = []
        for v in videos:
            results += self.publisher.publish(run, v["id"], owner_approved=(v["status"] == "approved"))
        return results
