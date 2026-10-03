"""Pexels and Pixabay: free stock photos and video clips through their official APIs. Cost: $0 (free keys).

Written 2026-09-30 from each API's documentation and Pexels' official client; **not yet verified against
the live APIs** (no keys yet). Verify before relying on them (see docs/PLAN_FULL_CONTROL.md stage 5).

* Pexels: ``GET https://api.pexels.com/v1/search`` (photos) and ``GET https://api.pexels.com/videos/search``
  (videos), header ``Authorization: <key>``. 200 requests/hour, 20,000/month. Guidelines: show a
  prominent link to Pexels and credit the photographer ("Photo by X on Pexels").
* Pixabay: ``GET https://pixabay.com/api/`` and ``GET https://pixabay.com/api/videos/`` with ``key=``.
  100 requests/60 s. Terms: cache requests for 24 h; no permanent hotlinking (files are downloaded);
  show where the content comes from.

Both licences (Pexels License, Pixabay Content License) allow free commercial use. They are *not* in the
constitution's original list (PD / CC0 / CC BY / CC BY-SA), so these providers are only registered when
the owner has amended the constitution and set ``assets.stock.enabled: true`` and the API keys.
Every asset records its licence, page URL and author, and credits appear in the video description.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from aimz.domain.models import AssetCandidate, AssetRecord
from aimz.providers.base import AssetProvider, HealthStatus, ProviderContext
from aimz.util import new_id, now_iso, sha256_file, slugify

log = logging.getLogger("aimz.assets.stock")

PEXELS_PHOTOS = "https://api.pexels.com/v1/search"
PEXELS_VIDEOS = "https://api.pexels.com/videos/search"
PIXABAY_PHOTOS = "https://pixabay.com/api/"
PIXABAY_VIDEOS = "https://pixabay.com/api/videos/"
CACHE_SECONDS = 24 * 3600  # Pixabay requires 24 h caching; used for both


class StockAssetProvider(AssetProvider):
    """Shared download, caching and bookkeeping. Subclasses parse their API's search results."""

    is_paid = False
    license_name = ""
    license_url = ""
    photo_url = ""
    video_url = ""

    def __init__(self, api_key: str, user_agent: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent}, timeout=30, follow_redirects=True
        )
        self._cache: dict[tuple[str, str], tuple[float, list[AssetCandidate]]] = {}

    # -- API ------------------------------------------------------------------------------------
    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _photos(self, data: dict[str, Any]) -> list[AssetCandidate]:
        raise NotImplementedError

    def _videos(self, data: dict[str, Any]) -> list[AssetCandidate]:
        raise NotImplementedError

    def _params(self, query: str, kind: str, n: int) -> dict[str, Any]:
        raise NotImplementedError

    def health(self) -> HealthStatus:
        if not self.api_key:
            return HealthStatus(False, f"{self.name}: no API key", "owner: add the key to .env")
        return HealthStatus(True, f"{self.name}: key present (not called by health checks)")

    def _search(self, ctx: ProviderContext, query: str, kind: str, max_results: int) -> list[AssetCandidate]:
        key = (kind, query.strip().lower())
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1][:max_results]
        url = self.photo_url if kind == "image" else self.video_url
        with self.authorized(ctx, f"{kind}_search"):
            try:
                data = self._get(url, self._params(query, kind, max(5, max_results * 2)))
            except Exception as exc:
                log.warning("%s %s search failed for %r: %s", self.name, kind, query, exc)
                return []
        found = self._photos(data) if kind == "image" else self._videos(data)
        self._cache[key] = (time.time(), found)
        return found[:max_results]

    def search(self, ctx: ProviderContext, query: str, max_results: int = 5) -> list[AssetCandidate]:
        return self._search(ctx, query, "image", max_results)

    def search_videos(self, ctx: ProviderContext, query: str, max_results: int = 3) -> list[AssetCandidate]:
        return self._search(ctx, query, "video", max_results)

    # -- download ---------------------------------------------------------------------------------
    def fetch(self, ctx: ProviderContext, candidate: AssetCandidate, dest_dir: Path) -> AssetRecord | None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        video = candidate.mime.startswith("video/")
        ext = ".mp4" if video else ".jpg"
        dest = dest_dir / f"{slugify(candidate.title, 40)}_{new_id('a')[-8:]}{ext}"
        with self.authorized(ctx, "asset_fetch"):
            try:
                with self._client.stream("GET", candidate.file_url) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in r.iter_bytes(1 << 16):
                            fh.write(chunk)
            except Exception as exc:
                log.warning("download failed %s: %s", candidate.file_url, exc)
                dest.unlink(missing_ok=True)
                return None
        width, height = candidate.width, candidate.height
        if not video:
            try:
                from PIL import Image

                with Image.open(dest) as im:
                    im.verify()
                with Image.open(dest) as im2:
                    width, height = im2.size
            except Exception:
                dest.unlink(missing_ok=True)
                return None
        elif dest.stat().st_size < 10_000:
            dest.unlink(missing_ok=True)
            return None
        rec = AssetRecord(
            id=new_id("asset"),
            provider=self.name,
            kind="video" if video else "image",
            title=candidate.title,
            file_path=str(dest),
            page_url=candidate.page_url,
            source_url=candidate.file_url,
            license=candidate.license,
            license_url=candidate.license_url,
            attribution=candidate.attribution,
            author=candidate.author,
            width=width,
            height=height,
        )
        ctx.db.insert(
            "assets",
            {
                "id": rec.id,
                "provider": rec.provider,
                "kind": rec.kind,
                "title": rec.title,
                "file_path": rec.file_path,
                "source_url": rec.source_url,
                "page_url": rec.page_url,
                "license": rec.license,
                "license_url": rec.license_url,
                "attribution": rec.attribution,
                "author": rec.author,
                "width": rec.width,
                "height": rec.height,
                "sha256": sha256_file(rec.file_path),
                "query": None,
                "created_at": now_iso(),
            },
        )
        return rec


def _pick_video_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The smallest file at least 720 px on its short side (enough for 1080x1920 after scaling), else the largest."""
    usable = [f for f in files if f.get("link") or f.get("url")]
    if not usable:
        return None
    good = [f for f in usable if min(int(f.get("width") or 0), int(f.get("height") or 0)) >= 720]
    if good:
        return min(good, key=lambda f: int(f.get("width") or 0) * int(f.get("height") or 0))
    return max(usable, key=lambda f: int(f.get("width") or 0) * int(f.get("height") or 0))


class PexelsAssetProvider(StockAssetProvider):
    name = "PexelsAssetProvider"
    license_name = "Pexels License"
    license_url = "https://www.pexels.com/license/"
    photo_url = PEXELS_PHOTOS
    video_url = PEXELS_VIDEOS

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        r = self._client.get(url, params=params, headers={"Authorization": self.api_key})
        r.raise_for_status()
        return dict(r.json())

    def _params(self, query: str, kind: str, n: int) -> dict[str, Any]:
        return {"query": query, "orientation": "portrait", "per_page": min(80, n)}

    def _photos(self, data: dict[str, Any]) -> list[AssetCandidate]:
        out = []
        for p in data.get("photos") or []:
            src = p.get("src") or {}
            author = str(p.get("photographer") or "")
            out.append(
                AssetCandidate(
                    provider=self.name,
                    title=str(p.get("alt") or f"Pexels photo {p.get('id')}")[:120],
                    page_url=str(p.get("url") or ""),
                    file_url=str(src.get("large2x") or src.get("portrait") or src.get("original") or ""),
                    license=self.license_name,
                    license_url=self.license_url,
                    author=author,
                    attribution=f"Photo by {author or 'unknown'} on Pexels ({p.get('url', '')})",
                    width=int(p.get("width") or 0),
                    height=int(p.get("height") or 0),
                    mime="image/jpeg",
                )
            )
        return [c for c in out if c.file_url]

    def _videos(self, data: dict[str, Any]) -> list[AssetCandidate]:
        out = []
        for v in data.get("videos") or []:
            f = _pick_video_file(list(v.get("video_files") or []))
            if not f:
                continue
            author = str((v.get("user") or {}).get("name") or "")
            out.append(
                AssetCandidate(
                    provider=self.name,
                    title=f"Pexels video {v.get('id')}",
                    page_url=str(v.get("url") or ""),
                    file_url=str(f.get("link") or ""),
                    license=self.license_name,
                    license_url=self.license_url,
                    author=author,
                    attribution=f"Video by {author or 'unknown'} on Pexels ({v.get('url', '')})",
                    width=int(f.get("width") or 0),
                    height=int(f.get("height") or 0),
                    mime="video/mp4",
                )
            )
        return out


class PixabayAssetProvider(StockAssetProvider):
    name = "PixabayAssetProvider"
    license_name = "Pixabay Content License"
    license_url = "https://pixabay.com/service/license-summary/"
    photo_url = PIXABAY_PHOTOS
    video_url = PIXABAY_VIDEOS

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        r = self._client.get(url, params={"key": self.api_key, **params})
        r.raise_for_status()
        return dict(r.json())

    def _params(self, query: str, kind: str, n: int) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query[:100], "safesearch": "true", "per_page": max(3, min(200, n))}
        if kind == "image":
            params.update(image_type="photo", orientation="vertical")
        return params

    def _photos(self, data: dict[str, Any]) -> list[AssetCandidate]:
        out = []
        for h in data.get("hits") or []:
            author = str(h.get("user") or "")
            out.append(
                AssetCandidate(
                    provider=self.name,
                    title=str(h.get("tags") or f"Pixabay image {h.get('id')}")[:120],
                    page_url=str(h.get("pageURL") or ""),
                    file_url=str(h.get("largeImageURL") or h.get("webformatURL") or ""),
                    license=self.license_name,
                    license_url=self.license_url,
                    author=author,
                    attribution=f"Image by {author or 'unknown'} from Pixabay ({h.get('pageURL', '')})",
                    width=int(h.get("imageWidth") or 0),
                    height=int(h.get("imageHeight") or 0),
                    mime="image/jpeg",
                )
            )
        return [c for c in out if c.file_url]

    def _videos(self, data: dict[str, Any]) -> list[AssetCandidate]:
        out = []
        for h in data.get("hits") or []:
            sizes = h.get("videos") or {}
            f = _pick_video_file([sizes[k] for k in ("large", "medium", "small", "tiny") if sizes.get(k)])
            if not f:
                continue
            author = str(h.get("user") or "")
            out.append(
                AssetCandidate(
                    provider=self.name,
                    title=str(h.get("tags") or f"Pixabay video {h.get('id')}")[:120],
                    page_url=str(h.get("pageURL") or ""),
                    file_url=str(f.get("url") or ""),
                    license=self.license_name,
                    license_url=self.license_url,
                    author=author,
                    attribution=f"Video by {author or 'unknown'} from Pixabay ({h.get('pageURL', '')})",
                    width=int(f.get("width") or 0),
                    height=int(f.get("height") or 0),
                    mime="video/mp4",
                )
            )
        return out
