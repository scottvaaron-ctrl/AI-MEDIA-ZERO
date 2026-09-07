"""Infrastructure-level budget enforcement.

Every provider call goes through :meth:`BudgetManager.authorize` *before* executing.
The budget comes from ``.env`` (``MONTHLY_BUDGET_USD``), which agents cannot write.
Authorization is recorded in the ``ledger`` table; a denial is also recorded so the
owner can see what the AI *wanted* to do.

Projected spend for the month = actual spend so far + outstanding authorizations
(estimated costs of calls that have not reported an actual cost yet). If the projection
would exceed the cap, :class:`BudgetDenied` is raised and the caller must not proceed.

Zero-cost providers still pass through here so that provider usage is tracked uniformly,
and so a provider that *claims* to be free but reports a non-zero actual cost trips the
cap on the next call.
"""

from __future__ import annotations

from dataclasses import dataclass

from aimz.core.errors import BudgetDenied
from aimz.db import Database
from aimz.util import month_key, new_id, now_iso


@dataclass(frozen=True)
class Authorization:
    id: str
    provider: str
    purpose: str
    estimated_cost: float
    run_id: str | None
    content_id: str | None
    budget_month: str


@dataclass(frozen=True)
class BudgetSnapshot:
    month: str
    budget_usd: float
    spent_usd: float
    outstanding_usd: float
    denied_count: int
    authorized_count: int

    @property
    def available_usd(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd - self.outstanding_usd)

    @property
    def projected_usd(self) -> float:
        return self.spent_usd + self.outstanding_usd


class BudgetManager:
    def __init__(self, db: Database, monthly_budget_usd: float):
        self._db = db
        # Defensive: the budget can never be negative and is frozen for the process lifetime.
        self._budget = max(0.0, float(monthly_budget_usd))

    @property
    def monthly_budget_usd(self) -> float:
        return self._budget

    # -- reporting -----------------------------------------------------------------------
    def snapshot(self, month: str | None = None) -> BudgetSnapshot:
        month = month or month_key()
        spent = float(
            self._db.scalar(
                "SELECT COALESCE(SUM(actual_cost_usd), 0) FROM ledger WHERE budget_month=? AND entry_type='spend'",
                [month],
                0.0,
            )
        )
        outstanding = float(
            self._db.scalar(
                "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM ledger "
                "WHERE budget_month=? AND entry_type='authorization' AND note='open'",
                [month],
                0.0,
            )
        )
        denied = self._db.count("ledger", "budget_month=? AND entry_type='denial'", [month])
        authorized = self._db.count("ledger", "budget_month=? AND entry_type='authorization'", [month])
        return BudgetSnapshot(month, self._budget, spent, outstanding, denied, authorized)

    # -- enforcement ---------------------------------------------------------------------
    def authorize(
        self,
        provider: str,
        purpose: str,
        estimated_cost: float,
        *,
        run_id: str | None = None,
        content_id: str | None = None,
    ) -> Authorization:
        estimated_cost = max(0.0, float(estimated_cost))
        month = month_key()
        snap = self.snapshot(month)
        projected = snap.projected_usd + estimated_cost
        # Strict: any non-zero estimate against a $0 budget is denied; any projection over cap is denied.
        if projected > self._budget + 1e-9:
            self._db.insert(
                "ledger",
                {
                    "id": new_id("ledg"),
                    "entry_type": "denial",
                    "provider": provider,
                    "action": purpose,
                    "content_id": content_id,
                    "run_id": run_id,
                    "estimated_cost_usd": estimated_cost,
                    "actual_cost_usd": 0.0,
                    "amount_usd": 0.0,
                    "budget_month": month,
                    "note": f"projected ${projected:.4f} > budget ${self._budget:.2f}",
                    "created_at": now_iso(),
                },
            )
            raise BudgetDenied(provider, purpose, estimated_cost, snap.available_usd)

        auth_id = new_id("auth")
        self._db.insert(
            "ledger",
            {
                "id": auth_id,
                "entry_type": "authorization",
                "provider": provider,
                "action": purpose,
                "content_id": content_id,
                "run_id": run_id,
                "estimated_cost_usd": estimated_cost,
                "actual_cost_usd": 0.0,
                "amount_usd": 0.0,
                "budget_month": month,
                "note": "open",
                "created_at": now_iso(),
            },
        )
        return Authorization(auth_id, provider, purpose, estimated_cost, run_id, content_id, month)

    def settle(self, auth: Authorization, actual_cost: float, status: str = "ok") -> None:
        """Close an authorization with the actual cost (which is recorded as spend)."""
        actual_cost = max(0.0, float(actual_cost))
        self._db.update("ledger", auth.id, {"note": f"closed:{status}", "actual_cost_usd": actual_cost})
        if actual_cost > 0:
            self._db.insert(
                "ledger",
                {
                    "id": new_id("ledg"),
                    "entry_type": "spend",
                    "provider": auth.provider,
                    "action": auth.purpose,
                    "content_id": auth.content_id,
                    "run_id": auth.run_id,
                    "estimated_cost_usd": auth.estimated_cost,
                    "actual_cost_usd": actual_cost,
                    "amount_usd": -actual_cost,
                    "budget_month": auth.budget_month,
                    "note": status,
                    "created_at": now_iso(),
                },
            )

    # -- money in / out (owner-only entry points) ------------------------------------------
    def record_revenue(self, amount: float, note: str = "", content_id: str | None = None) -> None:
        self._db.insert(
            "ledger",
            {
                "id": new_id("ledg"),
                "entry_type": "revenue",
                "provider": None,
                "action": "revenue",
                "content_id": content_id,
                "run_id": None,
                "estimated_cost_usd": 0.0,
                "actual_cost_usd": 0.0,
                "amount_usd": float(amount),
                "budget_month": month_key(),
                "note": note,
                "created_at": now_iso(),
            },
        )

    def record_owner_injection(self, amount: float, note: str = "") -> None:
        self._db.insert(
            "ledger",
            {
                "id": new_id("ledg"),
                "entry_type": "owner_injection",
                "provider": None,
                "action": "owner_injection",
                "content_id": None,
                "run_id": None,
                "estimated_cost_usd": 0.0,
                "actual_cost_usd": 0.0,
                "amount_usd": float(amount),
                "budget_month": month_key(),
                "note": note,
                "created_at": now_iso(),
            },
        )

    def financials(self) -> dict[str, float]:
        def total(kind: str) -> float:
            return float(
                self._db.scalar(
                    "SELECT COALESCE(SUM(amount_usd),0) FROM ledger WHERE entry_type=?", [kind], 0.0
                )
            )

        revenue = total("revenue")
        injections = total("owner_injection")
        spend = abs(total("spend"))
        reinvested = total("reinvestment")
        return {
            "revenue": revenue,
            "owner_injections": injections,
            "operating_spend": spend,
            "reinvested_revenue": reinvested,
            "profit": revenue - spend,
        }
