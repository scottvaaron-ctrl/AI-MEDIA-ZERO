"""Run and agent-run tracking: unique ids, timings, input/output refs, errors, costs."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from aimz.core.errors import BudgetDenied, KillSwitchEngaged
from aimz.db import Database
from aimz.util import dumps, new_id, now_iso

log = logging.getLogger("aimz.runs")


@dataclass
class RunContext:
    """Identifies one top-level invocation (a cycle or a single stage)."""

    id: str
    kind: str
    db: Database
    summary: dict[str, Any] = field(default_factory=dict)

    def note(self, key: str, value: Any) -> None:
        self.summary[key] = value

    def bump(self, key: str, by: int = 1) -> None:
        self.summary[key] = int(self.summary.get(key, 0)) + by


class RunTracker:
    def __init__(self, db: Database):
        self._db = db

    def start(self, kind: str) -> RunContext:
        run_id = new_id("run")
        self._db.insert("runs", {"id": run_id, "kind": kind, "status": "running", "started_at": now_iso()})
        log.info("run started", extra={"run_id": run_id, "operation": kind})
        return RunContext(id=run_id, kind=kind, db=self._db)

    def finish(self, ctx: RunContext, status: str = "ok", error: str | None = None) -> None:
        row = self._db.get("runs", ctx.id)
        started = row["started_at"] if row else now_iso()
        from datetime import datetime

        try:
            dur_ms = int(
                (datetime.fromisoformat(now_iso()) - datetime.fromisoformat(started)).total_seconds() * 1000
            )
        except ValueError:
            dur_ms = None
        self._db.update(
            "runs",
            ctx.id,
            {
                "status": status,
                "ended_at": now_iso(),
                "duration_ms": dur_ms,
                "summary_json": dumps(ctx.summary),
                "error": error,
            },
        )
        log.info("run finished status=%s", status, extra={"run_id": ctx.id, "operation": ctx.kind})

    @contextmanager
    def run(self, kind: str) -> Iterator[RunContext]:
        ctx = self.start(kind)
        try:
            yield ctx
        except KillSwitchEngaged as exc:
            self.finish(ctx, "killed", str(exc))
            raise
        except Exception as exc:
            self.record_error(ctx.id, "run", kind, exc)
            self.finish(ctx, "failed", f"{type(exc).__name__}: {exc}")
            raise
        else:
            self.finish(ctx, "ok")

    # -- agent-level spans --------------------------------------------------------------
    @contextmanager
    def agent(
        self,
        ctx: RunContext,
        agent: str,
        operation: str,
        input_refs: dict[str, Any] | None = None,
    ) -> Iterator[AgentSpan]:
        span = AgentSpan(self._db, ctx.id, agent, operation, input_refs or {})
        span.begin()
        try:
            yield span
        except BudgetDenied as exc:
            span.end("denied", error=str(exc))
            self.record_error(ctx.id, agent, operation, exc)
            raise
        except KillSwitchEngaged as exc:
            span.end("killed", error=str(exc))
            raise
        except Exception as exc:
            span.end("failed", error=f"{type(exc).__name__}: {exc}")
            self.record_error(ctx.id, agent, operation, exc)
            raise
        else:
            span.end("ok")

    def record_error(self, run_id: str | None, agent: str, operation: str, exc: BaseException) -> None:
        self._db.insert(
            "errors",
            {
                "id": new_id("err"),
                "run_id": run_id,
                "agent": agent,
                "operation": operation,
                "error_type": type(exc).__name__,
                "message": str(exc)[:2000],
                "traceback": "".join(traceback.format_exception(exc))[-8000:],
                "created_at": now_iso(),
            },
        )


class AgentSpan:
    def __init__(self, db: Database, run_id: str, agent: str, operation: str, input_refs: dict[str, Any]):
        self._db = db
        self.id = new_id("arun")
        self.run_id = run_id
        self.agent = agent
        self.operation = operation
        self.input_refs = input_refs
        self.output_refs: dict[str, Any] = {}
        self.estimated_cost = 0.0
        self.actual_cost = 0.0
        self._t0 = 0.0

    def begin(self) -> None:
        self._t0 = time.perf_counter()
        self._db.insert(
            "agent_runs",
            {
                "id": self.id,
                "run_id": self.run_id,
                "agent": self.agent,
                "operation": self.operation,
                "status": "running",
                "started_at": now_iso(),
                "input_refs_json": dumps(self.input_refs),
            },
        )
        log.info(
            "%s.%s start", self.agent, self.operation, extra={"run_id": self.run_id, "agent": self.agent}
        )

    def add_cost(self, estimated: float, actual: float) -> None:
        self.estimated_cost += estimated
        self.actual_cost += actual

    def end(self, status: str, error: str | None = None) -> None:
        dur_ms = int((time.perf_counter() - self._t0) * 1000)
        self._db.update(
            "agent_runs",
            self.id,
            {
                "status": status,
                "ended_at": now_iso(),
                "duration_ms": dur_ms,
                "output_refs_json": dumps(self.output_refs),
                "error": error,
                "estimated_cost_usd": self.estimated_cost,
                "actual_cost_usd": self.actual_cost,
            },
        )
        level = logging.INFO if status == "ok" else logging.WARNING
        log.log(
            level,
            "%s.%s %s in %dms%s",
            self.agent,
            self.operation,
            status,
            dur_ms,
            f": {error}" if error else "",
            extra={"run_id": self.run_id, "agent": self.agent, "duration_ms": dur_ms},
        )
