"""`aimz` command-line interface."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from aimz import __version__

app = typer.Typer(
    help="AI Media Zero: autonomous, $0/month AI media channel.", no_args_is_help=True, add_completion=False
)
metric_app = typer.Typer(help="Record or view performance metrics.", no_args_is_help=True)
publish_app = typer.Typer(help="Package / upload videos and confirm manual posts.", no_args_is_help=True)
youtube_app = typer.Typer(help="Owner-only YouTube OAuth helpers.", no_args_is_help=True)
strategy_app = typer.Typer(help="Inspect or edit strategy memory.", no_args_is_help=True)
app.add_typer(metric_app, name="metric")
app.add_typer(publish_app, name="publish")
app.add_typer(youtube_app, name="youtube")
app.add_typer(strategy_app, name="strategy")
console = Console()


def _svc(quiet: bool = False):  # noqa: ANN202
    from aimz.providers.registry import build_services

    return build_services(quiet_logs=quiet)


def _orch(svc):  # noqa: ANN001, ANN202
    from aimz.pipeline.orchestrator import Orchestrator

    return Orchestrator(svc)


def _print_json(obj: object) -> None:
    console.print_json(json.dumps(obj, default=str))


# --------------------------------------------------------------------------------------
@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"aimz {__version__}")


@app.command()
def init(force: Annotated[bool, typer.Option(help="Overwrite .env from .env.example")] = False) -> None:
    """Create .env, data folders and the database; seed the cold-start strategy."""
    from aimz.doctor import ensure_dirs
    from aimz.settings import load_env_settings

    root = Path.cwd()
    ensure_dirs(root)
    env_example = root / ".env.example"
    env_file = root / ".env"
    if env_example.exists() and (force or not env_file.exists()):
        shutil.copyfile(env_example, env_file)
        console.print(f"[green]wrote[/] {env_file}")
    env = load_env_settings(root)
    svc = _svc(quiet=True)
    try:
        orch = _orch(svc)
        orch.research.sync_sources()
        state = orch.strategy.current()
        console.print(
            f"[green]database ready[/] at {env.db_path} (strategy v{state['version']}, {len(state['families'])} families)"
        )
        console.print("Next: [bold]aimz doctor --fix[/] then [bold]aimz run[/]")
    finally:
        svc.close()


@app.command()
def doctor(
    fix: Annotated[bool, typer.Option(help="Download free assets (Piper voice) where possible")] = False,
) -> None:
    """Check local dependencies: Python, FFmpeg, Ollama + model, Piper, database, credentials."""
    from aimz.doctor import run_checks, summarize

    svc = _svc(quiet=True)
    try:
        checks = run_checks(svc, fix=fix)
    finally:
        svc.close()
    ok, text = summarize(checks)
    console.print(text)
    console.print(
        "\n[green]All required checks passed.[/]" if ok else "\n[red]Some required checks failed.[/]"
    )
    raise typer.Exit(0 if ok else 1)


@app.command()
def run(
    stages: Annotated[
        str | None,
        typer.Option(
            help="Comma-separated subset: research,ideas,select,write,produce,publish,metrics,comments,learn"
        ),
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Max videos to produce this cycle")] = None,
) -> None:
    """Run one full autonomous cycle (or a subset of stages)."""
    svc = _svc()
    try:
        summary = _orch(svc).cycle(
            [s.strip() for s in stages.split(",")] if stages else None, produce_limit=limit
        )
    finally:
        svc.close()
    _print_json(summary)


@app.command()
def research() -> None:
    """Fetch feeds and store new leads."""
    svc = _svc()
    try:
        orch = _orch(svc)
        with svc.tracker.run("research") as ctx:
            stats = orch.research.run(ctx)
    finally:
        svc.close()
    _print_json(stats)


@app.command()
def ideas(
    generate: Annotated[bool, typer.Option("--generate", help="Generate new ideas from fresh leads")] = False,
    select: Annotated[
        bool, typer.Option("--select", help="Have the Editor-in-Chief select ideas for production")
    ] = False,
    limit: int = 15,
) -> None:
    """List candidate ideas; optionally generate and/or select."""
    svc = _svc(quiet=not (generate or select))
    try:
        orch = _orch(svc)
        if generate or select:
            with svc.tracker.run("ideas") as ctx:
                state = orch.strategy.current()
                if generate:
                    orch.ideation.run(ctx, state)
                if select:
                    orch.editor.select(ctx, state, svc.analytics_store.video_performance())
        rows = svc.db.query(
            "SELECT id, status, opportunity_score, content_family, hook_type, title FROM ideas ORDER BY created_at DESC LIMIT ?",
            [limit],
        )
        t = Table("id", "status", "score", "family", "hook", "title")
        for r in rows:
            t.add_row(
                r["id"],
                r["status"],
                f"{r['opportunity_score']:.0f}",
                r["content_family"],
                r["hook_type"] or "",
                r["title"][:60],
            )
        console.print(t)
    finally:
        svc.close()


@app.command()
def produce(
    script_id: Annotated[str | None, typer.Option(help="Produce a specific approved script")] = None,
    write: Annotated[bool, typer.Option("--write", help="Write/QA scripts for selected ideas first")] = False,
    limit: int = 1,
) -> None:
    """Write + QA selected ideas and render approved scripts to MP4."""
    svc = _svc()
    try:
        orch = _orch(svc)
        with svc.tracker.run("produce") as ctx:
            if write:
                orch.write_selected(ctx, orch.strategy.current(), limit=limit)
            if script_id:
                vid = orch.producer.produce(ctx, script_id)
                console.print(f"[green]rendered[/] {vid}")
            else:
                vids = orch.produce_approved(ctx, limit)
                console.print(f"[green]rendered[/] {vids or 'nothing (no approved scripts)'}")
    finally:
        svc.close()


@publish_app.command("run")
def publish_run(
    video_id: Annotated[str | None, typer.Argument()] = None,
    approved: Annotated[bool, typer.Option("--approved", help="Owner approval for API uploads")] = False,
) -> None:
    """Package (and, if enabled, upload) rendered videos."""
    svc = _svc()
    try:
        orch = _orch(svc)
        with svc.tracker.run("publish") as ctx:
            if video_id:
                res = orch.publisher.publish(ctx, video_id, owner_approved=approved)
            else:
                res = orch.publish_rendered(ctx)
    finally:
        svc.close()
    _print_json(res)


@publish_app.command("mark-posted")
def publish_mark_posted(
    publication_id: str, url: str | None = None, platform_video_id: str | None = None
) -> None:
    """Confirm that a package was posted manually (TikTok / YouTube draft)."""
    svc = _svc(quiet=True)
    try:
        _orch(svc).publisher.mark_posted(publication_id, url, platform_video_id)
        console.print(f"[green]marked posted[/] {publication_id}")
    finally:
        svc.close()


@publish_app.command("list")
def publish_list(limit: int = 20) -> None:
    """List publications and their state."""
    svc = _svc(quiet=True)
    try:
        t = Table("id", "platform", "mode", "status", "url/package")
        for r in svc.db.query("SELECT * FROM publications ORDER BY created_at DESC LIMIT ?", [limit]):
            t.add_row(r["id"], r["platform"], r["mode"], r["status"], r["url"] or r["package_dir"] or "")
        console.print(t)
    finally:
        svc.close()


@app.command()
def approve(
    kind: Annotated[str, typer.Argument(help="script | video")], item_id: str, reject: bool = False
) -> None:
    """Owner approval / rejection of a script (needs_owner_review) or a rendered video."""
    from aimz.util import now_iso

    svc = _svc(quiet=True)
    try:
        table = "scripts" if kind == "script" else "videos"
        status = "rejected" if reject else "approved"
        if not svc.db.get(table, item_id):
            raise typer.BadParameter(f"{kind} {item_id} not found")
        svc.db.update(table, item_id, {"status": status, "updated_at": now_iso()})
        console.print(f"[green]{kind} {item_id} -> {status}[/]")
    finally:
        svc.close()


@app.command()
def analytics(
    collect: Annotated[
        bool, typer.Option("--collect", help="Pull metrics from enabled platform APIs")
    ] = False,
) -> None:
    """Show latest performance per video; optionally collect from APIs."""
    svc = _svc(quiet=not collect)
    try:
        orch = _orch(svc)
        if collect:
            with svc.tracker.run("analytics") as ctx:
                n = orch.analyst.collect_metrics(ctx)
                console.print(f"collected {n} snapshots")
        t = Table("video", "platform", "family", "hook", "views", "avg%", "shares", "subs", "score")
        for r in svc.analytics_store.video_performance():
            t.add_row(
                r["title"][:40],
                r["platform"],
                r["content_family"] or "",
                r["hook_type"] or "",
                str(r.get("views") or ""),
                str(r.get("avg_percent_viewed") or ""),
                str(r.get("shares") or ""),
                str(r.get("subscribers_gained") or r.get("followers_gained") or ""),
                f"{r['score']:.2f}" if r["score"] is not None else "",
            )
        console.print(t)
        _print_json(orch.analyst.milestone())
    finally:
        svc.close()


@metric_app.command("add")
def metric_add(
    publication_id: str,
    views: int | None = None,
    impressions: int | None = None,
    ctr: float | None = None,
    retention_3s: float | None = None,
    avg_watch_time_s: float | None = None,
    avg_percent_viewed: float | None = None,
    completion_rate: float | None = None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    subscribers_gained: int | None = None,
    followers_gained: int | None = None,
    returning_viewers: int | None = None,
    revenue_usd: float | None = None,
) -> None:
    """Manually record metrics for a publication (from YouTube Studio / TikTok analytics screens)."""
    from aimz.domain.models import MetricsSnapshot

    svc = _svc(quiet=True)
    try:
        pub = svc.db.get("publications", publication_id)
        if not pub:
            raise typer.BadParameter("publication not found")
        snap = MetricsSnapshot(
            views=views,
            impressions=impressions,
            ctr=ctr,
            retention_3s=retention_3s,
            avg_watch_time_s=avg_watch_time_s,
            avg_percent_viewed=avg_percent_viewed,
            completion_rate=completion_rate,
            likes=likes,
            comments=comments,
            shares=shares,
            subscribers_gained=subscribers_gained,
            followers_gained=followers_gained,
            returning_viewers=returning_viewers,
            revenue_usd=revenue_usd,
        )
        mid = svc.analytics_store.record(dict(pub), snap, source="manual")
        console.print(f"[green]recorded[/] {mid}")
    finally:
        svc.close()


@strategy_app.command("show")
def strategy_show() -> None:
    """Print the current strategy memory."""
    svc = _svc(quiet=True)
    try:
        orch = _orch(svc)
        row = orch.strategy.latest_row()
        console.print(row["markdown"] if row else "(no strategy yet; run `aimz init`)")
    finally:
        svc.close()


@strategy_app.command("learn")
def strategy_learn() -> None:
    """Run the learning step now (metrics -> experiments -> strategy update)."""
    svc = _svc()
    try:
        orch = _orch(svc)
        with svc.tracker.run("learn") as ctx:
            out = orch.analyst.learn(ctx, orch.strategy.current())
        console.print(f"strategy v{out['strategy_version']}: {out['change_summary']}")
    finally:
        svc.close()


@strategy_app.command("review")
def strategy_review(kind: Annotated[str, typer.Argument(help="daily | weekly | monthly")] = "weekly") -> None:
    """Write a daily/weekly/monthly review report."""
    svc = _svc(quiet=True)
    try:
        path = _orch(svc).analyst.review(kind)
        console.print(path.read_text(encoding="utf-8"))
    finally:
        svc.close()


@strategy_app.command("edit")
def strategy_edit(
    file: Annotated[Path, typer.Argument(help="JSON file with the full strategy state")],
) -> None:
    """Owner override: replace the strategy state from a JSON file (saved as a new version)."""
    svc = _svc(quiet=True)
    try:
        state = json.loads(file.read_text(encoding="utf-8"))
        v = _orch(svc).strategy.save(state, created_by="owner", change_summary=f"owner edit from {file.name}")
        console.print(f"[green]saved strategy v{v}[/]")
    finally:
        svc.close()


@app.command()
def experiments() -> None:
    """List experiments and their current evaluation."""
    svc = _svc(quiet=True)
    try:
        t = Table("id", "name", "variable", "status", "confidence", "conclusion")
        for r in svc.db.query("SELECT * FROM experiments ORDER BY created_at DESC"):
            t.add_row(
                r["id"],
                r["name"][:40],
                f"{r['variable']}: {r['control']} vs {r['treatment']}",
                r["status"],
                f"{r['confidence']:.2f}" if r["confidence"] is not None else "",
                (r["conclusion"] or "")[:60],
            )
        console.print(t)
    finally:
        svc.close()


@app.command()
def status() -> None:
    """System status: kill switch, budget, counts, last run."""
    svc = _svc(quiet=True)
    try:
        ks = svc.killswitch.status()
        snap = svc.budget.snapshot()
        fin = svc.budget.financials()
        last = svc.db.one("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
        info = {
            "kill_switch": "ENGAGED" if ks.engaged else "off",
            "kill_reason": ks.reason,
            "budget_month": snap.month,
            "budget_usd": snap.budget_usd,
            "spent_usd": snap.spent_usd,
            "denied_calls": snap.denied_count,
            "financials": fin,
            "llm": f"{svc.env.llm_provider}:{getattr(svc.llm, 'model', '')}",
            "tts": svc.env.tts_provider,
            "youtube_mode": svc.env.youtube_mode,
            "counts": {
                "source_items": svc.db.count("source_items"),
                "ideas": svc.db.count("ideas"),
                "scripts_approved": svc.db.count("scripts", "status='approved'"),
                "scripts_needing_review": svc.db.count("scripts", "status='needs_owner_review'"),
                "videos_rendered": svc.db.count("videos", "status IN ('rendered','approved','published')"),
                "publications": svc.db.count("publications"),
                "metrics": svc.db.count("metrics"),
                "experiments_running": svc.db.count("experiments", "status='running'"),
                "errors_24h": svc.db.count("errors", "created_at > datetime('now','-1 day')"),
            },
            "last_run": dict(last) if last else None,
        }
    finally:
        svc.close()
    _print_json(info)


@app.command()
def kill(reason: str = "owner request") -> None:
    """Engage the kill switch: blocks publishing, API writes, scheduling, and paid providers."""
    svc = _svc(quiet=True)
    try:
        st = svc.killswitch.engage(reason)
        console.print(f"[red]KILL SWITCH ENGAGED[/] ({st.reason}); sentinel: {svc.killswitch.sentinel_path}")
    finally:
        svc.close()


@app.command()
def resume() -> None:
    """Release the kill switch (owner only)."""
    svc = _svc(quiet=True)
    try:
        st = svc.killswitch.release()
        console.print("[green]kill switch off[/]" if not st.engaged else "[red]still engaged[/]")
    finally:
        svc.close()


@app.command()
def dashboard(host: str | None = None, port: int | None = None) -> None:
    """Start the local owner dashboard."""
    import uvicorn

    from aimz.dashboard.app import create_app
    from aimz.settings import load_env_settings

    env = load_env_settings()
    host = host or env.dashboard_host
    port = port or env.dashboard_port
    console.print(f"dashboard at http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@youtube_app.command("auth")
def youtube_auth() -> None:
    """Owner-only: run the Google OAuth consent flow and store the token locally."""
    from aimz.providers.publishers.youtube import ALL_SCOPES, run_oauth_flow

    svc = _svc(quiet=True)
    try:
        run_oauth_flow(svc.env.youtube_client_secret_file, svc.env.youtube_token_file, ALL_SCOPES)
        console.print(f"[green]token stored[/] at {svc.env.youtube_token_file}")
    finally:
        svc.close()


@app.command()
def budget(show: bool = True) -> None:
    """Show budget, ledger totals and recent authorizations/denials."""
    svc = _svc(quiet=True)
    try:
        snap = svc.budget.snapshot()
        console.print(
            f"month {snap.month}: budget ${snap.budget_usd:.2f}  spent ${snap.spent_usd:.2f}  outstanding ${snap.outstanding_usd:.2f}  denials {snap.denied_count}"
        )
        _print_json(svc.budget.financials())
        t = Table("when", "type", "provider", "action", "est $", "actual $", "note")
        for r in svc.db.query("SELECT * FROM ledger ORDER BY created_at DESC LIMIT 15"):
            t.add_row(
                r["created_at"],
                r["entry_type"],
                r["provider"] or "",
                r["action"] or "",
                f"{r['estimated_cost_usd']:.4f}",
                f"{r['actual_cost_usd']:.4f}",
                (r["note"] or "")[:40],
            )
        console.print(t)
    finally:
        svc.close()


if __name__ == "__main__":  # pragma: no cover
    app()
