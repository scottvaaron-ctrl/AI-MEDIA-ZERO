"""Image credits travel with every platform (constitution section 5: CC BY / CC BY-SA attribution wherever shared).

Credits are no longer drawn on the video frames, so the YouTube description, the TikTok caption and the
Bluesky reply thread are the only places they appear.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from aimz.providers.publishers.tiktok import TIKTOK_CAPTION_LIMIT, build_caption, utf16_len
from aimz.util import dumps

CREDITS = [
    "Huhu beetle by A. Person, CC BY-SA 4.0 (https://commons.wikimedia.org/wiki/File:Huhu.jpg)",
    "Photo by B. Shooter on Pexels (https://www.pexels.com/photo/123)",
    "Kauri forest by C. Walker, CC BY 2.0 (https://commons.wikimedia.org/wiki/File:Kauri.jpg)",
]


# ---- TikTok caption ----------------------------------------------------------------------------------------


def test_tiktok_caption_lists_every_credit() -> None:
    cap = build_caption("Islands of New Zealand: the huhu beetle", ["natural history"], credits=CREDITS)
    assert "Image credits:" in cap
    for credit in CREDITS:
        assert credit in cap
    assert cap.startswith("Islands of New Zealand")
    assert "AI-narrated" in cap and "#NaturalHistory" in cap
    assert utf16_len(cap) <= TIKTOK_CAPTION_LIMIT


def test_tiktok_caption_without_credits_is_unchanged() -> None:
    cap = build_caption("Huhu beetle", ["history"])
    assert cap == "Huhu beetle\n\nAI-narrated, sourced explainer. Sources in comments/bio.\n#History"
    assert build_caption("Huhu beetle", ["history"], credits=[]) == cap


def test_tiktok_long_title_is_trimmed_before_any_credit() -> None:
    cap = build_caption("T" * 3000, ["history"], credits=CREDITS)
    assert utf16_len(cap) <= TIKTOK_CAPTION_LIMIT
    for credit in CREDITS:
        assert credit in cap
    assert "AI-narrated" in cap  # the title absorbs the cut, the disclosure line stays


def test_tiktok_credits_alone_over_the_limit_keep_full_lines_and_point_to_youtube(
    caplog: pytest.LogCaptureFixture,
) -> None:
    many = [
        f"Picture {i} by Someone Long-Named, CC BY-SA 4.0 (https://example.org/{'x' * 60}/{i})"
        for i in range(40)
    ]
    with caplog.at_level(logging.WARNING):
        cap = build_caption("Huhu beetle", ["history"], credits=many)
    assert utf16_len(cap) <= TIKTOK_CAPTION_LIMIT
    assert cap.startswith("Image credits:\n")
    assert cap.endswith("\nFull credits: see YouTube description")
    body = cap.split("\n")[1:-1]
    assert body and all(line in many for line in body)  # only whole credit lines, in order
    assert body == many[: len(body)]
    assert len(body) < len(many)
    assert any(
        "credits" in r.getMessage() and "YouTube description" in r.getMessage() for r in caplog.records
    )


def test_tiktok_limit_counts_utf16_units() -> None:
    emoji_title = "\U0001f41b" * 1500  # 3000 UTF-16 units, 1500 code points
    cap = build_caption(emoji_title, [], credits=CREDITS)
    assert utf16_len(cap) <= TIKTOK_CAPTION_LIMIT
    for credit in CREDITS:
        assert credit in cap


def test_tiktok_package_caption_carries_credits(svc) -> None:  # noqa: ANN001
    from aimz.providers.publishers.tiktok import TikTokPackagePublisher
    from aimz.util import new_id, now_iso

    mp4 = svc.env.data_dir / "pkg.mp4"
    mp4.write_bytes(b"\x00" * 16)
    video = {"id": new_id("vid"), "title": "Huhu", "file_path": str(mp4), "created_at": now_iso()}
    meta = {
        "title": "Huhu",
        "tags": ["history"],
        "sources": [],
        "attributions": CREDITS,
        "ai_disclosure": "AI",
    }
    pkg = svc.env.data_dir / "pkg"
    TikTokPackagePublisher().publish(svc.ctx(), video, {"id": new_id("scr")}, meta, pkg)
    caption = (pkg / "caption.txt").read_text(encoding="utf-8")
    for credit in CREDITS:
        assert credit in caption


# ---- YouTube description ------------------------------------------------------------------------------------


def _metadata(svc: Any, sources: list[dict[str, str]], attributions: list[str]) -> dict[str, Any]:
    from aimz.agents.publisher import PublishStage

    video = {"timeline_json": dumps({"sources": sources, "attributions": attributions})}
    script = {"title": "Huhu beetle", "description": "The huhu beetle is New Zealand's largest beetle."}
    return PublishStage(svc, None).build_metadata(video, script, {}, owner_approved=True)


def test_youtube_description_keeps_credits_when_sources_are_very_long(svc) -> None:  # noqa: ANN001
    sources = [
        {"title": f"Source number {i} " + "w" * 80, "url": f"https://example.org/{i}"} for i in range(200)
    ]
    meta = _metadata(svc, sources, CREDITS)
    desc = meta["description"]
    assert len(desc) <= 4900
    assert "Image credits:" in desc
    for credit in CREDITS:
        assert f"- {credit}" in desc
    assert desc.index("Image credits:") < desc.index("Sources:")


def test_youtube_description_without_credits_has_no_credit_heading(svc) -> None:  # noqa: ANN001
    meta = _metadata(svc, [{"title": "Te Papa", "url": "https://example.org/huhu"}], [])
    assert "Image credits" not in meta["description"]
    assert "Sources:\n- Te Papa: https://example.org/huhu" in meta["description"]
