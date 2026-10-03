"""Stage 1 of docs/PLAN_FULL_CONTROL.md: every video records and tests every open setting."""

from __future__ import annotations

import json
import random
from typing import Any

import pytest

from aimz.domain.models import NicheMerge, SettingChange, StrategyUpdate
from aimz.experiments import niches
from aimz.experiments.settings import CATALOG, FLOOR_SCORED, SettingsEngine, enc
from aimz.util import now_iso

# The dials this test machine can use (stock video needs the owner's API keys).
AVAILABLE = {k for k, d in CATALOG.items() if d.requires in {"", "sfx"}}

# -- the space and the AI's control --------------------------------------------------------------


def test_catalog_starts_off_and_sync_keeps_the_ais_choices(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    space = eng.space()
    assert set(space) == AVAILABLE and all(r["status"] == "off" for r in space.values())
    assert eng.change("speech_length_scale", "open", [0.9, 1.1]) is None
    SettingsEngine(svc.db).sync_catalog()  # a restart re-syncs definitions
    row = SettingsEngine(svc.db).space()["speech_length_scale"]
    assert row["status"] == "open" and row["values"] == [0.9, 1.1]


@pytest.mark.parametrize(
    ("variable", "action", "values", "locked", "fragment"),
    [
        ("speech_length_scale", "open", [0.5, 1.0], None, "outside the engineering bounds"),
        ("speech_length_scale", "open", [1.0], None, "2-4 distinct values"),
        ("speech_length_scale", "open", [1.0, 1.0], None, "2-4 distinct values"),
        ("speech_length_scale", "open", [0.8, 0.9, 1.0, 1.1, 1.2], None, "2-4 distinct values"),
        ("beat_pause_s", "lock", [], "slow", "not a number"),
        ("voice_color", "open", [1, 2], None, "not a setting"),
        ("beat_pause_s", "shuffle", [], None, "open, lock or off"),
    ],
)
def test_out_of_bounds_changes_are_refused(svc, variable, action, values, locked, fragment) -> None:  # noqa: ANN001
    why = SettingsEngine(svc.db).change(variable, action, values, locked)
    assert why is not None and fragment in why
    assert SettingsEngine(svc.db).space().get("beat_pause_s", {}).get("status") == "off"


# -- assignment ------------------------------------------------------------------------------------


def _video(svc: Any, vid: str, status: str = "rendered") -> None:
    svc.db.execute("PRAGMA foreign_keys=OFF")
    svc.db.insert(
        "videos",
        {
            "id": vid,
            "script_id": "s",
            "idea_id": "i",
            "title": "t",
            "format": "short",
            "resolution": "1080x1920",
            "status": status,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )


IDEA = {"content_family": "space", "angle": "firsts", "hook_type": "numeric_hook"}
DEFAULTS = {"speech_length_scale": 1.0, "sentence_pause_s": 0.25, "beat_pause_s": 0.35}


def test_every_video_gets_a_full_row_set(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    eng.change("speech_length_scale", "open", [0.9, 1.1])
    eng.change("beat_pause_s", "lock", locked_value=0.6)
    a = eng.assign("vid_1", IDEA, DEFAULTS, [], random.Random(1))
    assert a.values["speech_length_scale"] in {0.9, 1.1}
    assert a.sources["speech_length_scale"] in {"random", "bandit"}
    assert (a.values["beat_pause_s"], a.sources["beat_pause_s"]) == (0.6, "locked")
    assert (a.values["sentence_pause_s"], a.sources["sentence_pause_s"]) == (0.25, "default")
    rows = {
        r["variable"]: dict(r) for r in svc.db.query("SELECT * FROM video_settings WHERE video_id='vid_1'")
    }
    assert set(rows) == {*AVAILABLE, "niche", "angle", "hook_type"}
    assert rows["niche"]["source"] == "ai" and json.loads(rows["niche"]["value"]) == "space"


def _scored(svc: Any, var: str, value: Any, scores: list[float], start: int) -> list[dict[str, Any]]:
    out = []
    for i, s in enumerate(scores):
        vid = f"vid_{start + i}"
        _video(svc, vid, "published")
        svc.db.execute(
            "INSERT INTO video_settings (video_id, variable, value, source, created_at) VALUES (?,?,?,?,?)",
            [vid, var, enc(value), "bandit", now_iso()],
        )
        out.append({"video_id": vid, "score": s, "content_family": "space", "duration_s": 40})
    return out


def test_exploration_floor_keeps_a_new_value_in_play(svc) -> None:  # noqa: ANN001
    """A value with no data still gets at least 1/(2k) of videos while another already looks best."""
    eng = SettingsEngine(svc.db)
    eng.change("speech_length_scale", "open", [0.9, 1.0, 1.2])
    scored = _scored(svc, "speech_length_scale", 0.9, [0.40] * 10, 0)
    scored += _scored(svc, "speech_length_scale", 1.0, [0.20] * 10, 100)
    rng = random.Random(3)
    picks = [eng._choose("speech_length_scale", [0.9, 1.0, 1.2], scored, rng)[0] for _ in range(600)]
    share_new = picks.count(1.2) / len(picks)
    assert share_new >= 1 / 6 - 0.03
    assert picks.count(0.9) > picks.count(1.0)  # the bandit favours the better-scoring value


def test_bandit_exploits_once_every_value_has_enough_data(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    scored = _scored(svc, "beat_pause_s", 0.2, [0.40, 0.42, 0.38] * 4, 0)
    scored += _scored(svc, "beat_pause_s", 0.8, [0.20, 0.22, 0.18] * 4, 100)
    assert min(len(v) for v in eng.scores_by_value("beat_pause_s", scored).values()) >= FLOOR_SCORED
    rng = random.Random(0)
    picks = [eng._choose("beat_pause_s", [0.2, 0.8], scored, rng) for _ in range(200)]
    assert all(src == "bandit" for _, src in picks)
    assert sum(1 for v, _ in picks if v == 0.2) > 190


# -- analysis ---------------------------------------------------------------------------------------


def test_value_stats_report_probability_of_being_best(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    scored = _scored(svc, "sentence_pause_s", 0.1, [0.30, 0.32, 0.31, 0.29], 0)
    scored += _scored(svc, "sentence_pause_s", 0.5, [0.20, 0.21, 0.19, 0.22], 100)
    stats = {st.value: st for st in eng.value_stats(scored)["sentence_pause_s"]}
    assert stats[0.1].p_best is not None and stats[0.1].p_best > 0.9
    assert stats[0.1].p_best + stats[0.5].p_best == pytest.approx(1.0)


def test_regression_recovers_a_setting_effect_with_niche_controls(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    rng = random.Random(5)
    rows: list[dict[str, Any]] = []
    for i in range(80):
        vid = f"vid_{i}"
        _video(svc, vid, "published")
        speed = [1.0, 1.2][i % 2]
        pause = [0.2, 0.6][(i // 2) % 2]  # no real effect
        niche = ["space", "history", "sport"][i % 3]
        for var, val in (("speech_length_scale", speed), ("beat_pause_s", pause)):
            svc.db.execute(
                "INSERT INTO video_settings (video_id, variable, value, source, created_at) VALUES (?,?,?,?,?)",
                [vid, var, enc(val), "bandit", now_iso()],
            )
        score = 0.25 + (0.05 if speed == 1.2 else 0) + {"space": 0.05, "history": 0, "sport": -0.03}[niche]
        rows.append(
            {"video_id": vid, "score": score + rng.gauss(0, 0.02), "content_family": niche, "duration_s": 40}
        )
    reg = eng.regression(rows)
    assert reg is not None and reg["n"] == 80
    eff = {(e["variable"], e["value"]): e for e in reg["effects"]}
    speed = eff[("speech_length_scale", 1.2)]
    assert speed["ci95"][0] > 0.02 and speed["ci95"][1] < 0.08
    pause = eff[("beat_pause_s", 0.6)]
    assert pause["ci95"][0] < 0 < pause["ci95"][1]
    assert "Regression on 80 scored videos" in eng.evidence_text(rows)


def test_no_regression_below_thirty_videos(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    rows = _scored(svc, "speech_length_scale", 1.0, [0.3] * 10, 0)
    assert eng.regression(rows) is None
    assert "Regression: not yet (10 scored videos" in eng.evidence_text(rows)


# -- the strategist ----------------------------------------------------------------------------------


def _analyst(svc: Any) -> Any:
    from tests.test_reliability import _orch

    return _orch(svc).analyst


def test_strategist_changes_apply_and_refusals_are_reported(svc) -> None:  # noqa: ANN001
    upd = StrategyUpdate(
        audience_model="x",
        change_summary="x",
        setting_changes=[
            SettingChange(variable="speech_length_scale", action="open", values=[0.9, 1.1]),
            SettingChange(variable="beat_pause_s", action="lock", locked_value=5.0),
        ],
    )
    _analyst(svc).apply_setting_changes(upd)
    space = SettingsEngine(svc.db).space()
    assert space["speech_length_scale"]["status"] == "open"
    assert space["beat_pause_s"]["status"] == "off"
    refused = svc.db.get_state("last_rejected_setting") or ""
    assert "beat_pause_s" in refused and "bounds" in refused


def test_strategy_memory_shows_settings_evidence(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _orch

    orch = _orch(svc)
    with svc.tracker.run("learn") as run:
        orch.analyst.learn(run, orch.strategy.current())
    md = (svc.env.config_dir / "strategy.md").read_text(encoding="utf-8")
    assert "## Settings evidence" in md and "speech_length_scale" in md


# -- niches -------------------------------------------------------------------------------------------


def test_niche_merge_relabels_live_ideas_and_counts_history_together(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea, _live
    from tests.test_reliability import _video as rel_video

    from aimz.domain.models import MetricsSnapshot

    live = _idea(svc, family="forgotten_history")
    old_vid = rel_video(svc, status="published", family="forgotten_history")
    svc.db.update("ideas", svc.db.get("videos", old_vid)["idea_id"], {"status": "published"})
    pub = dict(svc.db.get("publications", _live(svc, old_vid, "youtube", days_old=5)))
    svc.analytics_store.record(pub, MetricsSnapshot(views=100, avg_percent_viewed=40.0), source="api")
    assert niches.merge(svc.db, "forgotten_history", "history", "same niche") is None
    assert svc.db.get("ideas", live)["content_family"] == "history"
    published_idea = svc.db.get("videos", old_vid)["idea_id"]
    assert svc.db.get("ideas", published_idea)["content_family"] == "forgotten_history"  # history kept
    row = next(r for r in svc.analytics_store.video_performance() if r["video_id"] == old_vid)
    assert row["content_family"] == "history" and row["niche_label"] == "forgotten_history"
    # chains flatten, unknown labels are refused
    assert niches.merge(svc.db, "history", "past") is None
    assert niches.aliases(svc.db)["forgotten_history"] == "past"
    assert niches.merge(svc.db, "never_used", "past") is not None


def test_analyst_merges_niches_and_drops_the_alias_family(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    _idea(svc, family="unusual_historical_events")
    upd = StrategyUpdate(
        audience_model="x",
        change_summary="x",
        niche_merges=[
            NicheMerge(alias="unusual_historical_events", into="history"),
            NicheMerge(alias="made_up", into="history"),
        ],
    )
    merged = _analyst(svc).apply_setting_changes(upd)
    assert merged == ["unusual_historical_events"]
    assert "made_up" in (svc.db.get_state("last_rejected_setting") or "")


# -- end to end -----------------------------------------------------------------------------------------


def test_a_cycle_renders_with_the_settings_it_recorded(svc, ffmpeg_available: bool) -> None:  # noqa: ANN001
    from tests.test_pipeline_offline import FixtureFeedProvider

    from aimz.pipeline.orchestrator import Orchestrator

    eng = SettingsEngine(svc.db)
    eng.change("speech_length_scale", "open", [0.85, 1.15])
    eng.change("beat_pause_s", "lock", locked_value=1.2)
    fp = FixtureFeedProvider()
    svc.research = {k: fp for k in fp.kinds}
    Orchestrator(svc, seed=7).cycle()
    videos = [dict(r) for r in svc.db.query("SELECT * FROM videos")]
    assert videos, "the fixture cycle should reach production"
    for v in videos:
        rows = {
            r["variable"]: dict(r)
            for r in svc.db.query("SELECT * FROM video_settings WHERE video_id=?", [v["id"]])
        }
        assert set(rows) >= AVAILABLE
        assert json.loads(rows["speech_length_scale"]["value"]) in {0.85, 1.15}
        assert json.loads(rows["beat_pause_s"]["value"]) == 1.2
    if ffmpeg_available:
        rendered = [v for v in videos if v["status"] in {"rendered", "published"}]
        assert rendered
        durations = [
            float(r["duration_s"])
            for r in svc.db.query("SELECT duration_s FROM scenes WHERE video_id=?", [rendered[0]["id"]])
        ]
        assert durations and min(durations) >= 1.2  # every beat ends with the locked pause
