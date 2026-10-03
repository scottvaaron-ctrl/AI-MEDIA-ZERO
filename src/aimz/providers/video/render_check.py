"""Human-free checks on a rendered video: right length, captions written, picture not blank."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageStat


@dataclass
class RenderCheck:
    ok: bool
    problems: list[str] = field(default_factory=list)
    duration_s: float | None = None


def frame_is_blank(png: Path, min_stddev: float = 4.0) -> bool:
    """A frame whose pixels barely vary (one flat colour) counts as blank."""
    img = Image.open(png).convert("L")
    return float(ImageStat.Stat(img).stddev[0]) < min_stddev


def check_render(
    ffmpeg: str,
    video: Path,
    captions: Path,
    duration_s: float,
    expected_s: float,
    tolerance_s: float = 0.35,
) -> RenderCheck:
    problems: list[str] = []
    if not video.exists() or video.stat().st_size < 10_000:
        problems.append("video missing or tiny")
    if abs(duration_s - expected_s) > tolerance_s:
        problems.append(f"duration {duration_s:.2f}s, timeline says {expected_s:.2f}s")
    if not captions.exists() or "-->" not in captions.read_text(encoding="utf-8"):
        problems.append("captions missing or empty")
    frame = video.with_name("check_frame.png")
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.1, duration_s / 2):.2f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(frame),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0 or not frame.exists():
        problems.append("could not extract a frame")
    elif frame_is_blank(frame):
        problems.append("middle frame is blank")
    return RenderCheck(not problems, problems, duration_s)
