"""Kokoro unavailable: the producer re-narrates the whole video with the fallback voice and tells the owner."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aimz.core.attention import attention
from aimz.core.errors import VoiceUnavailable
from aimz.domain.models import Beat
from aimz.providers.base import HealthStatus
from aimz.providers.tts.silent import SilentTTSProvider
from aimz.util import new_id, now_iso

BELLA = "kokoro:af_bella"


class FlakyKokoroTTS:
    """A router stand-in: Kokoro voices fail on the ``fail_on``-th Kokoro call (0 = every call, None = never)."""

    def __init__(self, fail_on: int | None) -> None:
        self.silent = SilentTTSProvider()
        self.fail_on = fail_on
        self.kokoro_calls = 0
        self.spoken: list[str | None] = []
        self.voice = None
        self.length_scale = 1.0
        self.sentence_pause_s = 0.25
        self.kokoro = SimpleNamespace(
            health=lambda: HealthStatus(False, "Kokoro model files missing", "python -m aimz doctor --fix")
        )

    def synthesize(
        self, ctx: Any, text: str, out_path: Path, purpose: str = "narration", options: Any = None
    ) -> Any:
        voice = options.voice if options else None
        if (voice or "").startswith("kokoro:"):
            self.kokoro_calls += 1
            if self.fail_on is not None and self.fail_on in (0, self.kokoro_calls):
                raise VoiceUnavailable("RuntimeError: onnx session failed")
        self.spoken.append(voice)
        return self.silent.synthesize(ctx, text, out_path, purpose, options)


class FakeRenderer:
    def render(self, ctx: Any, timeline: Any, out_dir: Path) -> Any:
        return SimpleNamespace(
            duration_s=timeline.total_duration_s, video_path=str(out_dir / "v.mp4"), captions_path=""
        )


def _script(svc: Any, beats: int = 4) -> str:
    idea_id = new_id("idea")
    svc.db.insert(
        "ideas",
        {
            "id": idea_id,
            "title": "idea",
            "premise": "p",
            "hook": "h",
            "hook_type": "narrative_hook",
            "content_family": "maps",
            "target_platform": "both",
            "source_item_ids_json": "[]",
            "scores_json": "{}",
            "opportunity_score": 50,
            "status": "scripted",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    script_id = new_id("scr")
    bs = [
        Beat(narration=f"Sentence number {i}.", visual_query="q", visual_type="text_card")
        for i in range(beats)
    ]
    svc.db.insert(
        "scripts",
        {
            "id": script_id,
            "idea_id": idea_id,
            "title": "A Title",
            "hook_line": "h",
            "beats_json": "[" + ",".join(b.model_dump_json() for b in bs) + "]",
            "narration_text": "x",
            "status": "approved",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    return script_id


def _setup(svc: Any, fail_on: int | None) -> tuple[Any, FlakyKokoroTTS]:
    from aimz import identity
    from aimz.agents.producer import ProducerAgent

    svc.db.set_state(identity.IDENTITY_KEY, json.dumps({"genre": "maps", "voice": BELLA}))
    tts = FlakyKokoroTTS(fail_on)
    svc.tts = tts
    svc.assets = []
    svc.renderer = FakeRenderer()
    return ProducerAgent(svc, None), tts


def _settings(svc: Any, video_id: str) -> dict[str, tuple[Any, str]]:
    rows = svc.db.query("SELECT variable, value, source FROM video_settings WHERE video_id=?", [video_id])
    return {r["variable"]: (json.loads(r["value"]), r["source"]) for r in rows}


def test_kokoro_failure_mid_video_renarrates_every_beat_with_fallback(svc) -> None:  # noqa: ANN001
    from aimz.agents.producer import FALLBACK_VOICE

    producer, tts = _setup(svc, fail_on=3)
    with svc.tracker.run("produce") as run:
        video_id = producer.produce(run, _script(svc, beats=4))
    assert tts.spoken[:2] == [BELLA, BELLA]  # the first pass, abandoned at beat 3
    assert tts.spoken[2:] == [FALLBACK_VOICE] * 4  # every beat of the video re-narrated
    s = _settings(svc, video_id)
    assert s["voice"] == (FALLBACK_VOICE, "voice_fallback")
    assert s["voice_fallback"] == (BELLA, "voice_fallback")
    assert svc.db.get("videos", video_id)["status"] == "rendered"


def test_voice_state_set_then_cleared(svc) -> None:  # noqa: ANN001
    producer, tts = _setup(svc, fail_on=1)
    with svc.tracker.run("produce") as run:
        producer.produce(run, _script(svc))
    item = attention(svc.db)["voice_unavailable"][0]
    assert item["why"].startswith("Kokoro voice unavailable:")
    assert item["fix"] == "python -m aimz doctor --fix"
    tts.fail_on = None
    with svc.tracker.run("produce") as run:
        producer.produce(run, _script(svc))
    assert "voice_unavailable" not in attention(svc.db)


def test_one_warning_per_run(svc, caplog: pytest.LogCaptureFixture) -> None:  # noqa: ANN001
    producer, _ = _setup(svc, fail_on=0)
    with caplog.at_level(logging.WARNING), svc.tracker.run("produce") as run:
        producer.produce(run, _script(svc))
        producer.produce(run, _script(svc))
    assert sum("Kokoro voice unavailable" in r.getMessage() for r in caplog.records) == 1


def test_loudness_run_note(svc) -> None:  # noqa: ANN001
    producer, _ = _setup(svc, fail_on=None)

    class LoudRenderer(FakeRenderer):
        def render(self, ctx: Any, timeline: Any, out_dir: Path) -> Any:
            res = super().render(ctx, timeline, out_dir)
            res.loudness_lufs, res.true_peak_dbtp = -14.1, -1.3
            return res

    svc.renderer = LoudRenderer()
    with svc.tracker.run("produce") as run:
        video_id = producer.produce(run, _script(svc))
    assert run.summary["loudness"][video_id] == {"loudness_lufs": -14.1, "true_peak_dbtp": -1.3}
