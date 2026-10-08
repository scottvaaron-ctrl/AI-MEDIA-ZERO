"""Hard kill switch: when engaged, the agent does nothing (owner decision 2026-10-07).

Two independent signals, either of which engages the switch:

1. A sentinel file ``<data_dir>/KILL`` (works even if the database is locked or corrupt,
   and can be created by hand: ``echo stop > data\\KILL``).
2. The ``kill_switch`` key in ``system_state``.

``aimz kill`` sets both; ``aimz resume`` clears both.

While engaged:

* A cycle (``Orchestrator.cycle``) checks the switch once at the start and runs no stage at all:
  no recovery, guideline sync, research, ideas, writing, rendering, publishing, metrics, comments or
  learning, so no LLM, TTS, render or network call. It records the run as skipped. The one exception
  is YouTube's policy-required data retention (``youtube_data.enforce``), which keeps running because
  its 30-day limits apply however long the switch stays on.
* Owner-run CLI commands that do agent work (``produce``, ``research``, ``publish run`` ...) refuse.
  Owner control commands (``status``, ``kill``/``resume``, ``posting``, ``guideline`` ...) still work.
* Every outbound action (publishing, platform API writes, paid providers) still calls
  :meth:`KillSwitch.guard` as defence in depth.

To pause only posting while the rest of the work continues, use the posting switch
(``aimz/core/posting.py``); the kill switch overrides it. The agents never receive a handle to this
object that can clear it: the only clearing path is the owner CLI/dashboard, which writes the file
system and DB directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aimz.core.errors import KillSwitchEngaged
from aimz.db import Database
from aimz.util import now_iso

SENTINEL_NAME = "KILL"


@dataclass(frozen=True)
class KillStatus:
    engaged: bool
    reason: str
    since: str | None
    file_present: bool
    db_flag: bool


class KillSwitch:
    def __init__(self, db: Database, data_dir: Path):
        self._db = db
        self._sentinel = data_dir / SENTINEL_NAME

    @property
    def sentinel_path(self) -> Path:
        return self._sentinel

    def status(self) -> KillStatus:
        file_present = self._sentinel.exists()
        db_flag = self._db.get_state("kill_switch", "off") == "on"
        reason = self._db.get_state("kill_reason", "") or ""
        since = self._db.get_state("kill_since")
        if file_present and not reason:
            reason = "sentinel file present"
        return KillStatus(
            engaged=file_present or db_flag,
            reason=reason,
            since=since,
            file_present=file_present,
            db_flag=db_flag,
        )

    def is_engaged(self) -> bool:
        return self.status().engaged

    def guard(self, action: str) -> None:
        """Raise :class:`KillSwitchEngaged` if any outbound action must be blocked."""
        st = self.status()
        if st.engaged:
            raise KillSwitchEngaged(
                f"Kill switch engaged ({st.reason or 'no reason given'}); blocked: {action}"
            )

    # -- owner-only operations ---------------------------------------------------------
    def engage(self, reason: str = "owner request", actor: str = "owner") -> KillStatus:
        self._sentinel.parent.mkdir(parents=True, exist_ok=True)
        self._sentinel.write_text(f"{now_iso()} {reason}\n", encoding="utf-8")
        self._db.set_state("kill_switch", "on")
        self._db.set_state("kill_reason", reason)
        self._db.set_state("kill_since", now_iso())
        self._record(actor, "on", reason)
        return self.status()

    def release(self, actor: str = "owner") -> KillStatus:
        if self._sentinel.exists():
            self._sentinel.unlink()
        self._db.set_state("kill_switch", "off")
        self._db.set_state("kill_reason", "")
        self._record(actor, "off", "")
        return self.status()

    def _record(self, actor: str, value: str, reason: str) -> None:
        from aimz.util import new_id

        self._db.insert(
            "configuration_versions",
            {
                "id": new_id("cfg"),
                "key": "kill_switch",
                "content": f"{value} {reason}".strip(),
                "changed_by": actor,
                "created_at": now_iso(),
            },
        )
