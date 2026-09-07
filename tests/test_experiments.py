from __future__ import annotations

import random

from aimz.domain.models import ExperimentProposal
from aimz.experiments.engine import ExperimentEngine, kpi_value, summarize, welch_confidence

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
