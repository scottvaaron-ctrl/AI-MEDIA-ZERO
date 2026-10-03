"""SilentTTSProvider: writes timed silence. For offline tests/CI only (TTS_PROVIDER=silent)."""

from __future__ import annotations

import wave
from pathlib import Path

from aimz.providers.base import HealthStatus, ProviderContext, SpeechOptions, TTSProvider, TTSResult
from aimz.providers.tts.text_prep import clean_for_tts, split_sentences
from aimz.util import estimate_speech_seconds


class SilentTTSProvider(TTSProvider):
    name = "SilentTTSProvider"
    is_paid = False

    def __init__(self, sample_rate: int = 22050, wpm: float = 165.0):
        self.sample_rate = sample_rate
        self.wpm = wpm
        self.length_scale = 1.0
        self.sentence_pause_s = 0.2

    def health(self) -> HealthStatus:
        return HealthStatus(True, "silent TTS (test fallback; produces no speech)")

    def synthesize(
        self,
        ctx: ProviderContext,
        text: str,
        out_path: Path,
        purpose: str = "narration",
        options: SpeechOptions | None = None,
    ) -> TTSResult:
        opts = options or SpeechOptions()
        scale = self.length_scale if opts.length_scale is None else opts.length_scale
        pause = self.sentence_pause_s if opts.sentence_pause_s is None else opts.sentence_pause_s
        text = clean_for_tts(text)
        chunks = split_sentences(text)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with self.authorized(ctx, purpose):
            timings: list[tuple[float, float, str]] = []
            cursor = 0.0
            with wave.open(str(out_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                for chunk in chunks:
                    dur = estimate_speech_seconds(chunk, self.wpm) * scale
                    timings.append((cursor, cursor + dur, chunk))
                    wf.writeframes(b"\x00\x00" * int((dur + pause) * self.sample_rate))
                    cursor += dur + pause
        result = TTSResult(path=str(out_path), duration_s=cursor, sample_rate=self.sample_rate)
        result.chunks = timings  # type: ignore[attr-defined]
        return result
