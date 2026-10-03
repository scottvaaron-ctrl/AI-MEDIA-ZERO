"""Stage 5 of docs/PLAN_FULL_CONTROL.md: stock media, video-clip scenes, music, sound, templates, endings."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from aimz.agents.producer import audio_bed
from aimz.domain.models import AssetCandidate, Scene, Timeline
from aimz.experiments.settings import CATALOG, SettingsEngine, capabilities
from aimz.providers.assets.stock import PexelsAssetProvider, PixabayAssetProvider
from aimz.providers.audio.library import music_moods, music_tracks, sfx

# -- stock providers (mocked from the documented responses; live check pending the owner's keys) --------------

PEXELS_PHOTOS = {
    "photos": [
        {
            "id": 1,
            "width": 3000,
            "height": 4500,
            "url": "https://www.pexels.com/photo/1/",
            "photographer": "Ann Lee",
            "alt": "Moon over hills",
            "src": {
                "large2x": "https://images.pexels.com/1.jpg",
                "original": "https://images.pexels.com/1o.jpg",
            },
        }
    ]
}
PEXELS_VIDEOS = {
    "videos": [
        {
            "id": 9,
            "url": "https://www.pexels.com/video/9/",
            "duration": 12,
            "user": {"name": "Bo Chen", "url": "u"},
            "video_files": [
                {
                    "link": "https://videos.pexels.com/9-sd.mp4",
                    "width": 540,
                    "height": 960,
                    "quality": "sd",
                    "file_type": "video/mp4",
                },
                {
                    "link": "https://videos.pexels.com/9-hd.mp4",
                    "width": 1080,
                    "height": 1920,
                    "quality": "hd",
                    "file_type": "video/mp4",
                },
                {
                    "link": "https://videos.pexels.com/9-4k.mp4",
                    "width": 2160,
                    "height": 3840,
                    "quality": "uhd",
                    "file_type": "video/mp4",
                },
            ],
        }
    ]
}
PIXABAY_PHOTOS = {
    "hits": [
        {
            "id": 5,
            "pageURL": "https://pixabay.com/p-5/",
            "tags": "moon, night",
            "largeImageURL": "https://pixabay.com/5.jpg",
            "imageWidth": 2000,
            "imageHeight": 3000,
            "user": "Cy",
        }
    ]
}
PIXABAY_VIDEOS = {
    "hits": [
        {
            "id": 7,
            "pageURL": "https://pixabay.com/v-7/",
            "tags": "ocean",
            "duration": 20,
            "user": "Di",
            "videos": {
                "large": {"url": "https://cdn.pixabay.com/7-l.mp4", "width": 1920, "height": 1080, "size": 1},
                "small": {"url": "https://cdn.pixabay.com/7-s.mp4", "width": 640, "height": 360, "size": 1},
            },
        }
    ]
}


def _client(seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if "api.pexels.com/v1/search" in url:
            return httpx.Response(200, json=PEXELS_PHOTOS)
        if "api.pexels.com/videos/search" in url:
            return httpx.Response(200, json=PEXELS_VIDEOS)
        if "pixabay.com/api/videos/" in url:
            return httpx.Response(200, json=PIXABAY_VIDEOS)
        if "pixabay.com/api/" in url:
            return httpx.Response(200, json=PIXABAY_PHOTOS)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pexels_requests_and_parsing(svc) -> None:  # noqa: ANN001
    seen: list[httpx.Request] = []
    pex = PexelsAssetProvider("KEY", "ua", _client(seen))
    photos = pex.search(svc.ctx(), "moon")
    assert photos[0].file_url.endswith("1.jpg") and photos[0].license == "Pexels License"
    assert photos[0].attribution.startswith("Photo by Ann Lee on Pexels")
    assert seen[0].headers["Authorization"] == "KEY" and seen[0].url.params["orientation"] == "portrait"
    videos = pex.search_videos(svc.ctx(), "moon")
    assert videos[0].file_url.endswith("9-hd.mp4") and videos[0].mime == "video/mp4"  # smallest file >= 720p
    assert str(seen[1].url).startswith("https://api.pexels.com/videos/search")
    pex.search(svc.ctx(), "moon")
    assert len(seen) == 2  # cached


def test_pixabay_requests_and_parsing(svc) -> None:  # noqa: ANN001
    seen: list[httpx.Request] = []
    pix = PixabayAssetProvider("KEY", "ua", _client(seen))
    photos = pix.search(svc.ctx(), "moon")
    assert photos[0].license == "Pixabay Content License" and "from Pixabay" in photos[0].attribution
    assert seen[0].url.params["key"] == "KEY" and seen[0].url.params["orientation"] == "vertical"
    assert pix.search_videos(svc.ctx(), "ocean")[0].file_url.endswith("7-l.mp4")


def test_stock_is_never_registered_without_the_owners_switch(
    svc, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    from aimz.providers.registry import build_services

    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setenv("PIXABAY_API_KEY", "k")
    s = build_services(project, quiet_logs=True)
    try:
        assert not any("Pexels" in p.name or "Pixabay" in p.name for p in s.assets)
        assert "stock_video" not in capabilities(s)
        assert "clip_scenes" not in SettingsEngine(s.db, caps=capabilities(s)).space()
    finally:
        s.close()


# -- audio library -----------------------------------------------------------------------------------------------


def test_the_starter_audio_library_is_listed_with_free_licences() -> None:
    assert music_moods() == ["bright", "calm", "tense"]
    assert all(t["license"] == "CC0-1.0" for t in music_tracks())
    assert sfx("whoosh") is not None
    assert CATALOG["music"].bounds["choices"] == ["off", "bright", "calm", "tense"]


def test_audio_bed_fields() -> None:
    scenes = [Scene(idx=i, start_s=float(i * 3), duration_s=3.0, narration="x") for i in range(3)]
    bed = audio_bed({"music": "calm", "music_volume_db": -25, "sfx": "on"}, scenes)
    assert bed["music_path"].endswith("calm.m4a") and bed["music_volume_db"] == -25
    assert bed["sfx_times"] == [3.0, 6.0]
    assert audio_bed({"music": "off", "sfx": "off"}, scenes) == {}


# -- cards --------------------------------------------------------------------------------------------------------


def _png(svc: Any, name: str, **spec: Any) -> Image.Image:
    base = {"kind": "text_card", "seed": "v", "headline": "A headline"}
    return Image.open(svc.cards.render_card(svc.ctx(), {**base, **spec}, svc.env.data_dir / f"{name}.png"))


def test_templates_and_overlay(svc) -> None:  # noqa: ANN001
    photo = svc.env.data_dir / "p.png"
    Image.new("RGB", (900, 1400), (200, 120, 60)).save(photo)
    card = _png(svc, "card", image_path=str(photo))
    bleed = _png(svc, "bleed", image_path=str(photo), template="full_bleed")
    assert bleed.mode == "RGB" and card.tobytes() != bleed.tobytes()
    assert bleed.getpixel((540, 1100))[0] > 60  # the photo reaches the middle of the frame
    ranking = _png(svc, "rank", template="ranking", rank=3)
    assert ranking.tobytes() != _png(svc, "plain").tobytes()
    overlay = _png(svc, "overlay", overlay=True)
    assert (
        overlay.mode == "RGBA" and overlay.getpixel((540, 1000))[3] < 255
    )  # see-through where the clip shows
    assert _png(svc, "fallback", template="full_bleed").tobytes() == _png(svc, "plain2").tobytes()  # no photo


# -- the render ---------------------------------------------------------------------------------------------------


def _test_clip(ffmpeg: str, path: Path) -> Path:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=24",
            "-t",
            "1.5",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def test_clip_scene_music_sfx_and_loop_render(svc, ffmpeg_available: bool) -> None:  # noqa: ANN001
    if not ffmpeg_available:
        pytest.skip("ffmpeg not available")
    from aimz.agents.producer import ProducerAgent, caption_style
    from aimz.domain.models import Beat
    from aimz.providers.video.ffmpeg_renderer import FFmpegRenderer
    from aimz.providers.video.render_check import check_render

    renderer = FFmpegRenderer(svc.env.ffmpeg_bin, svc.env.ffprobe_bin)
    assert renderer.ffmpeg
    clip = _test_clip(renderer.ffmpeg, svc.env.data_dir / "clip.mp4")
    producer = ProducerAgent(svc, None)
    rec = svc.db  # noqa: F841
    beats = [
        Beat(narration="The first line of narration here.", visual_query="Jupiter moon sky"),
        Beat(narration="And a second line to finish.", visual_query="ocean waves shore"),
    ]
    chosen = {
        **producer.default_settings(),
        "clip_scenes": "on",
        "music": "calm",
        "sfx": "on",
        "ending": "loop",
    }

    class ClipProvider:
        name = "FakeClips"

        def search(self, ctx: Any, q: str, max_results: int = 5) -> list[Any]:
            return []

        def search_videos(self, ctx: Any, q: str, max_results: int = 3) -> list[AssetCandidate]:
            return [
                AssetCandidate(
                    provider="FakeClips",
                    title="clip",
                    page_url="p",
                    file_url="f",
                    license="Pexels License",
                    attribution="Video by X on Pexels",
                    mime="video/mp4",
                )
            ]

        def fetch(self, ctx: Any, cand: AssetCandidate, dest: Path) -> Any:
            from aimz.domain.models import AssetRecord

            return AssetRecord(
                id="asset_c",
                provider="FakeClips",
                kind="video",
                title="clip",
                file_path=str(clip),
                attribution=cand.attribution,
            )

    svc.assets = [ClipProvider()]
    svc.db.execute("PRAGMA foreign_keys=OFF")
    svc.db.insert(
        "assets",
        {
            "id": "asset_c",
            "provider": "FakeClips",
            "kind": "video",
            "title": "clip",
            "file_path": str(clip),
            "created_at": "2026-09-30T00:00:00+00:00",
        },
    )
    out = svc.env.data_dir / "stage5"
    scenes, attributions, _, total = producer.assemble(
        svc.ctx(), beats, chosen, out, "vid", "Title", "AI narration"
    )
    assert scenes[0].clip_path == str(clip) and "Video by X on Pexels" in attributions
    assert scenes[-1].narration == "" and scenes[-1].image_path == scenes[0].image_path  # loop tail
    tl = Timeline(
        video_id="vid",
        title="t",
        scenes=scenes,
        total_duration_s=total,
        caption_style=caption_style(chosen),
        **audio_bed(chosen, scenes),
    )
    assert tl.music_path and tl.sfx_times
    res = renderer.render(svc.ctx(), tl, out)
    check = check_render(
        renderer.ffmpeg, Path(res.video_path), Path(res.captions_path), res.duration_s, total
    )
    assert check.ok, check.problems
    probe = subprocess.run([renderer.ffmpeg, "-i", res.video_path], capture_output=True, text=True).stderr
    assert "Audio: aac" in probe
