from __future__ import annotations

from pathlib import Path

import pytest

from aimz.domain.models import Scene, Timeline
from aimz.providers.tts.silent import SilentTTSProvider
from aimz.providers.video.ffmpeg_renderer import FFmpegRenderer, _ass_escape, _fmt_ass_time, _fmt_srt_time


def test_time_formatting() -> None:
    assert _fmt_ass_time(61.25) == "0:01:01.25"
    assert _fmt_srt_time(61.25) == "00:01:01,250"
    assert _ass_escape("a{b}\\c") == "a(b)\\\\c"


def test_card_render_and_thumbnail(svc) -> None:  # noqa: ANN001
    out = svc.cards.render_card(
        svc.ctx(),
        {
            "kind": "stat_card",
            "seed": "s",
            "headline": "42%",
            "body": "of the fleet",
            "disclosure": "AI narration",
        },
        svc.env.data_dir / "card.png",
    )
    assert out.exists() and out.stat().st_size > 10_000
    thumb = svc.cards.render_thumbnail(
        svc.ctx(),
        "A very long title that needs wrapping across lines",
        "seed",
        svc.env.data_dir / "thumb.png",
    )
    assert thumb.exists()


def test_render_two_scene_video(svc, ffmpeg_available: bool) -> None:  # noqa: ANN001
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available")
    ctx = svc.ctx()
    tts = SilentTTSProvider()
    scenes = []
    cursor = 0.0
    for i, text in enumerate(
        ["First sentence here. Second one too.", "Third and final sentence of the test."]
    ):
        wav = svc.env.data_dir / f"s{i}.wav"
        res = tts.synthesize(ctx, text, wav)
        png = svc.cards.render_card(
            ctx, {"kind": "text_card", "seed": "v", "headline": f"Scene {i}"}, svc.env.data_dir / f"s{i}.png"
        )
        scenes.append(
            Scene(
                idx=i,
                start_s=cursor,
                duration_s=res.duration_s + 0.3,
                narration=text,
                caption=f"Scene {i}",
                audio_path=str(wav),
                image_path=str(png),
                caption_chunks=res.chunks,
                zoom="in" if i == 0 else "out",
            )
        )  # type: ignore[attr-defined]
        cursor += res.duration_s + 0.3
    tl = Timeline(video_id="v", title="t", scenes=scenes, total_duration_s=cursor, fps=24)
    out_dir = svc.env.data_dir / "render"
    result = FFmpegRenderer(svc.env.ffmpeg_bin, svc.env.ffprobe_bin).render(ctx, tl, out_dir)
    assert Path(result.video_path).exists() and Path(result.thumbnail_path).exists()
    assert abs(result.duration_s - cursor) < 1.5
    srt = Path(result.captions_path).read_text(encoding="utf-8")
    assert "First sentence here." in srt and "-->" in srt
