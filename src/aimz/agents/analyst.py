"""Analyst / Strategist: collects metrics, evaluates experiments, and rewrites strategy memory.

This is the feedback loop. Every cycle:

1. pull metrics from any enabled remote analytics provider (manual entries already live in the store);
2. compute per-family / per-hook / per-runtime / per-source statistics from *measured* videos;
3. evaluate running experiments (no conclusions below the minimum sample);
4. ask the model for a StrategyUpdate given those numbers, then merge it with guard-rails;
5. seed a first experiment at cold start if none is running;
6. save a new strategy version and a daily learning summary.

The next cycle's Editor-in-Chief and Ideation agents consume the saved strategy.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import ExperimentProposal, StrategyUpdate
from aimz.experiments.allocation import family_stats
from aimz.experiments.engine import ExperimentEngine
from aimz.util import now_iso

COLD_START_EXPERIMENT = ExperimentProposal(
    name="Numeric vs narrative hook on retention",
    hypothesis="Opening with a specific number improves early retention compared with a narrative opening.",
    variable="hook_type",
    control="narrative_hook",
    treatment="numeric_hook",
    primary_kpi="avg_percent_viewed",
    secondary_kpi="shares_per_1000",
)


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def group_stats(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("score") is None or not r.get(key):
            continue
        groups[str(r[key])].append(float(r["score"]))
    return {k: {"n": len(v), "mean": _mean(v)} for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def runtime_buckets(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    def bucket(d: float | None) -> str:
        if d is None:
            return "unknown"
        if d < 35:
            return "<35s"
        if d < 60:
            return "35-60s"
        return ">60s"

    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("score") is None:
            continue
        groups[bucket(r.get("duration_s"))].append(float(r["score"]))
    return {k: {"n": len(v), "mean": _mean(v)} for k, v in groups.items()}


class AnalystAgent(Agent):
    name = "analyst"

    def __init__(self, svc: Any, strategy: Any, experiments: ExperimentEngine):
        super().__init__(svc, strategy)
        self.experiments = experiments

    # -- 1. metrics ---------------------------------------------------------------------
    def collect_metrics(self, run: RunContext) -> int:
        n = 0
        pubs = [
            dict(r)
            for r in self.svc.db.query(
                "SELECT * FROM publications WHERE status IN ('uploaded','published') AND platform_video_id IS NOT NULL"
            )
        ]
        for pub in pubs:
            provider = self.svc.remote_analytics.get(pub["platform"])
            if provider is None:
                continue
            with self.svc.tracker.agent(
                run, self.name, f"metrics:{pub['platform']}", {"publication_id": pub["id"]}
            ) as span:
                try:
                    snap = provider.fetch_metrics(self.pctx(run, pub["video_id"], span), pub)
                except Exception as exc:
                    self.log.warning("metrics fetch failed for %s: %s", pub["id"], exc)
                    self.svc.tracker.record_error(run.id, self.name, "metrics", exc)
                    continue
                if snap is not None:
                    self.svc.analytics_store.record(pub, snap, source="api")
                    n += 1
        run.note("metrics_collected", n)
        return n

    # -- 2-6. learning --------------------------------------------------------------------
    def source_stats(self) -> list[dict[str, Any]]:
        rows = self.svc.db.query(
            "SELECT s.name, s.kind, s.items_yielded, s.ideas_yielded, s.videos_yielded, s.last_status FROM sources s WHERE s.enabled=1 ORDER BY s.videos_yielded DESC, s.ideas_yielded DESC"
        )
        return [dict(r) for r in rows]

    def evidence(self, perf_rows: list[dict[str, Any]], strategy_state: dict[str, Any]) -> dict[str, Any]:
        fam = family_stats(perf_rows, strategy_state["families"])
        return {
            "measured_videos": sum(1 for r in perf_rows if r.get("score") is not None),
            "published_videos": self.svc.db.count("publications", "status IN ('uploaded','published')"),
            "rendered_videos": self.svc.db.count("videos", "status IN ('rendered','approved','published')"),
            "families": {
                k: {"n": v.n, "mean": round(v.mean, 3) if v.n else None, "status": v.status}
                for k, v in fam.items()
            },
            "hooks": group_stats(perf_rows, "hook_type"),
            "allocation_modes": group_stats(perf_rows, "allocation_mode"),
            "runtime": runtime_buckets(perf_rows),
            "sources": self.source_stats()[:12],
            "experiments": self.experiments.all()[:6],
            "bottlenecks": self._bottlenecks(),
            "family_stat_objs": fam,
        }

    def _bottlenecks(self) -> dict[str, Any]:
        avg_prod = self.svc.db.scalar(
            "SELECT AVG(production_seconds) FROM videos WHERE production_seconds IS NOT NULL", [], None
        )
        failed = self.svc.db.count("videos", "status='failed'")
        rejected = self.svc.db.count("scripts", "status='rejected'")
        qa_fail = self.svc.db.count("scripts", "status='qa_failed'")
        errors = self.svc.db.count("errors", "created_at > datetime('now','-7 days')")
        return {
            "avg_production_seconds": round(float(avg_prod), 1) if avg_prod else None,
            "failed_renders": failed,
            "rejected_scripts": rejected,
            "qa_failed_scripts": qa_fail,
            "errors_7d": errors,
        }

    def learn(self, run: RunContext, strategy_state: dict[str, Any]) -> dict[str, Any]:
        perf_rows = self.svc.analytics_store.video_performance()
        exp_results = self.experiments.evaluate_all(perf_rows)
        ev = self.evidence(perf_rows, strategy_state)
        fam_objs = ev.pop("family_stat_objs")
        fam_lines = "\n".join(
            f"- family={k}: n={v['n']} mean_score={v['mean']} status={v['status']}"
            for k, v in ev["families"].items()
        )
        hook_lines = (
            "\n".join(f"- {k}: n={v['n']} mean_score={v['mean']}" for k, v in ev["hooks"].items())
            or "- no measured hooks yet"
        )
        rt_lines = (
            "\n".join(f"- {k}: n={v['n']} mean_score={v['mean']}" for k, v in ev["runtime"].items())
            or "- none"
        )
        src_lines = (
            "\n".join(
                f"- {s['name']}: items={s['items_yielded']} ideas={s['ideas_yielded']} videos={s['videos_yielded']} last={s['last_status']}"
                for s in ev["sources"]
            )
            or "- none"
        )
        exp_lines = (
            "\n".join(
                f"- {e['id']} {e['name']}: status={e['status']} result={e.get('conclusion') or e.get('result_json')}"
                for e in ev["experiments"]
            )
            or "- none"
        )
        top = sorted([r for r in perf_rows if r.get("score") is not None], key=lambda r: -r["score"])[:5]
        top_lines = (
            "\n".join(
                f"- {r['title'][:60]} | family={r['content_family']} hook={r['hook_type']} score={r['score']:.2f} views={r.get('views')}"
                for r in top
            )
            or "- none"
        )
        user = (
            "Update the strategy memory using ONLY the evidence below. Be conservative: with fewer than ~5 measured videos per family, keep statuses at 'testing' or 'hypothesis'. "
            "Do not declare winners from tiny samples; do not let one video lock the strategy. Propose at most one new experiment, only if none is running on that variable. "
            "Suggest an explore ratio between 0.25 and 0.8 that fits the evidence (more evidence -> less exploration). Write a one-paragraph audience model and a concise change_summary.\n\n"
            f"Measured videos: {ev['measured_videos']} (published {ev['published_videos']}, rendered {ev['rendered_videos']})\n"
            f"Families:\n{fam_lines}\nHooks:\n{hook_lines}\nRuntime buckets:\n{rt_lines}\nSources:\n{src_lines}\nExperiments:\n{exp_lines}\n"
            f"Top videos:\n{top_lines}\nProduction bottlenecks: {ev['bottlenecks']}\n"
            f"Audience requests so far: {'; '.join(strategy_state.get('audience_requests', [])) or 'none'}\n\n"
            f"Current strategy:\n{self.strategy.prompt_summary(strategy_state)}"
        )
        with self.svc.tracker.agent(
            run, self.name, "strategy_update", {"measured": ev["measured_videos"]}
        ) as span:
            try:
                upd = self.ask(
                    self.pctx(run, span=span),
                    "Strategist (learning agent)",
                    user,
                    StrategyUpdate,
                    "strategy_update",
                    temperature=0.3,
                    max_tokens=2500,
                )
            except Exception as exc:
                self.log.warning("strategy model call failed: %s; recording statistics only", exc)
                upd = StrategyUpdate(
                    audience_model=strategy_state.get("audience_model", ""),
                    change_summary=f"statistics refreshed; model unavailable ({str(exc)[:80]})",
                    confidence=float(strategy_state.get("confidence", 0.05)),
                    explore_ratio_suggestion=float(strategy_state.get("explore_ratio", 0.7)),
                )
            # experiments: retire on request, propose new (engine enforces concurrency / duplicates)
            for eid in upd.retire_experiment_ids:
                if self.svc.db.get("experiments", eid):
                    self.experiments.retire(eid)
            for prop in upd.new_experiments[:1]:
                self.experiments.propose(
                    prop,
                    created_by="ai",
                    min_sample=int(self.svc.config.get("experiments.default_min_sample_per_arm", 8)),
                )
            if not self.experiments.running():
                self.experiments.propose(
                    COLD_START_EXPERIMENT,
                    created_by="ai",
                    min_sample=int(self.svc.config.get("experiments.default_min_sample_per_arm", 8)),
                )
            exps = {
                "active": [f"{e['id']}: {e['name']}" for e in self.experiments.running()],
                "retired": [
                    f"{e['id']}: {e['name']} - {e.get('conclusion') or ''}"
                    for e in self.experiments.all()
                    if e["status"] in {"concluded", "retired"}
                ][:10],
            }
            new_state = self.strategy.apply_update(strategy_state, upd, fam_objs, exps)
            version = self.strategy.save(
                new_state, created_by="ai", run_id=run.id, change_summary=upd.change_summary[:500]
            )
            span.output_refs["strategy_version"] = version
        summary_path = self.write_learning_summary(new_state, ev, exp_results, upd.change_summary)
        run.note(
            "learn",
            {"strategy_version": version, "measured": ev["measured_videos"], "summary": str(summary_path)},
        )
        return {
            "strategy_version": version,
            "evidence": ev,
            "experiments": exp_results,
            "change_summary": upd.change_summary,
        }

    # -- reports ----------------------------------------------------------------------------
    def reports_dir(self) -> Path:
        d = self.svc.env.data_dir / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_learning_summary(
        self,
        state: dict[str, Any],
        ev: dict[str, Any],
        exp_results: list[dict[str, Any]],
        change_summary: str,
    ) -> Path:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        path = self.reports_dir() / f"daily_{today}.md"
        lines = [
            f"# Daily learning summary — {today}",
            "",
            f"Strategy version: {state.get('version')}  |  confidence: {state.get('confidence', 0):.2f}  |  explore ratio: {state.get('explore_ratio', 0.7):.0%}",
            f"Measured videos: {ev['measured_videos']}  |  published: {ev['published_videos']}  |  rendered: {ev['rendered_videos']}",
            "",
            "## What changed",
            change_summary or "(no change)",
            "",
            "## Families",
            *[f"- {k}: n={v['n']} mean={v['mean']} status={v['status']}" for k, v in ev["families"].items()],
            "",
            "## Hooks",
            *([f"- {k}: n={v['n']} mean={v['mean']}" for k, v in ev["hooks"].items()] or ["- no data"]),
            "",
            "## Experiments",
            *(
                [
                    f"- {e['name']}: {e['status']} p(treatment better)={e['p_treatment_better']} n={e['control']['n']}/{e['treatment']['n']}"
                    for e in exp_results
                ]
                or ["- none running"]
            ),
            "",
            "## Bottlenecks",
            f"{ev['bottlenecks']}",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def review(self, kind: str = "weekly") -> Path:
        """Deterministic weekly/monthly review answering the validation questions."""
        days = {"daily": 1, "weekly": 7, "monthly": 30}.get(kind, 7)
        perf = self.svc.analytics_store.video_performance()
        measured = [r for r in perf if r.get("score") is not None]
        measured.sort(key=lambda r: r.get("posted_at") or r.get("captured_at") or "")
        half = len(measured) // 2
        first, second = measured[:half], measured[half:]
        improved = None
        if first and second:
            improved = (sum(r["score"] for r in second) / len(second)) - (
                sum(r["score"] for r in first) / len(first)
            )
        hooks = group_stats(measured, "hook_type")
        fams = group_stats(measured, "content_family")
        subs = sorted(
            [r for r in measured if (r.get("subscribers_gained") or r.get("followers_gained"))],
            key=lambda r: -((r.get("subscribers_gained") or 0) + (r.get("followers_gained") or 0)),
        )[:5]
        versions = self.strategy.history(50)
        modes = group_stats(measured, "allocation_mode")
        selections = self.svc.db.query(
            "SELECT allocation_mode, COUNT(*) c FROM ideas WHERE status IN ('selected','scripted','produced','published') GROUP BY allocation_mode"
        )
        lines = [
            f"# {kind.title()} strategy review — {datetime.now(UTC).strftime('%Y-%m-%d')} (window {days}d)",
            "",
            "## Did performance improve over time?",
            f"- Measured videos: {len(measured)}; first-half mean score vs second-half: {improved:+.3f}"
            if improved is not None
            else "- Not enough measured videos to compare halves.",
            "",
            "## Which hooks worked?",
            *([f"- {k}: n={v['n']} mean={v['mean']}" for k, v in hooks.items()] or ["- No data."]),
            "",
            "## Which formats / families worked?",
            *([f"- {k}: n={v['n']} mean={v['mean']}" for k, v in fams.items()] or ["- No data."]),
            "",
            "## Which sources yielded useful stories?",
            *[
                f"- {s['name']}: ideas={s['ideas_yielded']} videos={s['videos_yielded']}"
                for s in self.source_stats()[:8]
            ],
            "",
            "## Which videos generated subscribers/followers?",
            *(
                [
                    f"- {r['title'][:70]}: +{(r.get('subscribers_gained') or 0) + (r.get('followers_gained') or 0)}"
                    for r in subs
                ]
                or ["- No data."]
            ),
            "",
            "## Is the AI making progressively better decisions?",
            f"- Strategy versions written: {len(versions)}; latest change: {versions[0]['change_summary'] if versions else 'n/a'}",
            "- Selections by mode: "
            + (", ".join(f"{r['allocation_mode']}={r['c']}" for r in selections) or "none")
            + f"; measured by mode: {modes or 'none'}",
            "",
            "## Would spending money improve results?",
            f"- Bottlenecks: {self._bottlenecks()}",
            "- Money would most plausibly buy: faster rendering (GPU time), better voices, and stock footage. Only justified once a family shows repeatable retention >50% and shares >5/1k.",
            "",
        ]
        path = self.reports_dir() / f"{kind}_{datetime.now(UTC).strftime('%Y-%m-%d')}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.svc.db.set_state(f"last_{kind}_review", now_iso())
        return path

    def milestone(self) -> dict[str, Any]:
        perf = self.svc.analytics_store.video_performance()
        measured = [r for r in perf if r.get("score") is not None]
        fams = {r["content_family"] for r in measured if r.get("content_family")}
        published = self.svc.db.count("publications", "status IN ('uploaded','published')")
        return {
            "published_videos": published,
            "target_published": 30,
            "measured_videos": len(measured),
            "distinct_families_measured": len(fams),
            "target_families": 3,
            "strategy_versions": len(self.strategy.history(500)),
            "experiments_concluded": self.svc.db.count("experiments", "status='concluded'"),
            "validated": published >= 30 and len(fams) >= 3 and len(measured) >= 20,
        }
