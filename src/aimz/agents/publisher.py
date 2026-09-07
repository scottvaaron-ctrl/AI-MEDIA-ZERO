"""Publish stage: idempotent, kill-switch-guarded hand-off from rendered videos to publishers.

State machine per (video, platform, mode):
``pending -> packaged`` (no API) or ``pending -> uploading -> uploaded`` (API), or ``-> failed``.
A failed upload is never retried automatically; the owner re-queues it deliberately.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aimz.agents.base import Agent
from aimz.agents.producer import package_name
from aimz.core.errors import KillSwitchEngaged, OwnerApprovalRequired
from aimz.core.runs import RunContext
from aimz.util import dumps, loads, new_id, now_iso


class PublishStage(Agent):
    name = "publisher"

    def build_metadata(
        self, video: dict[str, Any], script: dict[str, Any], idea: dict[str, Any], owner_approved: bool
    ) -> dict[str, Any]:
        timeline = loads(video.get("timeline_json"), {}) or {}
        sources = timeline.get("sources", [])
        attributions = timeline.get("attributions", [])
        disclosure = str(self.svc.config.get("publishing.ai_disclosure_text", ""))
        src_lines = "\n".join(f"- {s.get('title', '')}: {s.get('url', '')}" for s in sources)
        attr_lines = "\n".join(f"- {a}" for a in attributions)
        description = (
            f"{script.get('description') or script['title']}\n\n"
            f"{disclosure}\n\n"
            f"Sources:\n{src_lines or '- (see package sources.json)'}\n"
            + (f"\nImage credits:\n{attr_lines}\n" if attr_lines else "")
        )
        tags = list(loads(script.get("tags_json"), []) or [])
        tags += [t for t in self.svc.config.get("publishing.youtube.default_tags", []) or [] if t not in tags]
        # Posting time heuristic: next 17:00 local. Strategy may later learn better windows.
        post_time = (
            datetime.now().replace(hour=17, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ).isoformat(timespec="minutes")
        return {
            "title": script["title"],
            "description": description[:4900],
            "tags": tags[:15],
            "sources": sources,
            "attributions": attributions,
            "ai_disclosure": disclosure,
            "content_family": idea.get("content_family"),
            "hook_type": idea.get("hook_type"),
            "experiment": {"id": idea.get("experiment_id"), "arm": idea.get("experiment_arm")}
            if idea.get("experiment_id")
            else None,
            "recommended_post_time": post_time,
            "owner_approved": owner_approved,
        }

    def publish(
        self, run: RunContext, video_id: str, platforms: list[str] | None = None, owner_approved: bool = False
    ) -> list[dict[str, Any]]:
        db = self.svc.db
        video = dict(db.get("videos", video_id) or {})
        if not video or video["status"] not in {"rendered", "approved", "published"}:
            raise ValueError(f"video {video_id} not publishable (status={video.get('status')})")
        script = dict(db.get("scripts", video["script_id"]) or {})
        idea = dict(db.get("ideas", video["idea_id"]) or {})
        approved = owner_approved or video["status"] in {"approved", "published"}
        metadata = self.build_metadata(video, script, idea, approved)
        results: list[dict[str, Any]] = []
        for platform, pub in self.svc.publishers.items():
            if platforms and platform not in platforms:
                continue
            mode = getattr(pub, "mode", "package")
            key = f"{video_id}:{platform}:{mode}"
            existing = db.one("SELECT * FROM publications WHERE idempotency_key=?", [key])
            if existing and existing["status"] in {"uploaded", "published", "uploading"}:
                results.append(
                    {"platform": platform, "status": existing["status"], "skipped": "already done"}
                )
                continue
            if existing and existing["status"] == "failed":
                results.append(
                    {
                        "platform": platform,
                        "status": "failed",
                        "skipped": "previous failure needs owner review (aimz publish retry)",
                    }
                )
                continue
            pub_id = existing["id"] if existing else new_id("pub")
            package_dir = self.svc.env.data_dir / "packages" / platform / package_name(video)
            row = {
                "id": pub_id,
                "video_id": video_id,
                "platform": platform,
                "publisher": pub.name,
                "mode": mode,
                "idempotency_key": key,
                "status": "pending",
                "attempts": int(existing["attempts"]) + 1 if existing else 1,
                "package_dir": str(package_dir),
                "metadata_json": dumps({k: v for k, v in metadata.items() if k != "sources"}),
                "created_at": existing["created_at"] if existing else now_iso(),
                "updated_at": now_iso(),
            }
            if existing:
                db.update("publications", pub_id, row)
            else:
                db.insert("publications", row)
            with self.svc.tracker.agent(
                run, self.name, f"publish:{platform}", {"video_id": video_id, "publication_id": pub_id}
            ) as span:
                try:
                    if pub.performs_api_writes:
                        self.svc.killswitch.guard(f"publish to {platform}")
                        if not approved and self.svc.config.get("publishing.require_owner_approval", True):
                            raise OwnerApprovalRequired(
                                f"video {video_id} needs owner approval before {platform} upload"
                            )
                        db.update("publications", pub_id, {"status": "uploading", "updated_at": now_iso()})
                    res = pub.publish(self.pctx(run, video_id, span), video, script, metadata, package_dir)
                except (KillSwitchEngaged, OwnerApprovalRequired) as exc:
                    db.update(
                        "publications",
                        pub_id,
                        {"status": "blocked", "last_error": str(exc)[:500], "updated_at": now_iso()},
                    )
                    results.append({"platform": platform, "status": "blocked", "error": str(exc)})
                    span.output_refs["status"] = "blocked"
                    continue
                except Exception as exc:
                    db.update(
                        "publications",
                        pub_id,
                        {
                            "status": "failed",
                            "last_error": f"{type(exc).__name__}: {exc}"[:800],
                            "updated_at": now_iso(),
                        },
                    )
                    self.svc.tracker.record_error(run.id, self.name, f"publish:{platform}", exc)
                    results.append({"platform": platform, "status": "failed", "error": str(exc)})
                    span.output_refs["status"] = "failed"
                    continue
                update = {
                    "status": res.status,
                    "platform_video_id": res.platform_video_id,
                    "url": res.url,
                    "privacy": res.privacy,
                    "package_dir": res.package_dir or str(package_dir),
                    "last_error": None,
                    "updated_at": now_iso(),
                }
                if res.status in {"uploaded", "published"}:
                    update["posted_at"] = now_iso()
                db.update("publications", pub_id, update)
                span.output_refs["status"] = res.status
                results.append(
                    {
                        "platform": platform,
                        "status": res.status,
                        "package_dir": res.package_dir,
                        "url": res.url,
                        "message": res.message,
                    }
                )
        if any(r["status"] in {"uploaded", "published"} for r in results):
            db.update("videos", video_id, {"status": "published", "updated_at": now_iso()})
            db.update("ideas", video["idea_id"], {"status": "published", "updated_at": now_iso()})
        run.note(f"publish:{video_id}", results)
        return results

    def mark_posted(
        self,
        publication_id: str,
        url: str | None = None,
        platform_video_id: str | None = None,
        posted_at: str | None = None,
    ) -> None:
        """Owner confirms that a package was posted manually (TikTok, or YouTube in draft mode)."""
        pub = self.svc.db.get("publications", publication_id)
        if not pub:
            raise ValueError("publication not found")
        self.svc.db.update(
            "publications",
            publication_id,
            {
                "status": "published",
                "url": url or pub["url"],
                "platform_video_id": platform_video_id or pub["platform_video_id"],
                "posted_at": posted_at or now_iso(),
                "updated_at": now_iso(),
            },
        )
        self.svc.db.update("videos", pub["video_id"], {"status": "published", "updated_at": now_iso()})
        video = self.svc.db.get("videos", pub["video_id"])
        if video:
            self.svc.db.update("ideas", video["idea_id"], {"status": "published", "updated_at": now_iso()})
            self.svc.db.execute(
                "UPDATE sources SET videos_yielded = videos_yielded + 1 WHERE id IN (SELECT source_id FROM source_items WHERE id IN "
                "(SELECT value FROM json_each((SELECT source_item_ids_json FROM ideas WHERE id=?))))",
                [video["idea_id"]],
            )
