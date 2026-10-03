"""Stage 2 of docs/PLAN_FULL_CONTROL.md: the production dials reach the render."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageChops

from aimz.agents.producer import caption_style, motion_zoom, pad_wav, split_shots
from aimz.core.errors import ProviderUnavailable
from aimz.domain.models import CaptionStyle, Scene, Timeline
from aimz.experiments.settings import CATALOG, SettingsEngine
from aimz.providers.base import SpeechOptions
from aimz.providers.tts.piper import COMMERCIAL_SAFE_VOICES, PiperTTSProvider
from aimz.providers.tts.silent import SilentTTSProvider
from aimz.providers.video.ffmpeg_renderer import FFmpegRenderer, split_caption
from aimz.providers.video.fonts import shipped_fonts

# -- the catalogue -----------------------------------------------------------------------------------


def test_voice_choices_exclude_non_commercial_voices() -> None:
    choices = CATALOG["voice"].bounds["choices"]
    assert choices == list(COMMERCIAL_SAFE_VOICES)
    for banned in (
        "en_US-lessac-medium",
        "en_US-ryan-medium",
        "en_US-hfc_female-medium",
        "en_US-hfc_male-medium",
        "en_US-joe-medium",  # fine-tuned from lessac
        "en_US-bryce-medium",  # fine-tuned from an unreleased voice
    ):
        assert banned not in choices


def test_voice_has_no_default_and_cannot_be_turned_off(svc) -> None:  # noqa: ANN001
    import random

    eng = SettingsEngine(svc.db)
    assert "cannot be turned off" in (eng.change("voice", "off") or "")
    rng = random.Random(1)
    seen = {eng.assign(f"vid_{i}", {}, {"voice": None}, [], rng).values["voice"] for i in range(40)}
    assert seen and seen <= set(COMMERCIAL_SAFE_VOICES) and len(seen) > 1
    # A voice locked before it was found not cleared is never used.
    svc.db.execute(
        "UPDATE setting_space SET status='locked', locked_value=? WHERE variable='voice'",
        [json.dumps("en_US-joe-medium")],
    )
    assert eng.assign("vid_x", {}, {"voice": None}, [], rng).values["voice"] in COMMERCIAL_SAFE_VOICES


def test_count_dials_round_to_whole_numbers(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    assert eng.change("caption_max_words", "open", [3.4, 7.6]) is None
    assert eng.space()["caption_max_words"]["values"] == [3, 8]
    assert "outside" in (eng.change("caption_outline", "lock", locked_value=1) or "")


def test_font_manifest_only_offers_free_licensed_files(tmp_path: Path) -> None:
    (tmp_path / "Good.ttf").write_bytes(b"x")
    (tmp_path / "Paid.ttf").write_bytes(b"x")
    (tmp_path / "licenses.json").write_text(
        json.dumps(
            [
                {"family": "Good", "file": "Good.ttf", "license": "OFL-1.1", "source": "u"},
                {"family": "Paid", "file": "Paid.ttf", "license": "proprietary", "source": "u"},
                {"family": "Missing", "file": "Missing.ttf", "license": "OFL-1.1", "source": "u"},
            ]
        ),
        encoding="utf-8",
    )
    assert list(shipped_fonts(tmp_path)) == ["Good"]


# -- speech ------------------------------------------------------------------------------------------


def test_piper_never_speaks_an_uncleared_voice(tmp_path: Path) -> None:
    # The old default (lessac, research-only licence) left in an .env is ignored, not spoken.
    tts = PiperTTSProvider(tmp_path, voice="en_US-lessac-medium", auto_download=False)
    assert tts.voice is None
    for voice in (None, "en_US-lessac-medium", "en_US-joe-medium", "en_US-bryce-medium"):
        with pytest.raises(ProviderUnavailable, match="not licence-cleared"):
            tts._load(voice)
    assert "en_US-joe-medium" not in COMMERCIAL_SAFE_VOICES
    assert "en_US-lessac-medium" not in COMMERCIAL_SAFE_VOICES


def test_piper_loads_the_chosen_voice_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tts = PiperTTSProvider(tmp_path, voice="en_US-ljspeech-medium", auto_download=False)
    loaded: list[str] = []

    class FakeVoice:
        def __init__(self, name: str):
            self.name = name

    class FakePiper:
        class PiperVoice:
            @staticmethod
            def load(path: str) -> FakeVoice:
                loaded.append(Path(path).stem)
                return FakeVoice(Path(path).stem)

    monkeypatch.setattr(tts, "_import", lambda: FakePiper)
    monkeypatch.setattr(
        tts, "ensure_voice", lambda v=None: (tmp_path / f"{v or tts.voice}.onnx", tmp_path / "x")
    )
    assert tts._load("en_US-norman-medium").name == "en_US-norman-medium"
    assert tts._load("en_US-norman-medium").name == "en_US-norman-medium"
    assert tts._load(None).name == "en_US-ljspeech-medium"
    assert loaded == ["en_US-norman-medium", "en_US-ljspeech-medium"]


def test_silent_tts_ignores_voice_but_honours_speed(svc) -> None:  # noqa: ANN001
    tts = SilentTTSProvider()
    text = "One sentence here. Another sentence there."
    a = tts.synthesize(svc.ctx(), text, svc.env.data_dir / "a.wav")
    b = tts.synthesize(
        svc.ctx(), text, svc.env.data_dir / "b.wav", options=SpeechOptions(length_scale=1.3, voice="x")
    )
    assert b.duration_s > a.duration_s


# -- shots and captions ----------------------------------------------------------------------------------


def _wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(seconds * rate))
    return path


def _seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as r:
        return r.getnframes() / r.getframerate()


def test_beat_pause_is_padded_into_the_audio(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "a.wav", 2.0)
    pad_wav(wav, 2.5)
    assert _seconds(wav) == pytest.approx(2.5, abs=0.001)
    pad_wav(wav, 1.0)  # never truncates
    assert _seconds(wav) == pytest.approx(2.5, abs=0.001)


def test_long_beat_is_cut_into_equal_shots_with_captions_rebased(tmp_path: Path) -> None:
    wav = _wav(tmp_path / "b.wav", 9.0)
    chunks = [(0.0, 4.0, "first"), (4.0, 8.6, "second")]
    shots = split_shots(wav, 9.0, chunks, 4.0)
    assert len(shots) == 3 and all(d == pytest.approx(3.0) for _, d, _ in shots)
    assert sum(_seconds(p) for p, _, _ in shots) == pytest.approx(9.0, abs=0.01)
    assert shots[0][2] == [(0.0, 3.0, "first")]
    assert shots[1][2] == [(0.0, 1.0, "first"), (1.0, 3.0, "second")]
    assert shots[2][2] == [(0.0, 2.6, "second")]
    assert split_shots(wav, 9.0, chunks, None) == [(wav, 9.0, chunks)]


def test_motion_styles() -> None:
    assert [motion_zoom("alternate", i) for i in range(3)] == ["in", "out", "in"]
    assert motion_zoom("none", 5) == "none" and motion_zoom("out", 0) == "out"


def test_captions_split_by_word_limit_and_keep_timing() -> None:
    pieces = split_caption(1.0, 3.0, "one two three four five six", 2)
    assert [p[2] for p in pieces] == ["one two", "three four", "five six"]
    assert pieces[0][0] == 1.0 and pieces[-1][1] == pytest.approx(3.0)
    assert split_caption(0, 1, "short one", None) == [(0, 1, "short one")]


def test_caption_style_reaches_the_ass_header(tmp_path: Path) -> None:
    style = caption_style(
        {
            "caption_size": 0.05,
            "caption_position": "bottom",
            "caption_color": "yellow",
            "caption_outline": 6,
            "caption_max_words": 2,
        }
    )
    assert style == CaptionStyle(size=0.05, position="bottom", color="yellow", outline=6, max_words=2)
    scene = Scene(
        idx=0,
        duration_s=3.0,
        narration="one two three four",
        caption_chunks=[(0.0, 3.0, "one two three four")],
    )
    tl = Timeline(video_id="v", title="t", scenes=[scene], caption_style=style)
    FFmpegRenderer().write_captions(tl, tmp_path / "c.ass", tmp_path / "c.srt")
    ass = (tmp_path / "c.ass").read_text(encoding="utf-8")
    style_line = next(line for line in ass.splitlines() if line.startswith("Style: Cap"))
    fields = style_line.split(",")
    assert fields[2] == str(int(1920 * 0.05)) and fields[3] == "&H0000FFFF"
    assert fields[16] == "6" and fields[-2] == str(int(1920 * 0.22))  # above the platform overlay
    assert ass.count("Dialogue:") == 2
    assert caption_style({}) == CaptionStyle()  # every dial off: the renderer's old look


# -- cards ---------------------------------------------------------------------------------------------------


def _card(svc: Any, name: str, **spec: Any) -> Image.Image:
    base = {"kind": "text_card", "seed": "vid_same", "headline": "A headline worth reading"}
    return Image.open(
        svc.cards.render_card(svc.ctx(), {**base, **spec}, svc.env.data_dir / f"{name}.png")
    ).convert("RGB")


def _differs(a: Image.Image, b: Image.Image) -> bool:
    return ImageChops.difference(a, b).getbbox() is not None


def test_palette_hue_replaces_the_per_video_hash(svc) -> None:  # noqa: ANN001
    a = _card(svc, "a", palette_hue=0.1, seed="one")
    b = _card(svc, "b", palette_hue=0.1, seed="two")
    c = _card(svc, "c", palette_hue=0.6, seed="one")
    assert not _differs(a, b) and _differs(a, c)


def test_counter_and_headline_switches(svc) -> None:  # noqa: ANN001
    plain = _card(svc, "plain")
    counted = _card(svc, "counted", counter="3/7")
    assert _differs(plain, counted)
    # without a photo the headline stays, so the card is never blank
    assert not _differs(plain, _card(svc, "nohead", show_headline=False))
    photo = svc.env.data_dir / "photo.png"
    Image.new("RGB", (800, 600), (90, 120, 150)).save(photo)
    with_head = _card(svc, "ph1", image_path=str(photo))
    without = _card(svc, "ph2", image_path=str(photo), show_headline=False)
    assert _differs(with_head, without)


# -- the render ----------------------------------------------------------------------------------------------


def test_rendered_length_matches_the_timeline(svc, ffmpeg_available: bool) -> None:  # noqa: ANN001
    """The beat pause is heard: the video is as long as its timeline, so captions do not drift."""
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available")
    ctx = svc.ctx()
    tts = SilentTTSProvider()
    scenes, cursor = [], 0.0
    for i in range(4):
        wav = svc.env.data_dir / f"d{i}.wav"
        res = tts.synthesize(ctx, "A short line of narration.", wav)
        png = svc.cards.render_card(
            ctx, {"kind": "text_card", "seed": "v", "headline": "x"}, svc.env.data_dir / f"d{i}.png"
        )
        dur = res.duration_s + 0.8
        pad_wav(wav, dur)
        scenes.append(
            Scene(
                idx=i, start_s=cursor, duration_s=dur, narration="x", audio_path=str(wav), image_path=str(png)
            )
        )
        cursor += dur
    tl = Timeline(video_id="v", title="t", scenes=scenes, total_duration_s=cursor, fps=24)
    result = FFmpegRenderer(svc.env.ffmpeg_bin, svc.env.ffprobe_bin).render(ctx, tl, svc.env.data_dir / "r")
    assert result.duration_s == pytest.approx(cursor, abs=0.25)


def test_a_cycle_uses_the_new_dials(svc, ffmpeg_available: bool) -> None:  # noqa: ANN001
    from tests.test_pipeline_offline import FixtureFeedProvider

    from aimz.pipeline.orchestrator import Orchestrator

    eng = SettingsEngine(svc.db)
    for var, val in (
        ("max_scene_s", 2.5),
        ("motion_style", "none"),
        ("progress_counter", "on"),
        ("caption_color", "cyan"),
        ("caption_max_words", 3),
        ("palette_hue", 0.3),
    ):
        assert eng.change(var, "lock", locked_value=val) is None
    fp = FixtureFeedProvider()
    svc.research = {k: fp for k in fp.kinds}
    Orchestrator(svc, seed=7).cycle()
    video = svc.db.one("SELECT * FROM videos WHERE timeline_json IS NOT NULL")
    if video is None:
        pytest.skip("the fixture cycle did not reach a finished timeline (no ffmpeg)")
    tl = Timeline.model_validate_json(video["timeline_json"])
    assert tl.caption_style.color == "cyan" and tl.caption_style.max_words == 3
    assert all(s.duration_s <= 2.5 + 1e-6 for s in tl.scenes)
    assert {s.zoom for s in tl.scenes} <= {"none", "in", "out"}
    rows = {
        r["variable"]
        for r in svc.db.query("SELECT variable FROM video_settings WHERE video_id=?", [video["id"]])
    }
    assert {k for k, d in CATALOG.items() if d.requires in {"", "sfx"}} <= rows
    if ffmpeg_available:
        assert video["duration_s"] == pytest.approx(tl.total_duration_s, abs=0.5)


def test_captions_never_climb_into_the_card_text() -> None:
    from aimz.providers.video.ffmpeg_renderer import CAPTION_BAND, CAPTION_MARGINS, caption_char_limit

    long = " ".join(["word"] * 14)
    for size in (0.028, 0.036, 0.06):
        for position, margin in CAPTION_MARGINS.items():
            style = CaptionStyle(size=size, position=position)
            limit = caption_char_limit(1080, 1920, style)
            pieces = split_caption(0.0, 5.0, long, None, limit)
            assert all(len(p[2]) <= limit for p in pieces)
            chars_per_line = max(8, int((1080 - 2 * 170) / (1920 * size * 0.5)))
            lines = -(-limit // chars_per_line)
            assert margin + lines * size * 1.25 <= CAPTION_BAND + 1e-9


# -- 30 Sep screenshot: text shown twice, cut mid-word, and under the platform's own buttons --------------


def test_a_caption_that_repeats_the_narration_is_not_printed_again() -> None:
    from aimz.agents.producer import repeats_narration

    said = "CSDA strengthens data quality through calibration and validation partnerships with commercial data providers."
    assert repeats_narration("CSDA strengthens data quality through calibration and valida", said)
    assert repeats_narration("CSDA strengthens data quality", said)
    assert not repeats_narration("Quality you can trust", said)
    assert not repeats_narration("CSDA", said)  # a word or two is a label, not a repeat


def test_captions_are_never_cut_mid_word() -> None:
    from aimz.agents.script import _clip_words

    text = "CSDA strengthens data quality through calibration and validation partnerships"
    assert _clip_words(text, 60) == "CSDA strengthens data quality through calibration and"
    assert _clip_words("short", 60) == "short"


def test_text_stays_out_of_the_platform_overlay() -> None:
    from aimz.providers.video import layout
    from aimz.providers.video.ffmpeg_renderer import CAPTION_BAND, CAPTION_MARGINS

    assert min(CAPTION_MARGINS.values()) >= layout.BOTTOM_UI  # above channel name, title, description
    assert CAPTION_BAND <= 1 - layout.CARD_TEXT_BOTTOM  # below the card text
    assert layout.LABEL_Y >= layout.TOP_UI and layout.CARD_TEXT_TOP > layout.LABEL_Y
    assert layout.SIDE_UI_PX / 1080 >= 0.15  # clear of the like/comment/share column
