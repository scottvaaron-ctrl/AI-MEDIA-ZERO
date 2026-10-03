"""Owner posting limits (plan stage 3, owner decision 2026-09-30).

At most 2 posts per platform in a run; a run posts only 2.5 h after the previous posting run; at most 4
posting runs per local calendar day.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.test_autopublish import _seed_video

from aimz.core.errors import ProviderUnavailable, PublishError
from aimz.domain.models import PublishResult
from aimz.pipeline.posting import PostingGate, PostingLimits
from aimz.providers.base import HealthStatus, ProviderContext, Publisher

LIMITS = PostingLimits()


class FakeApi(Publisher):
    is_paid = False
    performs_api_writes = True
    mode = "api"

    def __init__(self, platform: str, fail: Exception | None = None):
        self.platform = platform
        self.name = f"Fake{platform}"
        self.fail = fail
        self.calls: list[str] = []

    def health(self) -> HealthStatus:
        return HealthStatus(True, "fake")

    def publish(
        self, ctx: ProviderContext, video: dict, script: dict, metadata: dict, package_dir: Path
    ) -> PublishResult:
        self.calls.append(video["id"])
        if self.fail:
            raise self.fail
        return PublishResult(status="uploaded", platform_video_id=f"{self.platform}-{video['id']}", url="u")


def _setup(
    svc: Any, n_videos: int, platforms: tuple[str, ...] = ("youtube", "bluesky")
) -> tuple[Any, list[str]]:
    from aimz.pipeline.orchestrator import Orchestrator

    svc.env = dataclasses.replace(svc.env, autopublish_consent=True)
    svc.publishers = {p: FakeApi(p) for p in platforms}
    vids = []
    for _ in range(n_videos):
        vids.append(_seed_video(svc))
    return Orchestrator(svc, seed=1), vids


def _age_runs(svc: Any, hours: float) -> None:
    """Pretend every posting run happened ``hours`` earlier (same local day unless it crosses midnight)."""
    for r in svc.db.query("SELECT * FROM posting_runs"):
        t = datetime.fromisoformat(r["started_at"]) - timedelta(hours=hours)
        svc.db.update("posting_runs", r["id"], {"started_at": t.isoformat()})


def test_limits_come_from_config(svc) -> None:  # noqa: ANN001
    lim = PostingLimits.from_config(svc.config)
    assert (lim.posts_per_run_per_platform, lim.min_hours_between_runs, lim.max_runs_per_day) == (2, 2.5, 2)
    assert lim.posts_per_day_per_platform == 4


def test_a_run_posts_at_most_two_per_platform_and_the_rest_wait(svc) -> None:  # noqa: ANN001
    orch, vids = _setup(svc, 5)
    with svc.tracker.run("cycle") as run:
        orch.publish_rendered(run)
    for p in ("youtube", "bluesky"):
        assert svc.publishers[p].calls == vids[:2]  # oldest first
    assert [svc.db.get("videos", v)["status"] for v in vids] == ["published"] * 2 + ["rendered"] * 3
    row = dict(svc.db.one("SELECT * FROM posting_runs"))
    assert row["counts_json"] == '{"youtube": 2, "bluesky": 2}'


def test_the_next_run_waits_two_and_a_half_hours(svc) -> None:  # noqa: ANN001
    orch, vids = _setup(svc, 5)
    with svc.tracker.run("cycle") as run:
        orch.publish_rendered(run)
    with svc.tracker.run("cycle") as run:
        assert orch.publish_rendered(run) == []
        assert "needs 2.5 h" in run.summary["posting"]["reason"]
    assert len(svc.publishers["youtube"].calls) == 2
    _age_runs(svc, 2.6)
    with svc.tracker.run("cycle") as run:
        orch.publish_rendered(run)
    assert svc.publishers["youtube"].calls == vids[:4]


def test_two_posting_runs_per_local_day() -> None:
    class Db:
        def __init__(self) -> None:
            self.rows: list[dict[str, Any]] = []

        def one(self, sql: str, params: Any = None) -> Any:
            return max(self.rows, key=lambda r: r["started_at"]) if self.rows else None

        def count(self, table: str, where: str, params: list[str]) -> int:
            return sum(1 for r in self.rows if r["local_day"] == params[0])

    db = Db()
    gate = PostingGate(db, LIMITS)  # type: ignore[arg-type]
    morning = datetime.now().astimezone().replace(hour=6, minute=0, second=0, microsecond=0).astimezone(UTC)
    for k in range(2):
        t = morning + timedelta(hours=3 * k)
        assert gate.check(t)[0]
        db.rows.append({"started_at": t.isoformat(), "local_day": t.astimezone().strftime("%Y-%m-%d")})
    later = morning + timedelta(hours=9)
    ok, why = gate.check(later)
    assert not ok and "2 posting runs today" in why
    tomorrow = morning + timedelta(days=1)
    assert gate.check(tomorrow)[0]


def test_a_simulated_day_never_breaks_the_limits(svc) -> None:  # noqa: ANN001
    """Six videos waiting, a run attempted every hour: gap and caps hold; four go out, two wait for tomorrow."""
    _, vids = _setup(svc, 6)
    gate = PostingGate(svc.db, LIMITS)
    start = datetime.now().astimezone().replace(hour=6, minute=0, second=0, microsecond=0).astimezone(UTC)
    waiting = list(vids)
    posted_at: list[datetime] = []
    for h in range(18):  # 06:00 to 23:00 local
        now = start + timedelta(hours=h)
        session = gate.open("run", now)
        n = 0
        while waiting and session.can_post("youtube"):
            waiting.pop(0)
            session.record("youtube", now)
            n += 1
        if n:
            posted_at.append(now)
            assert n <= 2
    assert len(waiting) == 2
    gaps = [(b - a).total_seconds() / 3600 for a, b in zip(posted_at, posted_at[1:], strict=False)]
    assert all(g >= 2.5 for g in gaps) and len(posted_at) == 2


def test_retries_count_and_wait_for_a_run_that_may_post(svc) -> None:  # noqa: ANN001
    orch, vids = _setup(svc, 3, platforms=("youtube",))
    svc.publishers["youtube"].fail = PublishError("timed out mid-upload")  # uncertain: may have posted
    with svc.tracker.run("cycle") as run:
        orch.publish_rendered(run)
    assert len(svc.publishers["youtube"].calls) == 2  # both attempts count against the run
    assert svc.db.count("publications", "status='failed'") == 2


def test_a_failure_that_never_reached_the_platform_does_not_count(svc) -> None:  # noqa: ANN001
    orch, _ = _setup(svc, 3, platforms=("youtube",))
    svc.publishers["youtube"].fail = ProviderUnavailable("login expired")
    with svc.tracker.run("cycle") as run:
        orch.publish_rendered(run)
    assert len(svc.publishers["youtube"].calls) == 3
    assert svc.db.count("posting_runs") == 0  # nothing posted, no run slot used


def test_a_platform_held_back_catches_up_next_run(svc) -> None:  # noqa: ANN001
    orch, vids = _setup(svc, 2)
    session_limits = {"posts_per_run_per_platform": 2, "min_hours_between_runs": 2.5, "max_runs_per_day": 4}
    svc.config.raw["publishing"]["limits"] = session_limits
    publisher = orch.publisher
    with svc.tracker.run("cycle") as run, publisher.posting(run) as session:
        session.counts["bluesky"] = 2  # bluesky's posts for this run are already used up
        for v in vids:
            publisher.publish(run, v)
    assert svc.publishers["bluesky"].calls == []
    assert all(svc.db.get("videos", v)["status"] == "published" for v in vids)
    _age_runs(svc, 3)
    with svc.tracker.run("cycle") as run:
        orch.publish_rendered(run)
    assert svc.publishers["bluesky"].calls == vids
    assert len(svc.publishers["youtube"].calls) == 2  # not posted twice


def test_production_pauses_when_two_days_of_posts_are_waiting(svc) -> None:  # noqa: ANN001
    orch, _ = _setup(svc, 16)
    with svc.tracker.run("cycle") as run:
        assert orch.produce_approved(run) == []
        assert "waiting to post" in run.summary["produce"]["paused"]


def test_owner_resend_respects_the_limits_and_changes_nothing(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    orch, vids = _setup(svc, 1, platforms=("youtube",))
    svc.publishers["youtube"].fail = PublishError("boom")
    with svc.tracker.run("cycle") as run:
        orch.publish_rendered(run)
    pub = dict(svc.db.one("SELECT * FROM publications"))
    monkeypatch.setattr("aimz.cli._svc", lambda quiet=False: svc)
    monkeypatch.setattr(svc, "close", lambda: None)
    result = CliRunner().invoke(app, ["publish", "retry", pub["id"]])
    assert "not sent now" in result.output
    assert svc.db.get("publications", pub["id"])["status"] == "failed"


@pytest.mark.parametrize(
    ("times", "fragment"),
    [
        (["09:00", "18:00"], None),
        (["09:00", "12:00", "15:00", "18:00"], "4 runs a day"),
        (["08:00", "10:00"], "2 h apart"),
        (["09:00", "12:00", "15:00", "18:00", "21:00"], "5 runs a day"),
        (["23:00", "01:00"], "2 h apart"),  # across midnight
    ],
)
def test_schedules_are_checked_against_the_limits(times: list[str], fragment: str | None) -> None:
    from aimz.pipeline.posting import check_schedule

    problem = check_schedule(times, LIMITS)
    assert (problem is None) if fragment is None else (problem is not None and fragment in problem)
