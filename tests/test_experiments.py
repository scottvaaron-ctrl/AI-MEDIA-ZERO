from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from aimz import guideline
from aimz.domain.models import ExperimentProposal, IdeaBatch, IdeaScores, SourceItemView
from aimz.experiments.engine import ExperimentEngine, kpi_value, summarize, welch_confidence
from aimz.experiments.settings import CATALOG
from aimz.util import new_id, now_iso

PROP = ExperimentProposal(
    name="numeric vs narrative",
    hypothesis="numbers hook better",
    variable="hook_type",
    control="narrative_hook",
    treatment="numeric_hook",
    primary_kpi="avg_percent_viewed",
)


def _rows(exp_id: str, control: list[float], treatment: list[float]) -> list[dict]:
    rows = []
    for v in control:
        rows.append(
            {
                "experiment_id": exp_id,
                "experiment_arm": "control",
                "avg_percent_viewed": v,
                "views": 500,
                "shares": 5,
            }
        )
    for v in treatment:
        rows.append(
            {
                "experiment_id": exp_id,
                "experiment_arm": "treatment",
                "avg_percent_viewed": v,
                "views": 500,
                "shares": 5,
            }
        )
    return rows


def test_no_conclusion_below_min_sample(svc) -> None:  # noqa: ANN001
    eng = ExperimentEngine(svc.db, default_min_sample=4, max_concurrent=2, min_confidence=0.85)
    eid = eng.propose(PROP)
    assert eid
    res = eng.evaluate(dict(svc.db.get("experiments", eid)), _rows(eid, [30, 31], [70, 72]))
    assert res["status"] == "running" and not res["enough_sample"]


def test_concludes_with_enough_evidence(svc) -> None:  # noqa: ANN001
    eng = ExperimentEngine(svc.db, default_min_sample=4, max_concurrent=2, min_confidence=0.85)
    eid = eng.propose(PROP)
    res = eng.evaluate(
        dict(svc.db.get("experiments", eid)), _rows(eid, [30, 32, 29, 31, 33], [70, 68, 72, 71, 69])
    )
    assert res["status"] == "concluded" and res["p_treatment_better"] > 0.95
    assert "numeric_hook" in (res["conclusion"] or "")
    assert svc.db.get("experiments", eid)["status"] == "concluded"


def test_no_effect_concludes_after_triple_sample(svc) -> None:  # noqa: ANN001
    eng = ExperimentEngine(svc.db, default_min_sample=2, max_concurrent=2, min_confidence=0.85)
    eid = eng.propose(PROP)
    rows = _rows(eid, [50, 52, 49, 51, 50, 52], [50, 51, 49, 52, 50, 51])
    res = eng.evaluate(dict(svc.db.get("experiments", eid)), rows)
    assert res["status"] == "concluded" and "No detectable effect" in res["conclusion"]


def test_balanced_arm_assignment_and_concurrency(svc) -> None:  # noqa: ANN001
    from aimz.util import now_iso

    eng = ExperimentEngine(svc.db, default_min_sample=4, max_concurrent=1)
    eid = eng.propose(PROP)
    assert eng.propose(PROP) is None  # duplicate variable / concurrency cap
    exp = dict(svc.db.get("experiments", eid))
    rng = random.Random(0)
    arms = []
    for i in range(6):
        arm, value = eng.assign_arm(exp, rng)
        arms.append(arm)
        svc.db.insert(
            "ideas",
            {
                "id": f"idea_{i}",
                "title": "t",
                "premise": "p",
                "hook": "h",
                "content_family": "x",
                "target_platform": "both",
                "source_item_ids_json": "[]",
                "scores_json": "{}",
                "opportunity_score": 1,
                "experiment_id": eid,
                "experiment_arm": arm,
                "hook_type": value,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
    assert arms.count("control") == 3 and arms.count("treatment") == 3


def test_kpi_helpers() -> None:
    row = {"views": 1000, "shares": 12, "subscribers_gained": 5, "avg_percent_viewed": 55.0}
    assert kpi_value(row, "shares_per_1000") == 12
    assert kpi_value(row, "subs_per_1000") == 5
    assert kpi_value(row, "3_second_retention") is None
    a, b = summarize([1, 1, 1]), summarize([1, 1, 1])
    assert welch_confidence(a, b) == 0.5


def test_unassignable_experiments_are_rejected(svc) -> None:  # noqa: ANN001
    eng = ExperimentEngine(svc.db, hook_types=["numeric_hook", "narrative_hook"])
    bad = ExperimentProposal(
        name="x",
        hypothesis="h",
        variable="Script complexity",
        control="simple",
        treatment="complex",
        primary_kpi="views",
    )
    assert eng.validate(bad) is not None and eng.propose(bad) is None
    bad_hook = PROP.model_copy(update={"treatment": "mystery_hook"})
    assert eng.propose(bad_hook) is None
    assert eng.propose(PROP) is not None
    assert svc.db.count("experiments") == 1


# == Task 4: one experiment per channel on a guideline item ===============================

GSAMPLE = (Path(__file__).parent / "fixtures" / "guideline_sample.md").read_text(encoding="utf-8")
LOW = [48, 50, 52, 49, 51, 50, 47, 53, 50, 51]
HIGH = [68, 70, 72, 69, 71, 70, 67, 73, 70, 71]


def _activate(svc: Any, text: str = GSAMPLE) -> guideline.Guideline:
    (svc.env.config_dir / "guideline.md").write_text(text, encoding="utf-8")
    g = guideline.for_services(svc)
    assert g is not None
    return g


def _geng(svc: Any, text: str = GSAMPLE) -> ExperimentEngine:
    return ExperimentEngine(svc.db, guideline=_activate(svc, text))


def _gp(item: str, variant: str) -> ExperimentProposal:
    return ExperimentProposal(
        name=f"try {item}",
        hypothesis="h",
        variable=item,
        control="",
        treatment=variant,
        primary_kpi="score",
        guideline_item=item,
        variant=variant,
    )


def _grows(exp_id: str, control: list[float], treatment: list[float]) -> list[dict]:
    rows = []
    for arm, vals, pct in (("control", control, 40.0), ("treatment", treatment, 60.0)):
        for v in vals:
            rows.append(
                {
                    "experiment_id": exp_id,
                    "experiment_arm": arm,
                    "video_id": new_id("vid"),
                    "score": v,
                    "scored_metrics": {"avg_percent_viewed": pct, "views": 500},
                }
            )
    return rows


def test_validate_unknown_item(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    assert eng.validate(_gp("G-nope", "anything")) == "unknown guideline item"
    assert eng.validate(_gp("hook_type", "numeric_hook")) == "unknown guideline item"


def test_validate_out_of_bounds_variant(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    why = eng.validate(_gp("G-caption_size", "0.5"))
    assert why is not None and why.startswith("variant out of bounds: ")
    why = eng.validate(_gp("G-runtime", "600"))
    assert why is not None and why.startswith("variant out of bounds: ")
    assert eng.validate(_gp("G-caption_size", "0.05")) is None


def test_validate_missing_capability(svc) -> None:  # noqa: ANN001
    text = GSAMPLE.replace("  music: off\n", "  music: off\n  clip_scenes: on\n")
    eng = _geng(svc, text)
    assert eng.guideline is not None and "clip_scenes" in eng.guideline.unavailable
    assert eng.validate(_gp("G-clip_scenes", "off")) == "variant needs stock_video, which is not installed"


def test_validate_one_running(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    assert eng.propose(_gp("G-hook", "Open on a question the video answers."))
    second = _gp("G-title", "Titles ask a question.")
    assert eng.validate(second) == "an experiment is already running on this channel"
    assert eng.propose(second) is None


def _published(svc: Any, n: int) -> None:
    from tests.test_reliability import _video

    for _ in range(n):
        _video(svc, status="published")


def test_validate_cooldown(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    old = "2026-01-01T00:00:00+00:00"
    svc.db.insert(
        "experiments",
        {
            "id": "exp_old",
            "name": "old",
            "hypothesis": "h",
            "variable": "G-hook",
            "control": "c",
            "treatment": "t",
            "primary_kpi": "score",
            "min_sample": 10,
            "status": "inconclusive",
            "concluded_at": old,
            "created_by": "ai",
            "created_at": old,
            "updated_at": old,
        },
    )
    _published(svc, 5)
    assert eng.cooldown_left("G-hook") == 15
    assert eng.validate(_gp("G-hook", "Open on a question.")) == "G-hook is cooling down for 15 more videos"
    # A rejected proposal blocks its item too (decided after every video so far).
    svc.db.insert(
        "guideline_proposals",
        {
            "id": "gp_1",
            "item": "G-title",
            "before": "b",
            "after": "a",
            "evidence_json": "{}",
            "summary": "s",
            "status": "rejected",
            "created_at": now_iso(),
            "decided_at": "2999-01-01T00:00:00+00:00",
        },
    )
    assert eng.cooldown_left("G-title") == 20
    assert eng.cooldown_left("G-cover") == 0
    _published(svc, 15)
    assert eng.cooldown_left("G-hook") == 0
    assert eng.validate(_gp("G-hook", "Open on a question.")) is None


def test_validate_constitution_screen(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    for bad in (
        "Invent a quote when the sources are thin.",
        "Use a clickbait title.",
        "Fabricate a statistic.",
    ):
        assert eng.validate(_gp("G-hook", bad)) == "variant contradicts the constitution"


def test_guideline_proposal_stored_values(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    eid = eng.propose(_gp("G-hook", "Open on a question the video answers."))
    assert eid and eng.guideline is not None
    row = svc.db.get("experiments", eid)
    assert row["variable"] == "G-hook"
    assert row["control"] == "The first sentence states the most surprising fact. No greetings."
    assert row["treatment"] == "Open on a question the video answers."
    assert row["guideline_version"] == eng.guideline.version
    assert row["min_sample"] == 10 and row["status"] == "running"
    idea = {"experiment_id": eid, "experiment_arm": "treatment"}
    assert guideline.variant_for(idea, svc.db) == {"G-hook": "Open on a question the video answers."}


def _run_eval(svc: Any, control: list[float], treatment: list[float]) -> dict[str, Any]:
    svc.db.execute("DELETE FROM experiments")
    eng = _geng(svc)
    eid = eng.propose(_gp("G-hook", "Open on a question the video answers."))
    assert eid
    return eng.evaluate(dict(svc.db.get("experiments", eid)), _grows(eid, control, treatment))


def test_variant_wins_at_95_with_10(svc) -> None:  # noqa: ANN001
    res = _run_eval(svc, LOW, HIGH)
    assert res["status"] == "variant_won" and res["p_treatment_better"] >= 0.95
    assert svc.db.scalar("SELECT concluded_at FROM experiments") is not None


def test_not_decided_at_9(svc) -> None:  # noqa: ANN001
    assert _run_eval(svc, LOW[:9], HIGH[:9])["status"] == "running"


def test_guideline_wins(svc) -> None:  # noqa: ANN001
    res = _run_eval(svc, HIGH, LOW)
    assert res["status"] == "concluded" and res["conclusion"].startswith("Guideline won")


def test_safety_stop_at_6(svc) -> None:  # noqa: ANN001
    res = _run_eval(svc, HIGH[:6], LOW[:6])
    assert res["status"] == "concluded" and res["conclusion"].startswith("Safety stop")
    assert _run_eval(svc, HIGH[:5], LOW[:5])["status"] == "running"


def test_inconclusive_at_30(svc) -> None:  # noqa: ANN001
    same = (LOW * 3)[:30]
    res = _run_eval(svc, same, list(reversed(same)))
    assert res["status"] == "inconclusive"
    assert svc.db.scalar("SELECT concluded_at FROM experiments") is not None
    assert _run_eval(svc, same[:29], same[:29])["status"] == "running"


def test_reports_avg_percent_viewed(svc) -> None:  # noqa: ANN001
    res = _run_eval(svc, LOW[:3], HIGH[:3])
    assert res["control"]["mean_avg_percent_viewed"] == 40
    assert res["treatment"]["mean_avg_percent_viewed"] == 60
    assert res["kpi"] == "score"


def test_setting_override_only_on_treatment(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    assert eng.setting_override({"experiment_id": None}) is None
    eid = eng.propose(_gp("G-caption_size", "0.05"))
    assert eid
    treat = {"experiment_id": eid, "experiment_arm": "treatment"}
    assert eng.setting_override(treat) == {"variable": "caption_size", "value": 0.05}
    assert eng.setting_override({"experiment_id": eid, "experiment_arm": "control"}) is None
    svc.db.update("experiments", eid, {"status": "concluded"})
    assert eng.setting_override(treat) is None
    svc.db.execute("DELETE FROM experiments")
    rule = eng.propose(_gp("G-hook", "Open on a question."))
    assert eng.setting_override({"experiment_id": rule, "experiment_arm": "treatment"}) is None


def test_producer_applies_setting_override(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea, _orch

    _activate(svc)
    orch = _orch(svc)
    eid = orch.experiments.propose(_gp("G-caption_size", "0.05"))
    assert eid
    treat = dict(svc.db.get("ideas", _idea(svc, experiment_id=eid, experiment_arm="treatment")))
    ctrl = dict(svc.db.get("ideas", _idea(svc, experiment_id=eid, experiment_arm="control")))
    assert orch.producer.choose_settings("vid_t", treat)["caption_size"] == 0.05
    assert orch.producer.choose_settings("vid_c", ctrl)["caption_size"] == 0.04


def test_close_for_baseline_change(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    eid = eng.propose(_gp("G-hook", "Open on a question."))
    assert eng.close_for_baseline_change({"G-title"}) == []
    assert eng.close_for_baseline_change({"G-hook", "G-title"}) == [eid]
    row = svc.db.get("experiments", eid)
    assert row["status"] == "retired" and row["conclusion"] == "baseline changed by owner"
    assert eng.cooldown_left("G-hook") == 0  # an owner edit is not an inconclusive result


def test_topic_arm_alternates(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    eng = _geng(svc)
    assert eng.topic_arm_for_new_idea() is None
    eid = eng.propose(_gp("G-topic", "Pick a surprising number and explain it."))
    arms = []
    for _ in range(4):
        got = eng.topic_arm_for_new_idea()
        assert got is not None
        arms.append(got[0])
        _idea(svc, experiment_id=eid, experiment_arm=got[0])
    assert arms == ["control", "treatment", "control", "treatment"]
    assert eng.topic_arm_for_new_idea() == (
        "control",
        "Pick a single named thing, person or event. No listicles.",
    )
    svc.db.execute("UPDATE ideas SET experiment_id=NULL")
    svc.db.execute("DELETE FROM experiments")
    eng.propose(_gp("G-hook", "Open on a question."))
    assert eng.topic_arm_for_new_idea() is None  # not a topic-stage rule


def _pick_first(ctx: Any, role: str, user: str, schema: Any, *a: Any, **kw: Any) -> Any:
    first = user.split("Shortlist:\n- ")[1].split(" |")[0]
    return schema.model_validate({"selected_idea_ids": [first]})


def test_editor_picks_topic_arm_with_fewer_ideas(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea, _orch

    _activate(svc)
    orch = _orch(svc)
    eid = orch.experiments.propose(_gp("G-topic", "Pick a surprising number and explain it."))
    _idea(svc, family="f", experiment_id=eid, experiment_arm="control", opportunity_score=90)
    t = _idea(svc, family="f", experiment_id=eid, experiment_arm="treatment", opportunity_score=10)
    _idea(svc, family="f", status="produced", experiment_id=eid, experiment_arm="control")
    monkeypatch.setattr(orch.editor, "ask", _pick_first)
    with svc.tracker.run("test") as run:
        chosen = orch.editor.select(run, orch.strategy.current(), [], k=1)
    assert chosen == [t]  # the treatment arm had fewer ideas in production
    row = svc.db.get("ideas", t)
    assert row["experiment_arm"] == "treatment" and row["experiment_id"] == eid


def test_editor_g_runtime_treatment_sets_runtime(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea, _orch

    _activate(svc)
    orch = _orch(svc)
    assert orch.experiments.propose(_gp("G-runtime", "90"))
    ids = [_idea(svc, family="f") for _ in range(2)]
    monkeypatch.setattr(orch.editor, "ask", _pick_first)
    with svc.tracker.run("test") as run:
        orch.editor.select(run, orch.strategy.current(), [], k=2)
    rows = [svc.db.get("ideas", i) for i in ids]
    assert sorted(r["experiment_arm"] for r in rows) == ["control", "treatment"]
    treat = next(r for r in rows if r["experiment_arm"] == "treatment")
    assert treat["suggested_runtime_s"] == 90


# -- controller ruling R6: a G-runtime treatment bypasses the guideline runtime range -----


def _runtime_idea(svc: Any, arm: str) -> str:
    from tests.test_reliability import _idea

    eid = svc.db.scalar("SELECT id FROM experiments WHERE variable='G-runtime'")
    if eid is None:
        eid = ExperimentEngine(svc.db, guideline=guideline.for_services(svc)).propose(_gp("G-runtime", "90"))
    return _idea(svc, status="selected", experiment_id=eid, experiment_arm=arm, suggested_runtime_s=90)


def test_g_runtime_treatment_bypasses_clamp_and_critic(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_guideline_prompts import _draft, _gate, _script_prompt

    g = _activate(svc)
    idea_id = _runtime_idea(svc, "treatment")
    assert guideline.runtime_variant(dict(svc.db.get("ideas", idea_id)), svc.db) == 90
    assert "Runtime: 90s" in _script_prompt(svc, monkeypatch, idea_id)
    long = _draft("Amalthea hid in plain sight.", filler_words=40)  # far above 45 s
    out = _gate(long, g, runtime_override=90)
    assert not any("outside the guideline's" in x for x in out.required_revisions)


def test_g_runtime_control_follows_guideline(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_guideline_prompts import _draft, _gate, _script_prompt

    g = _activate(svc)
    idea_id = _runtime_idea(svc, "control")
    assert guideline.runtime_variant(dict(svc.db.get("ideas", idea_id)), svc.db) is None
    prompt = _script_prompt(svc, monkeypatch, idea_id)
    assert "Runtime: 90s" not in prompt and "20s to 45s" in prompt
    out = _gate(_draft("Amalthea hid in plain sight.", filler_words=40), g)
    assert any("outside the guideline's 20-45s" in x for x in out.required_revisions)


def test_legacy_path_without_guideline(svc) -> None:  # noqa: ANN001
    eng = ExperimentEngine(svc.db)
    assert eng.guideline is None
    assert eng.validate(_gp("G-hook", "Open on a question.")) is not None  # not an assignable variable
    eid = eng.propose(PROP)
    assert eid
    row = svc.db.get("experiments", eid)
    assert row["variable"] == "hook_type" and row["guideline_version"] is None and row["min_sample"] == 8


@pytest.mark.parametrize("item", ["G-hook", "G-runtime", "G-music"])
def test_evaluate_all_keeps_guideline_experiments(svc, item: str) -> None:  # noqa: ANN001
    eng = _geng(svc)
    music = next(str(c) for c in CATALOG["music"].bounds["choices"] if c != "off")
    variant = {"G-hook": "Open on a question.", "G-runtime": "60", "G-music": music}[item]
    eid = eng.propose(_gp(item, variant))
    assert eid
    eng.evaluate_all([])
    assert svc.db.get("experiments", eid)["status"] == "running"


def test_ideation_writes_half_the_batch_per_topic_arm(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _orch

    _activate(svc)
    orch = _orch(svc)
    eid = orch.experiments.propose(_gp("G-topic", "Pick a surprising number and explain it."))
    assert eid
    leads = [
        SourceItemView(id=f"si_{i}", source_name="s", url=f"https://e.x/{i}", title=f"t{i}") for i in range(4)
    ]
    prompts: list[str] = []

    def fake_ask(ctx: Any, role: str, user: str, schema: Any, *a: Any, **kw: Any) -> Any:
        prompts.append(user)
        free = [s.id for s in leads if s.id in user.split("Leads:")[1]]
        scores = dict.fromkeys(IdeaScores.model_fields, 8)
        ideas = [
            {
                "title": f"Idea number {len(prompts)}-{n}",
                "premise": "A premise long enough.",
                "hook": "A hook line.",
                "hook_type": "narrative",
                "content_family": "f",
                "suggested_runtime_s": 30,
                "source_refs": [free[n]],
                "scores": scores,
            }
            for n in range(2)
        ]
        return IdeaBatch.model_validate({"ideas": ideas})

    monkeypatch.setattr(orch.ideation, "ask", fake_ask)
    with svc.tracker.run("test") as run:
        created = orch.ideation.run(run, orch.strategy.current(), n_ideas=4, leads=leads)
    assert len(prompts) == 2 and len(created) == 4
    assert "Pick a single named thing" in prompts[0] and "Pick a surprising number" not in prompts[0]
    assert "Pick a surprising number" in prompts[1] and "Pick a single named thing" not in prompts[1]
    assert "Generate 2 candidate" in prompts[0]
    arms = [svc.db.get("ideas", i)["experiment_arm"] for i in created]
    assert arms == ["control", "control", "treatment", "treatment"]
    assert all(svc.db.get("ideas", i)["experiment_id"] == eid for i in created)
    assert "si_0" not in prompts[1].split("Leads:")[1]  # the second half gets the leads the first left
