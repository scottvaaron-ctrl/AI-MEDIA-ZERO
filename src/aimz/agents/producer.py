"""Producer: approved script -> narration -> licensed visuals -> internal timeline -> MP4 + thumbnail + captions."""

from __future__ import annotations

import math
import random
import re
import time
import wave
from pathlib import Path
from typing import Any

from aimz import guideline, identity
from aimz.agents.base import Agent
from aimz.core.attention import (  # picture-check states (aimz status)
    DEFAULT_RELEVANCE_FIX,
    PREVIEWS_STATE,
    RELEVANCE_FIX_STATE,
    RELEVANCE_STATE,
    VOICE_FIX_STATE,
    VOICE_STATE,
)
from aimz.core.errors import VoiceUnavailable
from aimz.core.runs import RunContext
from aimz.domain.models import AssetCandidate, AssetRecord, Beat, CaptionStyle, Scene, Timeline
from aimz.experiments import shared
from aimz.experiments.allocation import per_video
from aimz.experiments.settings import SettingsEngine, capabilities
from aimz.guideline import Guideline
from aimz.providers.audio.library import sfx, track_for
from aimz.providers.base import SpeechOptions
from aimz.util import loads, new_id, now_iso, slugify

SCENE_PAD_S = 0.35
# Narration voice used for a whole video when the Kokoro voice fails (owner decision 2026-10-08).
FALLBACK_VOICE = "en_US-kristin-medium"
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


def rank_candidates(
    scored: list[tuple[AssetCandidate, float]], threshold: float, by_sa_penalty: float
) -> list[AssetCandidate]:
    """Candidates whose score reaches ``threshold``, best first; CC BY-SA ranks ``by_sa_penalty`` lower."""
    passing = [(c, s) for c, s in scored if s >= threshold]

    def rank(item: tuple[AssetCandidate, float]) -> float:
        cand, score = item
        return score - (by_sa_penalty if cand.license.upper().startswith("CC BY-SA") else 0.0)

    return [c for c, _ in sorted(passing, key=rank, reverse=True)]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("\u2019", "'"))


def repeats_narration(text: str, narration: str) -> bool:
    """True when on-screen text only repeats the spoken words: the burned captions already show them, so the
    card would show the same sentence twice."""
    said = _words(narration)
    shown = _words(text)
    if not shown or not said:
        return False
    heard = set(said)
    return sum(1 for w in shown if w in heard) / len(shown) >= 0.8 and len(shown) >= 3


LOOP_TAIL_S = 0.8  # the renderer never makes a scene shorter than 0.8 s


def write_silence(path: Path, seconds: float, like: Path) -> None:
    """A silent WAV in the same format as ``like`` (the narration), or 22.05 kHz mono 16-bit."""
    rate, width, channels = 22050, 2, 1
    if like.is_file():
        with wave.open(str(like), "rb") as r:
            rate, width, channels = r.getframerate(), r.getsampwidth(), r.getnchannels()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(int(seconds * rate) * width * channels))


def audio_bed(chosen: dict[str, Any], scenes: list[Scene]) -> dict[str, Any]:
    """Timeline fields for the music bed and cut sounds under the chosen settings."""
    out: dict[str, Any] = {}
    mood = chosen.get("music")
    track = track_for(str(mood)) if mood and mood != "off" else None
    if track is not None:
        out["music_path"] = str(track)
        if chosen.get("music_volume_db") is not None:
            out["music_volume_db"] = float(chosen["music_volume_db"])
    whoosh = sfx("whoosh") if chosen.get("sfx") == "on" else None
    if whoosh is not None:
        out["sfx_path"] = str(whoosh)
        out["sfx_times"] = [s.start_s for s in scenes[1:] if s.narration]  # not into the silent loop tail
    return out


def motion_zoom(style: str, idx: int) -> str:
    """The camera move for shot ``idx`` under a video-level motion style."""
    if style in {"in", "out", "none"}:
        return style
    return "in" if idx % 2 == 0 else "out"


def caption_style(chosen: dict[str, Any]) -> CaptionStyle:
    fields = {
        "font": chosen.get("caption_font"),
        "size": chosen.get("caption_size"),
        "position": chosen.get("caption_position"),
        "color": chosen.get("caption_color"),
        "outline": chosen.get("caption_outline"),
        "max_words": chosen.get("caption_max_words"),
    }
    return CaptionStyle(**{k: v for k, v in fields.items() if v is not None})


def pad_wav(path: Path, total_s: float) -> None:
    """Append silence so the narration file lasts ``total_s``, so the beat pause is really heard."""
    with wave.open(str(path), "rb") as r:
        params = r.getparams()
        frames = r.readframes(r.getnframes())
    need = int(round(total_s * params.framerate)) - params.nframes
    if need <= 0:
        return
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(frames + bytes(need * params.sampwidth * params.nchannels))


def split_shots(
    wav: Path, total_s: float, chunks: list[tuple[float, float, str]], max_scene_s: float | None
) -> list[tuple[Path, float, list[tuple[float, float, str]]]]:
    """Cut one beat into shots of equal length, none longer than ``max_scene_s``.

    Returns (audio file, duration, caption chunks relative to the shot) per shot. The narration file must
    already be padded to ``total_s``.
    """
    if not max_scene_s or total_s <= max_scene_s:
        return [(wav, total_s, chunks)]
    n = math.ceil(total_s / max_scene_s)
    seg = total_s / n
    with wave.open(str(wav), "rb") as r:
        params = r.getparams()
        frames = r.readframes(r.getnframes())
    frame_bytes = params.sampwidth * params.nchannels
    out: list[tuple[Path, float, list[tuple[float, float, str]]]] = []
    for k in range(n):
        s, e = k * seg, (k + 1) * seg
        a = int(round(s * params.framerate)) * frame_bytes
        b = int(round(e * params.framerate)) * frame_bytes if k < n - 1 else len(frames)
        part = wav.with_name(f"{wav.stem}_{k}.wav")
        with wave.open(str(part), "wb") as w:
            w.setparams(params)
            w.writeframes(frames[a:b])
        shot_chunks = [
            (round(max(c0, s) - s, 3), round(min(c1, e) - s, 3), t)
            for c0, c1, t in chunks
            if min(c1, e) - max(c0, s) > 0.15
        ]
        out.append((part, e - s, shot_chunks))
    return out


class ProducerAgent(Agent):
    name = "producer"

    def __init__(self, svc: Any, strategy: Any, rng: random.Random | None = None, experiments: Any = None):
        super().__init__(svc, strategy)
        self.settings = SettingsEngine(svc.db, caps=capabilities(svc))
        self.rng = rng or random.Random()
        # The ExperimentEngine: a G-<setting> treatment value reaches the render through it.
        self.experiments = experiments
        self._guideline: Guideline | None = None
        self._guideline_loaded = False
        # Picture-matching check: (run id, reason) once it is unavailable for that run; per-beat records.
        self._gate_off: tuple[str | None, str] | None = None
        # (run id,) once that run has warned that no preview could be loaded (a tuple so a None run id counts).
        self._previews_warned: tuple[str | None] | None = None
        # (run id,) once that run has warned that the Kokoro voice is unavailable.
        self._voice_warned: tuple[str | None] | None = None
        self._relevance_beats: list[dict[str, Any]] = []
        self._relevance_fallback = 0
        self._query_log: list[dict[str, Any]] | None = None

    def default_settings(self) -> dict[str, Any]:
        """What the renderer does for a setting that is off (today's behaviour)."""
        base = CaptionStyle()
        return {
            "speech_length_scale": getattr(self.svc.tts, "length_scale", None),
            "sentence_pause_s": getattr(self.svc.tts, "sentence_pause_s", None),
            "beat_pause_s": SCENE_PAD_S,
            "voice": getattr(self.svc.tts, "voice", None),
            "caption_font": base.font,
            "caption_size": base.size,
            "caption_position": base.position,
            "caption_color": base.color,
            "caption_outline": int(base.outline),
            "caption_max_words": None,
            "motion_style": "alternate",
            "max_scene_s": None,
            "palette_hue": None,
            "headline_on_cards": "on",
            "progress_counter": "off",
            "template": "full_bleed",
            "clip_scenes": "off",
            "music": "off",
            "music_volume_db": -14.0,
            "sfx": "off",
            "ending": "plain",
        }

    def choose_settings(self, video_id: str, idea: dict[str, Any]) -> dict[str, Any]:
        """Every setting this video is made with, recorded in ``video_settings`` before anything renders."""
        defaults = self.default_settings()
        own = per_video(self.svc.analytics_store.video_performance())
        scored = shared.pooled_rows(self.svc.env.instance, own, self.settings)
        fixed = identity.fixed_settings(self.svc.db)
        exclude = {"voice": identity.taken_voices(self.svc.env.instance)}
        g = self.guideline()
        return self.settings.assign(
            video_id,
            idea,
            defaults,
            scored,
            self.rng,
            fixed,
            exclude,
            guideline=g.locked if g else None,
            unavailable=set(g.unavailable) if g else None,
            experiment=self.experiments.setting_override(idea) if self.experiments is not None else None,
        ).values

    def guideline(self) -> Guideline | None:
        """The active guideline, loaded once per cycle (None when the channel has never had one)."""
        if not self._guideline_loaded:
            self._guideline = guideline.for_services(self.svc)
            self._guideline_loaded = True
        return self._guideline

    def assemble(
        self,
        ctx: Any,
        beats: list[Beat],
        chosen: dict[str, Any],
        out_dir: Path,
        video_id: str,
        title: str,
        disclosure: str,
        search_assets: bool = True,
    ) -> tuple[list[Scene], list[str], str | None, float]:
        """Narration, cards and shots for every beat under the chosen settings.

        Returns (scenes, attributions, first licensed image, total seconds). Also used by the render
        matrix (``scripts/render_matrix.py``), so samples go through exactly this code."""
        speech = SpeechOptions(
            length_scale=chosen.get("speech_length_scale"),
            sentence_pause_s=chosen.get("sentence_pause_s"),
            voice=chosen.get("voice"),
        )
        pad = chosen.get("beat_pause_s")
        beat_pad = SCENE_PAD_S if pad is None else float(pad)
        motion = str(chosen.get("motion_style") or "alternate")
        max_scene = chosen.get("max_scene_s")
        hue = chosen.get("palette_hue")
        show_headline = chosen.get("headline_on_cards") != "off"
        counter_on = chosen.get("progress_counter") == "on"
        template = str(chosen.get("template") or "full_bleed")
        clips_on = chosen.get("clip_scenes") == "on"
        scenes: list[Scene] = []
        attributions: list[str] = []
        cursor = 0.0
        shot_idx = 0
        first_image: str | None = None
        self._relevance_beats = []
        self._relevance_fallback = 0
        for i, beat in enumerate(beats):
            # ---- narration --------------------------------------------------
            wav = out_dir / f"scene_{i:02d}.wav"
            tts = self.svc.tts.synthesize(ctx, beat.narration, wav, options=speech)
            chunks = getattr(tts, "chunks", [])
            duration = tts.duration_s + beat_pad
            # ---- visual ------------------------------------------------------
            # Try a licensed photo for every beat except quotes; the card renders on top of it.
            asset: AssetRecord | None = None
            if search_assets and beat.visual_type != "quote_card":
                asset = self._pick_asset(ctx, beat, title, out_dir / "assets", i)
            clip: AssetRecord | None = None
            if clips_on and search_assets and beat.visual_type in {"image", "text_card"}:
                for query in asset_queries(beat.visual_query, title):
                    clip = self._find_clip(ctx, query, out_dir / "assets")
                    if clip is not None:
                        break
            # Card text that only repeats the narration is dropped: the subtitles already show those words.
            options = [beat.caption, beat.visual_query] if beat.visual_type != "image" else [beat.caption]
            if beat.visual_type not in {"stat_card", "quote_card", "image"} or not beat.caption:
                options.append(title)
            fresh = [t for t in options if t and not repeats_narration(t, beat.narration)]
            headline = fresh[0] if fresh else ""
            spec: dict[str, Any] = {
                "kind": beat.visual_type,
                "seed": video_id,
                "headline": headline[:80],
                "body": "",
                "image_path": asset.file_path if asset else None,
                "attribution": asset.attribution if asset else "",
                "disclosure": disclosure if i == 0 else "",
                "label": "SOURCES IN DESCRIPTION" if i == len(beats) - 1 else None,
                "palette_hue": hue,
                "show_headline": show_headline,
                "counter": f"{i + 1}/{len(beats)}" if counter_on else None,
                "template": template,
                "rank": i + 1,
            }
            if clip is not None:  # the card becomes a transparent text layer over the clip
                spec.update(overlay=True, image_path=None, attribution=clip.attribution)
            png = self.svc.cards.render_card(ctx, spec, out_dir / f"scene_{i:02d}.png")
            if asset and first_image is None:
                first_image = asset.file_path
            if asset and asset.attribution and clip is None:
                attributions.append(asset.attribution)
            if clip is not None and clip.attribution:
                attributions.append(clip.attribution)
            # The beat's own zoom (the script's choice) wins; otherwise the video's motion style.
            # Extra shots of a long beat reverse the move so the frame keeps changing.
            zoom = beat.zoom or motion_zoom(motion, shot_idx)
            pad_wav(wav, duration)
            timed = [(round(a, 3), round(b, 3), t) for a, b, t in chunks]
            shots = split_shots(wav, duration, timed, float(max_scene) if max_scene else None)
            for k, (part, part_s, part_chunks) in enumerate(shots):
                shot_zoom = zoom if k % 2 == 0 else {"in": "out", "out": "in"}.get(zoom, zoom)
                scenes.append(
                    Scene(
                        idx=shot_idx,
                        start_s=round(cursor, 3),
                        duration_s=round(part_s, 3),
                        narration=beat.narration,
                        caption=beat.caption,
                        visual_type=spec["kind"],
                        asset_id=asset.id if asset else None,
                        asset_path=asset.file_path if asset else None,
                        zoom=shot_zoom,
                        source="; ".join(beat.source_refs),
                        disclosure=disclosure if i == 0 else "",
                        audio_path=str(part),
                        image_path=str(png),
                        caption_chunks=part_chunks,
                        clip_path=clip.file_path if clip is not None else None,
                    )
                )
                cursor += part_s
                shot_idx += 1
        if chosen.get("ending") == "loop" and scenes:
            # End on the opening frame, silent, so the platform's replay continues seamlessly.
            tail = LOOP_TAIL_S
            silent = out_dir / "loop_tail.wav"
            write_silence(silent, tail, Path(scenes[0].audio_path or ""))
            scenes.append(
                scenes[0].model_copy(
                    update={
                        "idx": shot_idx,
                        "start_s": round(cursor, 3),
                        "duration_s": tail,
                        "zoom": "none",
                        "audio_path": str(silent),
                        "caption_chunks": [],
                        "narration": "",
                        "caption": "",
                    }
                )
            )
            cursor += tail
        return scenes, attributions, first_image, cursor

    def _voice_down(self, run: RunContext, exc: Exception) -> None:
        """Record why Kokoro failed (``aimz status``) and warn once per run."""
        reason = str(exc)
        kokoro = getattr(self.svc.tts, "kokoro", None)
        fix = None
        if kokoro is not None:
            try:
                fix = kokoro.health().fix
            except Exception:
                fix = None
        self.svc.db.set_state(VOICE_STATE, reason[:500])
        self.svc.db.set_state(VOICE_FIX_STATE, (fix or DEFAULT_RELEVANCE_FIX)[:200])
        if self._voice_warned != (run.id,):
            self._voice_warned = (run.id,)
            self.log.warning("Kokoro voice unavailable (%s); narrating with %s", reason, FALLBACK_VOICE)

    def _voice_up(self) -> None:
        for state in (VOICE_STATE, VOICE_FIX_STATE):
            if self.svc.db.get_state(state):
                self.svc.db.set_state(state, "")

    def _find_clip(self, ctx: Any, query: str, dest: Path) -> AssetRecord | None:
        for provider in self.svc.assets:
            if not hasattr(provider, "search_videos"):
                continue
            try:
                cands = provider.search_videos(ctx, query, max_results=3)
            except Exception as exc:
                self.log.warning("clip search failed on %s: %s", provider.name, exc)
                continue
            for cand in cands:
                rec = provider.fetch(ctx, cand, dest)
                if rec is not None:
                    self.svc.db.update("assets", rec.id, {"query": query[:200]})
                    return rec
        return None

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
            chosen = self.choose_settings(video_id, idea)
            with self.svc.tracker.agent(
                run, self.name, "produce", {"script_id": script_id, "video_id": video_id}
            ) as span:
                ctx = self.pctx(run, video_id, span)
                try:
                    scenes, attributions, first_image, cursor = self.assemble(
                        ctx, beats, chosen, out_dir, video_id, script["title"], disclosure
                    )
                    if str(chosen.get("voice") or "").startswith("kokoro:"):
                        self._voice_up()
                except VoiceUnavailable as exc:
                    # One video never mixes voices: the whole video is narrated again with the fallback.
                    self._voice_down(run, exc)
                    self.settings.overwrite(video_id, "voice", FALLBACK_VOICE, "voice_fallback")
                    self.settings.overwrite(video_id, "voice_fallback", chosen.get("voice"), "voice_fallback")
                    chosen = {**chosen, "voice": FALLBACK_VOICE}
                    scenes, attributions, first_image, cursor = self.assemble(
                        ctx, beats, chosen, out_dir, video_id, script["title"], disclosure
                    )
                relevance_note = self.relevance_note()
                if relevance_note is not None:
                    run.note("relevance", {**run.summary.get("relevance", {}), video_id: relevance_note})
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
                    caption_style=caption_style(chosen),
                    **audio_bed(chosen, scenes),
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

    # ---- picture-matching check (spec 2026-10-08-picture-relevance, sections 4-5) -----------------------------

    def _relevance_cfg(self) -> dict[str, Any]:
        cfg = self.svc.config.get("assets.relevance", {})
        return cfg if isinstance(cfg, dict) else {}

    def _match_text(self, beat: Beat) -> str | tuple[str, str] | None:
        """What the beat's pictures are scored against (``assets.relevance.text``); None when the check is off."""
        cfg = self._relevance_cfg()
        if not cfg.get("enabled"):
            return None
        signal = str(cfg.get("text") or "narration")
        if signal == "visual_query":
            return beat.visual_query or beat.narration
        if signal == "max" and beat.visual_query:
            return (beat.narration, beat.visual_query)
        return beat.narration

    def _gate_reason(self, ctx: Any) -> str | None:
        """Why the check is off for the rest of this run, or None while it is usable."""
        off = self._gate_off
        if off is not None and off[0] == getattr(ctx, "run_id", None):
            return off[1]
        return None

    def _gate_down(self, ctx: Any, reason: str, fix: str = "") -> None:
        """First download wins for the rest of this run; warn once and show it, with the command that
        fixes it, in ``aimz status``."""
        if self._gate_reason(ctx) is None:
            self.log.warning("picture-matching check unavailable, first download wins this run: %s", reason)
        self._gate_off = (getattr(ctx, "run_id", None), reason)
        self.svc.db.set_state(RELEVANCE_STATE, reason[:500])
        self.svc.db.set_state(RELEVANCE_FIX_STATE, (fix or DEFAULT_RELEVANCE_FIX)[:200])

    def _gate_scorer(self, ctx: Any) -> Any | None:
        """The healthy relevance scorer, or None (recorded once per run as unavailable)."""
        if self._gate_reason(ctx) is not None:
            return None
        from aimz.providers.image import relevance

        scorer = relevance.get_scorer(self.svc)
        if scorer is None:
            health = relevance.scorer_health(Path(self.svc.env.siglip_model_dir))
            if health.ok:  # files and packages are there, so the model itself failed to load or download
                self._gate_down(ctx, "SigLIP model could not be downloaded", DEFAULT_RELEVANCE_FIX)
            else:
                self._gate_down(ctx, health.detail, health.fix)
        return scorer

    def _pick_asset(self, ctx: Any, beat: Beat, title: str, dest: Path, idx: int) -> AssetRecord | None:
        """The beat's picture: the best one that passes the check, else none (text card); with the check
        off or unavailable, the first one that downloads."""
        queries = asset_queries(beat.visual_query, title)
        text = self._match_text(beat)
        if text is not None and self._gate_scorer(ctx) is not None:
            self._query_log = []
            asset: AssetRecord | None = None
            for query in queries:
                asset = self._find_asset(ctx, query, dest, text)
                if asset is not None or self._gate_reason(ctx) is not None:
                    break
            tried, self._query_log = self._query_log, None
            starved = (  # only while the check is still up: a scorer crash is its own alarm
                self._gate_reason(ctx) is None
                and asset is None
                and sum(q["candidates"] for q in tried) > 0
                and all(q["top_score"] is None for q in tried)
            )
            if self._gate_reason(ctx) is None and not starved:
                self._relevance_beats.append(
                    {"beat": idx, "queries": tried, "chosen": asset.id if asset is not None else "text_card"}
                )
                return asset
            if starved:
                self._previews_failing(ctx, sum(q["candidates"] for q in tried))
            self._relevance_fallback += 1
            if asset is not None:  # the scorer failed and _find_asset already fell back for that query
                return asset
        elif text is not None:
            self._relevance_fallback += 1
        for query in queries:
            asset = self._find_asset(ctx, query, dest)
            if asset is not None:
                return asset
        return None

    def _previews_failing(self, ctx: Any, candidates: int) -> None:
        """A beat found candidates but could load none of their previews: it falls back to first download
        wins (not a text card); warn once per run and show it in ``aimz status`` until a preview scores."""
        run_id = getattr(ctx, "run_id", None)
        reason = f"{candidates} picture(s) found but no preview could be loaded"
        if self._previews_warned != (run_id,):
            self.log.warning("picture-matching check: %s; first download wins for such beats", reason)
            self._previews_warned = (run_id,)
        self.svc.db.set_state(PREVIEWS_STATE, reason[:500])

    def relevance_note(self) -> dict[str, Any] | None:
        """The last ``assemble`` call's picture-matching record, or None when the check was off."""
        if not self._relevance_beats and not self._relevance_fallback:
            return None
        chosen = sum(1 for b in self._relevance_beats if b["chosen"] != "text_card")
        return {
            "beats": list(self._relevance_beats),
            "chosen": chosen,
            "text_card": len(self._relevance_beats) - chosen,
            "fallback": self._relevance_fallback,
        }

    def _find_asset(
        self, ctx: Any, query: str, dest: Path, text: str | tuple[str, str] | None = None
    ) -> AssetRecord | None:
        """One query's picture. ``text`` is what candidates are scored against (a pair: the higher score of
        the two counts); None, or no usable scorer, means the first candidate that downloads wins."""
        scorer = self._gate_scorer(ctx) if text is not None else None
        if scorer is None or text is None:
            return self._first_download(ctx, query, dest)
        cfg = self._relevance_cfg()
        width = int(cfg.get("preview_width") or 320)
        max_results = int(self.svc.config.get("assets.wikimedia.max_results", 6))
        pool: list[tuple[Any, AssetCandidate, Any]] = []  # (provider, candidate, preview image)
        found = 0
        for provider in self.svc.assets:
            try:
                cands = provider.search(ctx, query, max_results=max_results)
            except Exception as exc:
                self.log.warning("asset search failed on %s: %s", provider.name, exc)
                continue
            found += len(cands)
            for cand in cands:
                img = provider.preview(ctx, cand, width=width)
                if img is not None:  # a candidate that cannot be scored cannot pass
                    pool.append((provider, cand, img))
        scores: list[float] = []
        if pool:
            images = [img for _, _, img in pool]
            try:
                texts = (text,) if isinstance(text, str) else text
                per_text = [scorer.score_many(images, t) for t in texts]
                scores = [max(col) for col in zip(*per_text, strict=True)]
            except Exception as exc:
                self._gate_down(ctx, f"{type(exc).__name__}: {exc}")
                return self._first_download(ctx, query, dest)
            finally:
                for img in images:
                    img.close()
            for state in (RELEVANCE_STATE, RELEVANCE_FIX_STATE, PREVIEWS_STATE):  # a preview was scored
                if self.svc.db.get_state(state):
                    self.svc.db.set_state(state, "")
        if self._query_log is not None:
            self._query_log.append(
                {"query": query, "candidates": found, "top_score": round(max(scores), 4) if scores else None}
            )
        threshold = float(cfg.get("threshold") or 0.0)
        penalty = float(cfg.get("by_sa_penalty", 0.05) or 0.0)
        owner = {id(cand): provider for provider, cand, _ in pool}
        ranked = rank_candidates(
            [(c, s) for (_, c, _), s in zip(pool, scores, strict=True)], threshold, penalty
        )
        for cand in ranked:
            try:
                rec = owner[id(cand)].fetch(ctx, cand, dest)
            except Exception as exc:
                self.log.warning("asset fetch failed: %s", exc)
                continue
            if rec is not None:
                self.svc.db.update("assets", rec.id, {"query": query[:200]})
                return rec
        return None

    def _first_download(self, ctx: Any, query: str, dest: Path) -> AssetRecord | None:
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
