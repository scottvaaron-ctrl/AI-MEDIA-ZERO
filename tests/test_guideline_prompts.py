"""Guideline craft rules reach the prompts, and the critic gate enforces them (Task 3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aimz import guideline
from aimz.agents.critic import CriticAgent
from aimz.domain.models import Beat, CriticResult, FactCheckResult, ScriptDraft
from aimz.util import now_iso

SAMPLE = Path(__file__).parent / "fixtures" / "guideline_sample.md"


class _Stop(Exception):
    pass


def _activate(svc: Any) -> guideline.Guideline:
    (svc.env.config_dir / "guideline.md").write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    g = guideline.for_services(svc)
    assert g is not None
    return g


def _critic_prompt(svc: Any, monkeypatch: pytest.MonkeyPatch, idea_id: str) -> str:
    from tests.test_reliability import _orch

    orch = _orch(svc)
    seen: list[str] = []

    def fake_ask(ctx: Any, role: str, user: str, *a: Any, **kw: Any) -> Any:
        seen.append(user)
        raise _Stop

    monkeypatch.setattr(orch.critic, "ask", fake_ask)
    idea = dict(svc.db.get("ideas", idea_id))
    with svc.tracker.run("test") as run, pytest.raises(Exception):  # noqa: B017
        orch.critic.review(
            run, "scr_x", _clean_draft(), idea, FactCheckResult(verdicts=[], overall="pass"), []
        )
    return seen[0]


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


def _draft(opening: str, title: str = "The Discovery of Amalthea", filler_words: int = 12) -> ScriptDraft:
    filler = " ".join(["Amalthea is a small moon of Jupiter."] * (filler_words // 6 + 1))
    filler = " ".join(filler.split()[:filler_words])
    return ScriptDraft(
        title=title,
        hook_line=opening,
        beats=[Beat(narration=opening if i == 0 else filler) for i in range(5)],
    )


def _clean_draft() -> ScriptDraft:
    # about 50 words = ~19 s at 2.6 words/s; the opening is short, so stay inside 20-45 with 12-word beats
    return _draft("Amalthea hid in plain sight for centuries and nobody noticed.", filler_words=12)


def _result() -> CriticResult:
    return CriticResult.model_validate(
        {
            "pass": True,
            "score": 90,
            "hook_strength": 9,
            "originality": 9,
            "factual_support": 9,
            "pacing": 9,
            "payoff": 9,
            "clarity": 9,
        }
    )


def _gate(
    draft: ScriptDraft,
    g: guideline.Guideline,
    result: CriticResult | None = None,
    replace: dict[str, str] | None = None,
    **kw: Any,
) -> CriticResult:
    narration = " ".join(b.narration for b in draft.beats)
    return CriticAgent._overrides(
        result or _result(),
        draft,
        narration,
        FactCheckResult(verdicts=[], overall="pass"),
        70,
        [],
        guideline=g,
        replace=replace,
        **kw,
    )


@pytest.fixture()
def g() -> guideline.Guideline:
    from aimz.experiments.settings import CATALOG

    return guideline.parse(SAMPLE.read_text(encoding="utf-8"), CATALOG, set())


def test_script_prompt_contains_rules(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    _activate(svc)
    prompt = _script_prompt(svc, monkeypatch, _idea(svc, status="selected"))
    assert "Channel guideline (owner-approved; follow it):" in prompt
    assert "The first sentence states the most surprising fact." in prompt
    assert "20-45" in prompt
    assert "Titles name the subject" in prompt


def test_critic_broken_rule_requires_revision(g) -> None:  # noqa: ANN001
    r = _result()
    r.broken_rules = ["G-hook", "G-nonexistent"]
    out = _gate(_clean_draft(), g, r)
    assert out.passed is False
    assert out.required_revisions[0].startswith("Breaks G-hook (Open on the surprise)")
    assert len(out.required_revisions) == 1


def test_clean_script_passes_guideline_checks(g) -> None:  # noqa: ANN001
    out = _gate(_clean_draft(), g)
    assert out.required_revisions == [] and out.passed is True


def test_runtime_out_of_range(g) -> None:  # noqa: ANN001
    out = _gate(_draft("Amalthea hid in plain sight.", filler_words=40), g)  # ~200 words
    assert out.passed is False
    assert any("outside the guideline's 20-45s" in x for x in out.required_revisions)


def test_banned_opening(g) -> None:  # noqa: ANN001
    out = _gate(_draft("Did you know Amalthea is a moon? It orbits Jupiter closely."), g)
    assert out.passed is False
    assert "Opening 'Did you know' is banned by the guideline" in out.required_revisions


def test_title_too_long(g) -> None:  # noqa: ANN001
    title = "A very long title about a small moon of Jupiter that goes on and on forever"
    out = _gate(_draft("Amalthea hid in plain sight for centuries and nobody noticed.", title=title), g)
    assert out.passed is False
    assert f"Title is {len(title)} characters; the guideline allows 60" in out.required_revisions


def test_no_guideline_means_no_new_gates() -> None:
    draft = _draft("Did you know Amalthea is a moon?", filler_words=40)
    narration = " ".join(b.narration for b in draft.beats)
    out = CriticAgent._overrides(
        _result(), draft, narration, FactCheckResult(verdicts=[], overall="pass"), 70, []
    )
    assert out.passed is True and out.required_revisions == []


def _craft_experiment(svc: Any, status: str = "running", variable: str = "G-hook") -> None:
    svc.db.insert(
        "experiments",
        {
            "id": "exp_g",
            "name": "n",
            "hypothesis": "h",
            "variable": variable,
            "control": "orig",
            "treatment": "Open with a question about the moon.",
            "primary_kpi": "mean_score",
            "min_sample": 4,
            "status": status,
            "created_by": "ai",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )


def test_variant_for_arms_and_status(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    _craft_experiment(svc)
    treat = dict(svc.db.get("ideas", _idea(svc, experiment_id="exp_g", experiment_arm="treatment")))
    ctrl = dict(svc.db.get("ideas", _idea(svc, experiment_id="exp_g", experiment_arm="control")))
    assert guideline.variant_for(treat, svc.db) == {"G-hook": "Open with a question about the moon."}
    assert guideline.variant_for(ctrl, svc.db) == {}
    assert guideline.variant_for({"id": "x"}, svc.db) == {}
    svc.db.update("experiments", "exp_g", {"status": "concluded"})
    assert guideline.variant_for(treat, svc.db) == {}


def test_variant_for_ignores_runtime_and_setting_experiments(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    _craft_experiment(svc, variable="G-runtime")
    idea = dict(svc.db.get("ideas", _idea(svc, experiment_id="exp_g", experiment_arm="treatment")))
    assert guideline.variant_for(idea, svc.db) == {}
    svc.db.update("experiments", "exp_g", {"variable": "G-music"})
    assert guideline.variant_for(idea, svc.db) == {}
    svc.db.update("experiments", "exp_g", {"variable": "hook_type"})
    assert guideline.variant_for(idea, svc.db) == {}


def test_variant_replaces_rule_in_critic_prompt(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    _activate(svc)
    _craft_experiment(svc)
    idea_id = _idea(svc, status="selected", experiment_id="exp_g", experiment_arm="treatment")
    prompt = _critic_prompt(svc, monkeypatch, idea_id)
    assert "Open with a question about the moon." in prompt
    assert "The first sentence states the most surprising fact." not in prompt
    assert "List in broken_rules the ID of every guideline rule the script breaks." in prompt


def test_no_guideline_prompts_unchanged(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea

    idea_id = _idea(svc, status="selected")
    assert "Channel guideline" not in _script_prompt(svc, monkeypatch, idea_id)
    crit = _critic_prompt(svc, monkeypatch, idea_id)
    assert "Channel guideline" not in crit
    assert "List in broken_rules" not in crit


def test_editor_rejects_idea_breaking_topic_rule(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea, _orch

    _activate(svc)
    orch = _orch(svc)
    a, b = _idea(svc, family="f"), _idea(svc, family="f")
    seen: list[str] = []

    def fake_ask(ctx: Any, role: str, user: str, schema: Any, *a_: Any, **kw: Any) -> Any:
        seen.append(user)
        return schema.model_validate({"selected_idea_ids": [a, b], "breaks": [f"{a}:G-topic", f"{b}:G-hook"]})

    monkeypatch.setattr(orch.editor, "ask", fake_ask)
    cands = [dict(svc.db.get("ideas", i)) for i in (a, b)]
    with svc.tracker.run("test") as run:
        chosen, turned_down = orch.editor._choose(run, orch.strategy.current(), cands, "exploit", "r")
    assert "Channel guideline (owner-approved; follow it):" in seen[0]
    assert chosen is not None and chosen["id"] == b
    row = svc.db.get("ideas", a)
    assert row["status"] == "rejected" and row["eic_notes"] == "breaks guideline G-topic"
    assert json.loads(row["guideline_breaks"]) == ["G-topic"]
    assert a in turned_down
    assert svc.db.get("ideas", b)["status"] == "candidate"  # G-hook is not a topic rule


def test_editor_prompt_unchanged_without_guideline(svc, monkeypatch) -> None:  # noqa: ANN001
    from tests.test_reliability import _idea, _orch

    orch = _orch(svc)
    a = _idea(svc, family="f")
    seen: list[str] = []

    def fake_ask(ctx: Any, role: str, user: str, schema: Any, *a_: Any, **kw: Any) -> Any:
        seen.append(user + schema.__name__)
        return schema.model_validate({"selected_idea_ids": [a]})

    monkeypatch.setattr(orch.editor, "ask", fake_ask)
    with svc.tracker.run("test") as run:
        orch.editor._choose(run, orch.strategy.current(), [dict(svc.db.get("ideas", a))], "exploit", "r")
    assert "Channel guideline" not in seen[0] and seen[0].endswith("SelectionDecision")


def test_critic_ignores_broken_rules_outside_script_and_title(g) -> None:  # noqa: ANN001
    r = _result()
    r.broken_rules = ["G-topic", "G-cover", "G-title"]
    out = _gate(_clean_draft(), g, r)
    assert [x.split(" (")[0] for x in out.required_revisions] == ["Breaks G-title"]
    # A topic rule alone does not fail the script.
    r = _result()
    r.broken_rules = ["G-topic"]
    out = _gate(_clean_draft(), g, r)
    assert out.required_revisions == [] and out.passed is True
    # The variant text of a tested script rule is still what the revision quotes.
    r = _result()
    r.broken_rules = ["G-hook"]
    out = _gate(_clean_draft(), g, r, replace={"G-hook": "Open on a question."})
    assert out.required_revisions == ["Breaks G-hook (Open on the surprise): Open on a question."]


def _hook_out(g: guideline.Guideline, opening: str) -> list[str]:
    return _gate(_draft(opening), g).required_revisions


def test_hook_too_long(g) -> None:  # noqa: ANN001
    opening = " ".join(["word"] * 20) + "."
    out = _gate(_draft(opening), g)
    assert "Hook is 20 words; the guideline allows 15" in out.required_revisions
    assert out.passed is False


def test_hook_question(g) -> None:  # noqa: ANN001
    out = _hook_out(g, "Why did Amalthea hide for centuries?")
    assert "Hook asks a question; the guideline forbids it" in out


def test_hook_definition(g) -> None:  # noqa: ANN001
    out = _hook_out(g, "The zodiacal light is a faint glow in the sky.")
    expected = 'Hook opens with a definition ("The zodiacal light is a faint glow in...");'
    assert any(x.startswith(expected) for x in out)


def test_hook_definition_curly_quote(g) -> None:  # noqa: ANN001
    out = _hook_out(g, "“The zodiacal light is a faint glow.”")
    assert any(x.startswith("Hook opens with a definition") for x in out)


@pytest.mark.parametrize(
    "opening",
    [
        "This town is the only place where clocks stop.",
        "There is a village that sits inside another country.",
        "Here is a border nobody can cross.",
    ],
)
def test_hook_not_definition(g, opening: str) -> None:  # noqa: ANN001
    assert not any(x.startswith("Hook opens with a definition") for x in _hook_out(g, opening))


def test_banned_opening_in_hook_line(g) -> None:  # noqa: ANN001
    draft = _clean_draft().model_copy(update={"hook_line": "Did you know this?"})
    out = _gate(draft, g).required_revisions
    assert out.count("Opening 'Did you know' is banned by the guideline") == 1


def test_hook_same_text_reported_once(g) -> None:  # noqa: ANN001
    opening = " ".join(["word"] * 20) + "."
    out = _hook_out(g, opening)
    assert out.count("Hook is 20 words; the guideline allows 15") == 1


def test_hook_empty_texts(g) -> None:  # noqa: ANN001
    from aimz.agents.critic import hook_revisions

    assert hook_revisions(["", "   "], g.checks) == []


def test_genre_line_prefers_guideline(svc: Any) -> None:
    from aimz import identity

    g = _activate(svc)
    assert "'explainer' (owner guideline v" in identity.genre_line(svc.db, g)


def test_genre_line_without_guideline_unchanged(svc: Any) -> None:
    from aimz import identity

    assert identity.genre_line(svc.db) == ""
    assert identity.propose(svc.db, "main", {"genre": "Maps and borders"}) is None
    assert (
        identity.genre_line(svc.db) == "This channel's genre is 'Maps and borders'. Every idea must fit it.\n"
    )
    assert identity.genre_line(svc.db, None) == identity.genre_line(svc.db)


def test_ideation_prompt_uses_guideline_genre(svc: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_reliability import _orch

    from aimz import identity
    from aimz.domain.models import SourceItemView

    _activate(svc)
    assert identity.propose(svc.db, "main", {"genre": "Maps and borders"}) is None
    orch = _orch(svc)
    seen: list[str] = []

    def fake_ask(ctx: Any, role: str, user: str, *a: Any, **kw: Any) -> Any:
        seen.append(user)
        raise _Stop

    monkeypatch.setattr(orch.ideation, "ask", fake_ask)
    leads = [SourceItemView(id="si_0", source_name="s", url="https://e.x/0", title="t0")]
    with svc.tracker.run("test") as run, pytest.raises(_Stop):
        orch.ideation.run(run, orch.strategy.current(), n_ideas=2, leads=leads)
    assert "owner guideline v" in seen[0]
    assert "Maps and borders" not in seen[0]
