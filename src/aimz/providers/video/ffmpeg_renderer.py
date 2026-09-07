"""FFmpegRenderer: converts a :class:`Timeline` into an H.264/AAC MP4 with burned captions. Cost: $0.

Pipeline per video (all in a scratch ``work/`` folder so that ffmpeg filter arguments can use
relative paths; this sidesteps Windows drive-letter escaping inside filter strings):

1. every scene has a card PNG and a narration WAV (produced by the Producer);
2. each scene -> ``scene_NN.mp4`` (looped still + Ken Burns zoompan + audio);
3. concat demuxer joins the scenes, and the ``ass`` filter burns timed captions;
4. a poster PNG and an SRT are emitted alongside.

``ffmpeg`` is resolved from ``FFMPEG_BIN``, then PATH, then the bundled imageio-ffmpeg binary.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from aimz.core.errors import ProviderError, ProviderUnavailable
from aimz.domain.models import RenderResult, Timeline
from aimz.providers.base import HealthStatus, ProviderContext, VideoProvider

log = logging.getLogger("aimz.video.ffmpeg")


def resolve_ffmpeg(explicit: str = "") -> str | None:
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def resolve_ffprobe(explicit: str = "", ffmpeg_path: str | None = None) -> str | None:
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("ffprobe")
    if found:
        return found
    if ffmpeg_path:
        sibling = Path(ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.exists():
            return str(sibling)
    return None


def _fmt_ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _fmt_srt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


class FFmpegRenderer(VideoProvider):
    name = "FFmpegRenderer"
    is_paid = False

    def __init__(
        self,
        ffmpeg_bin: str = "",
        ffprobe_bin: str = "",
        font_name: str = "Arial",
        preset: str = "veryfast",
        crf: int = 21,
    ):
        self.ffmpeg = resolve_ffmpeg(ffmpeg_bin)
        self.ffprobe = resolve_ffprobe(ffprobe_bin, self.ffmpeg)
        self.font_name = font_name
        self.preset = preset
        self.crf = crf

    # -- health ----------------------------------------------------------------------
    def health(self) -> HealthStatus:
        if not self.ffmpeg:
            return HealthStatus(
                False, "ffmpeg not found", "winget install Gyan.FFmpeg  (or pip install imageio-ffmpeg)"
            )
        try:
            out = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, timeout=20).stdout
            ver = out.splitlines()[0] if out else "unknown"
            libass = "libass" in out or "--enable-libass" in out
            return HealthStatus(
                True, f"{ver} ({'libass ok' if libass else 'libass unknown'}) at {self.ffmpeg}"
            )
        except Exception as exc:
            return HealthStatus(False, f"ffmpeg present but failed to run: {exc}")

    def _run(self, args: list[str], cwd: Path, label: str) -> None:
        assert self.ffmpeg
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
        if proc.returncode != 0:
            raise ProviderError(f"ffmpeg {label} failed ({proc.returncode}): {proc.stderr[-1500:]}")

    def probe_duration(self, path: Path) -> float:
        if self.ffprobe:
            proc = subprocess.run(
                [
                    self.ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
            )
            try:
                return float(proc.stdout.strip())
            except ValueError:
                pass
        if not self.ffmpeg:
            raise ProviderUnavailable("ffmpeg not available")
        proc = subprocess.run([self.ffmpeg, "-i", str(path)], capture_output=True, text=True)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr)
        if not m:
            raise ProviderError(f"could not probe duration of {path}")
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    # -- captions ----------------------------------------------------------------------
    def write_captions(self, timeline: Timeline, ass_path: Path, srt_path: Path) -> None:
        w, h = timeline.width, timeline.height
        font_size = int(h * 0.036)
        margin_v = int(h * 0.17)
        header = (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Cap,{self.font_name},{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,80,80,{margin_v},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        events: list[str] = []
        srt: list[str] = []
        n = 0
        for scene in timeline.scenes:
            chunks = scene.caption_chunks or [(0.0, scene.duration_s, scene.narration)]
            for start, end, text in chunks:
                a = scene.start_s + start
                b = scene.start_s + min(end, scene.duration_s)
                if b - a < 0.15 or not text.strip():
                    continue
                n += 1
                events.append(
                    f"Dialogue: 0,{_fmt_ass_time(a)},{_fmt_ass_time(b)},Cap,,0,0,0,,{_ass_escape(text.strip())}"
                )
                srt.append(f"{n}\n{_fmt_srt_time(a)} --> {_fmt_srt_time(b)}\n{text.strip()}\n")
        ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
        srt_path.write_text("\n".join(srt) + "\n", encoding="utf-8")

    # -- render ------------------------------------------------------------------------
    def render(self, ctx: ProviderContext, timeline: Timeline, out_dir: Path) -> RenderResult:
        if not self.ffmpeg:
            raise ProviderUnavailable("ffmpeg not found; run `aimz doctor`")
        work = out_dir / "work"
        work.mkdir(parents=True, exist_ok=True)
        w, h, fps = timeline.width, timeline.height, timeline.fps
        scale_w, scale_h = int(w * 1.5), int(h * 1.5)

        with self.authorized(ctx, "render_video") as rec:
            clip_names: list[str] = []
            for scene in timeline.scenes:
                if not scene.image_path or not scene.audio_path:
                    raise ProviderError(f"scene {scene.idx} missing image/audio")
                img = Path(scene.image_path)
                wav = Path(scene.audio_path)
                # copy inputs into work dir with plain names so filters/inputs are relative
                img_local = work / f"img_{scene.idx:02d}.png"
                wav_local = work / f"aud_{scene.idx:02d}.wav"
                shutil.copyfile(img, img_local)
                shutil.copyfile(wav, wav_local)
                dur = max(0.8, scene.duration_s)
                frames = max(1, int(round(dur * fps)))
                if scene.zoom == "out":
                    zexpr = f"max(1.18-0.18*on/{frames},1.0)"
                elif scene.zoom == "none":
                    zexpr = "1.0"
                else:
                    zexpr = f"min(1.0+0.18*on/{frames},1.18)"
                vf = (
                    f"scale={scale_w}:{scale_h},"
                    f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},"
                    "format=yuv420p"
                )
                clip = f"scene_{scene.idx:02d}.mp4"
                self._run(
                    [
                        "-loop",
                        "1",
                        "-framerate",
                        str(fps),
                        "-t",
                        f"{dur:.3f}",
                        "-i",
                        img_local.name,
                        "-i",
                        wav_local.name,
                        "-vf",
                        vf,
                        "-t",
                        f"{dur:.3f}",
                        "-c:v",
                        "libx264",
                        "-preset",
                        self.preset,
                        "-crf",
                        str(self.crf),
                        "-r",
                        str(fps),
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-ar",
                        "44100",
                        "-ac",
                        "1",
                        "-shortest",
                        clip,
                    ],
                    work,
                    f"scene {scene.idx}",
                )
                clip_names.append(clip)

            (work / "list.txt").write_text("".join(f"file '{c}'\n" for c in clip_names), encoding="utf-8")
            ass_path = work / "captions.ass"
            srt_path = out_dir / "captions.srt"
            self.write_captions(timeline, ass_path, srt_path)

            final = out_dir / "video.mp4"
            self._run(
                [
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    "list.txt",
                    "-vf",
                    "ass=captions.ass",
                    "-c:v",
                    "libx264",
                    "-preset",
                    self.preset,
                    "-crf",
                    str(self.crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    str(final.resolve()),
                ],
                work,
                "concat+captions",
            )
            duration = self.probe_duration(final)
            poster = out_dir / "poster.png"
            self._run(
                ["-ss", "0.5", "-i", str(final.resolve()), "-frames:v", "1", str(poster.resolve())],
                work,
                "poster",
            )
            rec.actual_cost = 0.0
            rec.extra["scenes"] = len(clip_names)

        # keep scratch small
        for f in work.glob("scene_*.mp4"):
            f.unlink(missing_ok=True)
        return RenderResult(
            video_path=str(final),
            thumbnail_path=str(poster),
            captions_path=str(srt_path),
            duration_s=duration,
        )
