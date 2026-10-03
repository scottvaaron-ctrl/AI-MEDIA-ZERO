"""Stage 0 of docs/PLAN_FULL_CONTROL.md (2026-09-30): measurement fixes and the optional revenue scope."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from aimz.domain.models import ExperimentProposal, MetricsSnapshot
from aimz.experiments.allocation import family_stats, per_video
from aimz.experiments.engine import MEASURABLE_KPIS, ExperimentEngine, kpi_field
from aimz.providers.analytics.store import SQLiteAnalyticsProvider
from aimz.util import iso_ago, new_id, now_iso


def _pub(svc: Any, platform: str = "youtube", **kw: Any) -> dict[str, Any]:
    svc.db.execute("PRAGMA foreign_keys=OFF")  # a publication without the idea/script/video chain behind it
    row = {
        "id": new_id("pub"),
        "video_id": kw.pop("video_id", "vid_t"),
        "platform": platform,
        "publisher": "x",
        "mode": "public",
        "idempotency_key": new_id("k"),
        "status": "uploaded",
        "attempts": 1,
        "posted_at": "2026-01-01T00:00:00+00:00",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    row.update(kw)
    svc.db.insert("publications", row)
    return dict(svc.db.get("publications", row["id"]))


def _snaps(svc: Any, pub: dict[str, Any], rows: list[tuple[float, int, float | None]]) -> dict[str, Any]:
    """Record (hours, views, avg_percent_viewed) snapshots; return the latest metrics row."""
    for hours, views, pct in rows:
        svc.analytics_store.record(pub, MetricsSnapshot(views=views, avg_percent_viewed=pct), source="api")
        svc.db.execute(
            "UPDATE metrics SET hours_since_post=? WHERE id=(SELECT id FROM metrics ORDER BY rowid DESC LIMIT 1)",
            [hours],
        )
    return dict(
        svc.db.one(
            "SELECT * FROM metrics WHERE publication_id=? ORDER BY hours_since_post DESC LIMIT 1", [pub["id"]]
        )
    )


# -- 2. score only real data --------------------------------------------------------------


def test_youtube_waits_for_analytics_rows_before_scoring(svc) -> None:  # noqa: ANN001
    pub = _pub(svc)
    latest = _snaps(svc, pub, [(80, 150, None), (100, 170, None)])
    assert svc.analytics_store.scoring_snapshot(latest) is None  # views only: wait
    latest = _snaps(svc, pub, [(110, 180, 41.0)])
    snap = svc.analytics_store.scoring_snapshot(latest)
    assert snap is not None and snap["views"] == 180 and not snap.get("reach_only")


def test_youtube_without_analytics_by_120h_is_scored_on_reach_and_flagged(svc) -> None:  # noqa: ANN001
    pub = _pub(svc)
    latest = _snaps(svc, pub, [(80, 150, None), (130, 160, None), (200, 170, None)])
    snap = svc.analytics_store.scoring_snapshot(latest)
    assert snap is not None and snap["views"] == 160 and snap["reach_only"]


def test_other_platforms_keep_the_72h_rule(svc) -> None:  # noqa: ANN001
    pub = _pub(svc, platform="tiktok")
    latest = _snaps(svc, pub, [(40, 100, None), (80, 150, None), (300, 160, None)])
    snap = svc.analytics_store.scoring_snapshot(latest)
    assert snap is not None and snap["views"] == 150


def test_reach_only_flag_reaches_performance_rows(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _live, _video

    vid = _video(svc, status="published")
    pub = dict(svc.db.get("publications", _live(svc, vid, "youtube", days_old=6)))
    svc.analytics_store.record(pub, MetricsSnapshot(views=90), source="api")
    row = next(r for r in svc.analytics_store.video_performance() if r["video_id"] == vid)
    assert row["score"] is not None and row["score_reach_only"] and not row["score_late"]


# -- 8. age counts from when the video went public -----------------------------------------


def test_hours_count_from_scheduled_publish_and_never_go_negative() -> None:
    pub = {"posted_at": "2026-10-01T10:00:00+00:00", "scheduled_for": "2026-10-01T17:00:00+00:00"}
    live = SQLiteAnalyticsProvider.hours_live
    assert live(pub, at="2026-10-01T12:00:00+00:00") == 0.0  # uploaded private, not public yet
    assert live(pub, at="2026-10-02T17:00:00+00:00") == 24.0
    assert live({"posted_at": "2026-10-01T10:00:00+00:00"}, at="2026-10-01T13:00:00+00:00") == 3.0
    assert live({"posted_at": "2026-10-01T10:00:00"}, at="2026-10-01T13:00:00+00:00") == 3.0  # naive = UTC
    assert live({}, at="2026-10-01T13:00:00+00:00") is None


# -- 1. revenue ------------------------------------------------------------------------------


def test_lifetime_revenue_is_ledgered_once(svc) -> None:  # noqa: ANN001
    pub = _pub(svc, posted_at=iso_ago(days=5))
    for total in (1.0, 1.5, 1.5):
        svc.analytics_store.record(pub, MetricsSnapshot(views=10, revenue_usd=total), source="api")
    booked = svc.db.scalar("SELECT SUM(amount_usd) FROM ledger WHERE entry_type='revenue'", [], 0)
    assert booked == pytest.approx(1.5)


class _Query:
    def __init__(self, result: Any):
        self.result = result

    def execute(self) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Reports:
    def __init__(self, result: Any):
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def query(self, **kw: Any) -> _Query:
        self.calls.append(kw)
        return _Query(self.result)


class _YTA:
    def __init__(self, result: Any):
        self._reports = _Reports(result)

    def reports(self) -> _Reports:
        return self._reports


def test_revenue_rows_are_stored() -> None:
    from aimz.providers.analytics.youtube import YouTubeAnalyticsProvider

    yta = _YTA(
        {
            "columnHeaders": [
                {"name": n} for n in ("estimatedRevenue", "estimatedAdRevenue", "cpm", "playbackBasedCpm")
            ],
            "rows": [[0.42, 0.40, 3.1, 2.9]],
        }
    )
    snap = MetricsSnapshot()
    YouTubeAnalyticsProvider._fetch_revenue(yta, "abc", "2026-10-01", snap)
    assert snap.revenue_usd == pytest.approx(0.42) and snap.raw["revenue"]["cpm"] == 3.1
    assert yta._reports.calls[0]["filters"] == "video==abc"


@pytest.mark.parametrize("result", [PermissionError("403 Forbidden: not monetized"), {"rows": []}])
def test_not_monetized_is_no_revenue_not_an_error(result: Any) -> None:
    from aimz.providers.analytics.youtube import YouTubeAnalyticsProvider

    snap = MetricsSnapshot(views=5)
    YouTubeAnalyticsProvider._fetch_revenue(_YTA(result), "abc", "2026-10-01", snap)
    assert snap.revenue_usd is None and snap.views == 5


def test_stored_token_refreshes_with_its_own_scopes(tmp_path: Path) -> None:
    """Adding the revenue scope to the code must not make the token on disk ask for it (invalid_scope)."""
    from aimz.providers.publishers.youtube import (
        ALL_SCOPES,
        AUTH_SCOPES,
        SCOPE_MONETARY,
        has_scope,
        load_credentials,
    )

    token = tmp_path / "t.json"
    token.write_text(
        json.dumps(
            {
                "token": "a",
                "refresh_token": "r",
                "client_id": "c",
                "client_secret": "s",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ALL_SCOPES,
                "expiry": "2099-01-01T00:00:00Z",  # valid: no network refresh in the test
            }
        ),
        encoding="utf-8",
    )
    creds = load_credentials(token, AUTH_SCOPES)
    assert creds is not None and list(creds.scopes) == ALL_SCOPES
    assert not has_scope(creds, SCOPE_MONETARY)
    assert SCOPE_MONETARY in AUTH_SCOPES and SCOPE_MONETARY not in ALL_SCOPES


# -- 3. one sample per video ------------------------------------------------------------------


def test_a_video_on_two_platforms_is_one_sample() -> None:
    rows = [
        {"video_id": "v1", "platform": "youtube", "content_family": "f", "score": 0.6},
        {"video_id": "v1", "platform": "bluesky", "content_family": "f", "score": None},
        {"video_id": "v1", "platform": "tiktok", "content_family": "f", "score": 0.4},
        {"video_id": "v2", "platform": "bluesky", "content_family": "f", "score": None},
    ]
    one = per_video(rows)
    assert len(one) == 2
    v1 = next(r for r in one if r["video_id"] == "v1")
    assert v1["score"] == pytest.approx(0.5) and v1["platform"] == "youtube"
    assert per_video(one) == one  # idempotent
    assert family_stats(rows, {"f": {}})["f"].n == 1


def test_hook_and_runtime_stats_count_videos(svc) -> None:  # noqa: ANN001
    from aimz.agents.analyst import group_stats, runtime_buckets

    rows = [
        {"video_id": "v1", "hook_type": "numeric", "duration_s": 40, "score": 0.5},
        {"video_id": "v1", "hook_type": "numeric", "duration_s": 40, "score": 0.5},
    ]
    assert group_stats(rows, "hook_type")["numeric"]["n"] == 1
    assert runtime_buckets(rows)["35-60s"]["n"] == 1


# -- 4. arms balance on ideas that can still publish -----------------------------------------


def _proposal(variable: str = "hook_type", kpi: str = "mean_score", **kw: str) -> ExperimentProposal:
    return ExperimentProposal(
        name="n",
        hypothesis="h",
        variable=variable,
        control=kw.get("control", "numeric_hook"),
        treatment=kw.get("treatment", "narrative_hook"),
        primary_kpi=kpi,
    )


def test_rejected_ideas_do_not_count_toward_arm_balance(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    eng = ExperimentEngine(svc.db)
    eid = eng.propose(_proposal())
    exp = dict(svc.db.get("experiments", eid))
    _idea(svc, status="published", experiment_id=eid, experiment_arm="control")
    for _ in range(6):
        _idea(svc, status="rejected", experiment_id=eid, experiment_arm="control")
    _idea(svc, status="published", experiment_id=eid, experiment_arm="treatment")
    _idea(svc, status="scripted", experiment_id=eid, experiment_arm="treatment")
    # Live: control 1, treatment 2, so control is next. Counting rejected ideas (7 vs 2) would pick treatment.
    arm, _ = eng.assign_arm(exp, random.Random(0))
    assert arm == "control"


def test_retire_frees_rejected_ideas_too(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    eng = ExperimentEngine(svc.db)
    eid = eng.propose(_proposal())
    dead = _idea(svc, status="rejected", experiment_id=eid, experiment_arm="control")
    made = _idea(svc, status="published", experiment_id=eid, experiment_arm="treatment")
    eng.retire(str(eid))
    assert svc.db.get("ideas", dead)["experiment_id"] is None
    assert svc.db.get("ideas", made)["experiment_id"] == eid


# -- 5 & 6. only runnable experiments ---------------------------------------------------------


@pytest.mark.parametrize("kpi", ["retention_3s", "3_second_retention", "completion_rate"])
def test_unmeasurable_kpis_are_rejected(svc, kpi: str) -> None:  # noqa: ANN001
    eng = ExperimentEngine(svc.db)
    assert kpi_field(kpi) is None and kpi not in MEASURABLE_KPIS
    assert eng.validate(_proposal(kpi=kpi)) is not None


def test_target_platform_is_not_assignable_and_running_ones_retire(svc) -> None:  # noqa: ANN001
    eng = ExperimentEngine(svc.db)
    prop = _proposal("target_platform", control="tiktok", treatment="youtube_shorts")
    assert eng.validate(prop) is not None
    # one started before this change is retired on the next evaluation
    svc.db.insert(
        "experiments",
        {
            "id": "exp_old",
            "name": "platform",
            "hypothesis": "h",
            "variable": "target_platform",
            "control": "tiktok",
            "treatment": "youtube_shorts",
            "primary_kpi": "views",
            "min_sample": 8,
            "status": "running",
            "started_at": now_iso(),
            "created_by": "ai",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    eng.evaluate_all([])
    assert svc.db.get("experiments", "exp_old")["status"] == "retired"


# -- 7. the hook is written from the assigned type ---------------------------------------------


class _Stop(Exception):
    pass


def _script_prompt(svc: Any, monkeypatch: pytest.MonkeyPatch, idea_id: str) -> str:
    from tests.test_reliability import _orch

    orch = _orch(svc)
    seen: list[str] = []

    def fake_ask(ctx: Any, role: str, user: str, *a: Any, **kw: Any) -> Any:
        seen.append(user)
        raise _Stop

    monkeypatch.setattr(orch.script, "ask", fake_ask)
    idea = dict(svc.db.get("ideas", idea_id))
    with svc.tracker.run("test") as run, pytest.raises(_Stop):
        orch.script.write(run, idea, [], orch.strategy.current())
    return seen[0]


def test_hook_under_test_is_written_from_its_type(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    eid = ExperimentEngine(svc.db).propose(_proposal())
    idea = _idea(
        svc,
        status="selected",
        hook="Here is a story about a ship.",  # written as a narrative hook
        hook_type="numeric_hook",  # the arm forced afterwards
        experiment_id=eid,
        experiment_arm="control",
    )
    prompt = _script_prompt(svc, monkeypatch, idea)
    assert "Here is a story about a ship." not in prompt
    assert "as a numeric_hook hook" in prompt


def test_hook_line_is_kept_outside_hook_experiments(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    idea = _idea(svc, status="selected", hook="Here is a story about a ship.")
    prompt = _script_prompt(svc, monkeypatch, idea)
    assert "Hook: Here is a story about a ship." in prompt
