"""Titles and hooks are claims (owner decision 2026-10-01; YouTube misleading-metadata policy)."""

from __future__ import annotations

from aimz.agents.factcheck import FactCheckAgent, unsupported_title_specifics
from aimz.domain.models import Beat, ClaimVerdict, FactCheckResult, ScriptDraft, SourceItemView

SOURCE = SourceItemView(
    id="src_1",
    source_name="News",
    url="https://example.org/fire",
    title="Fire at $3.2 billion data center blinds fire crews",
    summary="Smoke cut the fire department's cameras at the facility, which cost $3.2 billion to build.",
)


def _draft(title: str, hook: str, narration: str) -> ScriptDraft:
    beats = [Beat(narration=narration)] + [
        Beat(narration=f"Filler sentence number {i} keeps this draft valid for the schema check here.")
        for i in range(3)
    ]
    return ScriptDraft(title=title, hook_line=hook, beats=beats)


def test_a_title_figure_the_narration_never_says_is_flagged() -> None:
    # The audit's case: the figure is in the source, but the video never says it.
    d = _draft(
        "The $3.2 Billion AI Data Center Fire",
        "Smoke blinded the fire crews.",
        "Smoke blinded the fire crews.",
    )
    assert unsupported_title_specifics(d, [SOURCE]) == ["3.2"]


def test_a_title_figure_that_is_sourced_and_said_passes() -> None:
    d = _draft(
        "The $3.2 Billion Data Center That Went Dark",
        "Smoke blinded the fire crews.",
        "The facility cost $3.2 billion, and smoke blinded the fire crews.",
    )
    assert unsupported_title_specifics(d, [SOURCE]) == []


def test_an_unsourced_year_in_the_hook_is_flagged() -> None:
    d = _draft("A Fire Nobody Could See", "In 1927 everything changed.", "In 1927 everything changed.")
    assert unsupported_title_specifics(d, [SOURCE]) == ["1927"]


def test_a_title_the_model_finds_contradicted_blocks_approval(svc) -> None:  # noqa: ANN001
    agent = FactCheckAgent.__new__(FactCheckAgent)
    agent.svc = svc
    d = _draft("NASA Finds Complex Life Defying Heat", "Life survives the heat.", "Life survives the heat.")
    result = FactCheckResult(
        verdicts=[
            ClaimVerdict(claim_index=0, status="contradicted", note="NASA funded the study; others did it"),
            ClaimVerdict(claim_index=1, status="supported"),
        ],
        overall="pass",
    )
    svc.db.insert(
        "ideas",
        {
            "id": "i",
            "title": "t",
            "premise": "p",
            "hook": "h",
            "content_family": "x",
            "target_platform": "both",
            "source_item_ids_json": "[]",
            "scores_json": "{}",
            "opportunity_score": 1,
            "created_at": "2026-10-01T00:00:00+00:00",
            "updated_at": "2026-10-01T00:00:00+00:00",
        },
    )
    svc.db.insert(
        "scripts",
        {
            "id": "s",
            "idea_id": "i",
            "title": "t",
            "hook_line": "h",
            "beats_json": "[]",
            "narration_text": "x",
            "created_at": "2026-10-01T00:00:00+00:00",
            "updated_at": "2026-10-01T00:00:00+00:00",
        },
    )
    revisions = agent._apply("s", d, result, [], [])  # no claims listed: index 0 is the title, 1 the hook
    assert any(r.startswith("TITLE is contradicted") for r in revisions)
    assert not any(r.startswith("HOOK") for r in revisions)


def test_allegations_about_a_named_person_are_routed_to_the_owner() -> None:
    from aimz.agents.critic import allegation_sentences

    unitree = (
        "Unitree Robotics has become a leader in affordable humanoid robots. "
        "Founder Wang Xingxing’s extreme micromanagement and cost-cutting focus have driven its success."
    )
    assert allegation_sentences(unitree) == [
        "Founder Wang Xingxing's extreme micromanagement and cost-cutting focus have driven its success."
    ]
    assert (
        allegation_sentences("The probe reached Jupiter in 2016. Its cameras were exploited for science.")
        == []
    )
    assert allegation_sentences("Engineers blamed the cold weather for the failure.") == []


def test_descriptions_and_tags_carry_no_internal_labels() -> None:
    from aimz.agents.publisher import public_summary, public_tags

    s = {
        "title": "The Fire",
        "description": "Sources: [src_20260919T152716_2463ea2a]",
        "hook_line": "Smoke blinded the crews.",
    }
    assert public_summary(s) == "The Fire. Smoke blinded the crews."
    s2 = {"title": "The Fire", "description": "How smoke blinded a fire crew [src_1a2b].", "hook_line": "x"}
    assert public_summary(s2) == "How smoke blinded a fire crew."
    assert public_summary({"title": "The Fire", "description": "The Fire", "hook_line": ""}) == "The Fire"
    idea = {"content_family": "corporate_failures", "hook_type": "narrative_hook", "angle": "mystery"}
    assert public_tags(
        ["robots", "corporate_failures", "maps_data_stories", "Robots", "mystery", "China"], idea
    ) == [
        "robots",
        "China",
    ]


def test_an_air_crash_investigation_is_not_financial_advice() -> None:
    import re

    from aimz.agents.critic import ELEVATED_KEYWORDS

    def hits(text: str) -> bool:
        low = text.lower()
        return any(re.search(r"\b" + re.escape(k), low) for k in ELEVATED_KEYWORDS["financial advice"])

    assert not hits("Investigators found the transponder was off. The investigation took two years.")
    assert hits("You should invest in this coin before it rises.")
