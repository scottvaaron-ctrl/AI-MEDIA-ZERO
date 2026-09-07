from __future__ import annotations

from aimz.agents.factcheck import remove_sentences_containing, unsupported_specifics
from aimz.domain.models import Beat, ScriptDraft, SourceItemView

SRC = [
    SourceItemView(
        id="src_1",
        source_name="s",
        url="u",
        title="The 1893 Panic",
        summary="In 1893 the bank failed with 500 depositors and $2,000,000 in losses.",
    )
]


def _draft(*narrations: str) -> ScriptDraft:
    return ScriptDraft(
        title="Test title",
        hook_line="Hook line here",
        beats=[Beat(narration=n) for n in narrations]
        + [
            Beat(
                narration="The filler beats below only exist to satisfy the minimum script length in tests."
            ),
            Beat(
                narration="They carry no numbers or years so the deterministic checks ignore them entirely."
            ),
            Beat(narration="Each one is a full sentence of ordinary words that adds nothing to the claims."),
            Beat(narration="The final filler beat closes the script and points viewers to the sources list."),
        ],
    )


def test_specifics_present_in_sources_are_not_flagged() -> None:
    d = _draft("In 1893 the bank failed.", "There were 500 depositors and 2,000,000 dollars lost.")
    assert unsupported_specifics(d, SRC) == []


def test_unsupported_numbers_and_years_are_flagged() -> None:
    d = _draft("In 1901 the bank failed.", "It had 12,000 branches.")
    assert unsupported_specifics(d, SRC) == ["12000", "1901"]


def test_remove_sentences_with_unsupported_specifics_keeps_beat_nonempty() -> None:
    d = _draft("In 1901 the bank failed. Everyone remembers it.", "It had 12,000 branches.")
    removed = remove_sentences_containing(d, ["1901", "12000"])
    assert removed == 2
    assert d.beats[0].narration == "Everyone remembers it."
    assert (
        d.beats[1].narration == "It had 12,000 branches."
    )  # sole sentence is preserved, flagged for revision instead
