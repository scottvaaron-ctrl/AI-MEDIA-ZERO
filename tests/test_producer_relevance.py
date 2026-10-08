"""The picture-matching gate in the producer: score previews, fetch the best passing candidate, else a text card."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from aimz.domain.models import AssetCandidate, AssetRecord, Beat
from aimz.util import new_id, now_iso


def _cand(title: str, lic: str = "CC BY 4.0") -> AssetCandidate:
    return AssetCandidate(
        provider="Fake",
        title=title,
        page_url=f"https://example.org/{title}",
        file_url=f"https://example.org/{title}.jpg",
        license=lic,
        attribution=f"{title} by X ({lic})",
    )


class FakeProvider:
    name = "Fake"

    def __init__(
        self,
        svc: Any,
        by_query: dict[str, list[AssetCandidate]],
        unscorable: set[str] | None = None,
        failing: set[str] | None = None,
    ) -> None:
        self.svc = svc
        self.by_query = by_query
        self.unscorable = unscorable or set()
        self.failing = failing or set()
        self.searched: list[str] = []
        self.previewed: list[str] = []
        self.fetched: list[str] = []
        self.img = svc.env.data_dir / "fake.png"
        Image.new("RGB", (64, 64), (90, 120, 160)).save(self.img)

    def search(self, ctx: Any, query: str, max_results: int = 5) -> list[AssetCandidate]:
        self.searched.append(query)
        return list(self.by_query.get(query, []))[:max_results]

    def preview(self, ctx: Any, candidate: AssetCandidate, width: int = 320) -> Image.Image | None:
        self.previewed.append(candidate.title)
        if candidate.title in self.unscorable:
            return None
        img = Image.new("RGB", (8, 8))
        img.info["title"] = candidate.title
        return img

    def fetch(self, ctx: Any, candidate: AssetCandidate, dest: Path) -> AssetRecord | None:
        self.fetched.append(candidate.title)
        if candidate.title in self.failing:
            return None
        rid = f"asset_{candidate.title}_{new_id('x')[-6:]}"
        self.svc.db.insert(
            "assets",
            {
                "id": rid,
                "provider": "Fake",
                "kind": "image",
                "title": candidate.title,
                "file_path": str(self.img),
                "created_at": now_iso(),
            },
        )
        return AssetRecord(
            id=rid,
            provider="Fake",
            kind="image",
            title=candidate.title,
            file_path=str(self.img),
            license=candidate.license,
            attribution=candidate.attribution,
        )


class FakeScorer:
    """Scores by candidate title; ``scores`` keys are a title or (title, text)."""

    def __init__(self, scores: dict[Any, float], raises: bool = False) -> None:
        self.scores = scores
        self.raises = raises
        self.calls: list[tuple[list[str], str]] = []

    def score_many(self, images: list[Image.Image], text: str) -> list[float]:
        titles = [str(im.info.get("title")) for im in images]
        self.calls.append((titles, text))
        if self.raises:
            raise RuntimeError("onnx session exploded")
        return [self.scores.get((t, text), self.scores.get(t, 0.0)) for t in titles]


def _gate(svc: Any, monkeypatch: pytest.MonkeyPatch, scorer: Any, **cfg: Any) -> None:
    rel = {
        "enabled": True,
        "text": "narration",
        "threshold": 0.2,
        "by_sa_penalty": 0.05,
        "preview_width": 320,
    }
    rel.update(cfg)
    svc.config.raw.setdefault("assets", {})["relevance"] = rel
    from aimz.providers.image import relevance

    monkeypatch.setattr(relevance, "get_scorer", lambda _svc: scorer)


def _producer(svc: Any) -> Any:
    from aimz.agents.producer import ProducerAgent

    return ProducerAgent(svc, None)


def _assemble(producer: Any, svc: Any, beats: list[Beat], run_id: str = "run_1") -> Any:
    chosen = producer.default_settings()
    out = svc.env.data_dir / f"out_{new_id('o')[-6:]}"
    return producer.assemble(svc.ctx(run_id), beats, chosen, out, "vid_1", "Title Words Here", "AI narration")


# ---- ranking -----------------------------------------------------------------------------------------------


def test_rank_candidates_threshold_and_penalty() -> None:
    from aimz.agents.producer import rank_candidates

    a, b, c = _cand("A", "CC BY-SA 4.0"), _cand("B", "CC BY 4.0"), _cand("C")
    assert rank_candidates([(a, 0.30), (b, 0.28), (c, 0.10)], 0.2, 0.05) == [b, a]


# ---- choosing within one query -------------------------------------------------------------------------------


def test_best_passing_is_only_fetch(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    prov = FakeProvider(svc, {"q": [_cand("one"), _cand("two"), _cand("three")]})
    svc.assets = [prov]
    scorer = FakeScorer({"one": 0.3, "two": 0.6, "three": 0.1})
    _gate(svc, monkeypatch, scorer)
    rec = _producer(svc)._find_asset(svc.ctx("run_1"), "q", svc.env.data_dir / "a", text="narration words")
    assert rec is not None and rec.title == "two"
    assert prov.fetched == ["two"]
    assert scorer.calls == [(["one", "two", "three"], "narration words")]


def test_failed_fetch_tries_next_passing(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    prov = FakeProvider(svc, {"q": [_cand("one"), _cand("two"), _cand("three")]}, failing={"two"})
    svc.assets = [prov]
    _gate(svc, monkeypatch, FakeScorer({"one": 0.3, "two": 0.6, "three": 0.1}))
    rec = _producer(svc)._find_asset(svc.ctx("run_1"), "q", svc.env.data_dir / "a", text="t")
    assert rec is not None and rec.title == "one"
    assert prov.fetched == ["two", "one"]


def test_unscorable_preview_does_not_pass(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    prov = FakeProvider(svc, {"q": [_cand("one"), _cand("two")]}, unscorable={"two"})
    svc.assets = [prov]
    scorer = FakeScorer({"one": 0.3, "two": 0.9})
    _gate(svc, monkeypatch, scorer)
    rec = _producer(svc)._find_asset(svc.ctx("run_1"), "q", svc.env.data_dir / "a", text="t")
    assert rec is not None and rec.title == "one"
    assert prov.fetched == ["one"]
    assert scorer.calls == [(["one"], "t")]


# ---- across queries and beats -----------------------------------------------------------------------------


def test_no_pass_first_query_then_second_query(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from aimz.agents.producer import asset_queries

    q1, q2 = asset_queries("Jupiter moon discovery", "Title Words Here")[:2]
    prov = FakeProvider(svc, {q1: [_cand("weak")], q2: [_cand("good")]})
    svc.assets = [prov]
    _gate(svc, monkeypatch, FakeScorer({"weak": 0.05, "good": 0.5}))
    beats = [
        Beat(
            narration="Amalthea was found in 1892.",
            visual_query="Jupiter moon discovery",
            visual_type="image",
        )
    ]
    scenes, *_ = _assemble(_producer(svc), svc, beats)
    assert prov.searched[:2] == [q1, q2]
    assert prov.fetched == ["good"]
    assert scenes[0].asset_id is not None and scenes[0].asset_id.startswith("asset_good")


def test_no_pass_any_query_gives_text_card(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from aimz.agents.producer import asset_queries

    queries = asset_queries("Jupiter moon discovery", "Title Words Here")
    prov = FakeProvider(svc, {q: [_cand(f"weak{i}")] for i, q in enumerate(queries)})
    svc.assets = [prov]
    _gate(svc, monkeypatch, FakeScorer({}))
    beats = [
        Beat(
            narration="Amalthea was found in 1892.",
            visual_query="Jupiter moon discovery",
            visual_type="image",
        )
    ]
    scenes, attributions, first_image, _ = _assemble(_producer(svc), svc, beats)
    assert prov.searched == queries
    assert prov.fetched == []
    assert scenes[0].asset_id is None and first_image is None and attributions == []


def test_disabled_config_first_download_wins(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    prov = FakeProvider(svc, {"Jupiter moon discovery": [_cand("one"), _cand("two")]})
    svc.assets = [prov]
    scorer = FakeScorer({"two": 0.9})
    _gate(svc, monkeypatch, scorer, enabled=False)
    from aimz.providers.image import relevance

    called: list[Any] = []
    monkeypatch.setattr(relevance, "get_scorer", lambda _svc: called.append(1) or scorer)
    beats = [
        Beat(
            narration="Amalthea was found in 1892.",
            visual_query="Jupiter moon discovery",
            visual_type="image",
        )
    ]
    scenes, *_ = _assemble(_producer(svc), svc, beats)
    assert prov.previewed == [] and called == [] and scorer.calls == []
    assert prov.fetched == ["one"]
    assert scenes[0].asset_id is not None and scenes[0].asset_id.startswith("asset_one")
    assert svc.db.get_state("relevance_unavailable") is None


def test_scorer_missing_falls_back_and_flags(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from aimz.core.attention import attention

    prov = FakeProvider(svc, {"Jupiter moon discovery": [_cand("one"), _cand("two")]})
    svc.assets = [prov]
    _gate(svc, monkeypatch, None)
    beats = [
        Beat(
            narration="Amalthea was found in 1892.",
            visual_query="Jupiter moon discovery",
            visual_type="image",
        )
    ]
    scenes, *_ = _assemble(_producer(svc), svc, beats)
    assert prov.previewed == [] and prov.fetched == ["one"]
    reason = svc.db.get_state("relevance_unavailable")
    assert reason
    items = attention(svc.db)["picture_check_unavailable"]
    assert items[0]["why"] == f"picture-matching check unavailable: {reason}"
    assert items[0]["fix"] == "python -m aimz doctor --fix"


def test_scorer_raises_falls_back_for_rest_of_run(
    svc,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,  # noqa: ANN001
) -> None:
    prov = FakeProvider(
        svc,
        {
            "Jupiter moon discovery": [_cand("one"), _cand("two")],
            "Saturn ring system": [_cand("three"), _cand("four")],
        },
    )
    svc.assets = [prov]
    scorer = FakeScorer({"two": 0.9, "four": 0.9}, raises=True)
    _gate(svc, monkeypatch, scorer)
    beats = [
        Beat(
            narration="Amalthea was found in 1892.",
            visual_query="Jupiter moon discovery",
            visual_type="image",
        ),
        Beat(narration="Saturn has bright rings.", visual_query="Saturn ring system", visual_type="image"),
    ]
    producer = _producer(svc)
    with caplog.at_level(logging.WARNING):
        scenes, *_ = _assemble(producer, svc, beats)
    assert len(scorer.calls) == 1  # never asked again after it raised
    assert prov.fetched == ["one", "three"]  # first download wins on both beats
    warnings = [r for r in caplog.records if "picture-matching" in r.getMessage()]
    assert len(warnings) == 1
    assert "onnx session exploded" in (svc.db.get_state("relevance_unavailable") or "")
    # the same producer instance stays on first-download-wins for the rest of the run
    prov.fetched.clear()
    _assemble(producer, svc, beats[:1])
    assert len(scorer.calls) == 1 and prov.fetched == ["one"]


def test_success_clears_unavailable_state(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    svc.db.set_state("relevance_unavailable", "model missing")
    prov = FakeProvider(svc, {"q": [_cand("one")]})
    svc.assets = [prov]
    _gate(svc, monkeypatch, FakeScorer({"one": 0.5}))
    _producer(svc)._find_asset(svc.ctx("run_1"), "q", svc.env.data_dir / "a", text="t")
    assert not svc.db.get_state("relevance_unavailable")


def test_relevance_run_note(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from aimz.agents.producer import asset_queries

    queries = asset_queries("Saturn ring system", "The Discovery of Amalthea")
    prov = FakeProvider(
        svc,
        {"Jupiter moon discovery": [_cand("one"), _cand("two")], **{q: [_cand("weak")] for q in queries}},
    )
    svc.assets = [prov]
    _gate(svc, monkeypatch, FakeScorer({"one": 0.1, "two": 0.7, "weak": 0.05}))
    beats = [
        Beat(
            narration="Amalthea was found in 1892.",
            visual_query="Jupiter moon discovery",
            visual_type="image",
        ),
        Beat(narration="Saturn has bright rings.", visual_query="Saturn ring system", visual_type="image"),
        Beat(narration="A quote from someone.", visual_query="quote", visual_type="quote_card"),
    ]
    idea_id = new_id("idea")
    svc.db.insert(
        "ideas",
        {
            "id": idea_id,
            "title": "idea",
            "premise": "p",
            "hook": "h",
            "hook_type": "narrative_hook",
            "content_family": "forgotten_history",
            "target_platform": "both",
            "source_item_ids_json": "[]",
            "scores_json": "{}",
            "opportunity_score": 50,
            "status": "scripted",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    script_id = new_id("scr")
    svc.db.insert(
        "scripts",
        {
            "id": script_id,
            "idea_id": idea_id,
            "title": "The Discovery of Amalthea",
            "hook_line": "h",
            "beats_json": "[" + ",".join(b.model_dump_json() for b in beats) + "]",
            "narration_text": "x",
            "status": "approved",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )

    class FakeRenderer:
        def render(self, ctx: Any, timeline: Any, out_dir: Path) -> Any:
            return SimpleNamespace(
                duration_s=timeline.total_duration_s, video_path=str(out_dir / "v.mp4"), captions_path=""
            )

    svc.renderer = FakeRenderer()
    producer = _producer(svc)
    with svc.tracker.run("produce") as run:
        video_id = producer.produce(run, script_id)
    note = run.summary["relevance"][video_id]
    beats_note = note["beats"]
    assert [b["beat"] for b in beats_note] == [0, 1]  # quote_card beats are not searched
    first = beats_note[0]
    assert first["queries"][0] == {"query": "Jupiter moon discovery", "candidates": 2, "top_score": 0.7}
    assert first["chosen"].startswith("asset_two")
    assert beats_note[1]["chosen"] == "text_card"
    assert [q["query"] for q in beats_note[1]["queries"]] == queries
    assert note["chosen"] == 1 and note["text_card"] == 1


def test_text_signal_visual_query_and_max(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    beat = Beat(
        narration="Amalthea was found in 1892.", visual_query="Jupiter moon discovery", visual_type="image"
    )

    # visual_query: the scorer sees the visual query string
    prov = FakeProvider(svc, {"Jupiter moon discovery": [_cand("one")]})
    svc.assets = [prov]
    scorer = FakeScorer({"one": 0.5})
    _gate(svc, monkeypatch, scorer, text="visual_query")
    _assemble(_producer(svc), svc, [beat])
    assert [t for _, t in scorer.calls] == ["Jupiter moon discovery"]

    # max: both strings are scored (one call each) and the higher score decides
    prov = FakeProvider(svc, {"Jupiter moon discovery": [_cand("one"), _cand("two")]})
    svc.assets = [prov]
    scorer = FakeScorer(
        {
            ("one", beat.narration): 0.25,
            ("one", "Jupiter moon discovery"): 0.05,
            ("two", beat.narration): 0.05,
            ("two", "Jupiter moon discovery"): 0.4,
        }
    )
    _gate(svc, monkeypatch, scorer, text="max")
    scenes, *_ = _assemble(_producer(svc), svc, [beat])
    assert sorted(t for _, t in scorer.calls) == sorted([beat.narration, "Jupiter moon discovery"])
    assert prov.fetched == ["two"]
    assert scenes[0].asset_id is not None and scenes[0].asset_id.startswith("asset_two")
