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
from aimz.core.lock import exclusive_lock
from aimz.core.runs import RunContext
from aimz.experiments.engine import ExperimentEngine
from aimz.providers.base import Publisher
from aimz.providers.registry import Services
from aimz.strategy.memory import StrategyMemory
from aimz.util import dumps, iso_ago, now_iso

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
        self.producer = ProducerAgent(svc, self.strategy, rng)
        self.publisher = PublishStage(svc, self.strategy)
        self.analyst = AnalystAgent(svc, self.strategy, self.experiments)
        self.comments = CommentAgent(svc, self.strategy)

    # -- full cycle ---------------------------------------------------------------------
    def cycle(self, stages: list[str] | None = None, produce_limit: int | None = None) -> dict[str, Any]:
        """Run one cycle. Raises ``CycleAlreadyRunning`` if another cycle holds the lock."""
        with exclusive_lock(self.svc.env.data_dir / "cycle.lock"):
            return self._cycle(stages or STAGES, produce_limit)

    def _cycle(self, stages: list[str], produce_limit: int | None) -> dict[str, Any]:
        with self.svc.tracker.run("cycle") as run:
            run.note("run_id", run.id)
            # Holding the lock means no other cycle is alive, so anything a dead one left half-done is
            # finished or set back before this cycle starts on new work.
            self._safe(run, "recover", lambda: self.recover(run))
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

    # -- recovery -------------------------------------------------------------------------
    def recover(self, run: RunContext) -> dict[str, int]:
        """Close runs that never finished and return the work they left half-done to the queue.

        A shutdown, sleep or crash stops a cycle with no chance to clean up. Nothing it was working on
        is dropped: ideas go back to ``selected``, a video caught mid-render is marked failed so its
        (still approved) script renders again, and an upload nobody can finish becomes a failure for the
        checked retry. Each abandoned run is recorded as an error, so this cycle ends ``degraded`` and the
        owner is alerted once.
        """
        db, tracker = self.svc.db, self.svc.tracker
        out = {"runs": 0, "ideas": 0, "videos": 0, "uploads": 0}
        stale = [
            dict(r)
            for r in db.query(
                "SELECT * FROM runs WHERE status='running' AND id<>? AND (kind='cycle' OR started_at < ?)",
                [run.id, iso_ago(hours=4)],
            )
        ]
        for r in stale:
            db.update(
                "runs",
                r["id"],
                {
                    "status": "abandoned",
                    "ended_at": now_iso(),
                    "error": f"never finished (shutdown, sleep or crash); closed by {run.id}",
                },
            )
            tracker.record_error(
                run.id,
                "orchestrator",
                "recover",
                RuntimeError(f"run {r['id']} (started {r['started_at']}) never finished; marked abandoned"),
            )
            out["runs"] += 1
        stale_ids = [r["id"] for r in stale]
        if stale_ids:
            marks = ",".join("?" * len(stale_ids))
            db.execute(
                f"UPDATE scripts SET status='interrupted', updated_at=? WHERE run_id IN ({marks}) "
                "AND status IN ('draft','factchecked','qa_passed','qa_failed')",
                [now_iso(), *stale_ids],
            )
        # An idea marked 'scripted' with no live script behind it was being written when its run died.
        for idea in db.query(
            "SELECT * FROM ideas i WHERE i.status='scripted' AND i.updated_at < ? AND NOT EXISTS ("
            "SELECT 1 FROM scripts s WHERE s.idea_id=i.id AND s.status IN ('approved','needs_owner_review','parked'))",
            [iso_ago(hours=1)],
        ):
            self._set_aside(run, dict(idea), RuntimeError("the run writing it never finished"))
            out["ideas"] += 1
        # A render that stopped mid-way: the script is still approved, so it renders again this cycle.
        for v in db.query(
            "SELECT id FROM videos WHERE status='rendering' AND updated_at < ?", [iso_ago(hours=2)]
        ):
            db.update(
                "videos",
                v["id"],
                {"status": "failed", "error": "interrupted mid-render", "updated_at": now_iso()},
            )
            out["videos"] += 1
        # An upload that no poll can finish. The post may or may not exist, so it goes to the checked retry.
        for pub in db.query("SELECT * FROM publications WHERE status='uploading'"):
            publisher = self.svc.publishers.get(pub["platform"])
            can_poll = publisher is not None and type(publisher).poll is not Publisher.poll
            if pub["updated_at"] < iso_ago(hours=24 if can_poll else 1):
                self.publisher.record_failure(
                    run, pub["id"], "upload interrupted before the platform answered", "uncertain"
                )
                out["uploads"] += 1
        if any(out.values()):
            run.note("recovered", out)
        return out

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
        interrupted = 0
        for idea in ideas[:limit]:
            try:
                sid = self.write_one(run, idea, state)
            except (KillSwitchEngaged, BudgetDenied) as exc:
                self._set_aside(run, idea, exc, count=False)
                raise
            except Exception as exc:
                log.warning("writing %s failed for a technical reason: %s", idea["id"], exc)
                self._set_aside(run, idea, exc)
                interrupted += 1
                break  # an outage hits every idea alike; stop rather than burn through the queue
            if sid:
                approved.append(sid)
        note: dict[str, Any] = {"approved_scripts": len(approved)}
        if interrupted:
            note["interrupted"] = interrupted
        run.note("write", note)
        return approved

    def _set_aside(
        self, run: RunContext, idea: dict[str, Any], exc: BaseException, count: bool = True
    ) -> None:
        """A technical failure is not an editorial verdict: the idea goes back to ``selected`` for the next
        cycle. After ``pipeline.max_technical_failures`` in a row it is parked for the owner, not rejected."""
        db = self.svc.db
        db.execute(
            "UPDATE scripts SET status='interrupted', updated_at=? WHERE idea_id=? AND run_id=? "
            "AND status IN ('draft','factchecked','qa_passed','qa_failed')",
            [now_iso(), idea["id"], run.id],
        )
        fresh = db.get("ideas", idea["id"])
        n = int((fresh["tech_failures"] if fresh else 0) or 0) + (1 if count else 0)
        limit = int(self.svc.config.get("pipeline.max_technical_failures", 5))
        why = f"{type(exc).__name__}: {exc}"[:200]
        if count and n >= limit:
            status, note = "parked", f"parked after {n} technical failures (last: {why})"
            self.svc.tracker.record_error(
                run.id, "orchestrator", "write", RuntimeError(f"idea {idea['id']} {note}")
            )
        else:
            status, note = "selected", f"technical failure {n}/{limit}, retried next cycle: {why}"
        db.update(
            "ideas",
            idea["id"],
            {"status": status, "tech_failures": n, "eic_notes": note, "updated_at": now_iso()},
        )

    def write_one(self, run: RunContext, idea: dict[str, Any], state: dict[str, Any]) -> str | None:
        max_rounds = int(self.svc.config.get("pipeline.max_revision_rounds", 2))
        self.research.enrich(run, self.script.idea_source_ids(idea))
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
                draft,
                target_s,
                int(sf.get("min_seconds", 10)),
                int(sf.get("max_seconds", 180)),
                enforce_target=self.script.runtime_under_test(idea),
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
            if not required and not critique.problems:
                break  # nothing to act on: another round would return the same draft
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
        # Backlog guard: under the owner's posting limits, rendering faster than posting only piles up
        # videos. Production pauses; the limits never loosen.
        full, why = self.publisher.gate().backlog_full()
        if full:
            run.note("produce", {"rendered": 0, "paused": why})
            return []
        limit = limit or int(self.svc.config.get("pipeline.max_productions_per_cycle", 2))
        scripts = [
            dict(r)
            for r in self.svc.db.query(
                "SELECT s.* FROM scripts s WHERE s.status='approved' AND NOT EXISTS (SELECT 1 FROM videos v WHERE v.script_id=s.id AND v.status NOT IN ('failed','superseded')) ORDER BY s.created_at LIMIT ?",
                [limit],
            )
        ]
        out: list[str] = []
        max_renders = int(self.svc.config.get("pipeline.max_render_attempts", 3))
        for s in scripts:
            try:
                out.append(self.producer.produce(run, s["id"]))
            except Exception as exc:
                log.exception("production failed for %s", s["id"])
                self.svc.tracker.record_error(run.id, "producer", "produce", exc)
                # A failed render is retried next cycle (the script stays approved). A script that keeps
                # failing is parked for the owner rather than retried forever.
                failures = self.svc.db.count("videos", "script_id=? AND status='failed'", [s["id"]])
                if failures >= max_renders:
                    self.svc.db.update("scripts", s["id"], {"status": "parked", "updated_at": now_iso()})
                    self.svc.tracker.record_error(
                        run.id,
                        "producer",
                        "produce",
                        RuntimeError(f"script {s['id']} parked after {failures} failed renders"),
                    )
        run.note("produce", {"rendered": len(out)})
        return out

    def publish_rendered(self, run: RunContext) -> list[dict[str, Any]]:
        """Post what is waiting, oldest first, within the owner's posting limits (``pipeline/posting.py``)."""
        self.publisher.poll_pending(run)
        results: list[dict[str, Any]] = []
        with self.publisher.posting(run) as session:
            if not session.allowed:
                run.note("posting", {"allowed": False, "reason": session.reason})
                return results
            self.publisher.retry_due(run)
            api = self.publisher.api_platforms()
            for v in self.publisher.postable_videos():
                if api and session.full(api):
                    break
                results += self.publisher.publish(run, v["id"], owner_approved=(v["status"] == "approved"))
            run.note("posting", {"allowed": True, "posts": dict(session.counts)})
        return results
