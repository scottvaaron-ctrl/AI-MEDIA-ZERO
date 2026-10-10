"""Piece D, stock video: clips pass the picture-matching check; AI-generated and low-quality clips are skipped."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image
from tests.test_producer_relevance import FakeScorer, _gate, _producer

from aimz.domain.models import AssetCandidate, AssetRecord
from aimz.providers.assets.stock import PexelsAssetProvider, PixabayAssetProvider
from aimz.util import new_id, now_iso

V = {
    "width": 1280,
    "height": 720,
    "size": 1,
    "url": "https://cdn.pixabay.com/v_{id}.mp4",
    "thumbnail": "https://cdn.pixabay.com/t_{id}.jpg",
}


def _hit(i: int, **flags: Any) -> dict[str, Any]:
    tiny = {k: (v.format(id=i) if isinstance(v, str) else v) for k, v in V.items()}
    return {
        "id": i,
        "pageURL": f"https://pixabay.com/videos/id-{i}/",
        "tags": f"clip {i}",
        "user": "u",
        "videos": {"tiny": tiny},
        **flags,
    }


def test_pixabay_skips_ai_generated_and_low_quality_clips_and_keeps_thumbnails() -> None:
    p = PixabayAssetProvider("k", "ua")
    out = p._videos({"hits": [_hit(1), _hit(2, isAiGenerated=True), _hit(3, isLowQuality=True)]})
    assert [c.page_url for c in out] == ["https://pixabay.com/videos/id-1/"]
    assert out[0].preview_url == "https://cdn.pixabay.com/t_1.jpg"


def test_pexels_clips_keep_their_thumbnail() -> None:
    p = PexelsAssetProvider("k", "ua")
    data = {"videos": [{"id": 7, "url": "https://www.pexels.com/video/7/", "image": "https://images.pexels.com/7.jpg",
                        "user": {"name": "n"}, "video_files": [{"link": "https://x/7.mp4", "width": 1080, "height": 1920}]}]}  # fmt: skip
    assert p._videos(data)[0].preview_url == "https://images.pexels.com/7.jpg"


def test_a_clip_preview_is_its_thumbnail(svc) -> None:  # noqa: ANN001
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 20, 30)).save(buf, format="JPEG")
    seen: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(str(r.url))
        return httpx.Response(200, content=buf.getvalue())

    p = PixabayAssetProvider("k", "ua", client=httpx.Client(transport=httpx.MockTransport(handler)))
    cand = p._videos({"hits": [_hit(1)]})[0]
    img = p.preview(svc.ctx(), cand)
    assert img is not None and seen == ["https://cdn.pixabay.com/t_1.jpg"]


class FakeVideos:
    name = "FakeVideos"

    def __init__(self, svc: Any, titles: list[str], unscorable: set[str] | None = None) -> None:
        self.svc, self.titles, self.unscorable = svc, titles, unscorable or set()
        self.fetched: list[str] = []
        self.file = svc.env.data_dir / "clip.mp4"
        self.file.write_bytes(b"mp4")

    def search(self, ctx: Any, query: str, max_results: int = 5) -> list[AssetCandidate]:
        return []

    def search_videos(self, ctx: Any, query: str, max_results: int = 3) -> list[AssetCandidate]:
        return [
            AssetCandidate(
                provider=self.name,
                title=t,
                page_url=f"https://e/{t}",
                file_url=f"https://e/{t}.mp4",
                license="Pixabay Content License",
                attribution=f"{t} by u",
                mime="video/mp4",
            )  # fmt: skip
            for t in self.titles
        ]

    def preview(self, ctx: Any, cand: AssetCandidate, width: int = 320) -> Image.Image | None:
        if cand.title in self.unscorable:
            return None
        img = Image.new("RGB", (8, 8))
        img.info["title"] = cand.title
        return img

    def fetch(self, ctx: Any, cand: AssetCandidate, dest: Path) -> AssetRecord | None:
        self.fetched.append(cand.title)
        rid = new_id("asset")
        self.svc.db.insert("assets", {"id": rid, "provider": self.name, "kind": "video", "title": cand.title,
                                      "file_path": str(self.file), "created_at": now_iso()})  # fmt: skip
        return AssetRecord(id=rid, provider=self.name, kind="video", title=cand.title, file_path=str(self.file),
                           license=cand.license, attribution=cand.attribution)  # fmt: skip


def test_the_best_matching_clip_is_used(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    videos = FakeVideos(svc, ["deer", "lighthouse"])
    svc.assets = [videos]
    _gate(svc, monkeypatch, FakeScorer({"deer": 0.01, "lighthouse": 0.6}))
    rec = _producer(svc)._find_clip(svc.ctx("run_1"), "lighthouse coast", svc.env.data_dir, "a lighthouse")
    assert rec is not None and rec.title == "lighthouse" and videos.fetched == ["lighthouse"]


def test_no_clip_when_none_matches_or_none_can_be_scored(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    videos = FakeVideos(svc, ["deer", "cat"], unscorable={"cat"})
    svc.assets = [videos]
    _gate(svc, monkeypatch, FakeScorer({"deer": 0.01, "cat": 0.9}))
    assert _producer(svc)._find_clip(svc.ctx("run_1"), "lighthouse", svc.env.data_dir, "a lighthouse") is None
    assert videos.fetched == []


def test_without_the_check_the_first_clip_that_downloads_is_used(svc) -> None:  # noqa: ANN001
    videos = FakeVideos(svc, ["deer", "lighthouse"])
    svc.assets = [videos]
    rec = _producer(svc)._find_clip(svc.ctx("run_1"), "lighthouse", svc.env.data_dir, None)
    assert rec is not None and rec.title == "deer"
