"""RSSResearchProvider: RSS/Atom feeds via feedparser (Wikipedia featured feeds, Reddit RSS, news). Cost: $0."""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import UTC, datetime
from time import mktime
from typing import Any
from urllib.parse import unquote

import feedparser
import httpx

from aimz.core.retry import retry
from aimz.domain.models import FetchedItem
from aimz.providers.base import HealthStatus, ProviderContext, ResearchProvider

log = logging.getLogger("aimz.research.rss")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(s: str) -> str:
    s = _TAG_RE.sub(" ", s or "")
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def _entry_date(entry: Any) -> str | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(mktime(st), tz=UTC).replace(microsecond=0).isoformat()
            except (OverflowError, ValueError):
                continue
    return None


def _entry_summary(entry: Any) -> str:
    return strip_html(_entry_html(entry))[:1500]


_WIKI_HREF_RE = re.compile(r'href="(/wiki/[^"#]+)"')
_BOLD_RE = re.compile(r"<b\b[^>]*>(.*?)</b>", re.DOTALL)
_SKIP_NAMESPACES = (
    "file:",
    "image:",
    "special:",
    "help:",
    "wikipedia:",
    "portal:",
    "category:",
    "template:",
    "talk:",
    "user:",
    "module:",
    "draft:",
)
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
# Years, decades and calendar dates are links, but not what a claim can be checked against.
_DATE_LIKE_RE = re.compile(rf"^(\d{{1,4}}(s|_BC)?|(?:{_MONTHS})_\d{{1,2}})$", re.IGNORECASE)


def _is_article_path(path: str) -> bool:
    slug = unquote(path.removeprefix("/wiki/"))
    if any(slug.lower().startswith(ns) for ns in _SKIP_NAMESPACES):
        return False
    return not _DATE_LIKE_RE.match(slug)


def wiki_article_url(fragment: str, fallback: str = "") -> str:
    """The best article link inside a Wikipedia feed fragment.

    Wikipedia bolds the subject of the day, so a bolded link wins outright. The naive "first link"
    is wrong on both feeds we read: in 'On this day' it is the year (``/wiki/1800``), and in
    'Featured article' it is the illustration's ``File:`` page. Neither lets a viewer check a
    claim, which is the only reason we publish sources at all.
    """
    for bold in _BOLD_RE.findall(fragment):
        for path in _WIKI_HREF_RE.findall(bold):
            if _is_article_path(path):
                return "https://en.wikipedia.org" + path
    for path in _WIKI_HREF_RE.findall(fragment):
        if _is_article_path(path):
            return "https://en.wikipedia.org" + path
    return fallback


def _entry_html(entry: Any) -> str:
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        return str(content[0]["value"])
    return str(entry.get("summary", "") or entry.get("description", ""))


def _split_wikipedia_onthisday(entry: Any) -> list[tuple[str, str]]:
    """The Wikipedia 'On this day' feed packs several events into one entry; split them into leads."""
    raw = _entry_html(entry)
    items = re.findall(r"<li>(.*?)</li>", raw, flags=re.DOTALL)
    out: list[tuple[str, str]] = []
    for li in items:
        text = strip_html(li)
        if len(text) < 30:
            continue
        out.append((text[:200], wiki_article_url(li, entry.get("link", ""))))
    return out


class RSSResearchProvider(ResearchProvider):
    name = "RSSResearchProvider"
    kinds = ("rss", "atom", "wikipedia_onthisday", "wikipedia_featured", "reddit_rss")
    is_paid = False

    def __init__(self, user_agent: str, timeout_s: int = 25, max_items: int = 40):
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self.max_items = max_items
        self._last_fetch: dict[str, float] = {}
        self.min_gap_s = 2.5  # polite spacing between hits on the same host (Reddit returns 429 otherwise)

    def health(self) -> HealthStatus:
        return HealthStatus(True, "feedparser available")

    def _download(self, url: str) -> bytes:
        host = url.split("/")[2] if "://" in url else url
        wait = self.min_gap_s - (time.monotonic() - self._last_fetch.get(host, -1e9))
        if wait > 0:
            time.sleep(wait)
        self._last_fetch[host] = time.monotonic()

        def _get() -> bytes:
            with httpx.Client(
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                },
                timeout=self.timeout_s,
                follow_redirects=True,
            ) as client:
                r = client.get(url)
                r.raise_for_status()
                return r.content

        return retry(_get, attempts=2, base_delay=1.5, label=f"fetch {url}")

    def fetch(self, ctx: ProviderContext, source: dict[str, Any]) -> list[FetchedItem]:
        url = source["url"]
        kind = source.get("kind", "rss")
        with self.authorized(ctx, "feed_fetch"):
            raw = self._download(url)
            parsed = feedparser.parse(raw)
        if parsed.get("bozo") and not parsed.get("entries"):
            raise RuntimeError(f"feed parse error: {parsed.get('bozo_exception')}")
        items: list[FetchedItem] = []
        for entry in parsed.get("entries", [])[: self.max_items]:
            link = entry.get("link") or ""
            title = strip_html(entry.get("title", ""))
            date = _entry_date(entry)
            if kind == "wikipedia_onthisday":
                for text, wiki_url in _split_wikipedia_onthisday(entry):
                    items.append(
                        FetchedItem(
                            url=wiki_url,
                            title=text[:140],
                            summary=text,
                            published_at=date,
                            raw={"feed_title": title},
                        )
                    )
                continue
            if kind == "wikipedia_featured":
                # entry.link is a Special:FeedItem permalink, useless as a citation; the article
                # itself is the bolded link in the blurb.
                link = wiki_article_url(_entry_html(entry), link)
            if not link or not title:
                continue
            summary = _entry_summary(entry)
            if kind == "reddit_rss":
                # keep the outbound article link when the post links elsewhere (feedparser puts it in summary html)
                m = re.search(r'href="(https?://(?!www\.reddit\.com)[^"]+)"', entry.get("summary", "") or "")
                raw_extra = {"reddit_permalink": link, "outbound": m.group(1) if m else None}
                items.append(
                    FetchedItem(url=link, title=title, summary=summary, published_at=date, raw=raw_extra)
                )
                continue
            items.append(
                FetchedItem(
                    url=link,
                    title=title,
                    summary=summary,
                    published_at=date,
                    raw={"author": entry.get("author", "")},
                )
            )
        return items
