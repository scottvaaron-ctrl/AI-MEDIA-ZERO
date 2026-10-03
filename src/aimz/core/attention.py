"""What needs the owner: work that was set aside, closed out or is waiting, with the command that clears it.

Nothing the pipeline sets aside is deleted. It is listed here (``aimz status``) until it is re-sent,
re-queued or rejected.
"""

from __future__ import annotations

from typing import Any

from aimz.db import Database
from aimz.util import iso_ago


def attention(db: Database) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}

    def pubs(where: str, fix: str) -> list[dict[str, Any]]:
        rows = db.query(
            "SELECT p.id, p.platform, p.attempts, p.next_attempt_at, p.last_error, v.title FROM publications p "
            f"JOIN videos v ON v.id = p.video_id WHERE {where} ORDER BY p.updated_at DESC"
        )
        return [
            {
                "publication": r["id"],
                "platform": r["platform"],
                "video": r["title"],
                "attempts": r["attempts"],
                "next_attempt_at": r["next_attempt_at"],
                "error": (r["last_error"] or "")[:160],
                "fix": fix.format(id=r["id"]),
            }
            for r in rows
        ]

    out["uploads_retrying"] = pubs(
        "p.status='failed' AND p.next_attempt_at IS NOT NULL",
        "none needed: retried automatically after checking the platform",
    )
    out["uploads_failed_no_retry"] = pubs(
        "p.status='failed' AND p.next_attempt_at IS NULL",
        "python -m aimz publish retry {id}",
    )
    out["uploads_closed_out"] = pubs("p.status='abandoned'", "python -m aimz publish retry {id}")
    out["uploads_blocked"] = pubs("p.status='blocked'", "python -m aimz publish retry {id} --approved")
    out["ideas_parked"] = [
        {
            "idea": r["id"],
            "title": r["title"],
            "why": r["eic_notes"],
            "fix": f"python -m aimz requeue idea {r['id']}",
        }
        for r in db.query("SELECT * FROM ideas WHERE status='parked' ORDER BY updated_at DESC")
    ]
    out["scripts_parked"] = [
        {"script": r["id"], "title": r["title"], "fix": f"python -m aimz requeue script {r['id']}"}
        for r in db.query("SELECT * FROM scripts WHERE status='parked' ORDER BY updated_at DESC")
    ]
    out["scripts_needing_review"] = [
        {"script": r["id"], "title": r["title"], "fix": f"python -m aimz approve script {r['id']} [--reject]"}
        for r in db.query("SELECT * FROM scripts WHERE status='needs_owner_review' ORDER BY updated_at DESC")
    ]
    out["runs_still_open"] = [
        {"run": r["id"], "started_at": r["started_at"], "fix": "closed automatically at the next cycle"}
        for r in db.query(
            "SELECT * FROM runs WHERE status='running' AND started_at < ? ORDER BY started_at",
            [iso_ago(hours=4)],
        )
    ]
    failing = db.get_state("youtube_auth_failing_since")
    if failing:
        out["youtube_login_failing"] = [
            {
                "since": failing,
                "why": "YouTube refuses the stored login. Per YouTube policy, this channel's YouTube data "
                "(metrics, comments) is deleted automatically 7 days after this date unless it works again",
                "fix": "python -m aimz youtube auth",
            }
        ]
    return {k: v for k, v in out.items() if v}
