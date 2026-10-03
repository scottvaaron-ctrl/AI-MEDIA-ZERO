"""Channel identity: distinct genre, voice and look per channel, chosen by the AI (plan stage C11)."""

from __future__ import annotations

import random

from aimz import identity, instances
from aimz.experiments import shared
from aimz.experiments.settings import CATALOG, SettingsEngine


def _other_channel(genre: str, voice: str, template: str = "card", hue: float = 0.5) -> None:
    instances.write_shared(
        "space",
        {
            "niches": [],
            "identity": {"genre": genre, "voice": voice, "template": template, "palette_hue": hue},
            "used_sources": ["hash_taken"],
        },
    )


def test_the_ai_sets_an_identity_distinct_from_other_channels(svc) -> None:  # noqa: ANN001
    _other_channel("Space explainers", "en_US-norman-medium")
    db = svc.db
    assert "channel 'space'" in (identity.propose(db, "main", {"genre": "space  EXPLAINERS!"}, CATALOG) or "")
    assert "voice" in (
        identity.propose(
            db, "main", {"genre": "Silent film stories", "voice": "en_US-norman-medium"}, CATALOG
        )
        or ""
    )
    assert "look" in (
        identity.propose(
            db, "main", {"genre": "Silent film stories", "template": "card", "palette_hue": 0.51}, CATALOG
        )
        or ""
    )
    assert "not one of" in (
        identity.propose(
            db, "main", {"genre": "Silent film stories", "voice": "en_US-lessac-medium"}, CATALOG
        )
        or ""
    )
    ok = identity.propose(
        db,
        "main",
        {
            "genre": "Silent film stories",
            "voice": "en_US-kristin-medium",
            "template": "full_bleed",
            "palette_hue": 0.1,
        },
        CATALOG,
    )
    assert ok is None
    assert identity.fixed_settings(db) == {
        "voice": "en_US-kristin-medium",
        "template": "full_bleed",
        "palette_hue": 0.1,
    }


def test_a_genre_is_kept_for_ten_videos_before_it_can_change(svc, monkeypatch) -> None:  # noqa: ANN001
    db = svc.db
    assert identity.propose(db, "main", {"genre": "Maps and borders"}) is None
    count = {"n": 3}
    monkeypatch.setattr(identity, "published_count", lambda db: count["n"])
    assert (
        identity.propose(db, "main", {"genre": "Maps and borders"}) is None
    )  # same genre, other fields free
    assert "until 10 videos" in (identity.propose(db, "main", {"genre": "Inventions"}) or "")
    count["n"] = 10
    assert identity.propose(db, "main", {"genre": "Inventions"}) is None
    ident = identity.load(db)
    assert ident["genre"] == "Inventions" and ident["history"][0]["genre"] == "Maps and borders"


def test_identity_fixes_voice_and_look_and_other_channels_voices_are_excluded(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    rng = random.Random(3)
    fixed = {"voice": "en_GB-cori-high", "template": "ranking"}
    a = eng.assign("v1", {}, {"voice": None, "template": "card"}, [], rng, fixed=fixed)
    assert a.values["voice"] == "en_GB-cori-high" and a.sources["voice"] == "identity"
    assert a.values["template"] == "ranking"
    taken = {"en_US-kristin-medium", "en_US-john-medium", "en_US-norman-medium"}
    seen = {
        eng.assign(f"v{i}", {}, {"voice": None}, [], rng, exclude={"voice": taken}).values["voice"]
        for i in range(2, 40)
    }
    assert seen and not (seen & taken)


def test_a_story_another_channel_used_is_not_offered_again(svc) -> None:  # noqa: ANN001
    _other_channel("Space explainers", "en_US-norman-medium")
    assert shared.sources_used_elsewhere("main") == {"hash_taken"}
    assert shared.sources_used_elsewhere("space") == set()  # a channel never blocks itself


def test_the_strategist_prompt_offers_the_owners_starting_genres(svc) -> None:  # noqa: ANN001
    text = identity.prompt_text(svc.db, "main", ["space and astronomy explainers", "silent-film era stories"])
    assert "no identity yet" in text and "silent-film era stories" in text
    assert svc.config.get("channels.starting_genres")  # the owner's guidance ships in config.yaml
