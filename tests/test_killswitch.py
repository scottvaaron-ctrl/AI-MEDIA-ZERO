from __future__ import annotations

from pathlib import Path

import pytest

from aimz.core.errors import KillSwitchEngaged
from aimz.providers.paid_examples import PaidTTSProviderExample


def test_engage_and_release(svc) -> None:  # noqa: ANN001
    assert not svc.killswitch.is_engaged()
    st = svc.killswitch.engage("test")
    assert st.engaged and st.file_present and st.db_flag
    with pytest.raises(KillSwitchEngaged):
        svc.killswitch.guard("publish")
    svc.killswitch.release()
    assert not svc.killswitch.is_engaged()
    svc.killswitch.guard("publish")  # no raise


def test_sentinel_file_alone_engages(svc) -> None:  # noqa: ANN001
    Path(svc.killswitch.sentinel_path).write_text("stop", encoding="utf-8")
    assert svc.killswitch.is_engaged()
    with pytest.raises(KillSwitchEngaged):
        svc.killswitch.guard("upload")


def test_kill_blocks_paid_providers_even_with_budget(svc) -> None:  # noqa: ANN001
    from aimz.core.budget import BudgetManager

    ctx = svc.ctx()
    ctx.budget = BudgetManager(svc.db, 100.0)
    svc.killswitch.engage("test")
    with pytest.raises(KillSwitchEngaged):
        PaidTTSProviderExample().synthesize(ctx, "hi", svc.env.data_dir / "x.wav")


def test_kill_blocks_api_publish_but_not_packaging(svc) -> None:  # noqa: ANN001
    """Kill switch: packages still get written locally; API-writing publishers are blocked."""
    from aimz.agents.publisher import PublishStage
    from aimz.pipeline.orchestrator import Orchestrator
    from aimz.util import dumps, new_id, now_iso

    orch = Orchestrator(svc, seed=1)
    # fabricate a rendered video row
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

    # make the YouTube publisher look like it would write to the API
    yt = svc.publishers["youtube"]
    yt.enabled, yt.mode = True, "private"
    svc.killswitch.engage("test")
    stage = PublishStage(svc, orch.strategy)
    with svc.tracker.run("publish") as run:
        results = stage.publish(run, video_id)
    by_platform = {r["platform"]: r for r in results}
    assert by_platform["youtube"]["status"] == "blocked"
    assert by_platform["tiktok"]["status"] == "packaged"
    assert svc.db.one("SELECT status FROM publications WHERE platform='youtube'")["status"] == "blocked"
