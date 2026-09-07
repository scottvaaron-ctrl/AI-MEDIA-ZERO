"""Shared package writer: everything an owner needs to complete a platform's posting flow by hand."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from aimz.util import now_iso


def write_common_package(
    package_dir: Path, video: dict[str, Any], script: dict[str, Any], metadata: dict[str, Any]
) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    src = Path(video["file_path"])
    dst = package_dir / "video.mp4"
    if src.exists() and (not dst.exists() or src.stat().st_size != dst.stat().st_size):
        shutil.copyfile(src, dst)
    if video.get("thumbnail_path") and Path(video["thumbnail_path"]).exists():
        shutil.copyfile(video["thumbnail_path"], package_dir / "thumbnail.png")
    if video.get("captions_path") and Path(video["captions_path"]).exists():
        shutil.copyfile(video["captions_path"], package_dir / "captions.srt")
    (package_dir / "sources.json").write_text(
        json.dumps(metadata.get("sources", []), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (package_dir / "attributions.txt").write_text(
        "\n".join(metadata.get("attributions", [])) + "\n", encoding="utf-8"
    )
    (package_dir / "ai_disclosure.txt").write_text(metadata.get("ai_disclosure", "") + "\n", encoding="utf-8")
    (package_dir / "metadata.json").write_text(
        json.dumps(
            {
                "video_id": video["id"],
                "script_id": script["id"],
                "title": metadata.get("title", video["title"]),
                "description": metadata.get("description", ""),
                "tags": metadata.get("tags", []),
                "duration_s": video.get("duration_s"),
                "resolution": video.get("resolution"),
                "content_family": metadata.get("content_family"),
                "hook_type": metadata.get("hook_type"),
                "experiment": metadata.get("experiment"),
                "ai_disclosure": metadata.get("ai_disclosure", ""),
                "generated_at": now_iso(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
