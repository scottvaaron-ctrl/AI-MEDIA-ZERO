"""Posting switch: the owner's on/off control for posting only (owner decision 2026-10-07).

Stored in ``system_state`` key ``posting`` (``on``/``off``); a missing key means ``on``, so automatic
posting under the existing consent and approval gates is the default. When it is off, the publish
stage does nothing: rendered videos wait as ``rendered`` with no publication rows and no retry
attempts used. Every other stage (research, writing, rendering, metrics, comments, learning) runs.

It is separate from the kill switch, which stops all agent work and overrides this switch, and from
``AUTOPUBLISH_CONSENT``, the per-channel legal consent. Posting needs the switch on *and* the
consent/approval gates. Only the owner CLI and dashboard change it.
"""

from __future__ import annotations

from dataclasses import dataclass

from aimz.db import Database
from aimz.util import new_id, now_iso

STATE_KEY = "posting"
SINCE_KEY = "posting_since"
REASON_KEY = "posting_reason"
OFF_MESSAGE = "posting switch off; turn it on with `python -m aimz posting on`"


@dataclass(frozen=True)
class PostingStatus:
    on: bool
    since: str | None
    reason: str

    def describe(self) -> str:
        """``on``, or ``off (since …, reason …)`` for status output."""
        if self.on:
            return "on"
        parts = [f"since {self.since}" if self.since else "", f"reason {self.reason}" if self.reason else ""]
        detail = ", ".join(p for p in parts if p)
        return f"off ({detail})" if detail else "off"


class PostingSwitch:
    def __init__(self, db: Database):
        self._db = db

    def status(self) -> PostingStatus:
        on = (self._db.get_state(STATE_KEY, "on") or "on") != "off"
        return PostingStatus(
            on=on,
            since=self._db.get_state(SINCE_KEY),
            reason=self._db.get_state(REASON_KEY, "") or "",
        )

    def is_on(self) -> bool:
        return self.status().on

    # -- owner-only operations ---------------------------------------------------------
    def turn_on(self, actor: str = "owner", reason: str = "") -> PostingStatus:
        return self._set("on", actor, reason)

    def turn_off(self, actor: str = "owner", reason: str = "") -> PostingStatus:
        return self._set("off", actor, reason)

    def _set(self, value: str, actor: str, reason: str) -> PostingStatus:
        self._db.set_state(STATE_KEY, value)
        self._db.set_state(SINCE_KEY, now_iso())
        self._db.set_state(REASON_KEY, reason)
        self._db.insert(
            "configuration_versions",
            {
                "id": new_id("cfg"),
                "key": "posting",
                "content": f"{value} {reason}".strip(),
                "changed_by": actor,
                "created_at": now_iso(),
            },
        )
        return self.status()
