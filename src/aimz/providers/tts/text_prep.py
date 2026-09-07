"""Narration text preparation: pronunciation overrides and sentence chunking for caption timing."""

from __future__ import annotations

import re

_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def apply_pronunciations(text: str, overrides: dict[str, str]) -> str:
    out = text
    for src, dst in sorted(overrides.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"(?<![A-Za-z0-9]){re.escape(src)}(?![A-Za-z0-9])", dst, out)
    return out


def split_sentences(text: str, max_words: int = 14) -> list[str]:
    """Split narration into caption-sized chunks (sentence first, then by commas/length)."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    for sent in _SENT_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent.split()) <= max_words:
            chunks.append(sent)
            continue
        # split long sentences at commas / semicolons / dashes
        parts = re.split(r"(?<=[,;:])\s+|\s+[-—]\s+", sent)
        buf: list[str] = []
        for part in parts:
            if len(" ".join([*buf, part]).split()) > max_words and buf:
                chunks.append(" ".join(buf))
                buf = [part]
            else:
                buf.append(part)
        if buf:
            chunks.append(" ".join(buf))
    # final safety: hard-wrap any remaining long chunk
    final: list[str] = []
    for c in chunks:
        w = c.split()
        while len(w) > max_words * 2:
            final.append(" ".join(w[:max_words]))
            w = w[max_words:]
        final.append(" ".join(w))
    return [c for c in final if c]


def clean_for_tts(text: str) -> str:
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\[[^\]]*\]", "", text)  # remove bracketed notes
    text = re.sub(r"[*_#`>]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text
