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
