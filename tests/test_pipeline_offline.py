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
    # experiment seeded at cold start
    assert svc.db.count("experiments", "status='running'") == 1
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
