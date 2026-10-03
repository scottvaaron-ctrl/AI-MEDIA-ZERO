"""Niche labels: the AI names niches freely, and may later merge two labels it considers the same.

A merge is recorded as an alias (``niche_aliases``), never by rewriting published history: reads map an
alias to its canonical label, and ideas not yet published are relabelled so the editor sees one niche.
"""

from __future__ import annotations

from typing import Any

from aimz.db import Database
from aimz.util import now_iso

LIVE_IDEA_STATUSES = ("candidate", "selected", "scripted", "produced")


def norm_label(label: str) -> str:
    return label.strip().lower().replace(" ", "_").replace("-", "_")


def aliases(db: Database) -> dict[str, str]:
    return {
        str(r["alias"]): str(r["canonical"]) for r in db.query("SELECT alias, canonical FROM niche_aliases")
    }


def canonical(label: str | None, alias_map: dict[str, str]) -> str | None:
    if not label:
        return label
    seen: set[str] = set()
    while label in alias_map and label not in seen:  # chains are flattened on write; this guards loops
        seen.add(label)
        label = alias_map[label]
    return label


def merge(db: Database, alias: str, into: str, reason: str = "", by: str = "ai") -> str | None:
    """Record ``alias`` as the same niche as ``into``. Returns a rejection reason, or None when merged."""
    alias, into = norm_label(alias), norm_label(into)
    amap = aliases(db)
    into = canonical(into, amap) or into
    if not alias or not into:
        return "both niche labels are required"
    if alias == into:
        return f"{alias!r} is already {into!r}"
    known = {
        str(r["content_family"])
        for r in db.query("SELECT DISTINCT content_family FROM ideas WHERE content_family IS NOT NULL")
    }
    if alias not in known and alias not in amap:
        return f"no idea was ever labelled {alias!r}"
    now = now_iso()
    db.execute(
        "INSERT INTO niche_aliases (alias, canonical, reason, created_by, created_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(alias) DO UPDATE SET canonical=excluded.canonical, reason=excluded.reason, "
        "created_by=excluded.created_by, created_at=excluded.created_at",
        [alias, into, reason[:300], by, now],
    )
    # Flatten: anything that pointed at the alias now points at the canonical label.
    db.execute("UPDATE niche_aliases SET canonical=? WHERE canonical=?", [into, alias])
    placeholders = ",".join("?" for _ in LIVE_IDEA_STATUSES)
    db.execute(
        f"UPDATE ideas SET content_family=?, updated_at=? WHERE content_family=? AND status IN ({placeholders})",
        [into, now, alias, *LIVE_IDEA_STATUSES],
    )
    return None


def canonicalize_rows(rows: list[dict[str, Any]], alias_map: dict[str, str]) -> None:
    """Map ``content_family`` to its canonical niche in place, keeping the original as ``niche_label``."""
    for r in rows:
        r["niche_label"] = r.get("content_family")
        r["content_family"] = canonical(r.get("content_family"), alias_map)
