"""Kokoro narration engine: WAV format, timings, speed, language, pronunciations, cleared voices, model files."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from aimz.core.errors import ProviderUnavailable
from aimz.providers.base import SpeechOptions
from aimz.providers.tts.kokoro import (
    MODEL_FILES,
    RELEASE_URL,
    KokoroTTSProvider,
    download_model,
    model_present,
)

BELLA = SpeechOptions(voice="kokoro:af_bella")


class FakeKokoro:
    """Stands in for ``kokoro_onnx.Kokoro``: half a second of a constant tone per call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, text: str, voice: str, speed: float = 1.0, lang: str = "en-us") -> tuple[Any, int]:
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        return np.full(int(0.5 * 24000), 0.25, np.float32), 24000


def provider(tmp_path: Path, **kw: Any) -> tuple[KokoroTTSProvider, FakeKokoro]:
    fake = FakeKokoro()
    return KokoroTTSProvider(tmp_path, auto_download=False, engine=fake, **kw), fake


def test_wav_is_16bit_mono_24k(svc, tmp_path: Path) -> None:  # noqa: ANN001
    tts, _ = provider(tmp_path)
    res = tts.synthesize(svc.ctx(), "One sentence.", tmp_path / "a.wav", options=BELLA)
    with wave.open(res.path) as wf:
        assert (wf.getsampwidth(), wf.getnchannels(), wf.getframerate()) == (2, 1, 24000)
    assert res.sample_rate == 24000


def test_chunk_timings_and_pauses(svc, tmp_path: Path) -> None:  # noqa: ANN001
    tts, _ = provider(tmp_path, sentence_pause_s=0.2)
    res = tts.synthesize(svc.ctx(), "One. Two.", tmp_path / "a.wav", options=BELLA)
    chunks = res.chunks  # type: ignore[attr-defined]
    assert [c[2] for c in chunks] == ["One.", "Two."]
    assert chunks[0][:2] == pytest.approx((0.0, 0.5))
    assert chunks[1][:2] == pytest.approx((0.7, 1.2))
    assert res.duration_s == pytest.approx(1.4)
    with wave.open(res.path) as wf:
        assert wf.getnframes() / wf.getframerate() == pytest.approx(1.4, abs=0.001)


def test_speed_is_inverse_length_scale_and_clamped(svc, tmp_path: Path) -> None:  # noqa: ANN001
    tts, fake = provider(tmp_path)
    tts.synthesize(
        svc.ctx(),
        "Hi.",
        tmp_path / "a.wav",
        options=SpeechOptions(voice="kokoro:af_bella", length_scale=1.25),
    )
    tts.synthesize(
        svc.ctx(),
        "Hi.",
        tmp_path / "b.wav",
        options=SpeechOptions(voice="kokoro:af_bella", length_scale=0.25),
    )
    assert fake.calls[0]["speed"] == pytest.approx(0.8)
    assert fake.calls[1]["speed"] == pytest.approx(2.0)


def test_lang_by_voice_prefix(svc, tmp_path: Path) -> None:  # noqa: ANN001
    tts, fake = provider(tmp_path)
    tts.synthesize(svc.ctx(), "Hi.", tmp_path / "a.wav", options=BELLA)
    assert fake.calls[0]["lang"] == "en-us"
    assert fake.calls[0]["voice"] == "af_bella"


def test_pronunciations_applied(svc, tmp_path: Path) -> None:  # noqa: ANN001
    tts, fake = provider(tmp_path, pronunciations={"Kamakura": "Kah-mah-koo-rah"})
    res = tts.synthesize(svc.ctx(), "Kamakura has a giant Buddha.", tmp_path / "a.wav", options=BELLA)
    assert "Kah-mah-koo-rah" in fake.calls[0]["text"]
    assert "Kamakura" in res.chunks[0][2]  # type: ignore[attr-defined]


def test_uncleared_kokoro_voice_refused(svc, tmp_path: Path) -> None:  # noqa: ANN001
    tts, fake = provider(tmp_path)
    with pytest.raises(ProviderUnavailable, match="not licence-cleared"):
        tts.synthesize(svc.ctx(), "Hi.", tmp_path / "a.wav", options=SpeechOptions(voice="kokoro:am_fenrir"))
    assert fake.calls == []


def _serve(fail_on: str | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        name = str(request.url).rsplit("/", 1)[-1]
        assert str(request.url) == f"{RELEASE_URL}/{name}"
        if name == fail_on:

            def broken() -> Any:
                yield b"partial"
                raise httpx.ReadError("connection reset")

            return httpx.Response(200, content=broken())
        return httpx.Response(200, content=b"model bytes " + name.encode())

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_model_uses_part_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    from aimz.providers.tts import kokoro

    served = {n: hashlib.sha256(b"model bytes " + n.encode()).hexdigest() for n in MODEL_FILES}
    monkeypatch.setattr(kokoro, "MODEL_SHA256", served)  # the fake bytes, not the real release
    download_model(tmp_path / "ok", http=_serve())
    assert model_present(tmp_path / "ok")
    bad = tmp_path / "bad"
    with pytest.raises(httpx.ReadError):
        download_model(bad, http=_serve(fail_on=MODEL_FILES[1]))
    assert not (bad / MODEL_FILES[1]).exists()
    assert not (bad / (MODEL_FILES[1] + ".part")).exists()
    assert not model_present(bad)
    (bad / (MODEL_FILES[1] + ".part")).write_bytes(b"half")
    assert not model_present(bad)


def test_health_reports_missing_model(tmp_path: Path) -> None:
    # A loaded engine skips the package check, so this runs where kokoro-onnx is not installed (CI).
    h = KokoroTTSProvider(tmp_path, auto_download=False, engine=FakeKokoro()).health()
    assert h.ok is False
    assert "doctor --fix" in (h.fix or "")


def _model_files(d: Path, voices: list[str]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / MODEL_FILES[0]).write_bytes(b"onnx")
    np.savez(d / "voices.npz", **{v: np.zeros((2, 2), np.float32) for v in voices})
    (d / "voices.npz").replace(d / MODEL_FILES[1])


def test_health_needs_bella_in_voices_file(tmp_path: Path) -> None:
    pytest.importorskip("kokoro_onnx")  # CI installs without the optional `voice` extra
    _model_files(tmp_path / "a", ["af_heart"])
    h = KokoroTTSProvider(tmp_path / "a", auto_download=False).health()
    assert h.ok is False and "af_bella" in h.detail
    _model_files(tmp_path / "b", ["af_heart", "af_bella"])
    assert KokoroTTSProvider(tmp_path / "b", auto_download=False).health().ok is True


def test_tests_never_see_a_kokoro_model(svc) -> None:  # noqa: ANN001
    assert svc.env.kokoro_auto_download is False
    assert not model_present(svc.env.kokoro_model_dir)
