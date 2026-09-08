"""Caption fragments shared by publishers so a second platform does not fork the logic."""

from __future__ import annotations

import re
import unicodedata

_ZWJ = chr(0x200D)  # zero-width joiner
_VARIATION_SELECTORS = {chr(0xFE0F), chr(0xFE0E)}  # emoji / text presentation selectors
_REGIONAL = range(0x1F1E6, 0x1F200)


def hashtags(tags: list[str], limit: int = 5) -> list[str]:
    """``["ancient history", "bugs"]`` -> ``["#AncientHistory", "#Bugs"]`` (deduped, order kept)."""
    out: list[str] = []
    for tag in tags:
        word = "".join(part.capitalize() for part in re.split(r"[\s_\-]+", tag.strip()) if part)
        word = re.sub(r"[^0-9A-Za-z]", "", word)
        if not word or word[0].isdigit():  # a purely numeric tag is not a usable hashtag
            continue
        candidate = "#" + word
        if candidate not in out:
            out.append(candidate)
        if len(out) >= limit:
            break
    return out


def grapheme_clusters(text: str) -> list[str]:
    """Split into approximate Unicode grapheme clusters.

    Bluesky counts *graphemes*, not code points, so an emoji or an accented letter written as
    base + combining mark must count as one. This is a pragmatic approximation of UAX #29 that
    covers what actually shows up in captions: CRLF, combining marks, variation selectors,
    ZWJ sequences (family/profession emoji) and regional-indicator flag pairs.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        start = i
        ch = text[i]
        i += 1
        if ch == "\r" and i < n and text[i] == "\n":
            i += 1
            out.append(text[start:i])
            continue
        if ord(ch) in _REGIONAL and i < n and ord(text[i]) in _REGIONAL:
            i += 1
            out.append(text[start:i])
            continue
        while i < n:
            nxt = text[i]
            if nxt in _VARIATION_SELECTORS or unicodedata.category(nxt) in {"Mn", "Mc", "Me"}:
                i += 1
                continue
            if nxt == _ZWJ and i + 1 < n:
                i += 2
                continue
            break
        out.append(text[start:i])
    return out


def grapheme_len(text: str) -> int:
    return len(grapheme_clusters(text))


def trim_graphemes(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` graphemes without splitting a cluster."""
    clusters = grapheme_clusters(text)
    if len(clusters) <= limit:
        return text
    return "".join(clusters[:limit])
