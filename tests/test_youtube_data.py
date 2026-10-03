"""YouTube API data retention and deletion (YouTube Developer Policies III.D, III.E.4; plan stage C)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aimz import youtube_data
from aimz.util import new_id, now_iso

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _seed(svc, platform: str = "youtube") -> dict:  # noqa: ANN001
    if not svc.db.get("ideas", "i"):
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
                "created_at": now_iso(),
                "updated_at": now_iso(),
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
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
    vid = new_id("vid")
    svc.db.insert(
        "videos",
        {
            "id": vid,
            "script_id": "s",
            "idea_id": "i",
            "title": "t",
            "format": "short",
            "resolution": "x",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    pub = {
        "id": new_id("pub"),
        "video_id": vid,
        "platform": platform,
        "publisher": "p",
        "mode": "public",
        "idempotency_key": new_id("k"),
        "status": "published",
        "platform_video_id": "abc123",
        "url": "https://www.youtube.com/watch?v=abc123",
        "posted_at": now_iso(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    svc.db.insert("publications", pub)
    return pub


def _comment(svc, pub: dict, refreshed: datetime, lead: str | None = None) -> str:  # noqa: ANN001
    cid = new_id("cmt")
    svc.db.insert(
        "comments",
        {
            "id": cid,
            "publication_id": pub["id"],
            "platform_comment_id": new_id("pc"),
            "author": "Viewer Name",
            "text": "please do one on the Hindenburg",
            "converted_source_item_id": lead,
            "created_at": _iso(refreshed),
            "refreshed_at": _iso(refreshed),
        },
    )
    return cid


def _lead(svc) -> str:  # noqa: ANN001
    from aimz.agents.comments import AUDIENCE_SOURCE_ID, CommentAgent

    CommentAgent.ensure_audience_source(type("A", (), {"svc": svc})())  # type: ignore[arg-type]
    sid = new_id("src")
    svc.db.insert(
        "source_items",
        {
            "id": sid,
            "source_id": AUDIENCE_SOURCE_ID,
            "url": f"audience://{sid}",
            "url_hash": sid,
            "title": "Audience request: the Hindenburg",
            "summary": "A viewer asked: please do one on the Hindenburg",
            "ingested_at": now_iso(),
            "status": "new",
            "dedupe_key": sid,
            "raw_json": json.dumps({"comment_id": "x"}),
        },
    )
    return sid


def _metric(svc, pub: dict) -> None:  # noqa: ANN001
    svc.db.insert(
        "metrics",
        {
            "id": new_id("met"),
            "publication_id": pub["id"],
            "video_id": pub["video_id"],
            "platform": pub["platform"],
            "captured_at": now_iso(),
            "source": "api",
            "views": 10,
        },
    )


def test_youtube_comments_not_refreshed_for_30_days_are_deleted(svc) -> None:  # noqa: ANN001
    yt, bsky = _seed(svc), _seed(svc, "bluesky")
    lead = _lead(svc)
    old = _comment(svc, yt, NOW - timedelta(days=31), lead)
    fresh = _comment(svc, yt, NOW - timedelta(days=29))
    other = _comment(svc, bsky, NOW - timedelta(days=90))  # not YouTube data
    out = youtube_data.expire(svc.db, svc.env.data_dir, NOW)
    assert out["comments_deleted"] == 1
    assert svc.db.get("comments", old) is None
    assert svc.db.get("comments", fresh) and svc.db.get("comments", other)
    item = svc.db.get("source_items", lead)
    assert "Hindenburg" not in item["summary"] and item["title"].startswith("Audience request")


def test_saved_upload_response_is_cut_to_the_id_after_30_days(svc) -> None:  # noqa: ANN001
    pkg = svc.env.data_dir / "packages" / "youtube" / "v1"
    pkg.mkdir(parents=True)
    old, new = pkg / "youtube_response.json", svc.env.data_dir / "packages" / "youtube" / "v2"
    old.write_text(json.dumps({"id": "abc", "snippet": {"title": "x"}, "status": {}}), encoding="utf-8")
    stamp = (NOW - timedelta(days=31)).timestamp()
    os.utime(old, (stamp, stamp))
    new.mkdir()
    (new / "youtube_response.json").write_text(json.dumps({"id": "def", "snippet": {}}), encoding="utf-8")
    recent = (NOW - timedelta(days=2)).timestamp()
    os.utime(new / "youtube_response.json", (recent, recent))
    assert youtube_data.expire(svc.db, svc.env.data_dir, NOW)["responses_minimised"] == 1
    assert json.loads(old.read_text(encoding="utf-8")) == {"id": "abc"}
    assert "snippet" in json.loads((new / "youtube_response.json").read_text(encoding="utf-8"))


def test_purge_deletes_all_youtube_api_data_and_the_token(svc, tmp_path: Path) -> None:  # noqa: ANN001
    yt, bsky = _seed(svc), _seed(svc, "bluesky")
    _metric(svc, yt)
    _metric(svc, bsky)
    _comment(svc, yt, NOW)
    svc.db.set_state("youtube_channel_stats", "{}")
    token = tmp_path / "youtube_token.json"
    token.write_text("{}", encoding="utf-8")
    out = youtube_data.purge(svc.db, svc.env.data_dir, token)
    assert out["metrics_deleted"] == 1 and out["comments_deleted"] == 1 and not token.exists()
    assert svc.db.one("SELECT COUNT(*) c FROM metrics WHERE platform='youtube'")["c"] == 0
    assert svc.db.one("SELECT COUNT(*) c FROM metrics WHERE platform='bluesky'")["c"] == 1
    row = svc.db.get("publications", yt["id"])
    assert row["platform_video_id"] is None and row["url"] is None
    assert svc.db.get_state("youtube_channel_stats") is None


def test_lost_authorization_purges_after_7_days_not_before(svc, monkeypatch) -> None:  # noqa: ANN001
    pub = _seed(svc)
    _metric(svc, pub)
    monkeypatch.setattr(youtube_data, "check_authorization", lambda token: False)
    token = svc.env.youtube_token_file
    first = youtube_data.enforce(svc.db, svc.env.data_dir, token, NOW)
    assert "purged" not in first and first["authorization_failing_since"]
    from aimz.core.attention import attention

    assert "youtube_login_failing" in attention(svc.db)
    later = youtube_data.enforce(svc.db, svc.env.data_dir, token, NOW + timedelta(days=6))
    assert "purged" not in later
    gone = youtube_data.enforce(svc.db, svc.env.data_dir, token, NOW + timedelta(days=7))
    assert gone["purged"]["metrics_deleted"] == 1


def test_a_renewed_login_stops_the_countdown(svc, monkeypatch) -> None:  # noqa: ANN001
    pub = _seed(svc)
    _metric(svc, pub)
    token = svc.env.youtube_token_file
    monkeypatch.setattr(youtube_data, "check_authorization", lambda token: False)
    youtube_data.enforce(svc.db, svc.env.data_dir, token, NOW)
    monkeypatch.setattr(youtube_data, "check_authorization", lambda token: True)
    youtube_data.enforce(svc.db, svc.env.data_dir, token, NOW + timedelta(days=3))
    out = youtube_data.enforce(svc.db, svc.env.data_dir, token, NOW + timedelta(days=10))
    assert "purged" not in out and "authorization_failing_since" not in out


def test_unknown_authorization_state_is_not_counted_as_revoked(svc, monkeypatch) -> None:  # noqa: ANN001
    _metric(svc, _seed(svc))
    monkeypatch.setattr(youtube_data, "check_authorization", lambda token: None)  # e.g. offline
    for day in (0, 8, 20):
        out = youtube_data.enforce(
            svc.db, svc.env.data_dir, svc.env.youtube_token_file, NOW + timedelta(days=day)
        )
        assert "purged" not in out


def test_refetching_a_comment_refreshes_it(svc) -> None:  # noqa: ANN001
    from aimz.agents.comments import CommentAgent
    from aimz.domain.models import FetchedComment

    pub = _seed(svc)
    agent = CommentAgent.__new__(CommentAgent)
    agent.svc = svc
    c = FetchedComment(platform_comment_id="pc1", author="A", text="first", posted_at=None)
    assert len(agent.ingest(pub, [c])) == 1
    svc.db.execute("UPDATE comments SET refreshed_at=?", [_iso(NOW - timedelta(days=40))])
    c2 = FetchedComment(platform_comment_id="pc1", author="A", text="edited", posted_at=None)
    assert agent.ingest(pub, [c2]) == []
    row = svc.db.one("SELECT * FROM comments")
    assert row["text"] == "edited" and row["refreshed_at"] > _iso(NOW - timedelta(days=40))
