"""The AI may experiment on any guideline item: the genre and each code check too (owner decision 2026-10-09)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.test_experiments import GSAMPLE, _activate, _geng, _gp

from aimz import guideline
from aimz import guideline_proposals as gp
from aimz.experiments.settings import CATALOG

NEW_GENRE = "true crime stories from small towns"


def _treatment_idea(svc: Any, eid: str, arm: str = "treatment") -> dict[str, Any]:
    from tests.test_reliability import _idea

    return dict(svc.db.get("ideas", _idea(svc, experiment_id=eid, experiment_arm=arm)))


# ---- items ---------------------------------------------------------------------------------------------


def test_genre_and_every_check_are_items(svc) -> None:  # noqa: ANN001
    ids = _activate(svc).item_ids()
    assert {
        "G-genre",
        "G-hook_max_words",
        "G-title_max_chars",
        "G-hook_no_question",
        "G-hook_no_definition",
    } <= ids
    assert "G-banned_openings" in ids


@pytest.mark.parametrize(
    ("item", "variant", "stored"),
    [
        ("G-genre", NEW_GENRE, NEW_GENRE),
        ("G-hook_max_words", "20", "20"),
        ("G-title_max_chars", "45", "45"),
        ("G-hook_no_question", "false", "false"),
        ("G-hook_no_definition", "no", "false"),
        ("G-banned_openings", "Did you know; Imagine", json.dumps(["Did you know", "Imagine"])),
        ("G-banned_openings", '["Today we"]', json.dumps(["Today we"])),
    ],
)
def test_new_items_can_be_tested(svc, item: str, variant: str, stored: str) -> None:  # noqa: ANN001
    eng = _geng(svc)
    eid = eng.propose(_gp(item, variant))
    assert eid, eng.validate(_gp(item, variant))
    assert svc.db.get("experiments", eid)["treatment"] == stored


@pytest.mark.parametrize(
    ("item", "variant", "why"),
    [
        ("G-genre", "", "variant text required"),
        ("G-genre", "explainer", "identical"),
        ("G-hook_max_words", "0", "out of bounds"),
        ("G-hook_max_words", "many", "out of bounds"),
        ("G-title_max_chars", "500", "out of bounds"),
        ("G-hook_no_question", "maybe", "out of bounds"),
        ("G-hook_no_question", "true", "identical"),
        ("G-banned_openings", "", "out of bounds"),
    ],
)
def test_bad_variants_are_refused(svc, item: str, variant: str, why: str) -> None:  # noqa: ANN001
    eng = _geng(svc)
    assert why in (eng.validate(_gp(item, variant)) or "")


# ---- the guideline as one idea sees it ------------------------------------------------------------------


def test_for_idea_swaps_genre_and_checks_only_on_the_treatment_arm(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    g = eng.guideline
    eid = eng.propose(_gp("G-hook_max_words", "20"))
    assert eid
    t = guideline.for_idea(g, _treatment_idea(svc, eid), svc.db)
    c = guideline.for_idea(g, _treatment_idea(svc, eid, "control"), svc.db)
    assert t is not None and c is not None
    assert t.checks["hook_max_words"] == 20 and c.checks["hook_max_words"] == 15
    assert t.checks["title_max_chars"] == 60  # the rest is the guideline's
    svc.db.execute("DELETE FROM experiments")
    eid = eng.propose(_gp("G-genre", NEW_GENRE))
    assert eid
    assert guideline.for_idea(g, _treatment_idea(svc, eid), svc.db).genre == NEW_GENRE  # type: ignore[union-attr]
    assert guideline.for_idea(None, {}, svc.db) is None


def test_genre_experiment_runs_like_a_topic_experiment(svc) -> None:  # noqa: ANN001
    eng = _geng(svc)
    eid = eng.propose(_gp("G-genre", NEW_GENRE))
    assert eng.topic_experiment()["id"] == eid  # type: ignore[index]
    assert eng.topic_arm_for_new_idea() == ("control", "explainer")
    assert guideline.variant_for(_treatment_idea(svc, eid), svc.db) == {}  # not a rule-text variant


def test_ideation_writes_treatment_half_under_the_variant_genre(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _orch

    from aimz.domain.models import SourceItemView

    _activate(svc)
    orch = _orch(svc)
    eid = orch.experiments.propose(_gp("G-genre", NEW_GENRE))
    assert eid
    seen: list[tuple[str, dict[str, Any]]] = []

    def fake_generate(run: Any, span: Any, prompt: str, part_leads: Any, *rest: Any) -> list[str]:
        seen.append((prompt, rest[4]))  # rest: families, amap, claimed, sf, label, ...
        return []

    monkeypatch.setattr(orch.ideation, "_generate", fake_generate)
    leads = [SourceItemView(id=f"src_{i}", title=f"t{i}", url="u", source_name="s") for i in range(4)]
    with svc.tracker.run("t") as run:
        orch.ideation.run(run, orch.strategy.current(), n_ideas=4, leads=leads)
    by_arm = {label.get("experiment_arm"): p for p, label in seen}
    assert set(by_arm) == {"control", "treatment"}
    assert NEW_GENRE in by_arm["treatment"] and NEW_GENRE not in by_arm["control"]
    assert "'explainer'" in by_arm["control"]


def test_editor_genre_gate_judges_treatment_ideas_by_the_variant(
    svc, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    from aimz.agents.editor import EditorInChief
    from aimz.domain.models import GenreFit

    eng = _geng(svc)
    eid = eng.propose(_gp("G-genre", NEW_GENRE))
    editor = EditorInChief(svc, None, eng)
    asked: list[str] = []

    def judge(ctx: Any, role: str, user: str, *a: Any, **kw: Any) -> GenreFit:
        asked.append(user)
        return GenreFit(fits=True)

    monkeypatch.setattr(editor, "ask", judge)
    pool = [_treatment_idea(svc, eid), _treatment_idea(svc, eid, "control")]
    with svc.tracker.run("t") as run:
        editor._genre_gate(run, eng.guideline, pool, [10])  # type: ignore[arg-type]
    assert f"Channel genre: {NEW_GENRE}" in asked[0]
    assert "Channel genre: explainer" in asked[1]


# ---- per-idea checks in the writer and the critic -----------------------------------------------------


def test_critic_and_writer_use_the_treatment_check(svc) -> None:  # noqa: ANN001
    from aimz.agents.script import ScriptAgent

    eng = _geng(svc)
    eid = eng.propose(_gp("G-hook_max_words", "20"))
    g = guideline.for_idea(eng.guideline, _treatment_idea(svc, eid), svc.db)
    assert "at most 20 words" in ScriptAgent._hook_limits_line(g)
    from aimz.agents.critic import hook_revisions

    eighteen = " ".join(["word"] * 18) + "."
    assert not any("Hook is" in r for r in hook_revisions([eighteen], g.checks))  # type: ignore[union-attr]
    assert any("Hook is 18 words" in r for r in hook_revisions([eighteen], eng.guideline.checks))  # type: ignore[union-attr]


# ---- proposals write wins into the file ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "after", "check"),
    [
        ("G-genre", NEW_GENRE, lambda g: g.genre == NEW_GENRE),
        ("G-hook_max_words", "20", lambda g: g.checks["hook_max_words"] == 20),
        ("G-hook_no_question", "false", lambda g: g.checks["hook_no_question"] is False),
        (
            "G-banned_openings",
            json.dumps(["Did you know", "Imagine"]),
            lambda g: g.checks["banned_openings"] == ["Did you know", "Imagine"],
        ),
    ],
)
def test_rewrite_item_writes_genre_and_checks(item: str, after: str, check: Any) -> None:
    new = gp.rewrite_item(GSAMPLE, item, after)
    old_g = guideline.parse(GSAMPLE, CATALOG, set())
    new_g = guideline.parse(new, CATALOG, set())
    assert check(new_g)
    assert gp.changed_items(old_g, new_g) == {item}


def test_current_value_of_genre_and_checks() -> None:
    g = guideline.parse(GSAMPLE, CATALOG, set())
    assert gp.current_value(g, "G-genre") == "explainer"
    assert gp.current_value(g, "G-hook_max_words") == "15"
    assert gp.current_value(g, "G-hook_no_question") == "true"
    assert gp.current_value(g, "G-banned_openings") == json.dumps(["Did you know", "In this video"])


def test_strategist_sees_genre_and_checks_as_testable(svc) -> None:  # noqa: ANN001
    from aimz.agents.analyst import AnalystAgent

    eng = _geng(svc)
    analyst = AnalystAgent(svc, None, eng)
    text = analyst.guideline_prompt(eng.guideline, [])  # type: ignore[arg-type]
    assert "- G-genre: explainer" in text
    assert "- G-hook_max_words (code check): 15" in text


def test_critic_review_applies_the_treatment_check_end_to_end(svc) -> None:  # noqa: ANN001
    from tests.test_guideline_prompts import _draft
    from tests.test_reliability import _orch

    from aimz.domain.models import FactCheckResult

    _activate(svc)
    orch = _orch(svc)
    eid = orch.experiments.propose(_gp("G-hook_max_words", "20"))
    assert eid
    eighteen = " ".join(["word"] * 17) + " Amalthea."
    ok = FactCheckResult(verdicts=[], overall="pass")
    with svc.tracker.run("t") as run:
        t = orch.critic.review(run, "scr_t", _draft(eighteen), _treatment_idea(svc, eid), ok, [])
        c = orch.critic.review(run, "scr_c", _draft(eighteen), _treatment_idea(svc, eid, "control"), ok, [])
    assert not any(r.startswith("Hook is") for r in t.required_revisions)
    assert any(r.startswith("Hook is 18 words") for r in c.required_revisions)
