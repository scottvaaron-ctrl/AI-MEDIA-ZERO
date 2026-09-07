"""Comment feedback loop: classify audience comments and turn useful ones into candidate leads.

The agent never replies. Content requests become ``source_items`` under the special
``audience`` source so the Ideation agent sees them as leads next cycle.
"""

from __future__ import annotations

from typing import Any

from aimz.agents.base import Agent
from aimz.core.runs import RunContext
from aimz.domain.models import CommentBatchClassification, FetchedComment
from aimz.util import dumps, new_id, normalize_title, now_iso, sha256_text

AUDIENCE_SOURCE_ID = "src_audience"


class CommentAgent(Agent):
    name = "comments"

    def ensure_audience_source(self) -> None:
        if not self.svc.db.get("sources", AUDIENCE_SOURCE_ID):
            self.svc.db.insert(
                "sources",
                {
                    "id": AUDIENCE_SOURCE_ID,
                    "name": "Audience comments",
                    "kind": "audience",
                    "url": None,
                    "category": None,
                    "credibility": 0.3,
                    "enabled": 1,
                    "created_at": now_iso(),
                    "updated_at": now_iso(),
                },
            )

    def ingest(self, publication: dict[str, Any], comments: list[FetchedComment]) -> list[str]:
        new_ids: list[str] = []
        for c in comments:
            if self.svc.db.one(
                "SELECT 1 FROM comments WHERE publication_id=? AND platform_comment_id=?",
                [publication["id"], c.platform_comment_id],
            ):
                continue
            cid = new_id("cmt")
            self.svc.db.insert(
                "comments",
                {
                    "id": cid,
                    "publication_id": publication["id"],
                    "platform_comment_id": c.platform_comment_id,
                    "author": c.author[:100],
                    "text": c.text[:2000],
                    "posted_at": c.posted_at,
                    "created_at": now_iso(),
                },
            )
            new_ids.append(cid)
        return new_ids

    def classify(self, run: RunContext, comment_ids: list[str]) -> int:
        self.ensure_audience_source()
        rows = [dict(self.svc.db.get("comments", cid) or {}) for cid in comment_ids]
        rows = [r for r in rows if r]
        done = 0
        for i in range(0, len(rows), 20):
            batch = rows[i : i + 20]
            user = (
                "Classify each audience comment as one of: question, correction, content_request, confusion, disagreement, joke, follow_up, audience_signal, spam. "
                "Mark useful=true when it should influence future content (corrections, requests, recurring confusion). For content_request, restate the request as a short topic line.\n\n"
                + "\n".join(f"[{j}] {r['text'][:300]}" for j, r in enumerate(batch))
            )
            with self.svc.tracker.agent(
                run, self.name, "classify", {"comments": [r["id"] for r in batch]}
            ) as span:
                try:
                    res = self.ask(
                        self.pctx(run, span=span),
                        "Audience analyst",
                        user,
                        CommentBatchClassification,
                        "comment_classify",
                        temperature=0.1,
                        max_tokens=2000,
                    )
                except Exception as exc:
                    self.log.warning("comment classification failed: %s", exc)
                    continue
                for r, verdict in zip(batch, res.results, strict=False):
                    upd: dict[str, Any] = {
                        "classification": verdict.classification,
                        "sentiment": verdict.sentiment,
                        "useful": 1 if verdict.useful else 0,
                    }
                    if verdict.classification == "content_request" and verdict.content_request.strip():
                        upd["converted_source_item_id"] = self._lead_from_request(
                            r, verdict.content_request.strip()
                        )
                    self.svc.db.update("comments", r["id"], upd)
                    done += 1
        return done

    def _lead_from_request(self, comment: dict[str, Any], request: str) -> str | None:
        pub = self.svc.db.get("publications", comment["publication_id"])
        url = f"{(pub['url'] if pub and pub['url'] else 'audience://request')}#comment-{comment['platform_comment_id'] or comment['id']}"
        url_hash = sha256_text(url.lower())
        if self.svc.db.one("SELECT 1 FROM source_items WHERE url_hash=?", [url_hash]):
            return None
        sid = new_id("src")
        self.svc.db.insert(
            "source_items",
            {
                "id": sid,
                "source_id": AUDIENCE_SOURCE_ID,
                "url": url,
                "url_hash": url_hash,
                "title": f"Audience request: {request[:120]}",
                "summary": f"A viewer asked: {comment['text'][:500]}",
                "category": None,
                "published_at": comment.get("posted_at"),
                "ingested_at": now_iso(),
                "freshness_score": 0.9,
                "content_angle": "audience request; must still be researched from real sources before scripting",
                "credibility": 0.3,
                "copyright_notes": "Audience comment; not a factual source.",
                "dedupe_key": normalize_title(request) or url_hash,
                "status": "new",
                "raw_json": dumps({"comment_id": comment["id"]}),
            },
        )
        return sid

    def run(self, run: RunContext) -> dict[str, int]:
        stats = {"fetched": 0, "new": 0, "classified": 0}
        pubs = [
            dict(r)
            for r in self.svc.db.query(
                "SELECT * FROM publications WHERE status IN ('uploaded','published') AND platform_video_id IS NOT NULL"
            )
        ]
        new_ids: list[str] = []
        for pub in pubs:
            provider = self.svc.remote_analytics.get(pub["platform"])
            if provider is None:
                continue
            with self.svc.tracker.agent(
                run, self.name, f"fetch:{pub['platform']}", {"publication_id": pub["id"]}
            ) as span:
                try:
                    comments = provider.fetch_comments(self.pctx(run, pub["video_id"], span), pub)
                except Exception as exc:
                    self.log.warning("comment fetch failed: %s", exc)
                    continue
                stats["fetched"] += len(comments)
                new_ids += self.ingest(pub, comments)
        # also classify anything ingested manually but not yet classified
        new_ids += [
            r["id"] for r in self.svc.db.query("SELECT id FROM comments WHERE classification IS NULL")
        ]
        new_ids = list(dict.fromkeys(new_ids))
        stats["new"] = len(new_ids)
        if new_ids:
            stats["classified"] = self.classify(run, new_ids)
        run.note("comments", stats)
        return stats
