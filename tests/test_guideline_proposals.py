"""Task 5: guideline change proposals, the owner's approve/reject, owner edits, status, CLI and dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_experiments import GSAMPLE, HIGH, LOW, _gp, _grows

from aimz import guideline
from aimz import guideline_proposals as gp
from aimz.core.attention import attention
from aimz.experiments.engine import ExperimentEngine
from aimz.util import loads


def _engine(svc: Any, text: str = GSAMPLE) -> ExperimentEngine:
    (svc.env.config_dir / "guideline.md").write_text(text, encoding="utf-8")
    g = guideline.for_services(svc)
    assert g is not None
    return ExperimentEngine(svc.db, guideline=g)


def _won(svc: Any, item: str, variant: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(experiment row, evaluate result) for a guideline experiment whose variant won."""
    eng = _engine(svc)
    eid = eng.propose(_gp(item, variant))
    assert eid
    res = eng.evaluate(dict(svc.db.get("experiments", eid)), _grows(eid, LOW, HIGH))
    assert res["status"] == "variant_won"
    return dict(svc.db.get("experiments", eid)), {"id": eid, **res}


def test_create_dedupes_pending_item(svc) -> None:  # noqa: ANN001
    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    assert pid
    row = svc.db.get("guideline_proposals", pid)
    assert row["status"] == "pending" and row["item"] == "G-hook"
    assert row["before"] == "The first sentence states the most surprising fact. No greetings."
    assert row["after"] == "Open on a question the video answers."
    ev = loads(row["evidence_json"], {})
    assert ev["treatment"]["n"] == 10 and ev["control"]["n"] == 10
    assert ev["treatment"]["mean_avg_percent_viewed"] == 60.0
    assert row["summary"] == gp.template_summary(ev)
    # The same item again, even from another experiment, returns the pending proposal.
    other = {**exp, "id": "exp_other"}
    assert gp.create_from_experiment(svc.db, other, {**res, "id": "exp_other"}, "") == pid
    assert svc.db.count("guideline_proposals") == 1
    assert [p["id"] for p in gp.pending(svc.db)] == [pid]


def test_summary_with_wrong_number_is_replaced(svc) -> None:  # noqa: ANN001
    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    res["treatment"]["mean_avg_percent_viewed"] = 62.0
    pid = gp.create_from_experiment(svc.db, exp, res, "Viewers watched 71% of the variant videos.")
    row = svc.db.get("guideline_proposals", pid)
    ev = loads(row["evidence_json"], {})
    assert row["summary"] == gp.template_summary(ev)
    assert "62%" in row["summary"]


def test_check_summary_numbers() -> None:
    ev = {
        "item": "G-hook",
        "after": "x",
        "control": {"n": 10, "mean": 50.3, "mean_avg_percent_viewed": 40.0},
        "treatment": {"n": 12, "mean": 70.04, "mean_avg_percent_viewed": 62.0},
        "p_treatment_better": 0.9734,
    }
    assert gp.check_summary(gp.template_summary(ev), ev)
    assert gp.check_summary("The variant scored 70 against 50.3 over 12 videos, 97% likely better.", ev)
    assert gp.check_summary("Watched 62% vs 40%.", ev)
    assert not gp.check_summary("Watched 71% vs 40%.", ev)
    assert not gp.check_summary("Scored 70.1.", ev)


def test_approve_setting_rewrites_yaml(svc) -> None:  # noqa: ANN001
    exp, res = _won(svc, "G-caption_max_words", "5")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    path = guideline.path(svc.env.config_dir)
    before = path.read_text(encoding="utf-8")
    version = gp.approve(svc.db, svc.env.config_dir, pid)
    after = path.read_text(encoding="utf-8")
    assert "  caption_max_words: 5\n" in after
    assert after == before.replace("  caption_max_words: 3\n", "  caption_max_words: 5\n")
    row = svc.db.one("SELECT * FROM guideline_versions WHERE version=?", [version])
    assert row["source"] == f"proposal:{pid}"
    g = guideline.for_services(svc)
    assert g is not None and g.locked["caption_max_words"] == 5 and g.version == version
    # load() did not record the same change again as an owner edit.
    assert svc.db.count("guideline_versions", "source='owner_edit'") == 0
    p = svc.db.get("guideline_proposals", pid)
    assert p["status"] == "approved" and p["decided_at"]
    assert gp.pending(svc.db) == []


def test_approve_runtime_rewrites_range(svc) -> None:  # noqa: ANN001
    exp, res = _won(svc, "G-runtime", "30")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    gp.approve(svc.db, svc.env.config_dir, pid)
    g = guideline.for_services(svc)
    assert g is not None and g.runtime_min <= 30 <= g.runtime_max
    assert (g.runtime_min, g.runtime_max) == (18, 42)


def test_approve_rule_rewrites_body(svc) -> None:  # noqa: ANN001
    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    path = guideline.path(svc.env.config_dir)
    before = path.read_text(encoding="utf-8")
    gp.approve(svc.db, svc.env.config_dir, pid)
    after = path.read_text(encoding="utf-8")
    assert after == before.replace(
        "The first sentence states the most surprising fact. No greetings.",
        "Open on a question the video answers.",
    )
    g = guideline.for_services(svc)
    assert g is not None
    assert g.rules["G-hook"].text == "Open on a question the video answers."
    assert g.rules["G-hook"].stage == "script"
    assert g.rules["G-title"].text == "Titles name the subject and the surprise in under 60 characters."


def test_reject_sets_note_and_cooldown(svc) -> None:  # noqa: ANN001
    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    content = guideline.path(svc.env.config_dir).read_text(encoding="utf-8")
    gp.reject(svc.db, pid, "Questions feel like clickbait on this channel.")
    row = svc.db.get("guideline_proposals", pid)
    assert row["status"] == "rejected" and row["decided_at"]
    assert row["owner_note"] == "Questions feel like clickbait on this channel."
    state = svc.db.get_state("last_rejected_guideline_proposal") or ""
    assert "G-hook" in state and "Questions feel like clickbait" in state
    assert guideline.path(svc.env.config_dir).read_text(encoding="utf-8") == content
    eng = _engine(svc)
    assert eng.cooldown_left("G-hook") == 20
    assert gp.pending(svc.db) == []


def test_owner_edit_closes_only_changed_item_experiment(svc) -> None:  # noqa: ANN001
    eng = _engine(svc)
    gp.sync_version(svc.db, eng)  # first activation: nothing closed, version remembered
    eid = eng.propose(_gp("G-hook", "Open on a question the video answers."))
    assert eid
    # Editing another item leaves the running experiment alone.
    edited = GSAMPLE.replace("No listicles.", "No listicles, ever.")
    eng = _engine(svc, edited)
    assert gp.sync_version(svc.db, eng) == []
    assert svc.db.get("experiments", eid)["status"] == "running"
    # Editing its own item closes it.
    eng = _engine(svc, edited.replace("No greetings.", "No greetings or intros."))
    assert gp.sync_version(svc.db, eng) == [eid]
    row = svc.db.get("experiments", eid)
    assert row["status"] == "retired" and row["conclusion"] == "baseline changed by owner"
    # Nothing new: nothing to do.
    assert gp.sync_version(svc.db, eng) == []


def test_changed_items() -> None:
    from aimz.experiments.settings import CATALOG

    old = guideline.parse(GSAMPLE, CATALOG, set())
    new = guideline.parse(
        GSAMPLE.replace("caption_max_words: 3", "caption_max_words: 4").replace("max: 45", "max: 50"),
        CATALOG,
        set(),
    )
    assert gp.changed_items(old, new) == {"G-caption_max_words", "G-runtime"}


def test_first_activation_closes_nothing(svc) -> None:  # noqa: ANN001
    eng = _engine(svc)
    eid = eng.propose(_gp("G-hook", "Open on a question the video answers."))
    assert gp.sync_version(svc.db, eng) == []
    assert svc.db.get("experiments", eid)["status"] == "running"
    assert svc.db.get_state("guideline_seen_version") == str(eng.guideline.version)


def test_attention_lists_pending_and_overdue(svc) -> None:  # noqa: ANN001
    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    out = attention(svc.db)
    [entry] = out["guideline_changes_waiting"]
    assert entry["fix"] == f"python -m aimz guideline approve {pid}"
    assert "note" not in entry
    svc.db.execute(
        "UPDATE guideline_proposals SET created_at=? WHERE id=?", ["2026-01-01T00:00:00+00:00", pid]
    )
    [entry] = attention(svc.db)["guideline_changes_waiting"]
    assert entry["note"] == "overdue"
    assert "guideline_invalid" not in out
    svc.db.set_state("guideline_error", "runtime_s must give min and max seconds")
    [bad] = attention(svc.db)["guideline_invalid"]
    assert bad["fix"] == "edit config/guideline.md"
    assert "runtime_s" in bad["error"]


def test_cli_approve(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    exp, res = _won(svc, "G-caption_max_words", "5")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    runner = CliRunner()
    shown = runner.invoke(app, ["guideline", "proposals"])
    assert shown.exit_code == 0, shown.output
    assert pid in shown.output
    out = runner.invoke(app, ["guideline", "approve", pid])
    assert out.exit_code == 0, out.output
    assert svc.db.get("guideline_proposals", pid)["status"] == "approved"
    assert "caption_max_words: 5" in guideline.path(svc.env.config_dir).read_text(encoding="utf-8")
    hist = runner.invoke(app, ["guideline", "history"])
    assert hist.exit_code == 0 and f"proposal:{pid}" in hist.output
    show = runner.invoke(app, ["guideline", "show"])
    assert show.exit_code == 0 and "caption_max_words: 5" in show.output


def test_cli_reject_needs_note(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    out = CliRunner().invoke(app, ["guideline", "reject", pid, "--note", "Not our voice."])
    assert out.exit_code == 0, out.output
    row = svc.db.get("guideline_proposals", pid)
    assert row["status"] == "rejected" and row["owner_note"] == "Not our voice."


def test_dashboard_guideline_page(svc) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from aimz.dashboard.app import create_app

    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    client = TestClient(create_app(svc))
    r = client.get("/guideline")
    assert r.status_code == 200
    assert pid in r.text and "Open on a question the video answers." in r.text
    assert "Open on the surprise" in r.text  # current text
    r = client.post(f"/guideline/proposals/{pid}/reject", data={"note": "No."}, follow_redirects=False)
    assert r.status_code == 303
    assert svc.db.get("guideline_proposals", pid)["owner_note"] == "No."


def test_analyst_learn_creates_proposal(svc, monkeypatch) -> None:  # noqa: ANN001
    from aimz.pipeline.orchestrator import Orchestrator

    _engine(svc)
    orch = Orchestrator(svc)
    eid = orch.experiments.propose(_gp("G-hook", "Open on a question the video answers."))
    assert eid
    rows = [
        {**r, "title": "A video", "content_family": "forgotten_history", "hook_type": "fact"}
        for r in _grows(eid, LOW, HIGH)
    ]
    monkeypatch.setattr(svc.analytics_store, "video_performance", lambda: rows)
    with svc.tracker.run("learn") as ctx:
        orch.analyst.learn(ctx, orch.strategy.current())
    [p] = gp.pending(svc.db)
    assert p["item"] == "G-hook" and p["experiment_id"] == eid


def test_module_has_no_import_in_guideline_load() -> None:
    src = Path(guideline.__file__).read_text(encoding="utf-8")
    assert "guideline_proposals" not in src


def test_attention_warns_unavailable_locked_items(svc) -> None:  # noqa: ANN001
    _engine(svc, GSAMPLE.replace("  music: off\n", "  music: off\n  clip_scenes: on\n"))
    [entry] = attention(svc.db)["guideline_items_unavailable"]
    assert entry["setting"] == "clip_scenes"
    assert "stock_video" in entry["why"]
    assert "Pexels or Pixabay" in entry["fix"] and "clip_scenes" in entry["fix"]
    _engine(svc)  # the owner removes the item: the warning goes
    assert "guideline_items_unavailable" not in attention(svc.db)


def _learn(svc: Any, monkeypatch: Any, rows: list[dict[str, Any]]) -> None:
    from aimz.pipeline.orchestrator import Orchestrator

    orch = Orchestrator(svc)
    monkeypatch.setattr(svc.analytics_store, "video_performance", lambda: rows)
    with svc.tracker.run("learn") as ctx:
        orch.analyst.learn(ctx, orch.strategy.current())


def test_learn_keeps_going_when_proposal_creation_fails_and_sweeps_later(svc, monkeypatch) -> None:  # noqa: ANN001
    from aimz.pipeline.orchestrator import Orchestrator

    _engine(svc)
    eid = Orchestrator(svc).experiments.propose(_gp("G-hook", "Open on a question the video answers."))
    assert eid
    rows = [
        {**r, "title": "A video", "content_family": "forgotten_history", "hook_type": "fact"}
        for r in _grows(eid, LOW, HIGH)
    ]

    def boom(*a: Any, **kw: Any) -> str:
        raise RuntimeError("disk full")

    with monkeypatch.context() as m:
        m.setattr(gp, "create_from_experiment", boom)
        _learn(svc, m, rows)  # must not raise
    assert svc.db.get("experiments", eid)["status"] == "variant_won"
    assert gp.pending(svc.db) == []
    # The next learn finds the won experiment with no proposal and creates it from the stored result.
    _learn(svc, monkeypatch, [])
    [p] = gp.pending(svc.db)
    assert p["experiment_id"] == eid and p["item"] == "G-hook"
    ev = gp.evidence_of(p)
    assert ev["treatment"]["n"] == 10 and ev["control"]["n"] == 10
    # Swept once: a further learn does not duplicate it.
    _learn(svc, monkeypatch, [])
    assert svc.db.count("guideline_proposals") == 1


def test_owner_edit_supersedes_pending_proposal_on_that_item(svc) -> None:  # noqa: ANN001
    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    eng = _engine(svc)
    gp.sync_version(svc.db, eng)
    # An edit to another item leaves it pending.
    edited = GSAMPLE.replace("No listicles.", "No listicles, ever.")
    gp.sync_version(svc.db, _engine(svc, edited))
    assert svc.db.get("guideline_proposals", pid)["status"] == "pending"
    # An edit to its own item supersedes it.
    owner = edited.replace("No greetings.", "No greetings or intros.")
    eng = _engine(svc, owner)
    gp.sync_version(svc.db, eng)
    row = svc.db.get("guideline_proposals", pid)
    assert row["status"] == "superseded" and row["owner_note"] == "baseline changed by owner"
    assert row["decided_at"]
    assert gp.pending(svc.db) == []
    assert eng.has_pending_proposal("G-hook") is False
    assert eng.cooldown_left("G-hook") == 0  # superseded is not a rejection
    assert "guideline_changes_waiting" not in attention(svc.db)
    import pytest

    with pytest.raises(ValueError, match="already superseded"):
        gp.approve(svc.db, svc.env.config_dir, pid)
    assert guideline.path(svc.env.config_dir).read_text(encoding="utf-8") == owner


def test_approve_refuses_when_owner_changed_item_before_the_next_cycle(svc) -> None:  # noqa: ANN001
    import pytest

    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    owner = GSAMPLE.replace("No greetings.", "No greetings or intros.")
    guideline.path(svc.env.config_dir).write_text(owner, encoding="utf-8")
    with pytest.raises(ValueError, match="changed G-hook"):
        gp.approve(svc.db, svc.env.config_dir, pid)
    assert svc.db.get("guideline_proposals", pid)["status"] == "superseded"
    assert guideline.path(svc.env.config_dir).read_text(encoding="utf-8") == owner


def test_owner_edit_closes_experiment_then_approval_reaches_next_assign(svc) -> None:  # noqa: ANN001
    """Offline end to end: an owner edit at cycle start closes the experiment on that item; approving a
    proposal on another item bumps the version and the next video's settings use the new value."""
    from tests.test_reliability import _idea, _orch

    # A won experiment on caption_size whose proposal waits for the owner.
    exp, res = _won(svc, "G-caption_size", "0.05")
    cap_pid = gp.create_from_experiment(svc.db, exp, res, "")
    orch = _orch(svc)
    gp.sync_version(svc.db, orch.experiments)
    hook_eid = orch.experiments.propose(_gp("G-hook", "Open on a question the video answers."))
    assert hook_eid
    v_before = orch.experiments.guideline.version

    # The owner edits the hook rule; the next cycle's orchestrator loads it and sync_version runs at start.
    guideline.path(svc.env.config_dir).write_text(
        GSAMPLE.replace("No greetings.", "No greetings or intros."), encoding="utf-8"
    )
    orch = _orch(svc)
    assert orch.experiments.guideline.version == v_before + 1
    assert gp.sync_version(svc.db, orch.experiments) == [hook_eid]
    row = svc.db.get("experiments", hook_eid)
    assert row["status"] == "retired" and row["conclusion"] == "baseline changed by owner"
    assert svc.db.get("guideline_proposals", cap_pid)["status"] == "pending"

    # The owner approves the caption_size proposal.
    v = gp.approve(svc.db, svc.env.config_dir, cap_pid)
    assert v == v_before + 2
    text = guideline.path(svc.env.config_dir).read_text(encoding="utf-8")
    assert "caption_size: 0.05" in text and "No greetings or intros." in text

    # Next cycle: the producer's assignment uses the approved value.
    orch = _orch(svc)
    assert orch.experiments.guideline.version == v
    assert gp.sync_version(svc.db, orch.experiments) == []
    idea = dict(svc.db.get("ideas", _idea(svc, status="selected")))
    assert orch.producer.choose_settings("vid_next", idea)["caption_size"] == 0.05


def test_cli_and_dashboard_treat_superseded_as_closed(svc) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from aimz.cli import app
    from aimz.dashboard.app import create_app

    exp, res = _won(svc, "G-hook", "Open on a question the video answers.")
    pid = gp.create_from_experiment(svc.db, exp, res, "")
    gp.supersede(svc.db, [pid])
    runner = CliRunner()
    shown = runner.invoke(app, ["guideline", "proposals"])
    assert shown.exit_code == 0 and pid not in shown.output
    out = runner.invoke(app, ["guideline", "approve", pid])
    assert out.exit_code == 1 and "already superseded" in out.output
    client = TestClient(create_app(svc))
    page = client.get("/guideline")
    assert page.status_code == 200 and f"/guideline/proposals/{pid}/approve" not in page.text
    r = client.post(f"/guideline/proposals/{pid}/approve", follow_redirects=False)
    assert r.status_code == 303 and "superseded" in r.headers["location"]
    assert svc.db.get("guideline_proposals", pid)["status"] == "superseded"
