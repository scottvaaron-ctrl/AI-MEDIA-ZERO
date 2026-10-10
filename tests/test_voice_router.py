"""The voice router: kokoro: voices go to Kokoro, everything else to Piper; Kokoro failures become VoiceUnavailable."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aimz.core.errors import BudgetDenied, KillSwitchEngaged, VoiceUnavailable
from aimz.providers.base import HealthStatus, SpeechOptions, TTSResult
from aimz.providers.tts.router import VoiceRouterTTS


class FakeTTS:
    def __init__(self, name: str, ok: bool = True, raises: BaseException | None = None) -> None:
        self.name = name
        self.ok = ok
        self.raises = raises
        self.calls: list[str | None] = []
        self.voice = "en_US-kristin-medium" if name == "piper" else None
        self.length_scale = 1.1
        self.sentence_pause_s = 0.3

    def synthesize(
        self, ctx: Any, text: str, out_path: Path, purpose: str = "narration", options: Any = None
    ) -> TTSResult:
        self.calls.append(options.voice if options else None)
        if self.raises is not None:
            raise self.raises
        return TTSResult(path=str(out_path), duration_s=1.0, sample_rate=24000)

    def health(self) -> HealthStatus:
        return HealthStatus(
            self.ok, f"{self.name} {'ok' if self.ok else 'down'}", None if self.ok else "fix it"
        )


def router(
    kokoro_raises: BaseException | None = None, kokoro_ok: bool = True
) -> tuple[VoiceRouterTTS, FakeTTS, FakeTTS]:
    piper, kokoro = FakeTTS("piper"), FakeTTS("kokoro", ok=kokoro_ok, raises=kokoro_raises)
    return VoiceRouterTTS(piper, kokoro), piper, kokoro  # type: ignore[arg-type]


def test_kokoro_voice_goes_to_kokoro_others_to_piper(tmp_path: Path) -> None:
    r, piper, kokoro = router()
    r.synthesize(None, "Hi.", tmp_path / "a.wav", options=SpeechOptions(voice="kokoro:af_bella"))  # type: ignore[arg-type]
    r.synthesize(None, "Hi.", tmp_path / "b.wav", options=SpeechOptions(voice="en_US-john-medium"))  # type: ignore[arg-type]
    r.synthesize(None, "Hi.", tmp_path / "c.wav")  # type: ignore[arg-type]
    assert kokoro.calls == ["kokoro:af_bella"]
    assert piper.calls == ["en_US-john-medium", None]
    assert (r.voice, r.length_scale, r.sentence_pause_s) == ("en_US-kristin-medium", 1.1, 0.3)
    assert r.piper is piper and r.kokoro is kokoro


def test_kokoro_failure_becomes_voice_unavailable(tmp_path: Path) -> None:
    r, piper, _ = router(kokoro_raises=RuntimeError("onnx session failed"))
    with pytest.raises(VoiceUnavailable, match="onnx session failed"):
        r.synthesize(None, "Hi.", tmp_path / "a.wav", options=SpeechOptions(voice="kokoro:af_bella"))  # type: ignore[arg-type]
    assert piper.calls == []  # the router never mixes voices itself: the producer re-runs the whole video


@pytest.mark.parametrize(
    "exc", [KillSwitchEngaged("kill switch on"), BudgetDenied("KokoroTTSProvider", "narration", 0.0, 0.0)]
)
def test_killswitch_and_budget_pass_through(tmp_path: Path, exc: BaseException) -> None:
    r, _, _ = router(kokoro_raises=exc)
    with pytest.raises(type(exc)):
        r.synthesize(None, "Hi.", tmp_path / "a.wav", options=SpeechOptions(voice="kokoro:af_bella"))  # type: ignore[arg-type]


def test_health_ok_when_piper_ok_even_if_kokoro_down() -> None:
    r, _, _ = router(kokoro_ok=False)
    h = r.health()
    assert h.ok is True
    assert "kokoro down" in h.detail


def test_registry_builds_router_for_piper_and_silent_for_tests(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aimz.providers.registry import build_services
    from aimz.providers.tts.silent import SilentTTSProvider

    svc = build_services(project, quiet_logs=True)
    try:
        assert isinstance(svc.tts, SilentTTSProvider)
    finally:
        svc.close()
    monkeypatch.setenv("TTS_PROVIDER", "piper")
    svc = build_services(project, quiet_logs=True)
    try:
        assert isinstance(svc.tts, VoiceRouterTTS)
        assert svc.tts.kokoro.model_dir == svc.env.kokoro_model_dir
    finally:
        svc.close()


def test_present_voices_lists_piper_files_and_kokoro_when_its_model_is_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np

    from aimz.providers.tts import router

    monkeypatch.setattr(router, "packages_importable", lambda: True)  # CI installs without kokoro-onnx

    from aimz.providers.tts.kokoro import MODEL_FILES, KokoroTTSProvider
    from aimz.providers.tts.piper import PiperTTSProvider

    (tmp_path / "piper").mkdir()
    (tmp_path / "piper" / "en_US-kristin-medium.onnx").write_bytes(b"x")
    r = VoiceRouterTTS(
        PiperTTSProvider(tmp_path / "piper", auto_download=False),
        KokoroTTSProvider(tmp_path / "k", auto_download=False),
    )
    assert r.present_voices() == ["en_US-kristin-medium"]
    (tmp_path / "k").mkdir()
    (tmp_path / "k" / MODEL_FILES[0]).write_bytes(b"onnx")
    np.savez(tmp_path / "k" / "v.npz", af_bella=np.zeros(2))
    (tmp_path / "k" / "v.npz").replace(tmp_path / "k" / MODEL_FILES[1])
    assert r.present_voices() == ["en_US-kristin-medium", "kokoro:af_bella"]
