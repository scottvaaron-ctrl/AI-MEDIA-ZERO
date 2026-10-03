"""Fixes of 2026-09-28: the critic must give a reason to fail a script, idea slots are not wasted, nothing
that passed its checks is lost to a technical failure, a retry looks before it re-sends, work a dead run
left behind is recovered, finished videos close out, and the AI's own experiments can collect data."""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path
from typing import Any

import pytest

from aimz.core.errors import (
    CannotVerify,
    PermanentPublishError,
    ProviderUnavailable,
    PublishError,
    TechnicalFailure,
)
from aimz.domain.models import (
    Beat,
    CriticResult,
    ExperimentProposal,
    FactCheckResult,
    MetricsSnapshot,
    PublishResult,
    ScriptDraft,
    SelectionDecision,
)
from aimz.providers.base import AnalyticsProvider, HealthStatus, ProviderContext, Publisher
from aimz.util import dumps, iso_ago, new_id, now_iso


def _orch(svc: Any, consent: bool = True) -> Any:
    from aimz.pipeline.orchestrator import Orchestrator

    svc.env = dataclasses.replace(svc.env, autopublish_consent=consent)
    return Orchestrator(svc, seed=1)


def _idea(svc: Any, family: str = "forgotten_history", status: str = "candidate", **kw: Any) -> str:
    idea_id = new_id("idea")
    row = {
        "id": idea_id,
        "title": f"idea {idea_id[-8:]}",
        "premise": "p",
        "hook": "h",
        "hook_type": "narrative_hook",
        "content_family": family,
        "target_platform": "both",
        "source_item_ids_json": "[]",
        "scores_json": "{}",
        "opportunity_score": 50,
        "status": status,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    row.update(kw)
    svc.db.insert("ideas", row)
    return idea_id


def _script(svc: Any, idea_id: str, status: str = "approved", run_id: str | None = None) -> str:
    script_id = new_id("scr")
    svc.db.insert(
        "scripts",
        {
            "id": script_id,
            "idea_id": idea_id,
            "run_id": run_id,
            "title": "The Discovery of Amalthea",
            "hook_line": "h",
            "beats_json": "[]",
            "narration_text": "x",
            "status": status,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    return script_id


def _video(svc: Any, status: str = "rendered", family: str = "forgotten_history") -> str:
    idea_id = _idea(svc, family=family, status="produced")
    script_id = _script(svc, idea_id)
    video_id = new_id("vid")
    mp4 = svc.env.data_dir / f"{video_id}.mp4"
    mp4.write_bytes(b"\x00" * 10)
    svc.db.insert(
        "videos",
        {
            "id": video_id,
            "script_id": script_id,
            "idea_id": idea_id,
            "title": "The Discovery of Amalthea",
            "format": "short",
            "resolution": "1080x1920",
            "file_path": str(mp4),
            "timeline_json": dumps({"sources": [], "attributions": []}),
            "status": status,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    return video_id


# == 1a. the critic has to give a reason =================================================


def _verdict(
    score: int, problems: list[str] | None = None, required: list[str] | None = None
) -> CriticResult:
    return CriticResult.model_validate(
        {
            "pass": False,
            "score": score,
            "problems": problems or [],
            "required_revisions": required or [],
            "hook_strength": 5,
            "originality": 5,
            "factual_support": 5,
            "pacing": 5,
            "payoff": 5,
            "clarity": 5,
        }
    )


def _run_critic(
    svc: Any, monkeypatch: pytest.MonkeyPatch, answers: list[Any], overall: str = "pass"
) -> tuple[CriticResult, int]:
    orch = _orch(svc)
    asked: list[str] = []

    def fake_ask(ctx: Any, role: str, user: str, schema: Any, purpose: str, **kw: Any) -> Any:
        asked.append(user)
        out = answers.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(orch.critic, "ask", fake_ask)
    draft = ScriptDraft(
        title="The Discovery of Amalthea",
        hook_line="A moon hid in plain sight.",
        beats=[Beat(narration="Amalthea is a small moon of Jupiter found in 1892.") for _ in range(4)],
    )
    with svc.tracker.run("cycle") as run:
        result = orch.critic.review(
            run,
            "scr_x",
            draft,
            {"title": "t", "content_family": "f", "hook_type": None},
            FactCheckResult(verdicts=[], overall=overall),
            [],
        )
    return result, len(asked)


def test_score_with_no_reason_twice_does_not_gate(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    # The live failure: score ~55 against a pass mark of 60, empty problems, identical rewrites x3.
    result, asks = _run_critic(svc, monkeypatch, [_verdict(55), _verdict(55)])
    assert asks == 2 and result.passed
    assert any("without giving a reason" in p for p in result.problems)


def test_reasons_given_on_the_second_ask_are_enforced(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    result, asks = _run_critic(
        svc, monkeypatch, [_verdict(55), _verdict(55, ["the ending restates the opening"])]
    )
    assert asks == 2 and not result.passed and "the ending restates the opening" in result.problems


def test_an_explained_failure_is_not_asked_twice(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    result, asks = _run_critic(svc, monkeypatch, [_verdict(55, ["generic phrasing"])])
    assert asks == 1 and not result.passed


def test_a_fact_check_rejection_still_fails_without_a_second_ask(
    svc, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    result, asks = _run_critic(svc, monkeypatch, [_verdict(55)], overall="reject")
    assert asks == 1 and not result.passed


def test_critic_outage_is_technical_not_a_verdict(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    with pytest.raises(TechnicalFailure, match="critic model call failed"):
        _run_critic(svc, monkeypatch, [RuntimeError("Ollama did not answer within 600 s")])


# == 3b. a technical failure never rejects an idea =========================================


def test_technical_failure_sends_the_idea_back_and_stops_the_stage(
    svc, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    orch = _orch(svc)
    first = _idea(svc, status="selected", opportunity_score=90)
    second = _idea(svc, status="selected", opportunity_score=80)
    tried: list[str] = []

    def boom(run: Any, idea: dict[str, Any], state: Any) -> str | None:
        tried.append(idea["id"])
        _script(svc, idea["id"], status="qa_failed", run_id=run.id)
        svc.db.update("ideas", idea["id"], {"status": "scripted"})
        raise TechnicalFailure("critic model call failed: timeout")

    monkeypatch.setattr(orch, "write_one", boom)
    with svc.tracker.run("cycle") as run:
        orch.write_selected(run, orch.strategy.current())
    assert tried == [first]  # an outage hits every idea alike, so the stage stops
    row = svc.db.get("ideas", first)
    assert row["status"] == "selected" and row["tech_failures"] == 1
    assert "technical failure 1/5" in row["eic_notes"]
    assert svc.db.one("SELECT status FROM scripts WHERE idea_id=?", [first])["status"] == "interrupted"
    assert svc.db.get("ideas", second)["status"] == "selected"
    assert run.summary["write"] == {"approved_scripts": 0, "interrupted": 1}


def test_repeated_technical_failures_park_the_idea_for_the_owner(
    svc, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    from aimz.core.attention import attention

    orch = _orch(svc)
    idea = _idea(svc, status="selected", tech_failures=4)
    monkeypatch.setattr(
        orch, "write_one", lambda run, i, s: (_ for _ in ()).throw(RuntimeError("bad sources"))
    )
    with svc.tracker.run("cycle") as run:
        orch.write_selected(run, orch.strategy.current())
    row = svc.db.get("ideas", idea)
    assert row["status"] == "parked" and row["tech_failures"] == 5
    assert svc.db.get("runs", run.id)["status"] == "degraded"
    assert attention(svc.db)["ideas_parked"][0]["fix"] == f"python -m aimz requeue idea {idea}"


def test_a_render_that_keeps_failing_parks_the_script(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    orch = _orch(svc)
    script_id = _script(svc, _idea(svc, status="scripted"))

    def failing_render(run: Any, sid: str) -> str:
        svc.db.insert(
            "videos",
            {
                "id": new_id("vid"),
                "script_id": sid,
                "idea_id": svc.db.get("scripts", sid)["idea_id"],
                "title": "t",
                "format": "short",
                "resolution": "1080x1920",
                "status": "failed",
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
        raise RuntimeError("ffmpeg crashed")

    monkeypatch.setattr(orch.producer, "produce", failing_render)
    for attempt in range(1, 4):
        with svc.tracker.run("cycle") as run:
            orch.produce_approved(run)
        assert svc.db.get("scripts", script_id)["status"] == ("parked" if attempt == 3 else "approved")
    with svc.tracker.run("cycle") as run:
        assert orch.produce_approved(run) == []  # parked: not rendered a fourth time


# == 1b. slots are not wasted on ideas the editor keeps turning down ========================


def test_editor_rejecting_a_whole_shortlist_offers_the_slot_to_other_ideas(
    svc, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    orch = _orch(svc)
    dead = _idea(svc, family="maps_data_stories", opportunity_score=99)
    good = _idea(svc, family="forgotten_history", opportunity_score=50)
    _idea(svc, family="forgotten_history", status="scripted")  # in progress: maps is the least sampled

    def decide(ctx: Any, role: str, user: str, schema: Any, purpose: str, **kw: Any) -> SelectionDecision:
        if dead in user:
            return SelectionDecision(selected_idea_ids=[], rejected=[{"idea_id": dead, "reason": "no angle"}])
        return SelectionDecision(selected_idea_ids=[good])

    monkeypatch.setattr(orch.editor, "ask", decide)
    with svc.tracker.run("cycle") as run:
        chosen = orch.editor.select(run, orch.strategy.current(), [], k=1)
    assert chosen == [good]
    assert svc.db.get("ideas", dead)["status"] == "rejected"  # not offered again next cycle
    picked = svc.db.get("ideas", good)
    assert picked["status"] == "selected" and picked["allocation_mode"] == "fallback"


def test_ideas_passed_over_for_a_better_one_stay_candidates(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    orch = _orch(svc)
    a = _idea(svc, opportunity_score=90)
    b = _idea(svc, opportunity_score=80)
    monkeypatch.setattr(
        orch.editor,
        "ask",
        lambda *a_, **k: SelectionDecision(
            selected_idea_ids=[a], rejected=[{"idea_id": b, "reason": "weaker"}]
        ),
    )
    with svc.tracker.run("cycle") as run:
        assert orch.editor.select(run, orch.strategy.current(), [], k=1) == [a]
    row = svc.db.get("ideas", b)
    assert row["status"] == "candidate" and row["eic_notes"].startswith("passed over")


def test_allocation_only_plans_fillable_families_and_counts_work_in_progress() -> None:
    from aimz.experiments.allocation import plan_allocation

    families = {"a": {"status": "new"}, "b": {"status": "new"}, "c": {"status": "new"}}
    # a already has a video in progress, so b is the least tried; c has no candidate and is never planned
    first = plan_allocation([], families, 1, {}, random.Random(1), available={"a", "b"}, pending={"a": 1})
    assert [s.family for s in first] == ["b"]
    many = plan_allocation([], families, 6, {}, random.Random(2), available={"a", "b"}, pending={"a": 1})
    assert {s.family for s in many} == {"a", "b"}
    only_a = plan_allocation([], families, 1, {}, random.Random(3), available={"a"}, pending={"a": 2})
    assert "2 in progress" in only_a[0].reason


# == 2. publishing: kill switch, checked retries, close-out ==============================


class ScriptedPublisher(Publisher):
    name = "ScriptedPublisher"
    platform = "youtube"
    mode = "public"
    is_paid = False
    performs_api_writes = True

    def __init__(
        self, outcomes: list[Any] | None = None, existing: PublishResult | None = None, verify: bool = True
    ):
        self.outcomes = list(outcomes or [])
        self.existing = existing
        self.verify = verify
        self.calls = 0
        self.checks = 0
        self.polls = 0

    def health(self) -> HealthStatus:
        return HealthStatus(True, "fake")

    def publish(
        self, ctx: ProviderContext, video: dict, script: dict, metadata: dict, package_dir: Path
    ) -> PublishResult:
        self.calls += 1
        out = (
            self.outcomes.pop(0)
            if self.outcomes
            else PublishResult(status="uploaded", platform_video_id="yt1")
        )
        if isinstance(out, BaseException):
            raise out
        return out

    def find_existing(self, ctx: ProviderContext, publication: dict[str, Any]) -> PublishResult | None:
        self.checks += 1
        if not self.verify:
            raise CannotVerify("no lookup")
        return self.existing


def _publish(svc: Any, pub: ScriptedPublisher) -> tuple[Any, str]:
    svc.publishers = {"youtube": pub}
    orch = _orch(svc)
    vid = _video(svc)
    with svc.tracker.run("cycle") as run:
        orch.publisher.publish(run, vid)
    return orch, vid


def _retry_now(svc: Any, orch: Any) -> Any:
    svc.db.execute("UPDATE publications SET next_attempt_at=? WHERE status='failed'", [iso_ago(hours=1)])
    with svc.tracker.run("cycle") as run:
        orch.publisher.retry_due(run)
    return svc.db.one("SELECT * FROM publications")


def test_failed_upload_is_retried_after_the_platform_confirms_it_is_absent(svc) -> None:  # noqa: ANN001
    from tests.conftest import relax_posting_limits

    relax_posting_limits(svc)
    pub = ScriptedPublisher([PublishError("YouTube upload failed: timed out")])
    orch, vid = _publish(svc, pub)
    row = svc.db.one("SELECT * FROM publications")
    assert row["status"] == "failed" and row["failure_kind"] == "uncertain" and row["next_attempt_at"]
    with svc.tracker.run("cycle") as run:
        assert orch.publisher.retry_due(run) == []  # not due yet: nothing happens in the same cycle
    row = _retry_now(svc, orch)
    assert pub.checks == 1 and pub.calls == 2
    assert row["status"] == "uploaded" and row["attempts"] == 2 and row["next_attempt_at"] is None
    assert svc.db.get("videos", vid)["status"] == "published"


def test_a_post_that_did_go_out_is_recorded_not_uploaded_twice(svc) -> None:  # noqa: ANN001
    found = PublishResult(
        status="uploaded", platform_video_id="ytLATE", url="https://www.youtube.com/watch?v=ytLATE"
    )
    pub = ScriptedPublisher([PublishError("timed out")], existing=found)
    orch, vid = _publish(svc, pub)
    row = _retry_now(svc, orch)
    assert pub.calls == 1  # never re-sent
    assert row["status"] == "uploaded" and row["platform_video_id"] == "ytLATE" and row["posted_at"]
    assert svc.db.get("videos", vid)["status"] == "published"


def test_unverifiable_uncertain_failure_is_closed_out_with_an_alert(svc) -> None:  # noqa: ANN001
    pub = ScriptedPublisher([PublishError("timed out")], verify=False)
    orch, _ = _publish(svc, pub)
    row = _retry_now(svc, orch)
    assert pub.calls == 1 and row["status"] == "abandoned" and row["next_attempt_at"] is None
    assert svc.db.count("errors", "operation='abandoned'") == 1


def test_unverifiable_but_provably_unsent_failure_is_retried(svc) -> None:  # noqa: ANN001
    pub = ScriptedPublisher([ProviderUnavailable("token expired")], verify=False)
    orch, _ = _publish(svc, pub)
    assert svc.db.one("SELECT failure_kind FROM publications")["failure_kind"] == "transient"
    row = _retry_now(svc, orch)
    assert pub.calls == 2 and row["status"] == "uploaded"


def test_attempts_are_capped_then_closed_out(svc) -> None:  # noqa: ANN001
    from tests.conftest import relax_posting_limits

    relax_posting_limits(svc)
    pub = ScriptedPublisher([PublishError("e1"), PublishError("e2"), PublishError("e3")])
    orch, _ = _publish(svc, pub)
    _retry_now(svc, orch)
    row = _retry_now(svc, orch)
    assert pub.calls == 3 and row["attempts"] == 3 and row["status"] == "abandoned"
    _retry_now(svc, orch)
    assert pub.calls == 3


def test_permanent_failure_is_closed_out_at_once(svc) -> None:  # noqa: ANN001
    pub = ScriptedPublisher([PermanentPublishError("video is 700s; limit 600s")])
    _publish(svc, pub)
    row = svc.db.one("SELECT * FROM publications")
    assert row["status"] == "abandoned" and row["failure_kind"] == "permanent"


def test_kill_switch_holds_retries_without_using_an_attempt(svc) -> None:  # noqa: ANN001
    pub = ScriptedPublisher([PublishError("timed out")])
    orch, _ = _publish(svc, pub)
    svc.killswitch.engage("test")
    row = _retry_now(svc, orch)
    assert pub.checks == 0 and pub.calls == 1 and row["status"] == "failed" and row["attempts"] == 1


def test_owner_requeue_reopens_a_closed_out_upload_with_fresh_attempts(svc) -> None:  # noqa: ANN001
    pub = ScriptedPublisher([PermanentPublishError("refused")])
    orch, _ = _publish(svc, pub)
    pub_id = svc.db.one("SELECT id FROM publications")["id"]
    info = orch.publisher.requeue(pub_id)
    row = svc.db.get("publications", pub_id)
    assert info["platform"] == "youtube" and row["status"] == "pending" and row["attempts"] == 0


class Poller(ScriptedPublisher):
    platform = "bluesky"
    mode = "api"

    def __init__(self, raise_exc: BaseException | None = None):
        super().__init__()
        self.raise_exc = raise_exc

    def poll(self, ctx: ProviderContext, publication: dict[str, Any]) -> PublishResult | None:
        self.polls += 1
        if self.raise_exc:
            raise self.raise_exc
        return PublishResult(status="published", platform_video_id="at://post")


def _uploading(svc: Any, platform: str = "bluesky", age_hours: float = 0) -> str:
    vid = _video(svc)
    pub_id = new_id("pub")
    svc.db.insert(
        "publications",
        {
            "id": pub_id,
            "video_id": vid,
            "platform": platform,
            "publisher": "x",
            "mode": "api",
            "idempotency_key": f"{vid}:{platform}",
            "status": "uploading",
            "attempts": 1,
            "created_at": iso_ago(hours=age_hours),
            "updated_at": iso_ago(hours=age_hours),
        },
    )
    return pub_id


def test_kill_switch_stops_a_pending_post_from_being_finished(svc) -> None:  # noqa: ANN001
    poller = Poller()
    svc.publishers = {"bluesky": poller}
    orch = _orch(svc)
    pub_id = _uploading(svc)
    svc.killswitch.engage("owner said stop")
    with svc.tracker.run("cycle") as run:
        orch.publisher.poll_pending(run)
    assert poller.polls == 0 and svc.db.get("publications", pub_id)["status"] == "uploading"
    svc.killswitch.release()
    with svc.tracker.run("cycle") as run:
        orch.publisher.poll_pending(run)
    assert svc.db.get("publications", pub_id)["status"] == "published"


def test_a_failed_finish_becomes_a_checked_retry_not_a_repost_every_cycle(svc) -> None:  # noqa: ANN001
    poller = Poller(raise_exc=TimeoutError("createRecord timed out"))
    svc.publishers = {"bluesky": poller}
    orch = _orch(svc)
    pub_id = _uploading(svc)
    with svc.tracker.run("cycle") as run:
        orch.publisher.poll_pending(run)
    row = svc.db.get("publications", pub_id)
    assert row["status"] == "failed" and row["failure_kind"] == "uncertain" and row["next_attempt_at"]
    with svc.tracker.run("cycle") as run:
        orch.publisher.poll_pending(run)
    assert poller.polls == 1  # no longer 'uploading', so it is not finished again blindly


class _Exec:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        return self.payload


class FakeYouTube:
    """channels.list(mine) -> uploads playlist -> playlistItems.list, as the Data API v3 returns them."""

    def __init__(self, items: list[dict[str, Any]]):
        self.items = items
        self.inserted = 0

    def channels(self) -> Any:
        class C:
            def list(self, **kw: Any) -> _Exec:
                assert kw == {"part": "contentDetails", "mine": True}
                return _Exec({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]})

        return C()

    def playlistItems(self) -> Any:  # noqa: N802 - the API's own name
        outer = self

        class P:
            def list(self, **kw: Any) -> _Exec:
                assert kw["playlistId"] == "UUx"
                return _Exec({"items": outer.items})

        return P()


def test_youtube_find_existing_matches_title_and_time(
    svc, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:  # noqa: ANN001
    from aimz.providers.publishers import youtube as yt_mod

    title = "The 1945 Borneo POW Camp Rescue"
    items = [
        {
            "snippet": {
                "title": title,
                "publishedAt": "2026-09-01T10:00:00Z",
                "resourceId": {"videoId": "old"},
            }
        },
        {
            "snippet": {
                "title": title,
                "publishedAt": "2026-09-19T15:35:10Z",
                "resourceId": {"videoId": "late1"},
            }
        },
        {
            "snippet": {
                "title": "Other",
                "publishedAt": "2026-09-19T15:36:00Z",
                "resourceId": {"videoId": "x"},
            }
        },
    ]
    monkeypatch.setattr(yt_mod, "load_credentials", lambda *a: object())
    monkeypatch.setattr(yt_mod, "build_youtube", lambda creds: FakeYouTube(items))
    pub = yt_mod.YouTubePublisher("public", "public", True, tmp_path / "cs.json", tmp_path / "tok.json")
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "youtube_request_body.json").write_text(dumps({"snippet": {"title": title}}), encoding="utf-8")
    publication = {
        "package_dir": str(package),
        "created_at": "2026-09-19T15:34:00+00:00",
        "metadata_json": "{}",
    }
    found = pub.find_existing(svc.ctx(), publication)
    assert found is not None and found.platform_video_id == "late1" and found.status == "uploaded"
    # the same title uploaded before this attempt started is not this attempt
    assert pub.find_existing(svc.ctx(), {**publication, "created_at": "2026-09-20T00:00:00+00:00"}) is None
    draft = yt_mod.YouTubePublisher("draft", "private", True, tmp_path / "cs.json", tmp_path / "tok.json")
    with pytest.raises(CannotVerify):
        draft.find_existing(svc.ctx(), publication)


# == 3a/3b. recovery of what a dead run left behind =====================================


def test_recovery_closes_dead_runs_and_requeues_their_work(svc) -> None:  # noqa: ANN001
    svc.publishers = {"youtube": ScriptedPublisher(), "bluesky": Poller()}
    orch = _orch(svc)
    dead_run = "run_dead"
    svc.db.insert(
        "runs", {"id": dead_run, "kind": "cycle", "status": "running", "started_at": iso_ago(days=2)}
    )
    idea = _idea(svc, status="scripted", updated_at=iso_ago(days=2))
    draft = _script(svc, idea, status="qa_failed", run_id=dead_run)
    rendering = _video(svc, status="rendering")
    svc.db.update("videos", rendering, {"updated_at": iso_ago(hours=3)})
    youtube_upload = _uploading(svc, "youtube", age_hours=2)  # YouTube has no poll: nobody can finish it
    bluesky_upload = _uploading(svc, "bluesky", age_hours=2)  # still pollable: left alone for now

    summary = orch.cycle(["metrics"])
    assert summary["recovered"] == {"runs": 1, "ideas": 1, "videos": 1, "uploads": 1}
    assert svc.db.get("runs", dead_run)["status"] == "abandoned"
    assert svc.db.get("runs", summary["run_id"])["status"] == "degraded"  # so the owner is alerted
    assert svc.db.get("ideas", idea)["status"] == "selected"
    assert svc.db.get("scripts", draft)["status"] == "interrupted"
    assert svc.db.get("videos", rendering)["status"] == "failed"
    yt = svc.db.get("publications", youtube_upload)
    assert yt["status"] == "failed" and yt["failure_kind"] == "uncertain" and yt["next_attempt_at"]
    assert svc.db.get("publications", bluesky_upload)["status"] == "uploading"


def test_a_second_cycle_cannot_run_alongside_the_first(svc) -> None:  # noqa: ANN001
    from aimz.core.lock import CycleAlreadyRunning, exclusive_lock

    orch = _orch(svc)
    with exclusive_lock(svc.env.data_dir / "cycle.lock"), pytest.raises(CycleAlreadyRunning):
        orch.cycle(["metrics"])
    assert svc.db.count("runs") == 0
    orch.cycle(["metrics"])  # released: runs normally
    assert svc.db.count("runs") == 1


def test_an_interrupted_run_is_closed_not_left_running(svc) -> None:  # noqa: ANN001
    with pytest.raises(KeyboardInterrupt), svc.tracker.run("cycle") as run:
        raise KeyboardInterrupt
    assert svc.db.get("runs", run.id)["status"] == "interrupted"


# == 1b (owner note). finished videos are closed out as measured ==========================


class FixedAnalytics(AnalyticsProvider):
    name = "FixedAnalytics"
    platform = "youtube"
    is_paid = False

    def __init__(self) -> None:
        self.fetched: list[str] = []

    def health(self) -> HealthStatus:
        return HealthStatus(True, "fake")

    def fetch_metrics(self, ctx: ProviderContext, publication: dict[str, Any]) -> MetricsSnapshot | None:
        self.fetched.append(publication["id"])
        return MetricsSnapshot(views=100, avg_percent_viewed=40.0)


def _live(svc: Any, vid: str, platform: str, days_old: float, platform_id: str | None = "x") -> str:
    pub_id = new_id("pub")
    svc.db.insert(
        "publications",
        {
            "id": pub_id,
            "video_id": vid,
            "platform": platform,
            "publisher": "x",
            "mode": "api",
            "idempotency_key": f"{vid}:{platform}",
            "platform_video_id": platform_id,
            "status": "uploaded",
            "attempts": 1,
            "posted_at": iso_ago(days=days_old),
            "created_at": iso_ago(days=days_old),
            "updated_at": iso_ago(days=days_old),
        },
    )
    return pub_id


def test_metric_collection_closes_after_the_window_and_the_video_is_measured(svc) -> None:  # noqa: ANN001
    analytics = FixedAnalytics()
    svc.remote_analytics = {"youtube": analytics}
    orch = _orch(svc)
    old = _video(svc, status="published")
    old_yt = _live(svc, old, "youtube", days_old=15)
    _live(svc, old, "tiktok", days_old=15, platform_id=None)  # TikTok private posts report nothing
    young = _video(svc, status="published")
    young_yt = _live(svc, young, "youtube", days_old=1)

    with svc.tracker.run("cycle") as run:
        orch.analyst.collect_metrics(run)
    assert sorted(analytics.fetched) == sorted([old_yt, young_yt])
    assert svc.db.get("publications", old_yt)[
        "metrics_closed_at"
    ]  # a final snapshot past 14 days, then closed
    video = svc.db.get("videos", old)
    assert video["status"] == "measured" and video["measured_at"]
    assert svc.db.get("videos", young)["status"] == "published"
    assert run.summary["measurement_closed"] == {"publications": 2, "videos": 1}

    analytics.fetched.clear()
    with svc.tracker.run("cycle") as run:
        orch.analyst.collect_metrics(run)
    assert analytics.fetched == [young_yt]  # a closed video costs no more API calls
    # a measured video keeps its score for learning
    assert any(
        r["video_id"] == old and r["score"] is not None for r in svc.analytics_store.video_performance()
    )


def test_published_count_is_videos_not_publications(svc) -> None:  # noqa: ANN001
    orch = _orch(svc)
    vid = _video(svc, status="published")
    for platform in ("youtube", "bluesky", "tiktok"):
        _live(svc, vid, platform, days_old=1)
    assert orch.analyst.published_video_count() == 1
    assert orch.analyst.milestone()["published_videos"] == 1


# == 4. experiments the AI designs can collect data ======================================


def _proposal(kpi: str, variable: str = "hook_type") -> ExperimentProposal:
    return ExperimentProposal(
        name="Narrative vs Numeric Hook Experiment",
        hypothesis="h",
        variable=variable,
        control="numeric_hook",
        treatment="narrative_hook",
        primary_kpi=kpi,
    )


def test_the_live_experiments_kpi_name_is_understood(svc) -> None:  # noqa: ANN001
    from aimz.experiments.engine import ExperimentEngine

    eng = ExperimentEngine(svc.db)
    assert eng.validate(_proposal("mean_score")) is None
    eid = eng.propose(_proposal("mean_score"))
    assert eid
    rows = [
        {"experiment_id": eid, "experiment_arm": "control", "video_id": "v1", "score": 0.2},
        {"experiment_id": eid, "experiment_arm": "control", "video_id": "v1", "score": 0.4},  # same video
        {"experiment_id": eid, "experiment_arm": "treatment", "video_id": "v2", "score": 0.5},
    ]
    result = eng.evaluate(dict(svc.db.get("experiments", eid)), rows)
    assert result["control"] == {"n": 1, "mean": 0.3, "sd": 0.0}  # one sample per video
    assert result["treatment"]["n"] == 1


def test_an_unmeasurable_kpi_is_rejected_with_the_allowed_list(svc) -> None:  # noqa: ANN001
    from aimz.experiments.engine import ExperimentEngine

    reason = ExperimentEngine(svc.db).validate(_proposal("viewer delight"))
    assert reason and "not a measured metric" in reason and "avg_percent_viewed" in reason


def test_kpis_read_the_snapshot_the_video_is_scored_on() -> None:
    from aimz.experiments.engine import kpi_value

    row = {"avg_percent_viewed": 90.0, "scored_metrics": {"avg_percent_viewed": 41.0, "views": 200}}
    assert kpi_value(row, "avg_percent_viewed") == 41.0
    assert kpi_value({"avg_percent_viewed": 90.0, "scored_metrics": None}, "avg_percent_viewed") is None


def test_a_second_experiment_gets_ideas_too(svc) -> None:  # noqa: ANN001
    from aimz.experiments.engine import ExperimentEngine

    eng = ExperimentEngine(svc.db)
    first = eng.propose(_proposal("score"))
    second = eng.propose(
        ExperimentProposal(
            name="runtime",
            hypothesis="h",
            variable="runtime",
            control="30",
            treatment="60",
            primary_kpi="views",
        )
    )
    _idea(svc, experiment_id=first, experiment_arm="control")
    nxt = eng.next_for_assignment()
    assert nxt is not None and nxt["id"] == second


def test_retiring_an_experiment_frees_ideas_not_yet_written(svc) -> None:  # noqa: ANN001
    from aimz.experiments.engine import ExperimentEngine

    eng = ExperimentEngine(svc.db)
    eid = eng.propose(_proposal("score"))
    waiting = _idea(svc, status="selected", experiment_id=eid, experiment_arm="control")
    made = _idea(svc, status="published", experiment_id=eid, experiment_arm="treatment")
    eng.retire(str(eid), "retired by owner")
    assert svc.db.get("ideas", waiting)["experiment_id"] is None
    assert svc.db.get("ideas", made)["experiment_id"] == eid


def test_late_scores_are_flagged(svc) -> None:  # noqa: ANN001
    vid = _video(svc, status="published")
    pub_id = _live(svc, vid, "youtube", days_old=10)
    pub = dict(svc.db.get("publications", pub_id))
    svc.analytics_store.record(pub, MetricsSnapshot(views=250, avg_percent_viewed=24.0), source="api")
    row = next(r for r in svc.analytics_store.video_performance() if r["video_id"] == vid)
    assert row["score"] is not None and row["score_late"] and row["score_age_h"] > 200


# == small fixes =========================================================================


def test_iso_ago_is_comparable_with_stored_timestamps() -> None:
    from datetime import UTC, datetime, timedelta

    stored = (datetime.now(UTC) - timedelta(hours=30)).replace(microsecond=0).isoformat()
    assert stored < iso_ago(days=1)  # 30 h old is older than a day
    assert now_iso() > iso_ago(days=1)


def test_migrations_record_their_version_in_the_same_transaction(tmp_path: Path) -> None:
    import sqlite3

    from aimz.db.migrations import apply_migrations, list_migrations

    conn = sqlite3.connect(tmp_path / "m.sqlite3")
    applied = apply_migrations(conn)
    assert len(applied) == len(list_migrations())
    assert apply_migrations(conn) == []  # nothing re-applied
    cols = {r[1] for r in conn.execute("PRAGMA table_info(publications)")}
    assert {"next_attempt_at", "failure_kind", "metrics_closed_at"} <= cols
