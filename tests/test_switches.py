"""Owner decision 2026-10-07: the kill switch is a full stop; the posting switch pauses only posting."""

from __future__ import annotations

from typing import Any

import pytest
from tests.test_autopublish import RecordingPublisher, _seed_video
from tests.test_pipeline_offline import FixtureFeedProvider

WORK_TABLES = ("ideas", "scripts", "videos", "source_items", "publications")


def _counts(svc) -> dict[str, int]:  # noqa: ANN001
    return {t: svc.db.count(t) for t in WORK_TABLES}


def _orch(svc):  # noqa: ANN001, ANN202
    from aimz.pipeline.orchestrator import Orchestrator

    fp = FixtureFeedProvider()
    svc.research = {k: fp for k in fp.kinds}
    return Orchestrator(svc, seed=7)


def _no_llm(svc, monkeypatch) -> list[Any]:  # noqa: ANN001
    calls: list[Any] = []

    def fail(*a: Any, **k: Any) -> Any:
        calls.append(a)
        raise RuntimeError("LLM must not be called while the kill switch is engaged")

    monkeypatch.setattr(svc.llm, "complete", fail)
    monkeypatch.setattr(svc.llm, "complete_model", fail)
    return calls


# -- kill switch: the agent does nothing ---------------------------------------------------------
def test_killed_cycle_does_nothing(svc, monkeypatch) -> None:  # noqa: ANN001
    from aimz import youtube_data

    orch = _orch(svc)
    llm_calls = _no_llm(svc, monkeypatch)
    retention: list[Any] = []
    monkeypatch.setattr(youtube_data, "enforce", lambda *a, **k: retention.append(a) or {})
    before = _counts(svc)
    svc.killswitch.engage("owner said stop")

    summary = orch.cycle()

    assert _counts(svc) == before
    assert llm_calls == []
    assert summary["skipped"] == "kill switch engaged: owner said stop"
    assert summary["run_id"]
    for stage in ("research", "ideation", "selection", "write", "produce", "posting"):
        assert stage not in summary
    assert len(retention) == 1
    run = svc.db.get("runs", summary["run_id"])
    assert run is not None and "kill switch engaged" in run["summary_json"]


def test_killed_cycle_retention_failure_does_not_crash(svc, monkeypatch) -> None:  # noqa: ANN001
    from aimz import youtube_data

    orch = _orch(svc)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("retention store unreadable")

    monkeypatch.setattr(youtube_data, "enforce", boom)
    svc.killswitch.engage("test")
    summary = orch.cycle()
    assert summary["skipped"].startswith("kill switch engaged")
    assert svc.db.count("errors", "operation='youtube_retention'") == 1


def test_cli_run_prints_skip_and_exits_zero_when_killed(svc, monkeypatch) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz import youtube_data
    from aimz.cli import app

    monkeypatch.setattr(youtube_data, "enforce", lambda *a, **k: {})
    svc.killswitch.engage("holiday")
    out = CliRunner().invoke(app, ["run"])
    assert out.exit_code == 0, out.output
    assert "kill switch engaged" in out.output
    assert svc.db.count("source_items") == 0


@pytest.mark.parametrize(
    "args",
    [
        ["produce"],
        ["research"],
        ["ideas", "--generate"],
        ["publish", "run"],
        ["publish", "retry", "pub_x"],
        ["publish", "poll"],
        ["strategy", "learn"],
        ["analytics", "--collect"],
    ],
)
def test_cli_agent_commands_refuse_when_killed(svc, args) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    svc.killswitch.engage("test")
    before = svc.db.count("runs")
    out = CliRunner().invoke(app, args)
    assert out.exit_code == 1, out.output
    assert "kill switch is engaged" in out.output
    assert svc.db.count("runs") == before


def test_cli_produce_refuses_when_killed(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    svc.killswitch.engage("test")
    out = CliRunner().invoke(app, ["produce"])
    assert out.exit_code == 1 and "kill switch is engaged" in out.output
    assert svc.db.count("videos") == 0


def test_owner_commands_still_work_when_killed(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    svc.killswitch.engage("test")
    runner = CliRunner()
    out = runner.invoke(app, ["status"])
    assert out.exit_code == 0 and "ENGAGED" in out.output
    assert runner.invoke(app, ["guideline", "show"]).exit_code == 0
    assert runner.invoke(app, ["posting", "status"]).exit_code == 0
    assert runner.invoke(app, ["ideas"]).exit_code == 0  # listing is not agent work
    assert runner.invoke(app, ["resume"]).exit_code == 0
    assert not svc.killswitch.is_engaged()


# -- posting switch ---------------------------------------------------------------------------
def test_posting_default_on(svc) -> None:  # noqa: ANN001
    st = svc.posting.status()
    assert st.on is True and st.since is None and st.reason == ""
    assert svc.db.get_state("posting") is None


def test_posting_switch_records_reason(svc) -> None:  # noqa: ANN001
    st = svc.posting.turn_off("owner", "reviewing the last uploads")
    assert st.on is False and st.since and st.reason == "reviewing the last uploads"
    assert svc.db.get_state("posting") == "off"
    assert "reviewing the last uploads" in st.describe()
    row = svc.db.one("SELECT * FROM configuration_versions WHERE key='posting' ORDER BY created_at DESC")
    assert row["changed_by"] == "owner" and row["content"] == "off reviewing the last uploads"
    st = svc.posting.turn_on("owner", "")
    assert st.on is True and st.describe() == "on"


def test_posting_off_skips_publish_but_renders(svc, monkeypatch, ffmpeg_available: bool) -> None:  # noqa: ANN001
    from tests.conftest import relax_posting_limits

    relax_posting_limits(svc)
    orch = _orch(svc)
    svc.posting.turn_off("owner", "test")

    summary = orch.cycle()

    assert summary["research"]["new_items"] >= 5
    assert summary["selection"]["selected"] >= 1
    assert summary["publish"] == "posting switch off"
    assert "posting" not in summary
    assert svc.db.count("publications") == 0
    if ffmpeg_available:
        assert svc.db.count("videos", "status='rendered'") >= 1
    else:
        _seed_video(svc)
    waiting = svc.db.count("videos", "status='rendered'")
    assert waiting >= 1

    # Turned back on: the next publish stage posts the queued video through the normal path.
    svc.posting.turn_on("owner", "")
    summary = orch.cycle(["publish"])
    assert "publish" not in summary or summary["publish"] != "posting switch off"
    assert summary["posting"]["allowed"] is True
    assert svc.db.count("publications") >= 1


def test_posting_off_does_not_retry_failed_uploads(svc) -> None:  # noqa: ANN001
    from tests.conftest import relax_posting_limits
    from tests.test_autopublish import FailingPublisher, _stage

    relax_posting_limits(svc)
    svc.publishers = {"youtube": FailingPublisher()}
    stage = _stage(svc, consent=True)
    vid = _seed_video(svc)
    with svc.tracker.run("publish") as run:
        stage.publish(run, vid)
    pub = svc.db.one("SELECT * FROM publications")
    assert pub["status"] == "failed"
    svc.db.update("publications", pub["id"], {"next_attempt_at": "2000-01-01T00:00:00+00:00"})  # retry due
    pub = svc.db.get("publications", pub["id"])
    svc.posting.turn_off("owner", "")
    rec = RecordingPublisher()
    svc.publishers = {"youtube": rec}
    orch = _orch(svc)
    with svc.tracker.run("publish") as run:
        assert orch.publish_rendered(run) == []
    assert rec.calls == []
    after = svc.db.get("publications", pub["id"])
    assert after["status"] == pub["status"] and after["attempts"] == pub["attempts"]


def test_publish_run_refuses_when_posting_off(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    vid = _seed_video(svc)
    svc.posting.turn_off("owner", "")
    out = CliRunner().invoke(app, ["publish", "run", vid, "--approved"])
    assert out.exit_code == 1
    assert "python -m aimz posting on" in out.output
    assert svc.db.count("publications") == 0
    assert svc.db.get("videos", vid)["status"] == "rendered"


def test_posting_cli_toggles(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    runner = CliRunner()
    out = runner.invoke(app, ["posting", "off", "--reason", "pause"])
    assert out.exit_code == 0 and not svc.posting.is_on()
    assert svc.posting.status().reason == "pause"
    out = runner.invoke(app, ["posting", "status"])
    assert out.exit_code == 0 and "off" in out.output and "pause" in out.output
    out = runner.invoke(app, ["posting", "on"])
    assert out.exit_code == 0 and svc.posting.is_on()


def test_status_shows_posting(svc) -> None:  # noqa: ANN001
    import json

    from typer.testing import CliRunner

    from aimz.cli import app

    runner = CliRunner()
    info = json.loads(runner.invoke(app, ["status"]).output)
    assert info["posting_switch"] == "on"
    svc.posting.turn_off("owner", "holiday")
    info = json.loads(runner.invoke(app, ["status"]).output)
    assert info["posting_switch"].startswith("off (") and "holiday" in info["posting_switch"]
    assert "limits" in info["posting"]  # the posting-limits block is unchanged


def test_dashboard_posting_toggle(svc) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from aimz.dashboard.app import create_app

    client = TestClient(create_app(svc))
    page = client.get("/")
    assert page.status_code == 200 and "posting" in page.text
    controls = client.get("/controls")
    assert controls.status_code == 200 and "/controls/posting" in controls.text
    r = client.post("/controls/posting", data={"state": "off", "reason": "pause"}, follow_redirects=False)
    assert r.status_code == 303 and not svc.posting.is_on()
    assert svc.posting.status().reason == "pause"
    assert "posting off" in client.get("/").text
    r = client.post("/controls/posting", data={"state": "on"}, follow_redirects=False)
    assert r.status_code == 303 and svc.posting.is_on()
    r = client.post("/controls/posting", data={"state": "maybe"}, follow_redirects=False)
    assert r.status_code == 400 and svc.posting.is_on()


def _old_upload(svc, platform: str, publisher: str) -> tuple[str, str]:  # noqa: ANN001
    """A publication sent before posting was turned off, still 'uploading' 30 hours later."""
    from aimz.util import dumps, iso_ago

    vid = _seed_video(svc)
    old = iso_ago(hours=30)
    svc.db.insert(
        "publications",
        {
            "id": "pub_inflight",
            "video_id": vid,
            "platform": platform,
            "publisher": publisher,
            "mode": "direct",
            "idempotency_key": "k-inflight",
            "status": "uploading",
            "metadata_json": dumps({"publish_id": "v_pub~1"}),
            "created_at": old,
            "updated_at": old,
        },
    )
    return vid, "pub_inflight"


def test_posting_off_polls_inflight_upload_to_completion(svc) -> None:  # noqa: ANN001
    from aimz.domain.models import PublishResult

    class Poller(RecordingPublisher):
        platform = "tiktok"
        mode = "direct"

        def poll(self, ctx, publication):  # noqa: ANN001, ANN202
            return PublishResult(status="published", platform_video_id="tt1", url="https://t/tt1")

    svc.publishers = {"tiktok": Poller()}
    vid, pid = _old_upload(svc, "tiktok", "Poller")
    svc.posting.turn_off("owner", "")
    summary = _orch(svc).cycle(["publish"])
    assert summary["publish"] == "posting switch off"
    pub = svc.db.get("publications", pid)
    assert pub["status"] == "published" and pub["attempts"] == 0
    assert svc.db.get("videos", vid)["status"] == "published"


def test_posting_off_recover_does_not_fail_inflight_upload(svc) -> None:  # noqa: ANN001
    """A publisher with no poll: recover() would call it uncertain after 1h, but not while posting is off."""
    svc.publishers = {"youtube": RecordingPublisher()}
    _vid, pid = _old_upload(svc, "youtube", "RecordingPublisher")
    svc.posting.turn_off("owner", "")
    _orch(svc).cycle(["publish"])
    pub = svc.db.get("publications", pid)
    assert pub["status"] == "uploading" and pub["attempts"] == 0 and not pub["last_error"]
    # Back on: the normal recovery judges it again (unchanged behaviour).
    svc.posting.turn_on("owner", "")
    _orch(svc).cycle(["publish"])
    assert svc.db.get("publications", pid)["status"] != "uploading"


def test_dashboard_blocked_posts_show_a_message(svc) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from aimz.dashboard.app import create_app

    vid = _seed_video(svc)
    svc.db.insert(
        "publications",
        {
            "id": "pub_failed",
            "video_id": vid,
            "platform": "youtube",
            "publisher": "RecordingPublisher",
            "mode": "public",
            "idempotency_key": "k-failed",
            "status": "failed",
            "created_at": "2026-10-01T00:00:00+00:00",
            "updated_at": "2026-10-01T00:00:00+00:00",
        },
    )
    client = TestClient(create_app(svc))
    svc.posting.turn_off("owner", "")
    r = client.post("/publications/pub_failed/retry", follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert svc.db.get("publications", "pub_failed")["status"] == "failed"
    page = client.get(r.headers["location"])
    assert "Not done" in page.text and "posting is off" in page.text
    svc.posting.turn_on("owner", "")
    svc.killswitch.engage("test")
    r = client.post(
        "/publications/pub_failed/tiktok",
        data={"privacy_level": "SELF_ONLY", "consent": "yes"},
        follow_redirects=False,
    )
    assert "error=" in r.headers["location"] and "kill" in r.headers["location"]
    assert svc.db.get("publications", "pub_failed")["status"] == "failed"
