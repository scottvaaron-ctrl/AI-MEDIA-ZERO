"""YouTube API data retention and deletion (YouTube Developer Policies, checked 2026-10-01; plan stage C).

What the policies require, and how this module meets it:

* **30-day limit for non-statistics data (III.E.4).** Comment authors and text are kept only while the
  platform keeps returning them: each fetch refreshes ``comments.refreshed_at``, and a YouTube comment
  not refreshed for ``RETENTION_DAYS`` is deleted, with the comment text removed from any lead made from
  it. A saved upload response (``youtube_response.json``) is cut down to the video id after 30 days.
* **Statistics may be kept while authorized, re-checked every 30 days (III.E.4.b).** Each cycle checks
  the channel's authorization (:func:`check_authorization`).
* **Delete within 7 days of revocation (III.D).** ``aimz youtube revoke`` revokes the token and purges at
  once. If authorization keeps failing (revoked, or a login nobody renewed) for ``REVOCATION_GRACE_DAYS``,
  or has not succeeded for ``RETENTION_DAYS``, the purge runs on its own. The grace period lets the owner
  renew an expired login before learning data is lost; ``aimz status`` shows the failure meanwhile.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aimz.db import Database
from aimz.util import now_iso

log = logging.getLogger("aimz.youtube_data")

RETENTION_DAYS = 30
REVOCATION_GRACE_DAYS = 7
AUTH_OK_KEY = "youtube_auth_ok_at"
AUTH_FAILING_KEY = "youtube_auth_failing_since"
EXPIRED_LEAD_SUMMARY = (
    "Audience request (the viewer's comment was deleted after 30 days, per YouTube policy)."
)


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def check_authorization(token_file: Path) -> bool | None:
    """True if the stored login still works, False if Google refuses it, None if it cannot be told."""
    from aimz.providers.publishers.youtube import load_credentials

    try:
        return load_credentials(token_file) is not None
    except Exception as exc:  # RefreshError (revoked/expired) vs a network problem
        name = type(exc).__name__
        if name == "RefreshError":
            return False
        log.warning("could not check the YouTube authorization: %s", exc)
        return None


def note_authorization(db: Database, ok: bool | None, now: datetime | None = None) -> None:
    stamp = _iso(_now(now))
    if ok is True:
        db.set_state(AUTH_OK_KEY, stamp)
        db.execute("DELETE FROM system_state WHERE key=?", [AUTH_FAILING_KEY])
    elif ok is False and not db.get_state(AUTH_FAILING_KEY):
        db.set_state(AUTH_FAILING_KEY, stamp)


def _youtube_comment_ids(db: Database, where: str = "", params: list[Any] | None = None) -> list[str]:
    rows = db.query(
        "SELECT c.id FROM comments c JOIN publications p ON p.id = c.publication_id "
        f"WHERE p.platform = 'youtube' {where}",
        params or [],
    )
    return [r["id"] for r in rows]


def _delete_comments(db: Database, ids: list[str]) -> int:
    for cid in ids:
        row = db.one("SELECT converted_source_item_id FROM comments WHERE id=?", [cid])
        if row and row["converted_source_item_id"]:
            db.execute(
                "UPDATE source_items SET summary=?, raw_json=? WHERE id=?",
                [EXPIRED_LEAD_SUMMARY, "{}", row["converted_source_item_id"]],
            )
        db.execute("DELETE FROM comments WHERE id=?", [cid])
    return len(ids)


def _response_files(data_dir: Path) -> list[Path]:
    root = data_dir / "packages" / "youtube"
    return sorted(root.rglob("youtube_response.json")) if root.exists() else []


def expire(db: Database, data_dir: Path, now: datetime | None = None) -> dict[str, int]:
    """Delete or minimise YouTube data older than the 30-day limit. Safe to run every cycle."""
    cutoff = _now(now) - timedelta(days=RETENTION_DAYS)
    ids = _youtube_comment_ids(db, "AND COALESCE(c.refreshed_at, c.created_at) < ?", [_iso(cutoff)])
    minimised = 0
    for path in _response_files(data_dir):
        if datetime.fromtimestamp(path.stat().st_mtime, UTC) >= cutoff:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if set(data) - {"id"}:
            path.write_text(json.dumps({"id": data.get("id")}), encoding="utf-8")
            minimised += 1
    return {"comments_deleted": _delete_comments(db, ids), "responses_minimised": minimised}


def purge(db: Database, data_dir: Path, token_file: Path | None = None) -> dict[str, int]:
    """Delete every piece of YouTube API data this instance holds (after revocation or loss of access)."""
    out = {
        "comments_deleted": _delete_comments(db, _youtube_comment_ids(db)),
        "metrics_deleted": db.count("metrics", "platform='youtube'"),
    }
    db.execute("DELETE FROM metrics WHERE platform='youtube'")
    db.execute(
        "DELETE FROM system_state WHERE key IN ('youtube_channel_stats', ?, ?)",
        [AUTH_OK_KEY, AUTH_FAILING_KEY],
    )
    # Video ids and URLs came from the API too. The publication rows stay (what this app uploaded, when).
    out["publications_cleared"] = db.count(
        "publications", "platform='youtube' AND platform_video_id IS NOT NULL"
    )
    db.execute(
        "UPDATE publications SET platform_video_id=NULL, url=NULL, metrics_closed_at=COALESCE(metrics_closed_at, ?) "
        "WHERE platform='youtube'",
        [now_iso()],
    )
    files = _response_files(data_dir)
    for path in files:
        path.unlink(missing_ok=True)
    out["responses_deleted"] = len(files)
    if token_file is not None and token_file.exists():
        token_file.unlink()
        out["token_deleted"] = 1
    db.set_state("youtube_data_purged_at", now_iso())
    log.warning("YouTube API data purged: %s", out)
    return out


def enforce(
    db: Database, data_dir: Path, token_file: Path, now: datetime | None = None, check: bool = True
) -> dict[str, Any]:
    """Run each cycle: check authorization, expire old data, and purge if access is gone."""
    current = _now(now)
    if check:
        note_authorization(db, check_authorization(token_file), current)
    result: dict[str, Any] = expire(db, data_dir, current)
    failing = _parse(db.get_state(AUTH_FAILING_KEY))
    ok = _parse(db.get_state(AUTH_OK_KEY))
    has_data = bool(
        db.one("SELECT 1 FROM metrics WHERE platform='youtube' LIMIT 1")
        or _youtube_comment_ids(db, "LIMIT 1")
    )
    reason = None
    if failing and current - failing >= timedelta(days=REVOCATION_GRACE_DAYS):
        reason = f"authorization failing since {_iso(failing)}"
    elif ok and current - ok >= timedelta(days=RETENTION_DAYS):
        reason = f"no successful authorization since {_iso(ok)}"
    if reason and has_data:
        result["purged"] = purge(db, data_dir)
        result["purge_reason"] = reason
    elif failing:
        result["authorization_failing_since"] = _iso(failing)
    return result


def revoke(db: Database, data_dir: Path, token_file: Path) -> dict[str, Any]:
    """Owner command: revoke the login with Google, then delete this instance's YouTube data."""
    import httpx

    revoked: str = "no token"
    if token_file.exists():
        try:
            token = json.loads(token_file.read_text(encoding="utf-8"))
            value = token.get("refresh_token") or token.get("token")
            r = httpx.post("https://oauth2.googleapis.com/revoke", data={"token": value}, timeout=30)
            revoked = (
                "revoked" if r.status_code == 200 else f"HTTP {r.status_code} (already revoked or expired)"
            )
        except Exception as exc:  # the purge must still happen
            revoked = f"revoke call failed: {exc}"
    return {"google": revoked, **purge(db, data_dir, token_file)}
