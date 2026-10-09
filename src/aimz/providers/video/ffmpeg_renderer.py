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

import json
import logging
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

from aimz.core.errors import ProviderError, ProviderUnavailable
from aimz.domain.models import CaptionStyle, RenderResult, Timeline
from aimz.providers.base import HealthStatus, ProviderContext, VideoProvider
from aimz.providers.video import layout
from aimz.providers.video.fonts import shipped_fonts

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


# Final loudness (YouTube plays back at about -14 LUFS and only turns loud audio down).
LOUDNORM = "loudnorm=I=-14:TP=-1:LRA=11"  # the fallback leveller, and the loudness meter
LOUDNESS_TARGET = -14.0
_LOUDNORM_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def parse_loudnorm_json(stderr: str) -> dict[str, float] | None:
    """The measured values from loudnorm's ``print_format=json`` block; None if absent or not finite (silence)."""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
        out = {k: float(data[k]) for k in _LOUDNORM_KEYS}
    except (ValueError, KeyError, TypeError):
        return None
    return out if all(math.isfinite(v) for v in out.values()) else None


# Real narration peaks near full scale while its body is quiet, so loudnorm alone cannot reach -14 LUFS without
# exceeding the peak limit (it falls back to dynamic mode, which lands near -15.5 on a Short). Instead: one measured
# gain, then a limiter run at 4x the sample rate (close to a true-peak limiter). Measured on the live voices
# 2026-10-09: -14.1 to -14.6 LUFS, true peak about -4.5 dBTP after AAC.
LIMIT_DBFS = -2.0
LEVEL_TOLERANCE_LU = 0.3  # stop correcting the gain once this close to the target
LEVEL_CORRECTIONS = 3  # the limiter takes some loudness back; re-measure at most this many times
# Near-silent audio (a failed voice, a music-only bed) would otherwise be boosted by 50 dB into audible hiss.
MAX_GAIN_DB = 20.0


def level_chain(gain_db: float) -> str:
    """Gain in float (16-bit input would clip before the limiter), then the oversampled peak limiter."""
    return (
        f"aformat=sample_fmts=flt,volume={gain_db:.2f}dB,aresample=192000,"
        f"alimiter=limit={10 ** (LIMIT_DBFS / 20):.4f}:attack=2:release=80:level=disabled,aresample=48000"
    )


RENDER_TIMEOUT_S = 1800  # a 60-90 s Short renders in a few minutes; this is only a hang guard
PROBE_TIMEOUT_S = 60


def _fmt_srt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ASS colours are &HAABBGGRR. Only high-contrast colours that read over the white-outlined card text.
CAPTION_COLORS = {
    "white": "&H00FFFFFF",
    "yellow": "&H0000FFFF",
    "cyan": "&H00FFFF00",
    "orange": "&H0000A5FF",
}
# Caption bottom edge above the frame's bottom, as a fraction of its height. All three sit above the
# platform's own overlay (channel name, title, description: layout.BOTTOM_UI) and below the card text.
CAPTION_MARGINS = {"bottom": layout.BOTTOM_UI, "low": 0.26, "mid_low": 0.30}


def split_caption(
    start: float, end: float, text: str, max_words: int | None, max_chars: int | None = None
) -> list[tuple[float, float, str]]:
    """Split one timed chunk into pieces of at most ``max_words`` words and ``max_chars`` characters,
    timing each piece by its share of the characters."""
    words = text.split()
    pieces: list[str] = []
    cur: list[str] = []
    for word in words:
        trial = [*cur, word]
        too_many = bool(max_words) and len(trial) > (max_words or 0)
        too_long = bool(max_chars) and len(" ".join(trial)) > (max_chars or 0)
        if cur and (too_many or too_long):
            pieces.append(" ".join(cur))
            cur = [word]
        else:
            cur = trial
    if cur:
        pieces.append(" ".join(cur))
    if len(pieces) <= 1:
        return [(start, end, text)]
    total = sum(len(piece) for piece in pieces) or 1
    out: list[tuple[float, float, str]] = []
    t = start
    for piece in pieces:
        d = (end - start) * len(piece) / total
        out.append((t, t + d, piece))
        t += d
    return out


# Captions never rise above layout.CAPTION_TOP, so they stay under the card text and its credits.
CAPTION_BAND = round(1 - layout.CAPTION_TOP, 4)


def caption_char_limit(width: int, height: int, style: CaptionStyle) -> int:
    """Most characters one caption may hold and still fit inside the caption band at this size and height.

    A legibility bound, not a style choice: a large font at a high position would otherwise wrap into
    the card's own text.
    """
    font_px = height * style.size
    margin = height * CAPTION_MARGINS.get(style.position, 0.17)
    lines = max(1, int((height * CAPTION_BAND - margin) // (font_px * 1.25)))
    text_w = width - 2 * layout.SIDE_UI_PX * width / 1080
    chars_per_line = max(8, int(text_w / (font_px * 0.5)))  # bold sans: ~0.5 em per character
    return lines * chars_per_line


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
        # A wedged encode used to hold the whole cycle until the task's 4-hour limit killed it mid-run.
        try:
            proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=RENDER_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"ffmpeg {label} did not finish within {RENDER_TIMEOUT_S} s") from exc
        if proc.returncode != 0:
            raise ProviderError(f"ffmpeg {label} failed ({proc.returncode}): {proc.stderr[-1500:]}")

    def _mix_loudness(self, timeline: Timeline, work: Path, chain: str | None) -> float | None:
        """Integrated loudness of the final audio mix, optionally after ``chain``; None for silence."""
        tail = (f"{chain}," if chain else "") + LOUDNORM + ":print_format=json"
        inputs, args = self._audio_mix(timeline, work, None, tail)
        err = self._run_capture(
            ["-f", "concat", "-safe", "0", "-i", "list.txt", *inputs, *args, "-vn", "-f", "null", "-"],
            work,
            "loudness measurement",
        )
        m = parse_loudnorm_json(err)
        return m["input_i"] if m else None

    def _level_gain(self, timeline: Timeline, work: Path) -> float | None:
        """The gain that brings the mix, after the limiter, to -14 LUFS; None when it cannot be measured."""
        try:
            first = self._mix_loudness(timeline, work, None)
            if first is None:
                return None
            gain = min(MAX_GAIN_DB, LOUDNESS_TARGET - first)
            for _ in range(LEVEL_CORRECTIONS):
                now = self._mix_loudness(timeline, work, level_chain(gain))
                if now is None or abs(LOUDNESS_TARGET - now) <= LEVEL_TOLERANCE_LU:
                    break
                gain = min(MAX_GAIN_DB, gain + LOUDNESS_TARGET - now)
            return gain
        except ProviderError as exc:
            log.warning("loudness measurement: %s", exc)
            return None

    def _run_capture(self, args: list[str], cwd: Path, label: str) -> str:
        """Like ``_run`` at log level info, returning ffmpeg's stderr (where loudnorm prints its JSON)."""
        assert self.ffmpeg
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "info", "-nostats", "-y", *args]
        try:
            proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=RENDER_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"ffmpeg {label} did not finish within {RENDER_TIMEOUT_S} s") from exc
        if proc.returncode != 0:
            raise ProviderError(f"ffmpeg {label} failed ({proc.returncode}): {proc.stderr[-1500:]}")
        return proc.stderr

    def measure_loudness(self, path: Path) -> tuple[float, float] | None:
        """(integrated LUFS, true peak dBTP) of a file's audio, or None when it cannot be measured."""
        try:
            err = self._run_capture(
                [
                    "-i",
                    str(Path(path).resolve()),
                    "-map",
                    "0:a",
                    "-af",
                    LOUDNORM + ":print_format=json",
                    "-f",
                    "null",
                    "-",
                ],
                Path(path).parent,
                "loudness check",
            )
        except ProviderError as exc:
            log.warning("loudness check failed: %s", exc)
            return None
        m = parse_loudnorm_json(err)
        return (m["input_i"], m["input_tp"]) if m else None

    def probe_duration(self, path: Path) -> float:
        if self.ffprobe:
            try:
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
                    timeout=PROBE_TIMEOUT_S,
                )
                return float(proc.stdout.strip())
            except (ValueError, subprocess.TimeoutExpired):
                pass
        if not self.ffmpeg:
            raise ProviderUnavailable("ffmpeg not available")
        try:
            proc = subprocess.run(
                [self.ffmpeg, "-i", str(path)], capture_output=True, text=True, timeout=PROBE_TIMEOUT_S
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"could not probe duration of {path}: timed out") from exc
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", proc.stderr)
        if not m:
            raise ProviderError(f"could not probe duration of {path}")
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    # -- captions ----------------------------------------------------------------------
    def caption_font(self, style: CaptionStyle) -> tuple[str, Path | None]:
        """(ASS font name, font file to ship with the render or None for the renderer's own font)."""
        if style.font != "default":
            font = shipped_fonts().get(style.font)
            if font:
                return style.font, Path(font["path"])
            log.warning("caption font %s is not shipped; using %s", style.font, self.font_name)
        return self.font_name, None

    def write_captions(self, timeline: Timeline, ass_path: Path, srt_path: Path) -> None:
        w, h = timeline.width, timeline.height
        style = timeline.caption_style
        font_name, _ = self.caption_font(style)
        font_size = int(h * style.size)
        margin_v = int(h * CAPTION_MARGINS.get(style.position, 0.17))
        colour = CAPTION_COLORS.get(style.color, CAPTION_COLORS["white"])
        outline = max(0, int(round(style.outline)))
        max_chars = caption_char_limit(w, h, style)
        side = int(layout.SIDE_UI_PX * w / 1080)  # clear of the platform's right-hand buttons
        header = (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Cap,{font_name},{font_size},{colour},&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},1,2,{side},{side},{margin_v},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        events: list[str] = []
        srt: list[str] = []
        n = 0
        for scene in timeline.scenes:
            chunks = scene.caption_chunks or [(0.0, scene.duration_s, scene.narration)]
            pieces = [p for c in chunks for p in split_caption(c[0], c[1], c[2], style.max_words, max_chars)]
            for start, end, text in pieces:
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

    # -- audio bed ---------------------------------------------------------------------
    @staticmethod
    def _audio_mix(
        timeline: Timeline, work: Path, ass_filter: str | None, tail: str
    ) -> tuple[list[str], list[str]]:
        """Inputs and filter arguments for the final pass: captions, music and cut sounds when set, and the
        loudness filter ``tail`` at the end of the audio. ``ass_filter=None`` builds the audio only (to measure it).

        Music loops for the whole video, fades in and out, and is ducked under the narration with a
        side-chain compressor, so it never covers the voice.
        """
        music = Path(timeline.music_path) if timeline.music_path else None
        sfx = Path(timeline.sfx_path) if timeline.sfx_path else None
        cuts = [c for c in timeline.sfx_times if c > 0.2] if sfx else []
        inputs: list[str] = []
        parts = [f"[0:v]{ass_filter}[v]"] if ass_filter is not None else []
        audio = "[0:a]"
        n = 1
        if music is not None and music.exists():
            local = work / f"music{music.suffix}"
            shutil.copyfile(music, local)
            inputs += ["-stream_loop", "-1", "-i", local.name]
            end = max(0.0, timeline.total_duration_s - 1.5)
            parts.append("[0:a]asplit=2[narr][key]")
            parts.append(
                f"[{n}:a]volume={timeline.music_volume_db:g}dB,afade=t=in:d=1,afade=t=out:st={end:.2f}:d=1.5[bed]"
            )
            parts.append("[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
            parts.append("[narr][duck]amix=inputs=2:duration=first:normalize=0[withmusic]")
            audio = "[withmusic]"
            n += 1
        if cuts and sfx is not None and sfx.exists():
            local = work / f"sfx{sfx.suffix}"
            shutil.copyfile(sfx, local)
            inputs += ["-i", local.name]
            labels = [f"[w{k}]" for k in range(len(cuts))]
            parts.append(
                f"[{n}:a]volume={timeline.sfx_volume_db:g}dB,asplit={len(cuts)}{''.join(labels)}"
                if len(cuts) > 1
                else f"[{n}:a]volume={timeline.sfx_volume_db:g}dB[w0]"
            )
            for k, t in enumerate(cuts):
                ms = int(max(0.0, t - 0.2) * 1000)  # the whoosh peaks as the cut happens
                parts.append(f"[w{k}]adelay={ms}|{ms}[d{k}]")
            delayed = "".join(f"[d{k}]" for k in range(len(cuts)))
            parts.append(f"{audio}{delayed}amix=inputs={len(cuts) + 1}:duration=first:normalize=0[aout]")
            audio = "[aout]"
        parts.append(f"{audio}{tail}[afinal]")
        maps = ["-map", "[v]"] if ass_filter is not None else []
        return inputs, ["-filter_complex", ";".join(parts), *maps, "-map", "[afinal]"]

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
                if scene.clip_path and Path(scene.clip_path).exists():
                    # A stock video clip, scaled and cropped to fill the frame (looped if it is shorter than the
                    # scene), with the scene's text layer (a transparent PNG) on top.
                    src_local = work / f"src_{scene.idx:02d}{Path(scene.clip_path).suffix or '.mp4'}"
                    shutil.copyfile(scene.clip_path, src_local)
                    inputs = ["-stream_loop", "-1", "-i", src_local.name, "-loop", "1", "-i", img_local.name]
                    inputs += ["-i", wav_local.name]
                    graph = (
                        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps}[bg];"
                        "[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
                    )
                    video_args = ["-filter_complex", graph, "-map", "[v]", "-map", "2:a"]
                else:
                    inputs = ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", img_local.name]
                    inputs += ["-i", wav_local.name]
                    video_args = ["-vf", vf]
                self._run(
                    [
                        *inputs,
                        *video_args,
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
                        # Pad the narration to the scene length. With -shortest the clip stopped when the
                        # audio did, so the beat pause was never rendered and captions drifted late.
                        "-af",
                        "apad",
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
            ass_filter = "ass=captions.ass"
            _, font_file = self.caption_font(timeline.caption_style)
            if font_file is not None:
                fonts = work / "fonts"
                fonts.mkdir(exist_ok=True)
                shutil.copyfile(font_file, fonts / font_file.name)
                ass_filter = "ass=captions.ass:fontsdir=fonts"

            final = out_dir / "video.mp4"
            gain = self._level_gain(timeline, work)
            if gain is None:
                log.info("loudness measurement failed; single-pass loudnorm")
            tail = level_chain(gain) if gain is not None else LOUDNORM
            mix_inputs, mix_args = self._audio_mix(timeline, work, ass_filter, tail)
            self._run(
                [
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    "list.txt",
                    *mix_inputs,
                    *mix_args,
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
                    "-ar",
                    "48000",
                    # Stereo: the upmix puts each channel 3 dB lower at the same loudness (BS.1770 sums channels); that headroom
                    # absorbs the peak ffmpeg's AAC adds (about 4 dB on mono speech). An upmix without it loses that margin.
                    "-ac",
                    "2",
                    "-movflags",
                    "+faststart",
                    str(final.resolve()),
                ],
                work,
                "concat+captions",
            )
            duration = self.probe_duration(final)
            loudness = self.measure_loudness(final)
            if loudness is not None and (abs(loudness[0] - LOUDNESS_TARGET) > 1.0 or loudness[1] > -1.0):
                log.warning(
                    "final loudness %.1f LUFS / %.1f dBTP is off target (-14 LUFS, -1 dBTP)", *loudness
                )
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
            loudness_lufs=loudness[0] if loudness else None,
            true_peak_dbtp=loudness[1] if loudness else None,
        )
