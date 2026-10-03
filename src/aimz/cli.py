"""`aimz` command-line interface."""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from aimz import __version__
from aimz.core.attention import attention
from aimz.util import iso_ago

app = typer.Typer(
    help="AI Media Zero: autonomous, $0/month AI media channel.", no_args_is_help=True, add_completion=False
)
metric_app = typer.Typer(help="Record or view performance metrics.", no_args_is_help=True)
publish_app = typer.Typer(help="Package / upload videos and confirm manual posts.", no_args_is_help=True)
youtube_app = typer.Typer(help="Owner-only YouTube OAuth helpers.", no_args_is_help=True)
tiktok_app = typer.Typer(help="Owner-only TikTok OAuth helpers.", no_args_is_help=True)
bluesky_app = typer.Typer(help="Owner-only Bluesky account helpers.", no_args_is_help=True)
schedule_app = typer.Typer(help="Run the cycle automatically (Windows Task Scheduler).", no_args_is_help=True)
strategy_app = typer.Typer(help="Inspect or edit strategy memory.", no_args_is_help=True)
instance_app = typer.Typer(help="Several channels: one instance per channel.", no_args_is_help=True)
app.add_typer(metric_app, name="metric")
app.add_typer(publish_app, name="publish")
app.add_typer(youtube_app, name="youtube")
app.add_typer(tiktok_app, name="tiktok")
app.add_typer(bluesky_app, name="bluesky")
app.add_typer(schedule_app, name="schedule")
app.add_typer(strategy_app, name="strategy")
app.add_typer(instance_app, name="instance")
console = Console()


@app.callback()
def _root(
    instance: Annotated[
        str, typer.Option("--instance", "-i", help="Channel instance to act on (default: main)")
    ] = "main",
) -> None:
    """Select the channel instance before any command loads its settings."""
    import os

    from aimz import instances

    if instance != instances.MAIN:
        root = instances.root_of(instance)
        if not (root / ".env").exists():
            console.print(
                f"[red]no instance {instance!r}[/] at {root}; create it with "
                f"`python -m aimz instance create {instance} --channel-name ...`"
            )
            raise typer.Exit(1)
        os.environ["AIMZ_PROJECT_ROOT"] = str(root)
    os.environ["AIMZ_INSTANCE"] = instance


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
    from aimz.core.lock import CycleAlreadyRunning

    svc = _svc()
    try:
        summary = _orch(svc).cycle(
            [s.strip() for s in stages.split(",")] if stages else None, produce_limit=limit
        )
        # This run's own row, not "the latest cycle", which could be another process's run.
        mine = svc.db.get("runs", str(summary.get("run_id") or ""))
    except CycleAlreadyRunning as exc:
        console.print(f"[yellow]skipped[/]: {exc}")
        return
    finally:
        svc.close()
    _print_json(summary)
    if mine and mine["status"] == "degraded":
        console.print(f"[yellow]run degraded[/]: {mine['error']}")
        # Exit 2 so the scheduler log and Task Scheduler's "last run result" show it.
        raise typer.Exit(2)


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


@publish_app.command("retry")
def publish_retry(
    publication_id: str,
    approved: Annotated[bool, typer.Option("--approved", help="Owner approval for API uploads")] = False,
) -> None:
    """Re-send a failed, blocked or abandoned publication now, with a fresh set of automatic attempts.

    Only that publication's platform is touched. This is a deliberate owner re-send: if the earlier attempt
    may have posted, check the platform first.
    """
    svc = _svc()
    try:
        orch = _orch(svc)
        allowed, why = orch.publisher.gate().check()
        if not allowed:
            # The owner's posting limits (config.yaml publishing.limits) apply to re-sends too.
            console.print(f"[yellow]not sent now:[/] {why}. Run it again later; nothing was changed.")
            return
        info = orch.publisher.requeue(publication_id)
        console.print(
            f"re-queued {info['platform']} publication {publication_id} "
            f"(was: {info['cleared_error'] or 'no error recorded'})"
        )
        with svc.tracker.run("publish") as ctx:
            res = orch.publisher.publish(
                ctx, info["video_id"], platforms=[info["platform"]], owner_approved=approved
            )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
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
def requeue(kind: Annotated[str, typer.Argument(help="idea | script")], item_id: str) -> None:
    """Put a parked idea or script back in the queue (publications: `aimz publish retry`)."""
    from aimz.util import now_iso

    svc = _svc(quiet=True)
    try:
        if kind not in {"idea", "script"}:
            raise typer.BadParameter("kind must be idea or script")
        table = "ideas" if kind == "idea" else "scripts"
        row = svc.db.get(table, item_id)
        if not row:
            raise typer.BadParameter(f"{kind} {item_id} not found")
        if row["status"] != "parked":
            raise typer.BadParameter(
                f"{kind} {item_id} is '{row['status']}'; only parked items are re-queued"
            )
        if kind == "idea":
            values = {"status": "selected", "tech_failures": 0, "updated_at": now_iso()}
        else:  # renders again next cycle, with a fresh set of attempts
            svc.db.execute(
                "UPDATE videos SET status='superseded' WHERE script_id=? AND status='failed'", [item_id]
            )
            values = {"status": "approved", "updated_at": now_iso()}
        svc.db.update(table, item_id, values)
        console.print(f"[green]{kind} {item_id} -> {values['status']}[/]")
    finally:
        svc.close()


@app.command()
def settings() -> None:
    """Show the per-video settings the AI can test, their status, and the evidence so far (read-only)."""
    from aimz.experiments.allocation import per_video
    from aimz.experiments.settings import SettingsEngine, capabilities

    svc = _svc(quiet=True)
    try:
        eng = SettingsEngine(svc.db, caps=capabilities(svc))
        text = eng.evidence_text(per_video(svc.analytics_store.video_performance()), max_lines=200)
        console.print(text, markup=False, highlight=False)
        n = svc.db.scalar("SELECT COUNT(DISTINCT video_id) FROM video_settings", [], 0)
        console.print(f"videos with recorded settings: {n}")
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
        from aimz.experiments.allocation import per_video

        # One row per video (the learning loop counts a video once, however many platforms it went to).
        # Metric columns are from the publication the score comes from, else the first one found.
        perf = svc.analytics_store.video_performance()
        platforms: dict[str, list[str]] = {}
        for r in perf:
            platforms.setdefault(r["video_id"], []).append(r["platform"])
        t = Table(
            "video",
            "platforms",
            "family",
            "hook",
            "views",
            "avg%",
            "shares",
            "subs",
            "score",
            "parts",
            "note",
        )
        for r in per_video(perf):
            note = "reach only" if r.get("score_reach_only") else "late" if r.get("score_late") else ""
            if r["score"] is None:
                note = "waiting"
            t.add_row(
                r["title"][:40],
                ",".join(sorted(platforms.get(r["video_id"], []))),
                r["content_family"] or "",
                r["hook_type"] or "",
                str(r.get("views") or ""),
                str(r.get("avg_percent_viewed") or ""),
                str(r.get("shares") or ""),
                str(r.get("subscribers_gained") or r.get("followers_gained") or ""),
                f"{r['score']:.2f}" if r["score"] is not None else "",
                " ".join(f"{k[:4]}={v:.2f}" for k, v in (r.get("score_parts") or {}).items()),
                note,
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
def status(
    all_instances: Annotated[bool, typer.Option("--all", help="Summarise every channel instance")] = False,
) -> None:
    """System status: kill switch, budget, counts, posting, last run (``--all``: every channel)."""
    if all_instances:
        _status_all()
        return
    _status_one()


def _status_all() -> None:
    """One line per channel. Each instance runs in its own process (settings are per process)."""
    import subprocess
    import sys

    from aimz import instances

    t = Table(
        "instance", "last run", "videos", "posting runs today", "may post now", "errors 24h", "attention"
    )
    for name in instances.list_instances():
        proc = subprocess.run(
            [sys.executable, "-m", "aimz", "--instance", name, "status"],
            capture_output=True,
            text=True,
            cwd=str(instances.REPO_ROOT),
            timeout=120,
        )
        try:
            info = json.loads(proc.stdout[proc.stdout.index("{") :])
        except ValueError:
            t.add_row(name, "status failed", "", "", "", "", (proc.stderr or proc.stdout)[-80:])
            continue
        last = info.get("last_run") or {}
        posting = info.get("posting") or {}
        t.add_row(
            name,
            f"{last.get('kind', '')} {str(last.get('started_at', ''))[:16]} {last.get('status', '')}",
            str(info["counts"]["videos_rendered"]),
            str(posting.get("posting_runs_today", "")),
            str(posting.get("may_post_now", "")),
            str(info["counts"]["errors_24h"]),
            str(len(info.get("needs_attention") or [])),
        )
    console.print(t)


def _status_one() -> None:
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
                "videos_rendered": svc.db.count(
                    "videos", "status IN ('rendered','approved','published','measured')"
                ),
                "videos_measured": svc.db.count("videos", "status='measured'"),
                "publications": svc.db.count("publications"),
                "metrics": svc.db.count("metrics"),
                "experiments_running": svc.db.count("experiments", "status='running'"),
                "errors_24h": svc.db.count("errors", "created_at > ?", [iso_ago(days=1)]),
            },
            "needs_attention": attention(svc.db),
            "last_run": dict(last) if last else None,
        }
        from aimz.pipeline.posting import PostingGate, PostingLimits

        gate = PostingGate(svc.db, PostingLimits.from_config(svc.config))
        last_post = gate.last_run()
        info["posting"] = {
            "limits": dataclasses.asdict(gate.limits),
            "posting_runs_today": gate.runs_today(),
            "last_posting_run": last_post["started_at"] if last_post else None,
            "may_post_now": gate.next_allowed(),
            "videos_waiting": gate.waiting_videos(),
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


@youtube_app.command("whoami")
def youtube_whoami() -> None:
    """Which YouTube channel this instance's login posts to (read-only, 1 quota unit)."""
    from aimz.providers.publishers.youtube import build_youtube, load_credentials

    svc = _svc(quiet=True)
    try:
        creds = load_credentials(svc.env.youtube_token_file)
        if creds is None:
            console.print(f"[red]no YouTube login[/] for instance {svc.env.instance}; run `youtube auth`")
            raise typer.Exit(1)
        items = build_youtube(creds).channels().list(part="snippet", mine=True).execute().get("items") or []
        for ch in items:
            sn = ch.get("snippet", {})
            console.print(f"{svc.env.instance}: {sn.get('title')} ({ch.get('id')}) {sn.get('customUrl', '')}")
        if not items:
            console.print("the login has no YouTube channel")
    finally:
        svc.close()


@youtube_app.command("revoke")
def youtube_revoke(
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation question")] = False,
) -> None:
    """Owner: disconnect this instance's YouTube channel and delete its YouTube API data.

    Revokes the login with Google, deletes the token, YouTube metrics, comments, saved upload responses
    and the video ids/links (YouTube Developer Policies: delete within 7 days of revocation)."""
    from aimz import youtube_data

    svc = _svc(quiet=True)
    try:
        if not yes and not typer.confirm(
            f"Disconnect YouTube for instance '{svc.env.instance}' and delete its YouTube data? This cannot be undone."
        ):
            raise typer.Exit(1)
        result = youtube_data.revoke(svc.db, svc.env.data_dir, svc.env.youtube_token_file)
        console.print_json(data=result)
    finally:
        svc.close()


@youtube_app.command("auth")
def youtube_auth() -> None:
    """Owner-only: run the Google OAuth consent flow and store the token locally."""
    from aimz.providers.publishers.youtube import AUTH_SCOPES, run_oauth_flow

    svc = _svc(quiet=True)
    try:
        run_oauth_flow(svc.env.youtube_client_secret_file, svc.env.youtube_token_file, AUTH_SCOPES)
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


@tiktok_app.command("auth")
def tiktok_auth() -> None:
    """Owner-only: connect your TikTok account (paste the redirected URL back here)."""
    from aimz.providers.publishers.tiktok_direct import TikTokClient

    svc = _svc(quiet=True)
    try:
        env = svc.env
        if not env.tiktok_client_key or not env.tiktok_client_secret or not env.tiktok_redirect_uri:
            raise typer.BadParameter(
                "set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET and TIKTOK_REDIRECT_URI in .env first"
            )
        client = TikTokClient(
            env.tiktok_client_key, env.tiktok_client_secret, env.tiktok_redirect_uri, env.tiktok_token_file
        )
        url, _state = client.authorize_url()
        console.print("1. Open this link, log in to TikTok and allow access:\n")
        console.print(url)
        console.print(
            "\n2. Your browser will land on your redirect page. Copy the FULL address bar URL and paste it here."
        )
        pasted = typer.prompt("Redirected URL")
        tok = client.exchange_code(TikTokClient.code_from_redirect(pasted))
        console.print(
            f"[green]TikTok connected[/] (open_id {tok.open_id}); token stored at {env.tiktok_token_file}"
        )
        info = client.creator_info()
        console.print(
            f"Creator: {info.get('creator_nickname')} · allowed privacy levels: {info.get('privacy_level_options')}"
        )
    finally:
        svc.close()


@tiktok_app.command("creator-info")
def tiktok_creator_info() -> None:
    """Show what TikTok allows for the connected creator (privacy options, max duration)."""
    from aimz.providers.publishers.tiktok_direct import TikTokClient

    svc = _svc(quiet=True)
    try:
        env = svc.env
        client = TikTokClient(
            env.tiktok_client_key, env.tiktok_client_secret, env.tiktok_redirect_uri, env.tiktok_token_file
        )
        _print_json(client.creator_info())
    finally:
        svc.close()


@bluesky_app.command("auth")
def bluesky_auth() -> None:
    """Owner-only: sign in to Bluesky with the app password from .env and store the session."""
    from aimz.providers.publishers.bluesky import BlueskyClient

    svc = _svc(quiet=True)
    try:
        env = svc.env
        if not env.bluesky_handle or not env.bluesky_app_password:
            raise typer.BadParameter(
                "set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD in .env first. Create the app password at "
                "Bluesky -> Settings -> Privacy and Security -> App Passwords (never your account password)."
            )
        client = BlueskyClient(
            env.bluesky_handle, env.bluesky_app_password, env.bluesky_pds_url, env.bluesky_session_file
        )
        session = client.login()
        console.print(
            f"[green]Bluesky connected[/] as {session.handle} ({session.did}); "
            f"session stored at {env.bluesky_session_file}"
        )
        _print_json(client.upload_limits())
    finally:
        svc.close()


@bluesky_app.command("limits")
def bluesky_limits() -> None:
    """Show the account's remaining daily video allowance."""
    from aimz.providers.publishers.bluesky import BlueskyClient

    svc = _svc(quiet=True)
    try:
        env = svc.env
        client = BlueskyClient(
            env.bluesky_handle, env.bluesky_app_password, env.bluesky_pds_url, env.bluesky_session_file
        )
        _print_json(client.upload_limits())
    finally:
        svc.close()


@publish_app.command("poll")
def publish_poll() -> None:
    """Advance uploads the platform was still processing."""
    svc = _svc()
    try:
        with svc.tracker.run("publish") as ctx:
            n = _orch(svc).publisher.poll_pending(ctx)
        console.print(f"advanced {n} publication(s)")
    finally:
        svc.close()


@schedule_app.command("install")
def schedule_install(
    times: Annotated[str, typer.Option(help="Comma-separated HH:MM local times")] = "09:00,18:00",
) -> None:
    """Register daily Task Scheduler jobs that run `aimz run`, within the owner's posting limits."""
    from aimz import instances, scheduler
    from aimz.pipeline.posting import PostingLimits, check_schedule
    from aimz.settings import load_app_config, load_env_settings

    env = load_env_settings()
    root = instances.REPO_ROOT
    wanted = [t.strip() for t in times.split(",") if t.strip()]
    problem = check_schedule(wanted, PostingLimits.from_config(load_app_config(env.config_dir)))
    if problem:
        console.print(f"[red]schedule breaks the posting limits:[/] {problem}")
        raise typer.Exit(1)
    try:
        name, removed = scheduler.install(root, wanted, env.instance, env.project_root)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print(f"[green]scheduled:[/] {name} daily at {', '.join(wanted)}")
    if removed:
        console.print("removed old tasks: " + ", ".join(removed))
    console.print(f"log: {env.data_dir / 'logs' / 'scheduled.log'}")


@schedule_app.command("remove")
def schedule_remove() -> None:
    """Delete this instance's scheduled job (other channels keep theirs)."""
    import os

    from aimz import scheduler

    instance = os.environ.get("AIMZ_INSTANCE", "main")
    console.print("removed: " + (", ".join(scheduler.remove(instance=instance)) or "none"))


@schedule_app.command("status")
def schedule_status() -> None:
    """List the scheduled jobs."""
    from aimz import scheduler

    tasks = scheduler.list_tasks()
    console.print(
        "\n".join(tasks) if tasks else "no AI Media Zero tasks scheduled (run `aimz schedule install`)"
    )


@app.command()
def money() -> None:
    """Partner Program progress, niches ranked by expected value, and the score's parts and weights."""
    from aimz.experiments.allocation import per_video
    from aimz.experiments.value import niche_value_text, niche_values, ypp_progress, ypp_text

    svc = _svc(quiet=True)
    try:
        rows = per_video(svc.analytics_store.video_performance())
        console.print(ypp_text(ypp_progress(svc.db)), markup=False)
        console.print("Niche value per video:", markup=False)
        console.print(niche_value_text(niche_values(rows), limit=30), markup=False, highlight=False)
        weights = {k: v for k, v in svc.analytics_store.weights.items()}
        console.print(f"Score weights (config.yaml scoring.weights): {weights}", markup=False)
    finally:
        svc.close()


@instance_app.command("create")
def instance_create(
    name: Annotated[str, typer.Argument(help="Short id, e.g. 'space' (lowercase, digits, - or _)")],
    channel_name: Annotated[str, typer.Option("--channel-name", help="The YouTube channel's display name")],
) -> None:
    """Owner: set up a new channel instance. Then authorise its YouTube account and schedule it."""
    from aimz import instances

    try:
        root = instances.create(name, channel_name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]created[/] {root}")
    console.print("Next, as the owner:")
    console.print(f"  python -m aimz --instance {name} youtube auth      (pick the '{channel_name}' channel)")
    console.print(f"  python -m aimz --instance {name} youtube whoami    (check it is the right channel)")
    console.print(f"  python -m aimz instance consent {name}             (allow automatic uploads to it)")
    # Stagger each channel 15 minutes after the one before, so their cycles rarely wait for the GPU.
    offset = 15 * (instances.list_instances().index(name) if name in instances.list_instances() else 1)
    times = ",".join(f"{h + (offset // 60):02d}:{offset % 60:02d}" for h in (9, 18))
    console.print(f"  python -m aimz --instance {name} schedule install --times {times}")


@instance_app.command("consent")
def instance_consent(
    name: Annotated[str, typer.Argument(help="Instance to change (e.g. 'space', or 'main')")],
    off: Annotated[bool, typer.Option("--off", help="Withdraw consent: videos wait for approval")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation question")] = False,
) -> None:
    """Owner: allow (or stop) automatic uploads for ONE channel, after seeing which channel it is.

    YouTube's Developer Policies (III.I) need the owner's prior, specific consent before automated uploads,
    so a new instance starts without it and consent is never copied from another channel."""
    from aimz import instances
    from aimz.providers.publishers.youtube import build_youtube, load_credentials

    root = instances.root_of(name)
    if not (root / ".env").exists():
        raise typer.BadParameter(f"no instance {name!r} at {root}")
    if off:
        instances.set_env(root, "AUTOPUBLISH_CONSENT", "false")
        console.print(f"{name}: automatic uploads [yellow]off[/]; videos wait for `approve video <id>`")
        return
    token = root / "secrets" / "youtube_token.json"
    creds = load_credentials(token)
    if creds is None:
        console.print(
            f"[red]no YouTube login[/] for {name}; run `python -m aimz --instance {name} youtube auth`"
        )
        raise typer.Exit(1)
    items = build_youtube(creds).channels().list(part="snippet", mine=True).execute().get("items") or []
    if len(items) != 1:
        console.print(f"[red]the login for {name} has {len(items)} channels; expected exactly one[/]")
        raise typer.Exit(1)
    title, channel_id = items[0].get("snippet", {}).get("title"), items[0].get("id")
    console.print(f"{name} uploads to: [bold]{title}[/] (https://www.youtube.com/channel/{channel_id})")
    if not yes and not typer.confirm(f"Allow this app to upload videos to '{title}' automatically?"):
        raise typer.Exit(1)
    instances.set_env(root, "AUTOPUBLISH_CONSENT", "true")
    instances.set_env(root, "YOUTUBE_CONSENTED_CHANNEL_ID", str(channel_id))
    console.print(f"{name}: automatic uploads [green]on[/] for {title}")


@instance_app.command("list")
def instance_list() -> None:
    """Every channel instance, where it lives and its dashboard port."""
    from aimz import instances

    t = Table("instance", "root", "dashboard port", "youtube login")
    for name in instances.list_instances():
        root = instances.root_of(name)
        env = instances._read_env(root / ".env")
        token = root / "secrets" / "youtube_token.json"
        t.add_row(name, str(root), env.get("DASHBOARD_PORT", "8420"), "yes" if token.exists() else "no")
    console.print(t)


if __name__ == "__main__":  # pragma: no cover
    app()
