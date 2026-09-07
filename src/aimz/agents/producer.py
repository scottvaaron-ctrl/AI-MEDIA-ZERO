"""Producer: approved script -> narration -> licensed visuals -> internal timeline -> MP4 + thumbnail + captions."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import AssetRecord, Beat, Scene, Timeline
from aimz.util import loads, new_id, now_iso, slugify

SCENE_PAD_S = 0.35
_STOP = {"the", "a", "an", "of", "and", "in", "on", "for", "to", "with", "by", "at", "from", "over", "about"}


def asset_queries(visual_query: str, title: str) -> list[str]:
    """Commons search works best with a few concrete nouns: strip numbers/stopwords, fall back to the title."""
    out: list[str] = []
    for text in (visual_query, title):
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z-]+", text) if w.lower() not in _STOP]
        if len(words) >= 2:
            out.append(" ".join(words[:4]))
        if len(words) >= 3:
            out.append(" ".join(words[:2]))
    return list(dict.fromkeys(q for q in out if q))


class ProducerAgent(Agent):
    name = "producer"

    def produce(self, run: RunContext, script_id: str) -> str:
        db = self.svc.db
        script = db.get("scripts", script_id)
        if not script:
            raise ValueError(f"script {script_id} not found")
        if script["status"] != "approved":
            raise ValueError(f"script {script_id} is '{script['status']}', not approved")
        idea = dict(db.get("ideas", script["idea_id"]) or {})
        sources = self.load_sources(self.idea_source_ids(idea))
        beats = [Beat.model_validate(b) for b in loads(script["beats_json"], [])]
        sf = self.svc.config.short_form
        width, height = (sf.get("resolution") or [1080, 1920])[:2]
        fps = int(sf.get("fps", 30))
        disclosure = str(self.svc.config.get("publishing.ai_disclosure_text", "AI narration"))

        video_id = new_id("vid")
        out_dir = self.svc.env.data_dir / "videos" / video_id
        out_dir.mkdir(parents=True, exist_ok=True)
        db.insert(
            "videos",
            {
                "id": video_id,
                "script_id": script_id,
                "idea_id": idea["id"],
                "run_id": run.id,
                "title": script["title"],
                "format": "short",
                "resolution": f"{width}x{height}",
                "ai_disclosure": disclosure,
                "status": "rendering",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
        t0 = time.perf_counter()
        try:
            with self.svc.tracker.agent(
                run, self.name, "produce", {"script_id": script_id, "video_id": video_id}
            ) as span:
                ctx = self.pctx(run, video_id, span)
                scenes: list[Scene] = []
                attributions: list[str] = []
                cursor = 0.0
                first_image: str | None = None
                for i, beat in enumerate(beats):
                    # ---- narration --------------------------------------------------
                    wav = out_dir / f"scene_{i:02d}.wav"
                    tts = self.svc.tts.synthesize(ctx, beat.narration, wav)
                    chunks = getattr(tts, "chunks", [])
                    duration = tts.duration_s + SCENE_PAD_S
                    # ---- visual ------------------------------------------------------
                    # Try a licensed photo for every beat except quotes; the card renders on top of it.
                    asset: AssetRecord | None = None
                    if beat.visual_type != "quote_card":
                        for query in asset_queries(beat.visual_query, script["title"]):
                            asset = self._find_asset(ctx, query, out_dir / "assets")
                            if asset is not None:
                                break
                    if beat.visual_type in {"stat_card", "quote_card"}:
                        headline = beat.caption or beat.visual_query or script["title"]
                    elif beat.visual_type == "image":
                        headline = beat.caption or script["title"]
                    else:
                        headline = beat.caption or beat.visual_query or script["title"]
                    spec: dict[str, Any] = {
                        "kind": beat.visual_type,
                        "seed": video_id,
                        "headline": headline[:80],
                        "body": "",
                        "image_path": asset.file_path if asset else None,
                        "attribution": asset.attribution if asset else "",
                        "disclosure": disclosure if i == 0 else "",
                        "label": "SOURCES IN DESCRIPTION" if i == len(beats) - 1 else None,
                    }
                    png = self.svc.cards.render_card(ctx, spec, out_dir / f"scene_{i:02d}.png")
                    if asset and first_image is None:
                        first_image = asset.file_path
                    if asset and asset.attribution:
                        attributions.append(asset.attribution)
                    scenes.append(
                        Scene(
                            idx=i,
                            start_s=round(cursor, 3),
                            duration_s=round(duration, 3),
                            narration=beat.narration,
                            caption=beat.caption,
                            visual_type=spec["kind"],
                            asset_id=asset.id if asset else None,
                            asset_path=asset.file_path if asset else None,
                            zoom="in" if i % 2 == 0 else "out",
                            source="; ".join(beat.source_refs),
                            disclosure=disclosure if i == 0 else "",
                            audio_path=str(wav),
                            image_path=str(png),
                            caption_chunks=[(round(a, 3), round(b, 3), t) for a, b, t in chunks],
                        )
                    )
                    cursor += duration
                timeline = Timeline(
                    video_id=video_id,
                    title=script["title"],
                    width=int(width),
                    height=int(height),
                    fps=fps,
                    scenes=scenes,
                    total_duration_s=round(cursor, 3),
                    disclosure_text=disclosure,
                    sources=[
                        {"id": s.id, "title": s.title, "url": s.url, "source": s.source_name} for s in sources
                    ],
                    attributions=list(dict.fromkeys(attributions)),
                )
                (out_dir / "timeline.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
                result = self.svc.renderer.render(ctx, timeline, out_dir)
                thumb = self.svc.cards.render_thumbnail(
                    ctx, script["title"], video_id, out_dir / "thumbnail.png", first_image
                )
                for sc in scenes:
                    db.insert(
                        "scenes",
                        {
                            "id": new_id("scn"),
                            "video_id": video_id,
                            "idx": sc.idx,
                            "start_s": sc.start_s,
                            "duration_s": sc.duration_s,
                            "narration": sc.narration,
                            "caption": sc.caption,
                            "visual_type": sc.visual_type,
                            "asset_id": sc.asset_id,
                            "crop": sc.crop,
                            "zoom": sc.zoom,
                            "animation": sc.animation,
                            "transition": sc.transition,
                            "source": sc.source,
                            "disclosure": sc.disclosure,
                            "notes": sc.notes,
                            "audio_path": sc.audio_path,
                            "image_path": sc.image_path,
                        },
                    )
                db.update(
                    "videos",
                    video_id,
                    {
                        "duration_s": round(result.duration_s, 2),
                        "file_path": result.video_path,
                        "thumbnail_path": str(thumb),
                        "captions_path": result.captions_path,
                        "timeline_json": timeline.model_dump_json(),
                        "production_seconds": round(time.perf_counter() - t0, 1),
                        "production_cost_usd": 0.0,
                        "status": "rendered",
                        "updated_at": now_iso(),
                    },
                )
                db.update("ideas", idea["id"], {"status": "produced", "updated_at": now_iso()})
                span.output_refs["video_path"] = result.video_path
                span.output_refs["duration_s"] = result.duration_s
        except Exception as exc:
            db.update(
                "videos",
                video_id,
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                    "production_seconds": round(time.perf_counter() - t0, 1),
                    "updated_at": now_iso(),
                },
            )
            raise
        run.bump("videos_rendered")
        return video_id

    def _find_asset(self, ctx: Any, query: str, dest: Path) -> AssetRecord | None:
        max_results = int(self.svc.config.get("assets.wikimedia.max_results", 6))
        for provider in self.svc.assets:
            try:
                cands = provider.search(ctx, query, max_results=max_results)
            except Exception as exc:
                self.log.warning("asset search failed on %s: %s", provider.name, exc)
                continue
            for cand in cands:
                try:
                    rec = provider.fetch(ctx, cand, dest)
                except Exception as exc:
                    self.log.warning("asset fetch failed: %s", exc)
                    continue
                if rec is not None:
                    self.svc.db.update("assets", rec.id, {"query": query[:200]})
                    return rec
        return None


def package_name(video: dict[str, Any]) -> str:
    return f"{video['created_at'][:10]}_{slugify(video['title'])}_{video['id'][-8:]}"
