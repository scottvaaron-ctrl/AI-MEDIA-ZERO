"""Minor review findings of 2026-10-09 (piece C and experiments on any item), fixed at the owner's request."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.test_experiments import GSAMPLE, _activate, _geng

from aimz import guideline, identity, instances
from aimz import guideline_proposals as gp
from aimz.core.attention import attention
from aimz.experiments.settings import CATALOG

# ---- experiments on any item --------------------------------------------------------------------------


def test_variant_genre_is_not_called_the_owners() -> None:
    g = guideline.parse(GSAMPLE, CATALOG, set())
    line = identity.genre_line(None, guideline.with_variant(g, "G-genre", "true crime"), test_variant=True)  # type: ignore[arg-type]
    assert "'true crime'" in line and "owner guideline" not in line and "test" in line


def test_script_writer_is_told_the_title_limit() -> None:
    from aimz.agents.script import ScriptAgent

    g = guideline.parse(GSAMPLE, CATALOG, set())
    assert "Title: at most 60 characters" in ScriptAgent._hook_limits_line(g)


BLOCK_STYLE = GSAMPLE.replace(
    '  banned_openings: ["Did you know", "In this video"]\n',
    "  banned_openings:\n    - Did you know\n    - In this video\n",
).replace("  genre: explainer\n", "  genre: >\n    explainer\n    for everyone\n")


@pytest.mark.parametrize(
    ("item", "after", "check"),
    [
        ("G-banned_openings", '["Imagine"]', lambda g: g.checks["banned_openings"] == ["Imagine"]),
        ("G-genre", "local history", lambda g: g.genre == "local history"),
    ],
)
def test_rewrite_handles_block_style_yaml(item: str, after: str, check: Any) -> None:
    new = gp.rewrite_item(BLOCK_STYLE, item, after)
    assert check(guideline.parse(new, CATALOG, set()))


def test_rewrite_keeps_unicode_and_drops_stale_comment_text() -> None:
    text = GSAMPLE.replace("  genre: explainer\n", '  genre: "explainer # old note"\n')
    new = gp.rewrite_item(text, "G-genre", "places — and their stories")
    assert "places — and their stories" in new and "old note" not in new
    assert guideline.parse(new, CATALOG, set()).genre == "places — and their stories"


def test_strategist_still_sees_unknown_checks(svc) -> None:  # noqa: ANN001
    from aimz.agents.analyst import AnalystAgent

    eng = _geng(
        svc, GSAMPLE.replace("  hook_max_words: 15\n", "  hook_max_words: 15\n  max_exclamations: 1\n")
    )
    text = AnalystAgent(svc, None, eng).guideline_prompt(eng.guideline, [])  # type: ignore[arg-type]
    assert "max_exclamations=1" in text and "not testable" in text


# ---- piece C ------------------------------------------------------------------------------------------


def test_fallback_voice_avoids_another_channels_voice(svc) -> None:  # noqa: ANN001
    from aimz.agents.producer import FALLBACK_VOICE, fallback_voice

    assert fallback_voice(svc.env.instance) == FALLBACK_VOICE
    instances.write_shared("space", {"niches": [], "identity": {"genre": "space", "voice": FALLBACK_VOICE}})
    other = fallback_voice(svc.env.instance)
    assert other != FALLBACK_VOICE and not other.startswith("kokoro:")


def test_off_target_loudness_shows_in_status_and_clears(svc) -> None:  # noqa: ANN001
    from tests.test_producer_voice import FakeRenderer, _script, _setup

    producer, _ = _setup(svc, fail_on=None)

    class Renderer(FakeRenderer):
        lufs = -17.2

        def render(self, ctx: Any, timeline: Any, out_dir: Path) -> Any:
            res = super().render(ctx, timeline, out_dir)
            res.loudness_lufs, res.true_peak_dbtp = self.lufs, -3.0
            return res

    svc.renderer = Renderer()
    with svc.tracker.run("p") as run:
        producer.produce(run, _script(svc))
    item = attention(svc.db)["loudness_off_target"][0]
    assert "-17.2" in item["why"]
    Renderer.lufs = -14.1
    with svc.tracker.run("p") as run:
        producer.produce(run, _script(svc))
    assert "loudness_off_target" not in attention(svc.db)


def test_fallback_pass_reuses_the_pictures_already_chosen(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from tests.test_producer_voice import _script, _setup

    producer, _ = _setup(svc, fail_on=3)
    picks: list[int] = []
    real = producer._pick_asset

    def spy(ctx: Any, beat: Any, title: str, dest: Path, i: int) -> Any:
        picks.append(i)
        return real(ctx, beat, title, dest, i)

    monkeypatch.setattr(producer, "_pick_asset", spy)
    with svc.tracker.run("p") as run:
        producer.produce(run, _script(svc, beats=4))
    assert sorted(picks) == [0, 1, 2, 3]  # each beat searched once, though narration ran twice


def test_voice_show_names_a_guideline_lock(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    _activate(svc, GSAMPLE.replace("  music: off\n", "  music: off\n  voice: en_US-john-medium\n"))
    out = CliRunner().invoke(app, ["voice", "show"])
    assert out.exit_code == 0 and "en_US-john-medium" in out.output and "guideline" in out.output


def test_present_voices_needs_the_kokoro_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np

    from aimz.providers.tts import kokoro, router
    from aimz.providers.tts.piper import PiperTTSProvider

    d = tmp_path / "k"
    d.mkdir()
    (d / kokoro.MODEL_FILES[0]).write_bytes(b"x")
    np.savez(d / "v.npz", af_bella=np.zeros(2))
    (d / "v.npz").replace(d / kokoro.MODEL_FILES[1])
    r = router.VoiceRouterTTS(
        PiperTTSProvider(tmp_path, auto_download=False), kokoro.KokoroTTSProvider(d, False)
    )
    monkeypatch.setattr(router, "packages_importable", lambda: False)
    assert r.present_voices() == []


def test_download_rejects_a_corrupt_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aimz.providers.tts import kokoro

    monkeypatch.setattr(
        kokoro, "MODEL_SHA256", {n: hashlib.sha256(b"good").hexdigest() for n in kokoro.MODEL_FILES}
    )
    good = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"good")))
    kokoro.download_model(tmp_path / "ok", http=good)
    assert kokoro.model_present(tmp_path / "ok")
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"evil")))
    with pytest.raises(kokoro.ProviderUnavailable, match="checksum"):
        kokoro.download_model(tmp_path / "bad", http=bad)
    assert not any((tmp_path / "bad").glob("*"))


def test_failed_download_is_not_retried_for_every_video(
    svc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    pytest.importorskip("kokoro_onnx")  # without the package no download is ever attempted (CI)
    from aimz.providers.base import SpeechOptions
    from aimz.providers.tts import kokoro

    calls: list[Path] = []

    def boom(model_dir: Path, http: Any = None) -> None:
        calls.append(model_dir)
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(kokoro, "download_model", boom)
    monkeypatch.setattr(kokoro, "_FAILED_DOWNLOADS", set())
    tts = kokoro.KokoroTTSProvider(tmp_path / "m", auto_download=True)
    for i in range(3):
        with pytest.raises(Exception):  # noqa: B017
            tts.synthesize(
                svc.ctx(), "Hi.", tmp_path / f"{i}.wav", options=SpeechOptions(voice="kokoro:af_bella")
            )
    assert len(calls) == 1


def test_pinned_checksums_cover_every_model_file() -> None:
    from aimz.providers.tts import kokoro

    assert set(kokoro.MODEL_SHA256) == set(kokoro.MODEL_FILES)
    assert all(len(v) == 64 for v in kokoro.MODEL_SHA256.values())
    assert json.dumps(sorted(kokoro.MODEL_SHA256))
