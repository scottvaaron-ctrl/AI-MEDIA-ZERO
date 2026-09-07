"""Research / Trend agent: pulls leads from free feeds, deduplicates, scores freshness, stores them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.settings import load_feeds
from aimz.util import dumps, new_id, normalize_title, now_iso, sha256_text


def freshness(published_at: str | None, window_days: int) -> float:
    if not published_at:
        return 0.4
    try:
        dt = datetime.fromisoformat(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
    except ValueError:
        return 0.4
    age_days = max(0.0, (datetime.now(UTC) - dt).total_seconds() / 86400)
    if age_days <= 1:
        return 1.0
    if age_days >= window_days:
        return 0.15
    return 1.0 - 0.85 * (age_days / window_days)


class ResearchAgent(Agent):
    name = "research"

    def sync_sources(self) -> int:
        """Mirror feeds.yaml into the sources table (owner-controlled list)."""
        feeds = load_feeds(self.svc.env.config_dir)
        seen_ids: list[str] = []
        for f in feeds:
            sid = "src_" + sha256_text(f["url"])[:16]
            seen_ids.append(sid)
            existing = self.svc.db.get("sources", sid)
            row = {
                "id": sid,
                "name": f.get("name", f["url"]),
                "kind": f.get("kind", "rss"),
                "url": f["url"],
                "category": f.get("category"),
                "credibility": float(f.get("credibility", 0.5)),
                "enabled": 1 if f.get("enabled", True) else 0,
                "updated_at": now_iso(),
            }
            if existing:
                self.svc.db.update("sources", sid, row)
            else:
                self.svc.db.insert("sources", {**row, "created_at": now_iso()})
        # sources removed from the file are disabled, not deleted (history stays intact)
        for r in self.svc.db.query("SELECT id FROM sources WHERE kind NOT IN ('audience','manual')"):
            if r["id"] not in seen_ids:
                self.svc.db.update("sources", r["id"], {"enabled": 0, "updated_at": now_iso()})
        return len(feeds)

    def run(self, run: RunContext, max_per_source: int | None = None) -> dict[str, Any]:
        self.sync_sources()
        window = int(self.svc.config.get("pipeline.freshness_window_days", 14))
        stats = {"sources": 0, "fetched": 0, "new_items": 0, "duplicates": 0, "errors": 0}
        sources = [
            dict(r)
            for r in self.svc.db.query(
                "SELECT * FROM sources WHERE enabled=1 AND kind NOT IN ('audience','manual')"
            )
        ]
        for src in sources:
            provider = self.svc.research.get(src["kind"])
            if provider is None:
                self.svc.db.update(
                    "sources", src["id"], {"last_status": "unsupported_kind", "updated_at": now_iso()}
                )
                continue
            stats["sources"] += 1
            with self.svc.tracker.agent(
                run, self.name, f"fetch:{src['name']}", {"source_id": src["id"]}
            ) as span:
                try:
                    items = provider.fetch(self.pctx(run, span=span), src)
                except Exception as exc:
                    stats["errors"] += 1
                    self.svc.db.update(
                        "sources",
                        src["id"],
                        {
                            "last_fetched_at": now_iso(),
                            "last_status": "error",
                            "last_error": str(exc)[:500],
                            "updated_at": now_iso(),
                        },
                    )
                    self.log.warning("feed %s failed: %s", src["name"], exc)
                    self.svc.tracker.record_error(run.id, self.name, f"fetch:{src['name']}", exc)
                    continue
                new_here = 0
                for item in items[: max_per_source or 60]:
                    stats["fetched"] += 1
                    url_hash = sha256_text(item.url.strip().lower())
                    dedupe_key = normalize_title(item.title)
                    if self.svc.db.one("SELECT 1 FROM source_items WHERE url_hash=?", [url_hash]) or (
                        dedupe_key
                        and self.svc.db.one("SELECT 1 FROM source_items WHERE dedupe_key=?", [dedupe_key])
                    ):
                        stats["duplicates"] += 1
                        continue
                    self.svc.db.insert(
                        "source_items",
                        {
                            "id": new_id("src"),
                            "source_id": src["id"],
                            "url": item.url,
                            "url_hash": url_hash,
                            "title": item.title[:300],
                            "summary": item.summary[:2000],
                            "category": src.get("category"),
                            "published_at": item.published_at,
                            "ingested_at": now_iso(),
                            "freshness_score": freshness(item.published_at, window),
                            "content_angle": None,
                            "credibility": src.get("credibility", 0.5),
                            "copyright_notes": "Lead only. Summaries are transformative; do not reuse source media without a license.",
                            "dedupe_key": dedupe_key or url_hash,
                            "status": "new",
                            "raw_json": dumps(item.raw)[:4000],
                        },
                    )
                    new_here += 1
                stats["new_items"] += new_here
                span.output_refs["new_items"] = new_here
                self.svc.db.update(
                    "sources",
                    src["id"],
                    {
                        "last_fetched_at": now_iso(),
                        "last_status": "ok",
                        "last_error": None,
                        "items_yielded": int(src.get("items_yielded", 0)) + new_here,
                        "updated_at": now_iso(),
                    },
                )
        run.note("research", stats)
        return stats
