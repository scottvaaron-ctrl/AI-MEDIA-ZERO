"""Every final video is levelled to -14 LUFS with true peak at or below -1 dBTP (two-pass loudnorm)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aimz.core.errors import ProviderError
from aimz.domain.models import Scene, Timeline
from aimz.providers.audio.library import music_moods, track_for
from aimz.providers.video.ffmpeg_renderer import (
    LOUDNORM,
    FFmpegRenderer,
    loudnorm_second_pass,
    parse_loudnorm_json,
    resolve_ffmpeg,
)

SAMPLE = """[Parsed_loudnorm_0 @ 000001]
{
\t"input_i" : "-23.50",
\t"input_tp" : "-5.20",
\t"input_lra" : "1.00",
\t"input_thresh" : "-33.80",
\t"output_i" : "-14.02",
\t"output_tp" : "-1.00",
\t"output_lra" : "1.00",
\t"output_thresh" : "-24.30",
\t"normalization_type" : "linear",
\t"target_offset" : "0.02"
}
"""


def test_parse_loudnorm_json() -> None:
    m = parse_loudnorm_json("size=N/A time=00:00:02\n" + SAMPLE)
    assert m == {
        "input_i": -23.5,
        "input_tp": -5.2,
        "input_lra": 1.0,
        "input_thresh": -33.8,
        "target_offset": 0.02,
    }
    assert parse_loudnorm_json(SAMPLE.replace('"-23.50"', '"-inf"')) is None  # silent audio
    assert parse_loudnorm_json("no json here") is None
    assert parse_loudnorm_json("{ not json") is None


def test_second_pass_string() -> None:
    s = loudnorm_second_pass(parse_loudnorm_json(SAMPLE) or {})
    assert s.startswith(LOUDNORM + ":")
    assert "measured_I=-23.5" in s and "measured_TP=-5.2" in s and "measured_LRA=1" in s
    assert "measured_thresh=-33.8" in s and "offset=0.02" in s
    assert s.endswith("linear=true")


def _tone(path: Path, gain_db: float, seconds: float = 3.0) -> None:
    ffmpeg = resolve_ffmpeg("")
    assert ffmpeg
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={seconds}", "-af", f"volume={gain_db}dB", "-ar", "24000", "-ac", "1", str(path)],
        check=True,
    )  # fmt: skip


def _timeline(svc: Any, gain_db: float, music: bool) -> Timeline:
    ctx = svc.ctx()
    scenes, cursor = [], 0.0
    for i in range(2):
        wav = svc.env.data_dir / f"tone{i}.wav"
        _tone(wav, gain_db)
        png = svc.cards.render_card(
            ctx, {"kind": "text_card", "seed": "v", "headline": f"Scene {i}"}, svc.env.data_dir / f"s{i}.png"
        )
        scenes.append(
            Scene(idx=i, start_s=cursor, duration_s=3.0, narration="Tone.", caption="", audio_path=str(wav),
                  image_path=str(png), caption_chunks=[(0.0, 3.0, "Tone.")], zoom="none")
        )  # fmt: skip
        cursor += 3.0
    extra: dict[str, Any] = {}
    if music:
        track = track_for(music_moods()[0])
        assert track is not None
        extra = {"music_path": str(track), "music_volume_db": -16.0}
    return Timeline(video_id="v", title="t", scenes=scenes, total_duration_s=cursor, fps=24, **extra)


@pytest.mark.parametrize("gain_db", [-30.0, 0.0], ids=["quiet", "loud"])
@pytest.mark.parametrize("music", [False, True], ids=["no-music", "music"])
def test_quiet_and_loud_tones_land_at_minus_14(
    svc, ffmpeg_available: bool, gain_db: float, music: bool
) -> None:  # noqa: ANN001
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available")
    renderer = FFmpegRenderer(svc.env.ffmpeg_bin, svc.env.ffprobe_bin)
    result = renderer.render(svc.ctx(), _timeline(svc, gain_db, music), svc.env.data_dir / "render")
    assert result.loudness_lufs is not None and result.true_peak_dbtp is not None
    assert -15.0 <= result.loudness_lufs <= -13.0
    assert result.true_peak_dbtp <= -1.0
    assert renderer.measure_loudness(Path(result.video_path)) == pytest.approx(
        (result.loudness_lufs, result.true_peak_dbtp)
    )


def test_pass1_failure_falls_back_to_single_pass(
    svc, ffmpeg_available: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:  # noqa: ANN001
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available")
    renderer = FFmpegRenderer(svc.env.ffmpeg_bin, svc.env.ffprobe_bin)
    real_capture = renderer._run_capture
    seen: list[list[str]] = []

    def capture(args: list[str], cwd: Path, label: str) -> str:
        if "-vn" in args:  # pass 1 of the final mix
            raise ProviderError("ffmpeg loudness pass 1 failed")
        return real_capture(args, cwd, label)

    real_run = renderer._run

    def run(args: list[str], cwd: Path, label: str) -> None:
        seen.append(args)
        real_run(args, cwd, label)

    monkeypatch.setattr(renderer, "_run_capture", capture)
    monkeypatch.setattr(renderer, "_run", run)
    with caplog.at_level(logging.INFO):
        renderer.render(svc.ctx(), _timeline(svc, -30.0, False), svc.env.data_dir / "render")
    final = next(a for a in seen if "list.txt" in a)
    graph = final[final.index("-filter_complex") + 1]
    assert LOUDNORM in graph and "measured_I" not in graph
    assert "48000" in final
    assert any("single-pass loudnorm" in r.getMessage() for r in caplog.records)
