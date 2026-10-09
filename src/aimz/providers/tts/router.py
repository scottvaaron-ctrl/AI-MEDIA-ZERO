"""VoiceRouterTTS: one narration service over Piper and Kokoro, routed by voice name. Cost: $0.

``kokoro:<id>`` voices go to Kokoro, every other voice to Piper. Any Kokoro failure (missing package or model,
inference error) is raised as ``VoiceUnavailable``; the producer then re-narrates the whole video with the
fallback voice, so one video never mixes voices. The kill switch and budget refusals pass through unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aimz.core.errors import BudgetDenied, KillSwitchEngaged, VoiceUnavailable
from aimz.providers.base import HealthStatus, ProviderContext, SpeechOptions, TTSProvider, TTSResult
from aimz.providers.tts.kokoro import COMMERCIAL_SAFE_KOKORO_VOICES, PREFIX, KokoroTTSProvider
from aimz.providers.tts.piper import COMMERCIAL_SAFE_VOICES, PiperTTSProvider


def cleared_voices() -> list[str]:
    """Every voice that may be spoken: Piper's cleared voices, then Kokoro's."""
    return list(COMMERCIAL_SAFE_VOICES) + list(COMMERCIAL_SAFE_KOKORO_VOICES)


class VoiceRouterTTS(TTSProvider):
    name = "VoiceRouterTTS"
    is_paid = False

    def __init__(self, piper: PiperTTSProvider, kokoro: KokoroTTSProvider):
        self.piper = piper
        self.kokoro = kokoro

    # Delivery defaults are Piper's (unchanged behaviour when a setting is off).
    @property
    def voice(self) -> str | None:
        return self.piper.voice

    @property
    def length_scale(self) -> float:
        return self.piper.length_scale

    @property
    def sentence_pause_s(self) -> float:
        return self.piper.sentence_pause_s

    def ensure_voice(self, voice: str | None = None) -> Any:
        return self.piper.ensure_voice(voice)

    def health(self) -> HealthStatus:
        p, k = self.piper.health(), self.kokoro.health()
        detail = f"{p.detail}; Kokoro: {k.detail}" + ("" if k.ok else f" (fix: {k.fix})")
        return HealthStatus(p.ok, detail, p.fix)

    def synthesize(
        self,
        ctx: ProviderContext,
        text: str,
        out_path: Path,
        purpose: str = "narration",
        options: SpeechOptions | None = None,
    ) -> TTSResult:
        voice = options.voice if options else None
        if not (voice or "").startswith(PREFIX):
            return self.piper.synthesize(ctx, text, out_path, purpose, options)
        try:
            return self.kokoro.synthesize(ctx, text, out_path, purpose, options)
        except (KillSwitchEngaged, BudgetDenied):
            raise
        except Exception as exc:
            raise VoiceUnavailable(f"{type(exc).__name__}: {exc}") from exc
