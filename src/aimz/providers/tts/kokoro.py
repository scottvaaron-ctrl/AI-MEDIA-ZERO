"""KokoroTTSProvider: local Kokoro-82M narration via the ``kokoro-onnx`` package (Apache-2.0). Cost: $0.

The model files come once from the kokoro-onnx GitHub release ``model-files-v1.0`` into ``KOKORO_MODEL_DIR``
when ``KOKORO_AUTO_DOWNLOAD=true`` (or ``aimz doctor --fix``). Voices are named ``kokoro:<id>`` so the voice
router can tell them from Piper's. Synthesis is sentence-chunked like Piper's, so captions get exact timings.
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Any

import httpx

from aimz.core.errors import ProviderUnavailable
from aimz.providers.base import HealthStatus, ProviderContext, SpeechOptions, TTSProvider, TTSResult
from aimz.providers.tts.text_prep import apply_pronunciations, clean_for_tts, split_sentences

log = logging.getLogger("aimz.tts.kokoro")

RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_FILES: tuple[str, ...] = ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin")
PREFIX = "kokoro:"

# Voices are cleared by ear (owner decision 2026-10-08); the model and its voicepacks are Apache-2.0.
COMMERCIAL_SAFE_KOKORO_VOICES: dict[str, str] = {
    "kokoro:af_bella": "Kokoro-82M weights and voicepacks Apache-2.0 (hexgrad/Kokoro-82M); "
    "cleared by the owner's ear test 2026-10-08",
}


def model_present(model_dir: Path) -> bool:
    """True only when every model file exists and is non-empty (a leftover ``.part`` never counts)."""
    d = Path(model_dir)
    return all((d / n).is_file() and (d / n).stat().st_size > 0 for n in MODEL_FILES)


def download_model(model_dir: Path, http: httpx.Client | None = None) -> None:
    """Fetch the model files from the release. Each goes to ``<name>.part`` first, then is renamed."""
    d = Path(model_dir)
    owns = http is None
    client = http or httpx.Client(
        follow_redirects=True, timeout=300, headers={"User-Agent": "AIMediaZero/0.1"}
    )
    try:
        for name in MODEL_FILES:
            dest = d / name
            if dest.is_file() and dest.stat().st_size > 0:
                continue
            d.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            log.info("downloading Kokoro model file %s", name)
            try:
                with client.stream("GET", f"{RELEASE_URL}/{name}") as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_bytes(1 << 16):
                            fh.write(chunk)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            tmp.replace(dest)
    finally:
        if owns:
            client.close()


def packages_importable() -> bool:
    """True when the optional ``voice`` extra (kokoro-onnx) imports."""
    try:
        import kokoro_onnx  # noqa: F401
    except Exception:  # ImportError or DLL load failure
        return False
    return True


class KokoroTTSProvider(TTSProvider):
    name = "KokoroTTSProvider"
    is_paid = False

    def __init__(
        self,
        model_dir: Path,
        auto_download: bool = True,
        pronunciations: dict[str, str] | None = None,
        sentence_pause_s: float = 0.25,
        length_scale: float = 1.0,
        engine: Any | None = None,
    ):
        self.model_dir = Path(model_dir)
        self.auto_download = auto_download
        self.pronunciations = pronunciations or {}
        self.sentence_pause_s = sentence_pause_s
        self.length_scale = length_scale
        self._engine = engine  # a loaded kokoro_onnx.Kokoro (tests pass a fake)

    # -- health ----------------------------------------------------------------------
    def health(self) -> HealthStatus:
        if self._engine is None and not packages_importable():
            return HealthStatus(False, "kokoro-onnx not importable", "pip install -e .[voice]")
        if not model_present(self.model_dir):
            return HealthStatus(
                False, f"Kokoro model files missing in {self.model_dir}", "python -m aimz doctor --fix"
            )
        import numpy as np

        try:
            with np.load(self.model_dir / MODEL_FILES[1]) as voices:
                names = set(voices.files)
        except Exception as exc:
            return HealthStatus(False, f"Kokoro voices file unreadable: {exc}", "python -m aimz doctor --fix")
        missing = [v for v in COMMERCIAL_SAFE_KOKORO_VOICES if v.removeprefix(PREFIX) not in names]
        if missing:
            return HealthStatus(
                False,
                f"Kokoro voices file lacks {', '.join(v.removeprefix(PREFIX) for v in missing)}",
                "delete the Kokoro model files and run `python -m aimz doctor --fix`",
            )
        return HealthStatus(
            True, f"Kokoro ready ({self.model_dir}); cleared: {', '.join(COMMERCIAL_SAFE_KOKORO_VOICES)}"
        )

    def ensure_model(self) -> None:
        if model_present(self.model_dir):
            return
        if not self.auto_download:
            raise ProviderUnavailable(
                f"Kokoro model files not found in {self.model_dir}. Run `python -m aimz doctor --fix`."
            )
        download_model(self.model_dir)

    def _load(self) -> Any:
        if self._engine is None:
            try:
                from kokoro_onnx import Kokoro
            except Exception as exc:
                raise ProviderUnavailable(
                    f"kokoro-onnx not importable: {exc}. pip install -e .[voice]"
                ) from exc
            self.ensure_model()
            self._engine = Kokoro(str(self.model_dir / MODEL_FILES[0]), str(self.model_dir / MODEL_FILES[1]))
        return self._engine

    @staticmethod
    def _resolve(voice: str | None) -> str:
        if voice not in COMMERCIAL_SAFE_KOKORO_VOICES:
            raise ProviderUnavailable(
                f"voice {voice!r} is not licence-cleared for commercial use "
                f"(allowed: {', '.join(COMMERCIAL_SAFE_KOKORO_VOICES)})"
            )
        return str(voice).removeprefix(PREFIX)

    # -- synthesis -------------------------------------------------------------------
    def synthesize(
        self,
        ctx: ProviderContext,
        text: str,
        out_path: Path,
        purpose: str = "narration",
        options: SpeechOptions | None = None,
    ) -> TTSResult:
        """Synthesize ``text`` to ``out_path`` (16-bit mono WAV at the model's rate); chunk timings on ``result.chunks``."""
        import numpy as np

        opts = options or SpeechOptions()
        voice_id = self._resolve(opts.voice)
        lang = "en-gb" if voice_id.startswith("b") else "en-us"
        length_scale = self.length_scale if opts.length_scale is None else opts.length_scale
        speed = min(2.0, max(0.5, 1.0 / float(length_scale)))
        pause_s = self.sentence_pause_s if opts.sentence_pause_s is None else opts.sentence_pause_s
        chunks = split_sentences(clean_for_tts(text))
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with self.authorized(ctx, purpose) as rec:
            engine = self._load()
            timings: list[tuple[float, float, str]] = []
            sample_rate = 24000
            frames: list[bytes] = []
            cursor = 0.0
            for chunk in chunks:
                spoken = apply_pronunciations(chunk, self.pronunciations)
                samples, sample_rate = engine.create(spoken, voice=voice_id, speed=speed, lang=lang)
                pcm = (np.clip(np.asarray(samples, np.float32), -1.0, 1.0) * 32767).astype("<i2").tobytes()
                dur = len(pcm) / (2 * sample_rate)
                timings.append((cursor, cursor + dur, chunk))
                frames.append(pcm)
                frames.append(b"\x00\x00" * int(pause_s * sample_rate))
                cursor += dur + pause_s
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
