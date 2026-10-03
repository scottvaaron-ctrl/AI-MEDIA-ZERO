"""Owner posting limits: the pace no agent can change (plan stage 3; owner decisions 2026-09-30, 2026-10-01).

``config.yaml`` ``publishing.limits`` (owner-edited only):

* ``posts_per_run_per_platform`` (2): posts one run may make on each platform;
* ``min_hours_between_runs`` (2.5): a run may post only this long after the previous posting run;
* ``max_runs_per_day`` (2): posting runs per local calendar day (so at most 4 posts per platform a day).

Only real posts count: a publisher that writes to a platform API. Draft packages are not posts. A run
becomes a "posting run" at its first post (``posting_runs``); a run that is allowed to post but has
nothing to post uses up no slot. Retries of failed uploads count like first attempts.

Videos a run may not post simply wait, oldest first, for the next run that may.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aimz.db import Database
from aimz.util import new_id, now_iso


@dataclass(frozen=True)
class PostingLimits:
    posts_per_run_per_platform: int = 2
    min_hours_between_runs: float = 2.5
    max_runs_per_day: int = 2
    backlog_days: float = 2.0  # production pauses when more than this many days of posts are waiting

    @classmethod
    def from_config(cls, cfg: Any) -> PostingLimits:
        raw = dict(cfg.get("publishing.limits", {}) or {})
        base = cls()
        return cls(
            posts_per_run_per_platform=max(
                0, int(raw.get("posts_per_run_per_platform", base.posts_per_run_per_platform))
            ),
            min_hours_between_runs=max(
                0.0, float(raw.get("min_hours_between_runs", base.min_hours_between_runs))
            ),
            max_runs_per_day=max(0, int(raw.get("max_runs_per_day", base.max_runs_per_day))),
            backlog_days=max(0.5, float(raw.get("backlog_days", base.backlog_days))),
        )

    @property
    def posts_per_day_per_platform(self) -> int:
        return self.posts_per_run_per_platform * self.max_runs_per_day


def _local_day(t: datetime) -> str:
    return t.astimezone().strftime("%Y-%m-%d")


@dataclass
class PostingSession:
    """One run's permission to post. ``allowed`` False means nothing may be posted in this run."""

    db: Database
    limits: PostingLimits
    run_id: str | None
    allowed: bool
    reason: str
    counts: dict[str, int] = field(default_factory=dict)
    row_id: str | None = None

    def can_post(self, platform: str) -> bool:
        return self.allowed and self.counts.get(platform, 0) < self.limits.posts_per_run_per_platform

    def full(self, platforms: list[str]) -> bool:
        return not self.allowed or all(not self.can_post(p) for p in platforms)

    def record(self, platform: str, now: datetime | None = None) -> None:
        """Count one post. The first post turns this run into a posting run."""
        self.counts[platform] = self.counts.get(platform, 0) + 1
        now = now or datetime.now(UTC)
        if self.row_id is None:
            self.row_id = new_id("prun")
            self.db.insert(
                "posting_runs",
                {
                    "id": self.row_id,
                    "run_id": self.run_id,
                    "started_at": now.replace(microsecond=0).isoformat(),
                    "local_day": _local_day(now),
                    "counts_json": json.dumps(self.counts),
                    "created_at": now_iso(),
                },
            )
        else:
            self.db.update("posting_runs", self.row_id, {"counts_json": json.dumps(self.counts)})


class PostingGate:
    def __init__(self, db: Database, limits: PostingLimits):
        self.db = db
        self.limits = limits

    def last_run(self) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM posting_runs ORDER BY started_at DESC LIMIT 1")
        return dict(row) if row else None

    def runs_today(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        return int(self.db.count("posting_runs", "local_day=?", [_local_day(now)]))

    def check(self, now: datetime | None = None) -> tuple[bool, str]:
        """Whether a run starting now may post, and why not."""
        now = now or datetime.now(UTC)
        lim = self.limits
        if lim.posts_per_run_per_platform <= 0 or lim.max_runs_per_day <= 0:
            return False, "posting limits are set to zero"
        today = self.runs_today(now)
        if today >= lim.max_runs_per_day:
            return False, f"{today} posting runs today already (limit {lim.max_runs_per_day} per day)"
        last = self.last_run()
        if last:
            since = (now - datetime.fromisoformat(last["started_at"])).total_seconds() / 3600
            if since < lim.min_hours_between_runs:
                return False, (
                    f"last posting run was {since:.1f} h ago (needs {lim.min_hours_between_runs:g} h)"
                )
        return True, "ok"

    def open(self, run_id: str | None, now: datetime | None = None) -> PostingSession:
        ok, why = self.check(now)
        return PostingSession(self.db, self.limits, run_id, ok, why)

    def next_allowed(self, now: datetime | None = None) -> str:
        """A plain-language note on when posting can resume (for status output)."""
        now = now or datetime.now(UTC)
        ok, why = self.check(now)
        return "now" if ok else why

    def waiting_videos(self) -> int:
        return int(self.db.count("videos", "status IN ('rendered','approved')"))

    def backlog_full(self) -> tuple[bool, str]:
        cap = self.limits.posts_per_day_per_platform * self.limits.backlog_days
        waiting = self.waiting_videos()
        if waiting >= cap:
            return (
                True,
                f"{waiting} rendered videos are waiting to post (more than {self.limits.backlog_days:g} days of posting)",
            )
        return False, ""


def check_schedule(times: list[str], limits: PostingLimits) -> str | None:
    """Why a daily schedule breaks the owner's limits, or None. Times are local HH:MM."""
    if len(times) > limits.max_runs_per_day:
        return f"{len(times)} runs a day; the limit is {limits.max_runs_per_day}"
    minutes = sorted(int(t.split(":")[0]) * 60 + int(t.split(":")[1]) for t in times)
    for a, b in zip(minutes, [*minutes[1:], minutes[0] + 24 * 60], strict=True):
        if len(minutes) > 1 and (b - a) / 60 < limits.min_hours_between_runs:
            return (
                f"runs {(b - a) / 60:g} h apart; the limit is {limits.min_hours_between_runs:g} h "
                "(leave extra time: a run posts a few minutes after it starts)"
            )
    return None
