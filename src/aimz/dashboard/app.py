"""FastAPI owner dashboard.

Read views: status, budget, runs, feeds, ideas, scripts, QA, production queue, videos, publishing,
metrics, experiments, strategy, provider health, errors.
Owner controls (POST): kill/resume, budget, publishing mode, credentials paths, constitution and
strategy edits, feeds add/remove, approve/reject, mark-posted, manual metrics.

The dashboard binds to 127.0.0.1 by default and has no auth; it is the owner's local console.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from aimz.domain.models import MetricsSnapshot
from aimz.providers.registry import Services, build_services
from aimz.settings import load_feeds, read_text, save_feeds, update_env_file
from aimz.util import loads, new_id, now_iso

HERE = Path(__file__).parent


def create_app(svc: Services | None = None) -> FastAPI:
    svc = svc or build_services(quiet_logs=True)
    from aimz.pipeline.orchestrator import Orchestrator

    orch = Orchestrator(svc)
    app = FastAPI(title="AI Media Zero", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["json"] = lambda v: json.dumps(v, indent=2, default=str)
    templates.env.filters["loads"] = lambda v: loads(v, {})

    def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        ks = svc.killswitch.status()
        snap = svc.budget.snapshot()
        base = {
            "request": request,
            "title": svc.config.get("dashboard.title", "AI Media Zero"),
            "kill": ks,
            "budget": snap,
            "env": svc.env,
        }
        return templates.TemplateResponse(request, name, {**base, **ctx})

    def q(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        return [dict(r) for r in svc.db.query(sql, params or [])]

    def record_config(key: str, content: str) -> None:
        svc.db.insert(
            "configuration_versions",
            {
                "id": new_id("cfg"),
                "key": key,
                "content": content[:20000],
                "changed_by": "owner",
                "created_at": now_iso(),
            },
        )

    # ---------------------------------------------------------------- read views
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        health = [(p.name, p.health()) for p in svc.all_providers()]
        counts = {
            "leads_new": svc.db.count("source_items", "status='new'"),
            "ideas_candidate": svc.db.count("ideas", "status='candidate'"),
            "ideas_selected": svc.db.count("ideas", "status='selected'"),
            "scripts_approved": svc.db.count("scripts", "status='approved'"),
            "scripts_review": svc.db.count("scripts", "status='needs_owner_review'"),
            "videos_rendered": svc.db.count("videos", "status='rendered'"),
            "videos_published": svc.db.count("videos", "status='published'"),
            "publications": svc.db.count("publications"),
            "metrics": svc.db.count("metrics"),
            "experiments_running": svc.db.count("experiments", "status='running'"),
            "errors_24h": svc.db.count("errors", "created_at > datetime('now','-1 day')"),
        }
        return render(
            request,
            "index.html",
            counts=counts,
            runs=q("SELECT * FROM runs ORDER BY started_at DESC LIMIT 8"),
            feeds=q(
                "SELECT * FROM sources WHERE kind NOT IN ('audience','manual') ORDER BY enabled DESC, name"
            ),
            health=health,
            financials=svc.budget.financials(),
            milestone=orch.analyst.milestone(),
            errors=q("SELECT * FROM errors ORDER BY created_at DESC LIMIT 5"),
            usage=q(
                "SELECT provider, COUNT(*) n, SUM(estimated_cost_usd) est, SUM(actual_cost_usd) act, SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)) tokens FROM provider_usage GROUP BY provider ORDER BY n DESC"
            ),
        )

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> HTMLResponse:
        return render(
            request,
            "document.html",
            heading="Privacy Policy",
            body=read_text(svc.env.project_root / "PRIVACY.md"),
        )

    @app.get("/terms", response_class=HTMLResponse)
    def terms(request: Request) -> HTMLResponse:
        return render(
            request,
            "document.html",
            heading="Terms of Service",
            body=read_text(svc.env.project_root / "TERMS.md"),
        )

    @app.get("/ideas", response_class=HTMLResponse)
    def ideas(request: Request) -> HTMLResponse:
        rows = q(
            "SELECT * FROM ideas ORDER BY CASE status WHEN 'selected' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END, opportunity_score DESC LIMIT 200"
        )
        return render(request, "ideas.html", ideas=rows)

    @app.get("/scripts", response_class=HTMLResponse)
    def scripts(request: Request) -> HTMLResponse:
        rows = q(
            "SELECT s.*, i.content_family, i.hook_type FROM scripts s JOIN ideas i ON i.id=s.idea_id ORDER BY s.created_at DESC LIMIT 200"
        )
        return render(request, "scripts.html", scripts=rows)

    @app.get("/scripts/{script_id}", response_class=HTMLResponse)
    def script_detail(request: Request, script_id: str) -> HTMLResponse:
        s = dict(svc.db.get("scripts", script_id) or {})
        idea = dict(svc.db.get("ideas", s["idea_id"]) or {}) if s else {}
        claims = q("SELECT * FROM claims WHERE script_id=? ORDER BY created_at", [script_id])
        return render(
            request,
            "script.html",
            s=s,
            idea=idea,
            claims=claims,
            beats=loads(s.get("beats_json"), []),
            qa=loads(s.get("qa_json"), {}),
            fc=loads(s.get("factcheck_json"), {}),
        )

    @app.get("/videos", response_class=HTMLResponse)
    def videos(request: Request) -> HTMLResponse:
        rows = q(
            "SELECT v.*, i.content_family, i.hook_type FROM videos v JOIN ideas i ON i.id=v.idea_id ORDER BY v.created_at DESC LIMIT 200"
        )
        return render(request, "videos.html", videos=rows)

    @app.get("/videos/{video_id}", response_class=HTMLResponse)
    def video_detail(request: Request, video_id: str) -> HTMLResponse:
        v = dict(svc.db.get("videos", video_id) or {})
        pubs = q("SELECT * FROM publications WHERE video_id=? ORDER BY created_at DESC", [video_id])
        scenes = q("SELECT * FROM scenes WHERE video_id=? ORDER BY idx", [video_id])
        return render(
            request, "video.html", v=v, pubs=pubs, scenes=scenes, timeline=loads(v.get("timeline_json"), {})
        )

    @app.get("/media/{video_id}/{name}")
    def media(video_id: str, name: str) -> FileResponse:
        v = svc.db.get("videos", video_id)
        base = svc.env.data_dir / "videos" / video_id
        if not v or "/" in name or "\\" in name or not (base / name).exists():
            return FileResponse(str(HERE / "static" / "missing.txt"))
        return FileResponse(str(base / name))

    @app.get("/publishing", response_class=HTMLResponse)
    def publishing(request: Request) -> HTMLResponse:
        rows = q(
            "SELECT p.*, v.title FROM publications p JOIN videos v ON v.id=p.video_id ORDER BY p.created_at DESC LIMIT 200"
        )
        return render(request, "publishing.html", pubs=rows)

    @app.get("/metrics", response_class=HTMLResponse)
    def metrics(request: Request) -> HTMLResponse:
        perf = svc.analytics_store.video_performance()
        pubs = q(
            "SELECT p.id, p.platform, v.title FROM publications p JOIN videos v ON v.id=p.video_id ORDER BY p.created_at DESC LIMIT 100"
        )
        return render(
            request,
            "metrics.html",
            perf=perf,
            pubs=pubs,
            snapshots=q("SELECT * FROM metrics ORDER BY captured_at DESC LIMIT 50"),
        )

    @app.get("/experiments", response_class=HTMLResponse)
    def experiments(request: Request) -> HTMLResponse:
        return render(
            request, "experiments.html", exps=q("SELECT * FROM experiments ORDER BY created_at DESC")
        )

    @app.get("/strategy", response_class=HTMLResponse)
    def strategy(request: Request) -> HTMLResponse:
        row = orch.strategy.latest_row()
        state = orch.strategy.current()
        return render(
            request,
            "strategy.html",
            md=row["markdown"] if row else "",
            state=state,
            history=orch.strategy.history(20),
            constitution=read_text(svc.env.config_dir / "constitution.md"),
        )

    @app.get("/feeds", response_class=HTMLResponse)
    def feeds(request: Request) -> HTMLResponse:
        return render(
            request,
            "feeds.html",
            feeds=load_feeds(svc.env.config_dir),
            sources=q("SELECT * FROM sources ORDER BY enabled DESC, items_yielded DESC"),
        )

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request) -> HTMLResponse:
        return render(
            request,
            "runs.html",
            runs=q("SELECT * FROM runs ORDER BY started_at DESC LIMIT 50"),
            agent_runs=q("SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 100"),
            errors=q("SELECT * FROM errors ORDER BY created_at DESC LIMIT 50"),
            ledger=q("SELECT * FROM ledger ORDER BY created_at DESC LIMIT 50"),
        )

    @app.get("/controls", response_class=HTMLResponse)
    def controls(request: Request) -> HTMLResponse:
        return render(
            request,
            "controls.html",
            constitution=read_text(svc.env.config_dir / "constitution.md"),
            state=orch.strategy.current(),
            env_text=_masked_env(svc.env.env_file),
        )

    # ---------------------------------------------------------------- owner controls
    @app.post("/controls/kill")
    def kill(reason: str = Form("dashboard")) -> RedirectResponse:
        svc.killswitch.engage(reason)
        return RedirectResponse("/", status_code=303)

    @app.post("/controls/resume")
    def resume() -> RedirectResponse:
        svc.killswitch.release()
        return RedirectResponse("/", status_code=303)

    @app.post("/controls/budget")
    def set_budget(
        monthly_budget_usd: float = Form(...), allow_paid_providers: str = Form("false")
    ) -> RedirectResponse:
        update_env_file(
            svc.env.env_file,
            {
                "MONTHLY_BUDGET_USD": f"{max(0.0, monthly_budget_usd):.2f}",
                "ALLOW_PAID_PROVIDERS": "true" if allow_paid_providers == "true" else "false",
            },
        )
        record_config(
            "budget",
            f"MONTHLY_BUDGET_USD={monthly_budget_usd:.2f} ALLOW_PAID_PROVIDERS={allow_paid_providers}",
        )
        return RedirectResponse("/controls?restart=1", status_code=303)

    @app.post("/controls/publishing")
    def set_publishing(
        youtube_mode: str = Form("draft"),
        youtube_enabled: str = Form("false"),
        youtube_analytics_enabled: str = Form("false"),
    ) -> RedirectResponse:
        mode = youtube_mode if youtube_mode in {"draft", "private", "scheduled", "public"} else "draft"
        update_env_file(
            svc.env.env_file,
            {
                "YOUTUBE_MODE": mode,
                "YOUTUBE_ENABLED": youtube_enabled,
                "YOUTUBE_ANALYTICS_ENABLED": youtube_analytics_enabled,
            },
        )
        record_config(
            "youtube_mode", f"{mode} enabled={youtube_enabled} analytics={youtube_analytics_enabled}"
        )
        return RedirectResponse("/controls?restart=1", status_code=303)

    @app.post("/controls/credentials")
    def set_credentials(client_secret_file: str = Form(...), token_file: str = Form(...)) -> RedirectResponse:
        # Only *paths* are stored; secret contents never pass through the dashboard.
        update_env_file(
            svc.env.env_file,
            {
                "YOUTUBE_CLIENT_SECRET_FILE": client_secret_file.strip(),
                "YOUTUBE_TOKEN_FILE": token_file.strip(),
            },
        )
        record_config("credentials_paths", f"{client_secret_file} {token_file}")
        return RedirectResponse("/controls?restart=1", status_code=303)

    @app.post("/controls/constitution")
    def edit_constitution(content: str = Form(...)) -> RedirectResponse:
        (svc.env.config_dir / "constitution.md").write_text(content, encoding="utf-8")
        record_config("constitution", content)
        return RedirectResponse("/strategy", status_code=303)

    @app.post("/controls/strategy")
    def edit_strategy(state_json: str = Form(...)) -> RedirectResponse:
        state = json.loads(state_json)
        orch.strategy.save(state, created_by="owner", change_summary="owner edit via dashboard")
        return RedirectResponse("/strategy", status_code=303)

    @app.post("/feeds/add")
    def feed_add(
        name: str = Form(...),
        url: str = Form(...),
        kind: str = Form("rss"),
        category: str = Form(""),
        credibility: float = Form(0.5),
    ) -> RedirectResponse:
        feeds = load_feeds(svc.env.config_dir)
        feeds.append(
            {
                "name": name,
                "kind": kind,
                "url": url,
                "category": category or None,
                "credibility": credibility,
                "enabled": True,
            }
        )
        save_feeds(svc.env.config_dir, feeds)
        record_config("feeds", json.dumps(feeds))
        orch.research.sync_sources()
        return RedirectResponse("/feeds", status_code=303)

    @app.post("/feeds/remove")
    def feed_remove(url: str = Form(...)) -> RedirectResponse:
        feeds = [f for f in load_feeds(svc.env.config_dir) if f.get("url") != url]
        save_feeds(svc.env.config_dir, feeds)
        record_config("feeds", json.dumps(feeds))
        orch.research.sync_sources()
        return RedirectResponse("/feeds", status_code=303)

    @app.post("/feeds/toggle")
    def feed_toggle(url: str = Form(...)) -> RedirectResponse:
        feeds = load_feeds(svc.env.config_dir)
        for f in feeds:
            if f.get("url") == url:
                f["enabled"] = not f.get("enabled", True)
        save_feeds(svc.env.config_dir, feeds)
        orch.research.sync_sources()
        return RedirectResponse("/feeds", status_code=303)

    @app.post("/scripts/{script_id}/decide")
    def script_decide(script_id: str, decision: str = Form(...)) -> RedirectResponse:
        svc.db.update(
            "scripts",
            script_id,
            {"status": "approved" if decision == "approve" else "rejected", "updated_at": now_iso()},
        )
        return RedirectResponse(f"/scripts/{script_id}", status_code=303)

    @app.post("/videos/{video_id}/decide")
    def video_decide(video_id: str, decision: str = Form(...)) -> RedirectResponse:
        svc.db.update(
            "videos",
            video_id,
            {"status": "approved" if decision == "approve" else "rejected", "updated_at": now_iso()},
        )
        return RedirectResponse(f"/videos/{video_id}", status_code=303)

    @app.post("/publications/{pub_id}/mark-posted")
    def mark_posted(pub_id: str, url: str = Form(""), platform_video_id: str = Form("")) -> RedirectResponse:
        orch.publisher.mark_posted(pub_id, url or None, platform_video_id or None)
        return RedirectResponse("/publishing", status_code=303)

    @app.post("/metrics/add")
    def metric_add(
        publication_id: str = Form(...),
        views: str = Form(""),
        impressions: str = Form(""),
        ctr: str = Form(""),
        retention_3s: str = Form(""),
        avg_watch_time_s: str = Form(""),
        avg_percent_viewed: str = Form(""),
        completion_rate: str = Form(""),
        likes: str = Form(""),
        comments: str = Form(""),
        shares: str = Form(""),
        subscribers_gained: str = Form(""),
        followers_gained: str = Form(""),
        returning_viewers: str = Form(""),
        revenue_usd: str = Form(""),
    ) -> RedirectResponse:
        def num(v: str, cast: Any) -> Any:
            v = v.strip()
            return cast(v) if v else None

        pub = svc.db.get("publications", publication_id)
        if pub:
            snap = MetricsSnapshot(
                views=num(views, int),
                impressions=num(impressions, int),
                ctr=num(ctr, float),
                retention_3s=num(retention_3s, float),
                avg_watch_time_s=num(avg_watch_time_s, float),
                avg_percent_viewed=num(avg_percent_viewed, float),
                completion_rate=num(completion_rate, float),
                likes=num(likes, int),
                comments=num(comments, int),
                shares=num(shares, int),
                subscribers_gained=num(subscribers_gained, int),
                followers_gained=num(followers_gained, int),
                returning_viewers=num(returning_viewers, int),
                revenue_usd=num(revenue_usd, float),
            )
            svc.analytics_store.record(dict(pub), snap, source="manual")
        return RedirectResponse("/metrics", status_code=303)

    @app.get("/publications/{pub_id}/tiktok", response_class=HTMLResponse)
    def tiktok_post_form(request: Request, pub_id: str) -> HTMLResponse:
        pub = dict(svc.db.get("publications", pub_id) or {})
        info: dict[str, Any] = {}
        error = ""
        publisher = svc.publishers.get("tiktok")
        client = getattr(publisher, "client", None)
        if client is not None:
            try:
                info = client.creator_info()
            except Exception as exc:
                error = str(exc)
        else:
            error = "TIKTOK_MODE is not 'direct' (set it in .env and run `aimz tiktok auth`)"
        return render(request, "tiktok_post.html", pub=pub, info=info, error=error)

    @app.post("/publications/{pub_id}/tiktok")
    def tiktok_post(pub_id: str, privacy_level: str = Form(...), consent: str = Form("")) -> RedirectResponse:
        """One-tap TikTok post: the owner picks privacy from creator_info options and consents explicitly."""
        pub = svc.db.get("publications", pub_id)
        if pub and consent == "yes":
            if pub["status"] in {"packaged", "blocked", "failed"}:
                svc.db.update("publications", pub_id, {"status": "pending", "updated_at": now_iso()})
            with svc.tracker.run("publish") as ctx:
                orch.publisher.publish(
                    ctx,
                    pub["video_id"],
                    platforms=["tiktok"],
                    owner_approved=True,
                    extra_metadata={"tiktok_privacy_level": privacy_level},
                )
        return RedirectResponse("/publishing", status_code=303)

    @app.post("/publications/{pub_id}/retry")
    def retry_publish(pub_id: str) -> RedirectResponse:
        """Owner explicitly re-queues a failed publication (the system never does this on its own)."""
        pub = svc.db.get("publications", pub_id)
        if pub and pub["status"] in {"failed", "blocked"}:
            svc.db.update("publications", pub_id, {"status": "pending", "updated_at": now_iso()})
            with svc.tracker.run("publish") as ctx:
                orch.publisher.publish(ctx, pub["video_id"], platforms=[pub["platform"]], owner_approved=True)
        return RedirectResponse("/publishing", status_code=303)

    return app


def _masked_env(path: Path) -> str:
    if not path.exists():
        return "(no .env file; defaults in use)"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            if any(s in k.upper() for s in ("SECRET", "TOKEN", "KEY", "PASSWORD")) and v.strip():
                v = "<masked>"
            out.append(f"{k}={v}")
        else:
            out.append(line)
    return "\n".join(out)
