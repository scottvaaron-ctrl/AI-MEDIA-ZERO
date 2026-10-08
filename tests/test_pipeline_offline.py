"""End-to-end offline pipeline: fixture feed -> ideas -> selection -> script/QA -> (render if ffmpeg) -> package -> learn."""

from __future__ import annotations

from pathlib import Path

import pytest

from aimz.domain.models import FetchedItem
from aimz.pipeline.orchestrator import Orchestrator
from aimz.providers.base import HealthStatus, ProviderContext, ResearchProvider

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "feeds" / "sample_rss.xml"


class FixtureFeedProvider(ResearchProvider):
    name = "FixtureFeedProvider"
    kinds = ("rss", "atom", "wikipedia_onthisday", "wikipedia_featured", "reddit_rss")

    def health(self) -> HealthStatus:
        return HealthStatus(True, "fixture feed")

    def fetch(self, ctx: ProviderContext, source: dict) -> list[FetchedItem]:
        import feedparser

        with self.authorized(ctx, "feed_fetch"):
            parsed = feedparser.parse(FIXTURE.read_bytes())
        return [
            FetchedItem(
                url=e.link, title=e.title, summary=e.summary, published_at="2026-09-01T00:00:00+00:00"
            )
            for e in parsed.entries
        ]


@pytest.fixture()
def orch(svc):  # noqa: ANN001, ANN201
    fp = FixtureFeedProvider()
    svc.research = {k: fp for k in fp.kinds}
    return Orchestrator(svc, seed=7)


def test_research_dedupes_and_stores(svc, orch) -> None:  # noqa: ANN001
    with svc.tracker.run("research") as run:
        stats = orch.research.run(run)
    assert stats["new_items"] >= 5 and stats["errors"] == 0
    n = svc.db.count("source_items")
    with svc.tracker.run("research") as run:
        stats2 = orch.research.run(run)
    assert stats2["new_items"] == 0 and svc.db.count("source_items") == n  # idempotent


def test_full_cycle_offline(svc, orch, ffmpeg_available: bool) -> None:  # noqa: ANN001
    summary = orch.cycle()
    assert summary["research"]["new_items"] >= 5
    assert summary["ideation"]["ideas"] >= 3
    assert summary["selection"]["selected"] >= 1
    assert svc.db.count("ideas", "status IN ('selected','scripted','produced','published')") >= 1
    assert svc.db.count("scripts", "status='approved'") + svc.db.count("videos") >= 1
    assert svc.db.count("claims") >= 1
    # provider usage tracked, no spend, no denials
    assert svc.db.count("provider_usage") > 5
    assert svc.budget.snapshot().spent_usd == 0.0 and svc.budget.snapshot().denied_count == 0
    # strategy memory advanced
    versions = orch.strategy.history()
    assert len(versions) >= 2 and versions[0]["created_by"] == "ai"
    assert (svc.env.config_dir / "strategy.md").read_text(encoding="utf-8").startswith("# Strategy Memory")
    # no experiment is seeded by code: experiments are the strategist's own (owner decision 2026-09-23)
    assert svc.db.count("experiments", "created_by='ai' AND name LIKE 'Numeric vs narrative%'") == 0
    # learning summary written
    assert list((svc.env.data_dir / "reports").glob("daily_*.md"))
    if ffmpeg_available:
        vid = svc.db.one("SELECT * FROM videos WHERE status IN ('rendered','published')")
        assert vid is not None, dict(svc.db.one("SELECT * FROM videos") or {})
        assert Path(vid["file_path"]).exists() and vid["duration_s"] > 5
        pubs = {r["platform"]: dict(r) for r in svc.db.query("SELECT * FROM publications")}
        assert pubs["tiktok"]["status"] == "packaged" and pubs["youtube"]["status"] == "packaged"
        pkg = Path(pubs["tiktok"]["package_dir"])
        for name in (
            "video.mp4",
            "caption.txt",
            "metadata.json",
            "sources.json",
            "ai_disclosure.txt",
            "posting_notes.md",
            "cover.png",
            "tiktok_metadata.json",
        ):
            assert (pkg / name).exists(), name
        ypkg = Path(pubs["youtube"]["package_dir"])
        assert (ypkg / "youtube_request_body.json").exists()
        # no API writes happened
        assert svc.db.count("provider_usage", "action='youtube_upload'") == 0
    # elevated-review / approval gating: nothing marked published without owner
    assert svc.db.count("videos", "status='published'") == 0


def test_manual_metrics_feed_learning(svc, orch, ffmpeg_available: bool) -> None:  # noqa: ANN001
    if not ffmpeg_available:
        pytest.skip("ffmpeg required for a rendered publication")
    from aimz.domain.models import MetricsSnapshot

    orch.cycle()
    pub = dict(svc.db.one("SELECT * FROM publications WHERE platform='tiktok'"))
    orch.publisher.mark_posted(pub["id"], url="https://www.tiktok.com/@x/video/1")
    svc.analytics_store.record(
        dict(svc.db.get("publications", pub["id"])),
        MetricsSnapshot(
            views=1200, avg_percent_viewed=62.0, shares=9, followers_gained=4, completion_rate=40.0
        ),
        source="manual",
    )
    perf = svc.analytics_store.video_performance()
    assert len(perf) == 1 and perf[0]["score"] is not None and 0.3 < perf[0]["score"] < 1.0
    with svc.tracker.run("learn") as run:
        out = orch.analyst.learn(run, orch.strategy.current())
    fam = perf[0]["content_family"]
    assert out["evidence"]["families"][fam]["n"] == 1
    state = orch.strategy.current()
    assert state["families"][fam]["n"] == 1 and state["families"][fam]["status"] in {"testing", "new"}
    assert orch.analyst.milestone()["measured_videos"] == 1
    report = orch.analyst.review("weekly")
    assert report.exists() and "Which hooks worked" in report.read_text(encoding="utf-8")


def test_dry_run_cycle_follows_guideline(svc, ffmpeg_available: bool) -> None:  # noqa: ANN001
    """Guideline dry run (2026-10-07): the sample guideline drives a whole offline cycle.

    Locked settings are recorded with source 'guideline'; a forced G-caption_max_words experiment
    (variant 5 against the guideline's 3) gives one control video at 3 and one treatment video at 5.
    """
    import json

    from aimz.domain.models import ExperimentProposal

    if not ffmpeg_available:
        pytest.skip("ffmpeg required for production")
    sample = Path(__file__).resolve().parent / "fixtures" / "guideline_sample.md"
    # The fixture LLM writes 80-character titles; the sample's 60-character check would fail nearly every
    # script, so the temporary copy relaxes that one check. The fixture LLM's hooks are questions, so the
    # hook question and length checks are relaxed too. The locked settings stay as the sample has them.
    text = (
        sample.read_text(encoding="utf-8")
        .replace("title_max_chars: 60", "title_max_chars: 100")
        .replace("hook_no_question: true", "hook_no_question: false")
        .replace("hook_max_words: 15", "hook_max_words: 40")
    )
    assert (
        "title_max_chars: 100" in text and "hook_no_question: false" in text and "hook_max_words: 40" in text
    )
    (svc.env.config_dir / "guideline.md").write_text(text, encoding="utf-8")
    fp = FixtureFeedProvider()
    svc.research = {k: fp for k in fp.kinds}
    orch = Orchestrator(svc, seed=7)
    assert orch.experiments.guideline is not None

    # Cycle 1 under the guideline: no experiment yet, so every locked setting comes from the guideline.
    orch.cycle(produce_limit=1)
    first = svc.db.query("SELECT id FROM videos")
    assert first, "the first cycle produced no video"
    locked = {"caption_max_words": 3, "caption_size": 0.04, "music": "off"}
    for v in first:
        rows = {
            r["variable"]: r for r in svc.db.query("SELECT * FROM video_settings WHERE video_id=?", [v["id"]])
        }
        for key, value in locked.items():
            assert rows[key]["source"] == "guideline", key
            assert json.loads(rows[key]["value"]) == value, key

    # Force the experiment, then run until a control and a treatment video exist.
    eid = orch.experiments.propose(
        ExperimentProposal(
            name="Caption words 3 vs 5",
            hypothesis="Longer caption chunks hold attention.",
            variable="",
            control="",
            treatment="",
            primary_kpi="score",
            guideline_item="G-caption_max_words",
            variant="5",
        )
    )
    assert eid is not None
    exp = svc.db.get("experiments", eid)
    assert exp["status"] == "running" and exp["variable"] == "G-caption_max_words"
    assert exp["guideline_version"] == orch.experiments.guideline.version and exp["min_sample"] == 10

    seen: dict[str, tuple[object, str]] = {}
    for _ in range(6):
        orch.cycle(stages=["research", "ideas", "select", "write", "produce"], produce_limit=2)
        for r in svc.db.query(
            "SELECT i.experiment_arm arm, vs.value, vs.source FROM videos v "
            "JOIN ideas i ON i.id=v.idea_id JOIN video_settings vs ON vs.video_id=v.id "
            "WHERE i.experiment_id=? AND vs.variable='caption_max_words'",
            [eid],
        ):
            seen[r["arm"]] = (json.loads(r["value"]), r["source"])
        if {"control", "treatment"} <= set(seen):
            break
    assert seen.get("control") == (3, "guideline"), seen
    assert seen.get("treatment") == (5, "experiment"), seen
