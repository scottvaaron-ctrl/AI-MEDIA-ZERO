"""Score fairness, elevated-review evidence, degraded runs, and full-text sources (2026-09-23 fixes)."""

from __future__ import annotations

from typing import Any

import pytest

from aimz.agents.base import Agent, relevant_excerpt
from aimz.agents.critic import CriticAgent
from aimz.domain.models import (
    Beat,
    CriticResult,
    FactCheckResult,
    MetricsSnapshot,
    ScriptDraft,
    SourceItemView,
)
from aimz.providers.analytics.store import performance_score
from aimz.providers.research.rss import RSSResearchProvider, article_text_from_html, wikipedia_title

# -- score ------------------------------------------------------------------------------


def test_few_views_cannot_outscore_many_on_retention_alone() -> None:
    # Real channel numbers: before the fix, 19 views scored ~0.85 and 252 views ~0.11.
    tiny = {"views": 19, "avg_percent_viewed": 85.27, "shares": 0, "subscribers_gained": 0}
    big = {"views": 252, "avg_percent_viewed": 24.0, "shares": 0, "subscribers_gained": 0}
    s_tiny, s_big = performance_score(tiny), performance_score(big)
    assert s_tiny is not None and s_big is not None
    assert s_tiny - s_big < 0.1


def test_share_rate_is_smoothed_not_gated() -> None:
    # One share on five views must not read as 200 shares per 1,000.
    s = performance_score({"views": 5, "shares": 1})
    assert s is not None and s < 0.4


def test_no_views_means_no_score() -> None:
    assert performance_score({"views": None, "shares": 0, "followers_gained": 0}) is None


def _publication(svc: Any, posted_at: str) -> dict[str, Any]:
    from aimz.util import now_iso

    svc.db.execute("PRAGMA foreign_keys=OFF")  # a publication without the idea/script/video chain behind it
    svc.db.insert(
        "publications",
        {
            "id": "pub_t",
            "video_id": "vid_t",
            "platform": "youtube",
            "publisher": "YouTubePublisher",
            "mode": "public",
            "idempotency_key": "k",
            "status": "uploaded",
            "attempts": 1,
            "posted_at": posted_at,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    return dict(svc.db.get("publications", "pub_t"))


def test_young_api_snapshot_is_not_scored_yet(svc) -> None:  # noqa: ANN001
    from aimz.util import now_iso

    pub = _publication(svc, now_iso())
    svc.analytics_store.record(pub, MetricsSnapshot(views=6, avg_percent_viewed=90.0), source="api")
    latest = svc.analytics_store.latest_per_publication()[0]
    assert svc.analytics_store.scoring_snapshot(latest) is None
    # the owner's manual numbers are used as entered
    svc.analytics_store.record(pub, MetricsSnapshot(views=6, avg_percent_viewed=90.0), source="manual")
    latest = dict(svc.db.one("SELECT * FROM metrics WHERE source='manual'"))
    assert svc.analytics_store.scoring_snapshot(latest) is not None


def test_scored_at_first_snapshot_past_72_hours(svc) -> None:  # noqa: ANN001
    pub = _publication(svc, "2026-01-01T00:00:00+00:00")
    for hours, views in [(40, 100), (80, 150), (300, 160)]:
        svc.analytics_store.record(pub, MetricsSnapshot(views=views), source="api")
        svc.db.execute(
            "UPDATE metrics SET hours_since_post=? WHERE id=(SELECT id FROM metrics ORDER BY rowid DESC LIMIT 1)",
            [hours],
        )
    latest = dict(svc.db.one("SELECT * FROM metrics ORDER BY hours_since_post DESC LIMIT 1"))
    snap = svc.analytics_store.scoring_snapshot(latest)
    assert snap is not None and snap["views"] == 150


# -- elevated review --------------------------------------------------------------------

ALL_TOPICS = [
    "medical claims",
    "legal claims",
    "financial advice",
    "politics or persuasion",
    "emergencies or disasters in progress",
    "allegations involving real, living people",
]


def _draft(text: str) -> ScriptDraft:
    words = " ".join(["Amalthea is a small moon of Jupiter found in 1892."] * 3)
    return ScriptDraft(
        title="The Discovery of Amalthea",
        hook_line="A moon hid in plain sight.",
        beats=[Beat(narration=text if i == 0 else words) for i in range(5)],
    )


def _review(draft: ScriptDraft, reasons: list[str], evidence: list[str]) -> CriticResult:
    base = CriticResult.model_validate(
        {
            "pass": True,
            "score": 85,
            "hook_strength": 8,
            "originality": 8,
            "factual_support": 8,
            "pacing": 8,
            "payoff": 8,
            "clarity": 8,
            "elevated_review_required": True,
            "elevated_review_reasons": reasons,
            "elevated_review_evidence": evidence,
        }
    )
    narration = " ".join(b.narration for b in draft.beats)
    return CriticAgent._overrides(
        base, draft, narration.lower(), FactCheckResult(verdicts=[], overall="pass"), 70, []
    )


def test_echoed_topic_list_without_quote_is_dropped() -> None:
    r = _review(_draft("Its orbit is secure and obscure."), ALL_TOPICS, [])
    assert not r.elevated_review_required and r.passed


def test_flag_with_quote_from_script_is_kept() -> None:
    r = _review(
        _draft("The senator was indicted for fraud last week."), ["legal claims"], ["indicted for fraud"]
    )
    assert r.elevated_review_required and "legal claims" in r.elevated_review_reasons


def test_invented_quote_does_not_count_but_keywords_still_do() -> None:
    r = _review(_draft("Amalthea was found by telescope."), ["medical claims"], ["a cure for cancer"])
    assert not r.elevated_review_required
    r = _review(_draft("It was said to cure fevers."), [], [])
    assert r.elevated_review_required and r.elevated_review_reasons == ["medical claims"]


# -- degraded runs ----------------------------------------------------------------------


def test_run_with_errors_finishes_degraded(svc) -> None:  # noqa: ANN001
    with svc.tracker.run("cycle") as run:
        svc.tracker.record_error(run.id, "analyst", "metrics", RuntimeError("invalid_grant"))
    row = svc.db.get("runs", run.id)
    assert row["status"] == "degraded" and "RuntimeError x1 (analyst.metrics)" in row["error"]
    with svc.tracker.run("cycle") as clean:
        pass
    assert svc.db.get("runs", clean.id)["status"] == "ok"


# -- full-text sources ------------------------------------------------------------------


def test_relevant_excerpt_keeps_lede_and_matching_paragraph() -> None:
    paras = ["Lede paragraph about the year."] + [f"Unrelated event number {i} happened." for i in range(50)]
    paras.insert(30, "The Batu Lintang camp was liberated by the Australian 9th Division.")
    out = relevant_excerpt("\n".join(paras), "Batu Lintang camp liberated", 200)
    assert out.startswith("Lede") and "Batu Lintang" in out and len(out) <= 200


def test_sources_block_includes_article_text_only_when_asked() -> None:
    s = SourceItemView(
        id="si1", source_name="x", url="u", title="t", summary="short", full_text="Body text here."
    )
    assert "Article text" not in Agent.sources_block([s])
    assert "Body text here." in Agent.sources_block([s], text_budget=1000)


def test_article_text_from_html_drops_chrome() -> None:
    raw = (
        "<nav><p>Menu menu menu menu menu menu menu menu menu menu menu menu menu</p></nav>"
        "<p>The Turtle was the first combat submarine, built in 1775 by David Bushnell in Connecticut.</p>"
        "<p>Short.</p>"
    )
    out = article_text_from_html(raw, 5000)
    assert "Turtle" in out and "Menu" not in out and "Short." not in out


def test_wikipedia_title() -> None:
    assert wikipedia_title("https://en.wikipedia.org/wiki/Batu_Lintang_camp") == "Batu Lintang camp"
    assert wikipedia_title("https://www.nasa.gov/x") is None


def test_fetch_article_skips_hosts_without_articles_and_is_authorized(
    svc,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    p = RSSResearchProvider("test-agent")
    calls: list[str] = []
    monkeypatch.setattr(
        p,
        "_download",
        lambda url: calls.append(url) or b'{"query":{"pages":{"1":{"extract":"Camp text.\\n\\n\\nMore."}}}}',
    )
    ctx = svc.ctx(None, None, None)
    assert p.fetch_article(ctx, "https://www.reddit.com/r/todayilearned/comments/x") is None
    assert calls == []
    assert p.fetch_article(ctx, "https://en.wikipedia.org/wiki/Batu_Lintang_camp") == "Camp text.\nMore."
    assert "prop=extracts" in calls[0]
    assert svc.db.count("provider_usage", "action='article_fetch'") == 1


def test_enrich_records_status_and_failure_is_not_a_run_error(
    svc,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    from aimz.pipeline.orchestrator import Orchestrator
    from aimz.util import now_iso

    orch = Orchestrator(svc, seed=1)
    orch.research.sync_sources()
    src = svc.db.one("SELECT id FROM sources LIMIT 1")["id"]
    for i, url in enumerate(["https://example.org/a", "https://example.org/b"]):
        svc.db.insert(
            "source_items",
            {
                "id": f"si{i}",
                "source_id": src,
                "url": url,
                "url_hash": f"h{i}",
                "dedupe_key": f"d{i}",
                "title": "t",
                "ingested_at": now_iso(),
                "status": "new",
            },
        )

    def fake(ctx: Any, url: str) -> str:
        if url.endswith("b"):
            raise RuntimeError("403 Forbidden")
        return "Full body."

    for prov in svc.research.values():
        monkeypatch.setattr(prov, "fetch_article", fake)
    with svc.tracker.run("cycle") as run:
        assert orch.research.enrich(run, ["si0", "si1"]) == 1
    assert svc.db.get("source_items", "si0")["full_text"] == "Full body."
    assert svc.db.get("source_items", "si1")["full_text_status"].startswith("failed: RuntimeError")
    assert svc.db.get("runs", run.id)["status"] == "ok"


# -- AI creative control (owner decision 2026-09-23) ------------------------------------


def test_editor_can_explore_families_the_ai_invented(svc) -> None:  # noqa: ANN001
    """With no human starting families, a family named during ideation must still get selected."""
    import random

    from aimz.agents.editor import EditorInChief
    from aimz.experiments.engine import ExperimentEngine
    from aimz.pipeline.orchestrator import Orchestrator

    orch = Orchestrator(svc, seed=3)
    assert orch.strategy.current()["families"] == {}
    with svc.tracker.run("cycle") as run:
        orch.research.run(run)
        orch.ideation.run(run, orch.strategy.current())
        editor = EditorInChief(svc, orch.strategy, ExperimentEngine(svc.db), random.Random(3))
        chosen = editor.select(run, orch.strategy.current(), [])
    assert chosen


def test_script_length_follows_sources_unless_runtime_is_under_test() -> None:
    from aimz.agents.script import ScriptAgent

    draft = _draft("Amalthea is a small moon.")  # 125 words, ~48 s at 2.6 words/s
    assert ScriptAgent.word_budget_ok(draft, 150)[0]
    ok, msg = ScriptAgent.word_budget_ok(draft, 150, enforce_target=True)  # needs >= 0.6 * 150 s
    assert not ok and "only facts in the sources" in msg
