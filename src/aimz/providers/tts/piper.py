"""PiperTTSProvider: local neural TTS via the ``piper-tts`` package (GPL-3 engine, MIT voices). Cost: $0.

Voices are downloaded once from Hugging Face (``rhasspy/piper-voices``) into ``PIPER_VOICES_DIR``
when ``PIPER_AUTO_DOWNLOAD=true``. Synthesis is sentence-chunked so that each chunk's exact
duration is known; the renderer uses those timings for captions.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Any

from aimz.core.errors import ProviderUnavailable
from aimz.providers.base import HealthStatus, ProviderContext, TTSProvider, TTSResult
from aimz.providers.tts.text_prep import apply_pronunciations, clean_for_tts, split_sentences

log = logging.getLogger("aimz.tts.piper")

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def voice_paths(voices_dir: Path, voice: str) -> tuple[Path, Path]:
    return voices_dir / f"{voice}.onnx", voices_dir / f"{voice}.onnx.json"


def voice_urls(voice: str) -> tuple[str, str]:
    # en_US-lessac-medium -> en/en_US/lessac/medium/en_US-lessac-medium.onnx
    lang_code, name, quality = voice.split("-", 2)
    family = lang_code.split("_")[0]
    base = f"{HF_BASE}/{family}/{lang_code}/{name}/{quality}/{voice}"
    return f"{base}.onnx?download=true", f"{base}.onnx.json?download=true"


def download_voice(voices_dir: Path, voice: str, user_agent: str = "AIMediaZero/0.1") -> tuple[Path, Path]:
    import httpx

    onnx, cfg = voice_paths(voices_dir, voice)
    voices_dir.mkdir(parents=True, exist_ok=True)
    for url, dest in zip(voice_urls(voice), (onnx, cfg), strict=True):
        if dest.exists() and dest.stat().st_size > 0:
            continue
        log.info("downloading Piper voice file %s", dest.name)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with (
            httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": user_agent}) as client,
            client.stream("GET", url) as r,
        ):
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 16):
                    fh.write(chunk)
        tmp.replace(dest)
    return onnx, cfg


class PiperTTSProvider(TTSProvider):
    name = "PiperTTSProvider"
    is_paid = False

    def __init__(
        self,
        voices_dir: Path,
        voice: str = "en_US-lessac-medium",
        auto_download: bool = True,
        length_scale: float = 1.0,
        pronunciations: dict[str, str] | None = None,
        sentence_pause_s: float = 0.25,
        user_agent: str = "AIMediaZero/0.1",
    ):
        self.voices_dir = Path(voices_dir)
        self.voice = voice
        self.auto_download = auto_download
        self.length_scale = length_scale
        self.pronunciations = pronunciations or {}
        self.sentence_pause_s = sentence_pause_s
        self.user_agent = user_agent
        self._voice: Any = None

    # -- health ----------------------------------------------------------------------
    def _import(self) -> Any:
        try:
            import piper  # noqa: F401

            return piper
        except Exception as exc:  # ImportError or DLL load failure
            raise ProviderUnavailable(
                f"piper-tts not importable: {exc}. Install with `pip install piper-tts`."
            ) from exc

    def ensure_voice(self) -> tuple[Path, Path]:
        onnx, cfg = voice_paths(self.voices_dir, self.voice)
        if onnx.exists() and cfg.exists():
            return onnx, cfg
        if not self.auto_download:
            raise ProviderUnavailable(
                f"Piper voice {self.voice} not found in {self.voices_dir}. "
                f"Run `aimz doctor --fix` or `python -m piper.download_voices {self.voice} --download-dir {self.voices_dir}`."
            )
        return download_voice(self.voices_dir, self.voice, self.user_agent)

    def health(self) -> HealthStatus:
        try:
            self._import()
        except ProviderUnavailable as exc:
            return HealthStatus(False, str(exc), "pip install piper-tts")
        onnx, cfg = voice_paths(self.voices_dir, self.voice)
        if onnx.exists() and cfg.exists():
            return HealthStatus(True, f"piper-tts importable; voice {self.voice} present")
        return HealthStatus(
            False,
            f"voice {self.voice} missing from {self.voices_dir}",
            "auto-download on first use (PIPER_AUTO_DOWNLOAD=true) or run `aimz doctor --fix`",
        )

    def _load(self) -> Any:
        if self._voice is None:
            piper = self._import()
            onnx, _ = self.ensure_voice()
            self._voice = piper.PiperVoice.load(str(onnx))
        return self._voice

    # -- synthesis -------------------------------------------------------------------
    def synthesize(
        self, ctx: ProviderContext, text: str, out_path: Path, purpose: str = "narration"
    ) -> TTSResult:
        """Synthesize ``text`` to ``out_path`` (16-bit mono WAV). Returns duration.

        Chunk timings are attached as ``result.chunks`` (list of (start, end, text)).
        """
        from piper import SynthesisConfig  # type: ignore

        voice = self._load()
        text = clean_for_tts(text)
        chunks = split_sentences(text)
        cfg = SynthesisConfig(length_scale=self.length_scale)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with self.authorized(ctx, purpose) as rec:
            timings: list[tuple[float, float, str]] = []
            sample_rate = 22050
            frames: list[bytes] = []
            cursor = 0.0
            for chunk in chunks:
                spoken = apply_pronunciations(chunk, self.pronunciations)
                pcm = b""
                for audio in voice.synthesize(spoken, syn_config=cfg):
                    sample_rate = audio.sample_rate
                    pcm += audio.audio_int16_bytes
                dur = len(pcm) / (2 * sample_rate)
                timings.append((cursor, cursor + dur, chunk))
                frames.append(pcm)
                pause = b"\x00\x00" * int(self.sentence_pause_s * sample_rate)
                frames.append(pause)
                cursor += dur + self.sentence_pause_s
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                for f in frames:
                    wf.writeframes(f)
            rec.actual_cost = 0.0
            rec.extra["chunks"] = len(chunks)
        result = TTSResult(path=str(out_path), duration_s=cursor, sample_rate=sample_rate)
        result.chunks = timings  # type: ignore[attr-defined]
        return result
