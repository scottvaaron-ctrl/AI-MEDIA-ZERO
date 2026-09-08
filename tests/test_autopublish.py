"""Owner consent gate: API-writing publishers run without per-video approval only with AUTOPUBLISH_CONSENT."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from aimz.domain.models import PublishResult
from aimz.providers.base import HealthStatus, ProviderContext, Publisher


class RecordingPublisher(Publisher):
    name = "RecordingPublisher"
    platform = "youtube"
    mode = "public"
    is_paid = False
    performs_api_writes = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def health(self) -> HealthStatus:
        return HealthStatus(True, "fake")

    def publish(
        self, ctx: ProviderContext, video: dict, script: dict, metadata: dict, package_dir: Path
    ) -> PublishResult:
        self.calls.append(metadata)
        return PublishResult(
            status="uploaded", platform_video_id="yt123", url="https://youtu.be/yt123", privacy="public"
        )


def _seed_video(svc) -> str:  # noqa: ANN001
    from aimz.util import dumps, new_id, now_iso

    idea_id, script_id, video_id = new_id("idea"), new_id("scr"), new_id("vid")
    svc.db.insert(
        "ideas",
        {
            "id": idea_id,
            "title": "t",
            "premise": "p",
            "hook": "h",
            "hook_type": "numeric_hook",
            "content_family": "forgotten_history",
            "target_platform": "both",
            "source_item_ids_json": "[]",
            "scores_json": "{}",
            "opportunity_score": 50,
            "status": "produced",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    svc.db.insert(
        "scripts",
        {
            "id": script_id,
            "idea_id": idea_id,
            "title": "t",
            "hook_line": "h",
            "beats_json": "[]",
            "narration_text": "x",
            "status": "approved",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    mp4 = svc.env.data_dir / "fake.mp4"
    mp4.write_bytes(b"\x00" * 10)
    svc.db.insert(
        "videos",
        {
            "id": video_id,
            "script_id": script_id,
            "idea_id": idea_id,
            "title": "t",
            "format": "short",
            "resolution": "1080x1920",
            "file_path": str(mp4),
            "timeline_json": dumps({"sources": [], "attributions": []}),
            "status": "rendered",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    return video_id


def _stage(svc, consent: bool):  # noqa: ANN001, ANN202
    from aimz.agents.publisher import PublishStage
    from aimz.pipeline.orchestrator import Orchestrator

    svc.env = dataclasses.replace(svc.env, autopublish_consent=consent)
    orch = Orchestrator(svc, seed=1)
    return PublishStage(svc, orch.strategy)


def test_without_consent_api_upload_is_blocked(svc) -> None:  # noqa: ANN001
    rec = RecordingPublisher()
    svc.publishers = {"youtube": rec}
    stage = _stage(svc, consent=False)
    vid = _seed_video(svc)
    with svc.tracker.run("publish") as run:
        res = stage.publish(run, vid)
    assert res[0]["status"] == "blocked" and rec.calls == []
    assert svc.db.one("SELECT status FROM publications")["status"] == "blocked"


def test_with_consent_api_upload_runs_and_marks_published(svc) -> None:  # noqa: ANN001
    rec = RecordingPublisher()
    svc.publishers = {"youtube": rec}
    stage = _stage(svc, consent=True)
    vid = _seed_video(svc)
    with svc.tracker.run("publish") as run:
        res = stage.publish(run, vid)
    assert res[0]["status"] == "uploaded" and len(rec.calls) == 1 and rec.calls[0]["owner_approved"] is True
    pub = svc.db.one("SELECT * FROM publications")
    assert pub["platform_video_id"] == "yt123" and pub["posted_at"] is not None
    assert svc.db.get("videos", vid)["status"] == "published"
    # idempotent: a second cycle does not upload again
    with svc.tracker.run("publish") as run:
        res2 = stage.publish(run, vid)
    assert res2[0].get("skipped") and len(rec.calls) == 1


def test_consent_never_overrides_kill_switch(svc) -> None:  # noqa: ANN001
    rec = RecordingPublisher()
    svc.publishers = {"youtube": rec}
    stage = _stage(svc, consent=True)
    vid = _seed_video(svc)
    svc.killswitch.engage("test")
    with svc.tracker.run("publish") as run:
        res = stage.publish(run, vid)
    assert res[0]["status"] == "blocked" and rec.calls == []


def test_poll_pending_promotes_uploading_publications(svc) -> None:  # noqa: ANN001
    from aimz.util import dumps, now_iso

    class Poller(RecordingPublisher):
        platform = "tiktok"
        mode = "direct"

        def poll(self, ctx: ProviderContext, publication: dict) -> PublishResult | None:
            return PublishResult(
                status="published",
                platform_video_id="tt1",
                url="https://www.tiktok.com/@a/video/tt1",
                publish_id="v_pub~1",
            )

    svc.publishers = {"tiktok": Poller()}
    stage = _stage(svc, consent=True)
    vid = _seed_video(svc)
    svc.db.insert(
        "publications",
        {
            "id": "pub1",
            "video_id": vid,
            "platform": "tiktok",
            "publisher": "Poller",
            "mode": "direct",
            "idempotency_key": "k",
            "status": "uploading",
            "metadata_json": dumps({"publish_id": "v_pub~1"}),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    with svc.tracker.run("publish") as run:
        n = stage.poll_pending(run)
    assert n == 1
    pub = svc.db.get("publications", "pub1")
    assert pub["status"] == "published" and pub["platform_video_id"] == "tt1" and pub["posted_at"]
    assert svc.db.get("videos", vid)["status"] == "published"
