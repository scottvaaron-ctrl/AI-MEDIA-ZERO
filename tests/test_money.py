"""Stage 6 of docs/PLAN_FULL_CONTROL.md: score parts that point at money, niche value, Partner Program."""

from __future__ import annotations

import json
from typing import Any

import pytest

from aimz.domain.models import MetricsSnapshot
from aimz.experiments.value import niche_values, ypp_progress
from aimz.providers.analytics.store import performance_score, score_parts, score_weights

ROW = {
    "views": 1000,
    "avg_percent_viewed": 60.0,
    "shares": 5,
    "subscribers_gained": 2,
    "watch_time_minutes": 400.0,
}


def test_new_parts_are_shown_but_weightless_by_default(svc) -> None:  # noqa: ANN001
    weights = score_weights(svc.config)
    assert weights["watch_time"] == 0 and weights["revenue"] == 0
    parts = score_parts({**ROW, "revenue_usd": 0.05})
    assert parts is not None and parts["watch_time"] == pytest.approx(0.4)
    assert parts["revenue"] == pytest.approx(0.05 * 1000 / 1200 / 0.20)
    base = {k: v for k, v in weights.items() if k not in {"watch_time", "revenue"}}
    assert performance_score({**ROW, "revenue_usd": 0.05}, weights) == pytest.approx(
        performance_score(ROW, base)
    )


def test_owner_weights_change_the_score(svc) -> None:  # noqa: ANN001
    svc.config.raw["scoring"] = {"weights": {"revenue": 1.0}}
    weights = score_weights(svc.config)
    rich = performance_score({**ROW, "revenue_usd": 0.2}, weights)
    poor = performance_score({**ROW, "revenue_usd": 0.0}, weights)
    assert rich is not None and poor is not None and rich > poor


def _row(
    niche: str, views: int, pct: float, revenue: float | None = None, score: float = 0.3
) -> dict[str, Any]:
    snap = {"views": views, "avg_percent_viewed": pct, "revenue_usd": revenue}
    return {"content_family": niche, "score": score, "scored_metrics": snap, "video_id": f"{niche}{views}"}


def test_niches_rank_by_watch_until_revenue_is_known() -> None:
    rows = [_row("space", 1000, 30.0), _row("space", 500, 40.0), _row("sport", 300, 90.0)]
    ranked = niche_values(rows)
    # space: 750 views x 35% watched = 262.5; sport: 300 x 90% = 270, so fewer but better-watched views win
    assert [v.niche for v in ranked] == ["sport", "space"]
    assert ranked[1].watch_value == pytest.approx(750 * 0.35)
    rows = [_row("space", 1000, 30.0, 0.01), _row("sport", 300, 90.0, 0.05)]
    ranked = niche_values(rows)
    assert [v.niche for v in ranked] == ["sport", "space"] and ranked[0].revenue_per_1000 == pytest.approx(
        0.1667, abs=1e-3
    )


def test_partner_program_progress(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _live, _video

    recent = _video(svc, status="published")
    old = _video(svc, status="published")
    for vid, days, views in ((recent, 10, 900), (old, 120, 5000)):
        pub = dict(svc.db.get("publications", _live(svc, vid, "youtube", days_old=days)))
        svc.analytics_store.record(pub, MetricsSnapshot(views=views), source="api")
    svc.db.set_state("youtube_channel_stats", json.dumps({"subscriberCount": "2", "viewCount": "6000"}))
    p = ypp_progress(svc.db)
    assert p["subscribers"] == 2 and p["shorts_views_90d"] == 900 and not p["eligible"]


def test_channel_statistics_are_collected(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _orch

    class FakeYT:
        platform = "youtube"

        def channel_statistics(self, ctx: Any) -> dict[str, str]:
            return {"subscriberCount": "7", "viewCount": "100"}

        def fetch_metrics(self, ctx: Any, pub: Any) -> None:
            return None

    svc.remote_analytics = {"youtube": FakeYT()}
    orch = _orch(svc)
    with svc.tracker.run("m") as run:
        orch.analyst.collect_metrics(run)
    assert json.loads(svc.db.get_state("youtube_channel_stats") or "{}")["subscriberCount"] == "7"
