"""Small shared utilities: ids, time, hashing, JSON helpers."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    """ISO-8601 UTC timestamp with second precision (sortable, Postgres-compatible)."""
    return utcnow().replace(microsecond=0).isoformat()


def month_key(dt: datetime | None = None) -> str:
    dt = dt or utcnow()
    return dt.strftime("%Y-%m")


def new_id(prefix: str) -> str:
    """Prefixed, time-sortable, collision-resistant id, e.g. ``idea_20260907T161200_a1b2c3d4``."""
    stamp = utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{prefix}_{stamp}_{secrets.token_hex(4)}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_title(title: str) -> str:
    """Lowercased, punctuation-stripped, stopword-light key used for near-duplicate detection."""
    t = title.lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    stop = {
        "the",
        "a",
        "an",
        "of",
        "and",
        "to",
        "in",
        "on",
        "for",
        "is",
        "was",
        "how",
        "why",
        "what",
        "this",
        "that",
    }
    words = [w for w in t.split() if w not in stop]
    return " ".join(sorted(set(words)))


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def slugify(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s[:max_len].rstrip("-") or "untitled"


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def words(text: str) -> int:
    return len(text.split())


def estimate_speech_seconds(text: str, wpm: float = 165.0) -> float:
    return max(1.0, words(text) / wpm * 60.0)
