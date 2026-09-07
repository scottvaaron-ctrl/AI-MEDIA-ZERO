"""OwnerLibraryAssetProvider: images the owner dropped into ``assets/owner``. Cost: $0.

Filenames are matched against query words. Licensing is the owner's responsibility;
records are tagged ``owner-provided``. An optional ``credits.txt`` (``filename | attribution``)
supplies attribution lines.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from aimz.domain.models import AssetCandidate, AssetRecord
from aimz.providers.base import AssetProvider, HealthStatus, ProviderContext
from aimz.util import new_id, now_iso, sha256_file

_EXT = {".jpg", ".jpeg", ".png"}


class OwnerLibraryAssetProvider(AssetProvider):
    name = "OwnerLibraryAssetProvider"
    is_paid = False

    def __init__(self, library_dir: Path):
        self.library_dir = Path(library_dir)

    def health(self) -> HealthStatus:
        if not self.library_dir.exists():
            return HealthStatus(True, f"owner library empty ({self.library_dir} missing) - optional")
        n = sum(1 for p in self.library_dir.rglob("*") if p.suffix.lower() in _EXT)
        return HealthStatus(True, f"owner library: {n} images")

    def _credits(self) -> dict[str, str]:
        path = self.library_dir / "credits.txt"
        out: dict[str, str] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if "|" in line:
                    k, v = line.split("|", 1)
                    out[k.strip().lower()] = v.strip()
        return out

    def search(self, ctx: ProviderContext, query: str, max_results: int = 5) -> list[AssetCandidate]:
        if not self.library_dir.exists():
            return []
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
        credits = self._credits()
        scored: list[tuple[int, Path]] = []
        with self.authorized(ctx, "asset_search"):
            for p in self.library_dir.rglob("*"):
                if p.suffix.lower() not in _EXT:
                    continue
                name = p.stem.lower().replace("_", " ").replace("-", " ")
                score = sum(1 for w in words if w in name)
                if score:
                    scored.append((score, p))
        scored.sort(key=lambda t: -t[0])
        return [
            AssetCandidate(
                provider=self.name,
                title=p.stem,
                page_url="",
                file_url=str(p),
                license="owner-provided",
                attribution=credits.get(p.name.lower(), f"{p.stem} (owner library)"),
                mime="image/png" if p.suffix.lower() == ".png" else "image/jpeg",
            )
            for _, p in scored[:max_results]
        ]

    def fetch(self, ctx: ProviderContext, candidate: AssetCandidate, dest_dir: Path) -> AssetRecord | None:
        src = Path(candidate.file_url)
        if not src.exists():
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        with self.authorized(ctx, "asset_fetch"):
            if src.resolve() != dest.resolve():
                shutil.copyfile(src, dest)
        try:
            from PIL import Image

            with Image.open(dest) as im:
                width, height = im.size
        except Exception:
            return None
        rec = AssetRecord(
            id=new_id("asset"),
            provider=self.name,
            kind="image",
            title=candidate.title,
            file_path=str(dest),
            source_url=str(src),
            license=candidate.license,
            attribution=candidate.attribution,
            width=width,
            height=height,
        )
        ctx.db.insert(
            "assets",
            {
                "id": rec.id,
                "provider": rec.provider,
                "kind": "image",
                "title": rec.title,
                "file_path": rec.file_path,
                "source_url": rec.source_url,
                "page_url": "",
                "license": rec.license,
                "license_url": "",
                "attribution": rec.attribution,
                "author": "owner",
                "width": width,
                "height": height,
                "sha256": sha256_file(rec.file_path),
                "query": None,
                "created_at": now_iso(),
            },
        )
        return rec
