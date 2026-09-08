"""TikTokPackagePublisher: owner-completed posting package. Cost: $0. No API calls, no browser bots.

Why a package and not Direct Post in V0 (verified Sept 2026, developers.tiktok.com):

* Unaudited API clients "can only post contents in SELF_ONLY viewership" and the account
  must be private at posting time; public posting requires a TikTok app audit.
* The Content Sharing Guidelines require the *creator* to preview the video, edit the
  caption, and manually select the privacy level from ``creator_info`` options with no default,
  and to expressly consent before bytes are sent. Those are human steps by design.
* Automating tiktok.com with a browser bot violates TikTok's terms.

So V0 emits everything needed for the owner to finish in the TikTok app: ``video.mp4``,
``caption.txt``, ``metadata.json`` (including the ``is_aigc`` recommendation), ``sources.json``,
``cover.png`` + suggested cover timestamp, ``ai_disclosure.txt`` and ``posting_notes.md``.

A future ``TikTokDirectPostPublisher`` (``/v2/post/publish/video/init/`` with ``FILE_UPLOAD``)
can implement this same interface once the owner's app is audited; the pipeline will not change.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from aimz.domain.models import PublishResult
from aimz.providers.base import HealthStatus, ProviderContext, Publisher
from aimz.providers.publishers.captions import hashtags
from aimz.providers.publishers.packages import write_common_package


def build_caption(title: str, tags: list[str], max_len: int = 2200) -> str:
    tag_line = " ".join(hashtags(tags, 5))
    cap = f"{title}\n\nAI-narrated, sourced explainer. Sources in comments/bio.\n{tag_line}".strip()
    return cap[:max_len]


class TikTokPackagePublisher(Publisher):
    name = "TikTokPackagePublisher"
    platform = "tiktok"
    is_paid = False
    performs_api_writes = False

    def __init__(self, recommend_ai_label: bool = True):
        self.recommend_ai_label = recommend_ai_label

    def health(self) -> HealthStatus:
        return HealthStatus(True, "TikTok package mode (owner completes posting in the TikTok app)")

    def publish(
        self,
        ctx: ProviderContext,
        video: dict[str, Any],
        script: dict[str, Any],
        metadata: dict[str, Any],
        package_dir: Path,
    ) -> PublishResult:
        with self.authorized(ctx, "tiktok_package"):
            write_common_package(package_dir, video, script, metadata)
            caption = build_caption(metadata.get("title", video["title"]), metadata.get("tags", []))
            (package_dir / "caption.txt").write_text(caption + "\n", encoding="utf-8")
            if video.get("thumbnail_path") and Path(video["thumbnail_path"]).exists():
                shutil.copyfile(video["thumbnail_path"], package_dir / "cover.png")
            tiktok_meta = {
                "post_info_recommendation": {
                    "title": caption,
                    "privacy_level": "CHOOSE_IN_APP (TikTok requires the creator to pick this manually; no default)",
                    "disable_duet": False,
                    "disable_stitch": False,
                    "disable_comment": False,
                    "video_cover_timestamp_ms": 500,
                    "is_aigc": bool(self.recommend_ai_label),
                    "brand_content_toggle": False,
                    "brand_organic_toggle": False,
                },
                "constraints": {
                    "max_caption_utf16": 2200,
                    "format": "mp4 h264 1080x1920 23-60fps <= 4GB",
                    "duration_limit": "creator max_video_post_duration_sec (query creator_info if using the API)",
                },
                "why_manual": "Unaudited TikTok API clients may only post SELF_ONLY to private accounts; the creator must preview, edit, select privacy, and consent.",
            }
            (package_dir / "tiktok_metadata.json").write_text(
                json.dumps(tiktok_meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (package_dir / "posting_notes.md").write_text(
                "# TikTok posting notes (owner completes in the TikTok app)\n\n"
                "1. Open TikTok, tap +, upload `video.mp4`.\n"
                "2. Paste `caption.txt` (edit freely; you must be the final editor).\n"
                "3. Choose the cover (suggested: `cover.png` or the frame at 0.5s).\n"
                "4. Turn ON the 'AI-generated content' label (narration and script are AI-assisted).\n"
                "5. Select the privacy level yourself. Nothing here sets it for you.\n"
                "6. Sources are in `sources.json`; consider pinning a comment with the main source.\n"
                "7. After posting, record views/likes/shares/comments with `aimz metric add` (TikTok exposes no watch-time API to normal apps).\n"
                f"\nRecommended posting time (local): {metadata.get('recommended_post_time', 'not set')}\n",
                encoding="utf-8",
            )
        return PublishResult(
            status="packaged",
            package_dir=str(package_dir),
            privacy="owner_selects_in_app",
            message="TikTok package written; owner completes posting in the app.",
        )
