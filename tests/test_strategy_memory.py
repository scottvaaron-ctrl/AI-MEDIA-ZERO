from __future__ import annotations

from aimz.domain.models import FamilyUpdate, StrategyUpdate
from aimz.experiments.allocation import FamilyStat
from aimz.strategy.memory import StrategyMemory


def test_ai_cannot_reduce_exploration_or_declare_winners_without_evidence(svc) -> None:  # noqa: ANN001
    mem = StrategyMemory(svc.db, svc.env.config_dir, svc.config.starting_families, 0.7, 12)
    state = mem.current()
    upd = StrategyUpdate(
        audience_model="everyone",
        family_updates=[FamilyUpdate(key="forgotten_history", status="winning", note="one great video")],
        explore_ratio_suggestion=0.3,
        confidence=0.9,
        change_summary="premature",
    )
    stats = {"forgotten_history": FamilyStat(key="forgotten_history", n=1, mean=0.95, scores=[0.95])}
    new = mem.apply_update(state, upd, stats, {"active": [], "retired": []})
    assert new["explore_ratio"] == 0.7
    assert new["families"]["forgotten_history"]["status"] == "testing"
    assert new["families"]["forgotten_history"]["n"] == 1
    # with enough evidence the suggestion is honoured
    stats_big = {"forgotten_history": FamilyStat(key="forgotten_history", n=12, mean=0.8, scores=[0.8] * 12)}
    new2 = mem.apply_update(state, upd, stats_big, {"active": [], "retired": []})
    assert new2["explore_ratio"] == 0.3 and new2["families"]["forgotten_history"]["status"] == "winning"
    v = mem.save(new2, created_by="ai", change_summary="test")
    assert v >= 1 and mem.current()["version"] == v


def test_markdown_shows_explore_ratio_without_guideline() -> None:
    from aimz.strategy.memory import render_markdown

    md = render_markdown({"explore_ratio": 0.7, "families": {}})
    assert "70% of production slots explore." in md
    assert "replaced by the guideline" not in md


def test_markdown_explore_ratio_replaced_by_guideline() -> None:
    from aimz.strategy.memory import render_markdown

    md = render_markdown({"explore_ratio": 0.0, "guideline_version": 1, "families": {}})
    assert "Exploration ratio: replaced by the guideline (one experiment at a time)" in md
    assert "of production slots explore" not in md


def test_learn_with_guideline_sets_explore_share_zero(svc) -> None:  # noqa: ANN001
    from tests.test_learning_inputs import _guideline_on, _learn_prompt

    _guideline_on(svc)
    orch, _ = _learn_prompt(svc)
    state = orch.strategy.current()
    assert state["explore_ratio"] == 0.0 and state["guideline_version"] == 1
    md = (svc.env.config_dir / "strategy.md").read_text(encoding="utf-8")
    assert "Exploration ratio: replaced by the guideline (one experiment at a time)" in md


def test_editor_has_no_explore_slots_with_guideline(svc) -> None:  # noqa: ANN001
    from tests.test_learning_inputs import _guideline_on

    from aimz.pipeline.orchestrator import Orchestrator

    _guideline_on(svc)
    orch = Orchestrator(svc, seed=3)
    with svc.tracker.run("cycle") as run:
        orch.research.run(run)
        orch.ideation.run(run, orch.strategy.current())
        chosen = orch.editor.select(run, orch.strategy.current(), [])
    assert chosen
    modes = {svc.db.get("ideas", i)["allocation_mode"] for i in chosen}
    assert modes == {"guideline"}
