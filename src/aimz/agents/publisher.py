"""Publish stage: idempotent, kill-switch-guarded hand-off from rendered videos to publishers.

State machine per (video, platform, mode)::

    pending -> packaged                      (no API)
    pending -> uploading -> uploaded/published
    pending | uploading -> failed -> (checked retry) -> pending ...
                        -> abandoned         (closed out; the owner can still requeue it)

Retries (owner decision 2026-09-28, replacing "never retried"): a video that passed every stage must not
be lost to a technical failure, and a timed-out upload may already be live, so a retry is never blind.

* Before any retry the platform is asked whether the post already exists (``Publisher.find_existing``).
  Found: the row is recorded as posted and nothing is uploaded. Confirmed absent: it is uploaded again.
* A platform that cannot answer is retried only when the failure proves nothing reached it
  (``failure_kind='transient'``: expired login, network down); otherwise the row is closed out.
* ``publishing.max_attempts`` attempts in all, spaced by ``publishing.retry_after_hours``. After the
  last one, or on a failure no retry can fix (``PermanentPublishError``), the row becomes ``abandoned``
  and an error is recorded, which raises the owner's alert.
* The kill switch and the consent gate apply to retries exactly as to first attempts.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from aimz.agents.base import Agent
from aimz.agents.producer import package_name
from aimz.core.errors import (
    CannotVerify,
    KillSwitchEngaged,
    OwnerApprovalRequired,
    PermanentPublishError,
    ProviderUnavailable,
)
from aimz.core.runs import RunContext
from aimz.util import dumps, iso_ahead, loads, new_id, now_iso

PUBLISHABLE_VIDEO = {"rendered", "approved", "published", "measured"}


def classify_failure(exc: BaseException) -> str:
    """``permanent``: no retry can fix it. ``transient``: provably nothing reached the platform.
    ``uncertain``: the post may exist (a timeout or error mid-upload), so it is looked for before a retry."""
    if isinstance(exc, PermanentPublishError):
        return "permanent"
    if isinstance(exc, (ProviderUnavailable, httpx.ConnectError)):
        return "transient"
    # google-auth's RefreshError: the stored login expired before any upload started.
    if type(exc).__name__ in {"RefreshError", "TransportError"}:
        return "transient"
    return "uncertain"


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

    # -- failure bookkeeping ------------------------------------------------------------
    def _max_attempts(self) -> int:
        return max(1, int(self.svc.config.get("publishing.max_attempts", 3)))

    def record_failure(self, run: RunContext, pub_id: str, message: str, kind: str) -> str:
        """Mark a publication failed and schedule its checked retry, or close it out. Returns the status."""
        db = self.svc.db
        pub = db.get("publications", pub_id)
        attempts = int(pub["attempts"] or 0) if pub else 0
        if kind == "permanent" or attempts >= self._max_attempts():
            db.update(
                "publications",
                pub_id,
                {
                    "status": "abandoned",
                    "failure_kind": kind,
                    "next_attempt_at": None,
                    "last_error": message[:800],
                    "updated_at": now_iso(),
                },
            )
            why = "cannot be fixed by a retry" if kind == "permanent" else f"after {attempts} attempts"
            self.svc.tracker.record_error(
                run.id,
                self.name,
                "abandoned",
                RuntimeError(
                    f"{pub['platform'] if pub else '?'} publication {pub_id} closed out {why}: {message[:300]}. "
                    f"Owner can re-send it with `python -m aimz publish retry {pub_id}`"
                ),
            )
            return "abandoned"
        hours = float(self.svc.config.get("publishing.retry_after_hours", 1))
        db.update(
            "publications",
            pub_id,
            {
                "status": "failed",
                "failure_kind": kind,
                "next_attempt_at": iso_ahead(hours=hours),
                "last_error": message[:800],
                "updated_at": now_iso(),
            },
        )
        return "failed"

    # -- publish ------------------------------------------------------------------------
    def publish(
        self,
        run: RunContext,
        video_id: str,
        platforms: list[str] | None = None,
        owner_approved: bool = False,
        extra_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        db = self.svc.db
        video = dict(db.get("videos", video_id) or {})
        if not video or video["status"] not in PUBLISHABLE_VIDEO:
            raise ValueError(f"video {video_id} not publishable (status={video.get('status')})")
        script = dict(db.get("scripts", video["script_id"]) or {})
        idea = dict(db.get("ideas", video["idea_id"]) or {})
        # Owner consent: per-video approval, or the standing AUTOPUBLISH_CONSENT the owner set in .env.
        approved = (
            owner_approved
            or video["status"] in {"approved", "published", "measured"}
            or bool(self.svc.env.autopublish_consent)
        )
        metadata = self.build_metadata(video, script, idea, approved)
        metadata.update(extra_metadata or {})
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
            if existing and existing["status"] in {"failed", "abandoned"}:
                if existing["status"] == "failed" and existing["next_attempt_at"]:
                    why = f"checked retry scheduled after {existing['next_attempt_at']}"
                else:
                    why = "closed out; the owner can re-send it (aimz publish retry)"
                results.append({"platform": platform, "status": existing["status"], "skipped": why})
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
                "next_attempt_at": None,
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
                    self.svc.tracker.record_error(run.id, self.name, f"publish:{platform}", exc)
                    status = self.record_failure(
                        run, pub_id, f"{type(exc).__name__}: {exc}", classify_failure(exc)
                    )
                    results.append({"platform": platform, "status": status, "error": str(exc)})
                    span.output_refs["status"] = status
                    continue
                update = {
                    "status": res.status,
                    "platform_video_id": res.platform_video_id,
                    "url": res.url,
                    "privacy": res.privacy,
                    "package_dir": res.package_dir or str(package_dir),
                    "last_error": None,
                    "failure_kind": None,
                    "updated_at": now_iso(),
                }
                if res.status in {"uploaded", "published"}:
                    update["posted_at"] = now_iso()
                if res.publish_id:
                    update["metadata_json"] = dumps(
                        {**loads(str(row.get("metadata_json") or ""), {}), "publish_id": res.publish_id}
                    )
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
            self._mark_video_published(video_id)
        run.note(f"publish:{video_id}", results)
        return results

    def _mark_video_published(self, video_id: str) -> None:
        db = self.svc.db
        video = db.get("videos", video_id)
        if video is None:
            return
        if video["status"] != "measured":  # a late retry must not reopen a closed measurement window
            db.update("videos", video_id, {"status": "published", "updated_at": now_iso()})
        db.update("ideas", video["idea_id"], {"status": "published", "updated_at": now_iso()})

    # -- uploads the platform was still processing ----------------------------------------
    def poll_pending(self, run: RunContext) -> int:
        """Advance publications the platform was still processing (e.g. TikTok PROCESSING_UPLOAD)."""
        db = self.svc.db
        done = 0
        for pub in [dict(r) for r in db.query("SELECT * FROM publications WHERE status='uploading'")]:
            publisher = self.svc.publishers.get(pub["platform"])
            if publisher is None:
                continue
            if publisher.performs_api_writes and self.svc.killswitch.status().engaged:
                # Finishing a post (Bluesky creates the post record here) is a platform write. The row
                # stays 'uploading' and completes on the first cycle after the owner resumes.
                run.note("poll_blocked", "kill switch engaged")
                continue
            with self.svc.tracker.agent(
                run, self.name, f"poll:{pub['platform']}", {"publication_id": pub["id"]}
            ) as span:
                try:
                    res = publisher.poll(self.pctx(run, pub["video_id"], span), pub)
                except KillSwitchEngaged:
                    continue
                except Exception as exc:
                    # Not left 'uploading' to be finished again next cycle (that re-posted on every cycle):
                    # it becomes a failure, and the checked retry looks for the post before doing anything.
                    self.log.warning("poll failed for %s: %s", pub["id"], exc)
                    self.svc.tracker.record_error(run.id, self.name, f"poll:{pub['platform']}", exc)
                    span.output_refs["status"] = self.record_failure(
                        run, pub["id"], f"{type(exc).__name__}: {exc}", "uncertain"
                    )
                    continue
                if res is None:
                    continue
                if res.status == "failed":
                    span.output_refs["status"] = self.record_failure(
                        run, pub["id"], res.message or "platform reported failure", "transient"
                    )
                    done += 1
                    continue
                self._apply_result(pub, res)
                span.output_refs["status"] = res.status
                done += 1
        return done

    def _apply_result(self, pub: dict[str, Any], res: Any) -> None:
        update: dict[str, Any] = {
            "status": res.status,
            "updated_at": now_iso(),
            "last_error": None,
            "failure_kind": None,
            "next_attempt_at": None,
        }
        if res.platform_video_id:
            update["platform_video_id"] = res.platform_video_id
        if res.url:
            update["url"] = res.url
        if res.status in {"uploaded", "published"}:
            update["posted_at"] = now_iso()
        self.svc.db.update("publications", pub["id"], update)
        if res.status in {"uploaded", "published"}:
            self._mark_video_published(pub["video_id"])

    # -- checked retries -----------------------------------------------------------------
    def retry_due(self, run: RunContext) -> list[dict[str, Any]]:
        """Retry failed publications whose wait is over, after checking the platform for the post."""
        db = self.svc.db
        due = [
            dict(r)
            for r in db.query(
                "SELECT * FROM publications WHERE status='failed' AND next_attempt_at IS NOT NULL "
                "AND next_attempt_at <= ? ORDER BY next_attempt_at",
                [now_iso()],
            )
        ]
        out: list[dict[str, Any]] = []
        for pub in due:
            publisher = self.svc.publishers.get(pub["platform"])
            if publisher is None:
                continue  # platform switched off: leave the row for when it is back
            if self.svc.killswitch.status().engaged:
                run.note("retry_blocked", "kill switch engaged")
                break  # retries wait for the owner to resume; no attempt is used up
            with self.svc.tracker.agent(
                run, self.name, f"retry-check:{pub['platform']}", {"publication_id": pub["id"]}
            ) as span:
                try:
                    found = publisher.find_existing(self.pctx(run, pub["video_id"], span), pub)
                    verdict = "found" if found is not None else "absent"
                except CannotVerify as exc:
                    found, verdict = None, "unknown"
                    span.output_refs["cannot_verify"] = str(exc)[:200]
                except Exception as exc:
                    # The check itself failed (network, login). Try again next cycle without using an attempt.
                    self.log.warning("retry check failed for %s: %s", pub["id"], exc)
                    db.update(
                        "publications",
                        pub["id"],
                        {"next_attempt_at": iso_ahead(hours=1), "updated_at": now_iso()},
                    )
                    out.append({"publication_id": pub["id"], "result": "check failed; next cycle"})
                    continue
                span.output_refs["verdict"] = verdict
            if found is not None:
                # The earlier attempt did reach the platform: record it; uploading again would duplicate it.
                self._apply_result(pub, found)
                out.append({"publication_id": pub["id"], "result": f"already on {pub['platform']}"})
                continue
            if verdict == "unknown" and pub["failure_kind"] != "transient":
                self.record_failure(
                    run,
                    pub["id"],
                    f"{pub['last_error'] or 'failed'} (not retried: {pub['platform']} cannot confirm whether "
                    "the earlier attempt posted)",
                    "permanent",
                )
                out.append({"publication_id": pub["id"], "result": "closed out; cannot verify"})
                continue
            db.update("publications", pub["id"], {"status": "pending", "updated_at": now_iso()})
            res = self.publish(run, pub["video_id"], platforms=[pub["platform"]])
            out.append({"publication_id": pub["id"], "result": res[0]["status"] if res else "skipped"})
        if out:
            run.note("retries", out)
        return out

    # -- owner actions -------------------------------------------------------------------
    def requeue(self, publication_id: str) -> dict[str, Any]:
        """Owner-deliberate reset of a failed, blocked or abandoned publication, with a fresh set of attempts.

        This is a blind re-send: the owner has decided it should go out (check the platform first if the
        earlier attempt may have posted).
        """
        pub = self.svc.db.get("publications", publication_id)
        if not pub:
            raise ValueError(f"publication {publication_id} not found")
        if pub["status"] not in {"failed", "blocked", "abandoned"}:
            raise ValueError(
                f"publication {publication_id} is '{pub['status']}'; only failed, blocked or abandoned ones "
                "are re-queued"
            )
        self.svc.db.update(
            "publications",
            publication_id,
            {
                "status": "pending",
                "attempts": 0,
                "next_attempt_at": None,
                "failure_kind": None,
                "last_error": None,
                "updated_at": now_iso(),
            },
        )
        return {
            "publication_id": publication_id,
            "video_id": pub["video_id"],
            "platform": pub["platform"],
            "cleared_error": (pub["last_error"] or "")[:200],
        }

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
                "next_attempt_at": None,
                "updated_at": now_iso(),
            },
        )
        self._mark_video_published(pub["video_id"])
        video = self.svc.db.get("videos", pub["video_id"])
        if video:
            self.svc.db.execute(
                "UPDATE sources SET videos_yielded = videos_yielded + 1 WHERE id IN (SELECT source_id FROM source_items WHERE id IN "
                "(SELECT value FROM json_each((SELECT source_item_ids_json FROM ideas WHERE id=?))))",
                [video["idea_id"]],
            )
