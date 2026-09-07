"""WikimediaAssetProvider: search Wikimedia Commons for reusable images. Cost: $0.

Uses the public MediaWiki API (``action=query&generator=search`` + ``prop=imageinfo`` with
``extmetadata``). Only files whose license short-name is in the allow-list (public domain,
CC0, CC BY, CC BY-SA) are returned, and attribution is recorded for every download.
Wikimedia asks API clients to send a descriptive User-Agent; we do.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from aimz.domain.models import AssetCandidate, AssetRecord
from aimz.providers.base import AssetProvider, HealthStatus, ProviderContext
from aimz.util import new_id, sha256_file, slugify

log = logging.getLogger("aimz.assets.wikimedia")

API = "https://commons.wikimedia.org/w/api.php"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _TAG_RE.sub("", s or "").strip()


def license_allowed(short_name: str, allowed: list[str]) -> bool:
    """True only for licenses that permit reuse with attribution (no NC/ND, no fair use)."""
    s = (short_name or "").lower().replace("-", " ").replace("_", " ").strip()
    if not s:
        return False
    tokens = set(s.split())
    if tokens & {"nc", "nd"} or "fair" in s or "copyright" in s:
        return False
    for a in allowed:
        key = a.lower().replace("-", " ")
        if key in {"pd", "public domain"} and ("public domain" in s or s.startswith("pd")):
            return True
        if key == "cc0" and "cc0" in s:
            return True
        if key == "cc by sa" and s.startswith("cc by sa"):
            return True
        if key == "cc by" and s.startswith("cc by") and not s.startswith("cc by sa"):
            return True
    return False


class WikimediaAssetProvider(AssetProvider):
    name = "WikimediaAssetProvider"
    is_paid = False

    def __init__(self, user_agent: str, allowed_licenses: list[str] | None = None, thumb_width: int = 1280):
        self.user_agent = user_agent
        self.allowed = allowed_licenses or ["pd", "cc0", "cc-by", "cc-by-sa", "public domain"]
        self.thumb_width = thumb_width
        self._client = httpx.Client(headers={"User-Agent": user_agent}, timeout=30, follow_redirects=True)

    def health(self) -> HealthStatus:
        try:
            r = self._client.get(
                API, params={"action": "query", "meta": "siteinfo", "format": "json"}, timeout=10
            )
            r.raise_for_status()
            return HealthStatus(True, "Wikimedia Commons API reachable")
        except Exception as exc:
            return HealthStatus(
                False,
                f"Wikimedia Commons unreachable: {exc}",
                "check network; provider degrades to generated cards",
            )

    def search(self, ctx: ProviderContext, query: str, max_results: int = 5) -> list[AssetCandidate]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": "6",
            "gsrlimit": str(max(5, max_results * 3)),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|size|mime",
            "iiurlwidth": str(self.thumb_width),
        }
        with self.authorized(ctx, "asset_search"):
            try:
                r = self._client.get(API, params=params)
                r.raise_for_status()
                pages = (r.json().get("query", {}) or {}).get("pages", {}) or {}
            except Exception as exc:
                log.warning("commons search failed for %r: %s", query, exc)
                return []
        out: list[AssetCandidate] = []
        for page in pages.values():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            meta = info.get("extmetadata", {}) or {}
            lic = _strip_html((meta.get("LicenseShortName", {}) or {}).get("value", ""))
            if not license_allowed(lic, self.allowed):
                continue
            mime = info.get("mime", "")
            if mime not in {"image/jpeg", "image/png"}:
                continue
            width = int(info.get("width", 0) or 0)
            height = int(info.get("height", 0) or 0)
            if width < 600 or height < 400:
                continue
            author = _strip_html((meta.get("Artist", {}) or {}).get("value", ""))[:120]
            credit = _strip_html((meta.get("Credit", {}) or {}).get("value", ""))[:120]
            title = page.get("title", "").replace("File:", "")
            attribution = (
                f"{title} — {author or credit or 'Wikimedia Commons'} — {lic} — via Wikimedia Commons"
            )
            out.append(
                AssetCandidate(
                    provider=self.name,
                    title=title,
                    page_url=info.get("descriptionurl", ""),
                    file_url=info.get("thumburl") or info.get("url", ""),
                    license=lic,
                    license_url=_strip_html((meta.get("LicenseUrl", {}) or {}).get("value", "")),
                    author=author,
                    attribution=attribution,
                    width=width,
                    height=height,
                    mime=mime,
                )
            )
            if len(out) >= max_results:
                break
        return out

    def fetch(self, ctx: ProviderContext, candidate: AssetCandidate, dest_dir: Path) -> AssetRecord | None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        ext = ".png" if candidate.mime == "image/png" else ".jpg"
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
                return None
        try:
            from PIL import Image

            with Image.open(dest) as im:
                im.verify()
            with Image.open(dest) as im2:
                width, height = im2.size
        except Exception:
            dest.unlink(missing_ok=True)
            return None
        rec = AssetRecord(
            id=new_id("asset"),
            provider=self.name,
            kind="image",
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
                "created_at": __import__("aimz.util", fromlist=["now_iso"]).now_iso(),
            },
        )
        return rec
