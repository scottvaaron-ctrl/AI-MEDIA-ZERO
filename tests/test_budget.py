from __future__ import annotations

import pytest

from aimz.core.budget import BudgetManager
from aimz.core.errors import BudgetDenied
from aimz.providers.paid_examples import PaidLLMProviderExample, PaidTTSProviderExample


def test_zero_budget_denies_any_paid_call(svc) -> None:  # noqa: ANN001
    with pytest.raises(BudgetDenied) as exc:
        svc.budget.authorize("PaidTTSProvider", "narration", 0.14)
    msg = str(exc.value)
    assert "DENIED" in msg and "PaidTTSProvider" in msg and "$0.14" in msg and "$0.00" in msg
    snap = svc.budget.snapshot()
    assert snap.denied_count == 1 and snap.spent_usd == 0.0


def test_free_calls_are_authorized_and_tracked(svc) -> None:  # noqa: ANN001
    auth = svc.budget.authorize("OllamaProvider", "ideation", 0.0, run_id="run_x", content_id="idea_x")
    svc.budget.settle(auth, 0.0)
    snap = svc.budget.snapshot()
    assert snap.authorized_count == 1 and snap.denied_count == 0 and snap.outstanding_usd == 0.0
    row = svc.db.one("SELECT * FROM ledger WHERE id=?", [auth.id])
    assert row["note"] == "closed:ok" and row["run_id"] == "run_x" and row["content_id"] == "idea_x"


def test_projection_includes_outstanding_authorizations(svc) -> None:  # noqa: ANN001
    bm = BudgetManager(svc.db, 1.00)
    a1 = bm.authorize("PaidLLMProvider", "x", 0.60)
    with pytest.raises(BudgetDenied):
        bm.authorize("PaidLLMProvider", "y", 0.50)  # 0.60 outstanding + 0.50 > 1.00
    bm.settle(a1, 0.20)
    bm.authorize("PaidLLMProvider", "y", 0.50)  # 0.20 spent + 0.50 <= 1.00
    assert bm.snapshot().spent_usd == pytest.approx(0.20)


def test_paid_provider_template_is_blocked_before_any_work(svc) -> None:  # noqa: ANN001
    ctx = svc.ctx()
    tts = PaidTTSProviderExample()
    with pytest.raises(BudgetDenied):
        tts.synthesize(ctx, "hello world" * 50, svc.env.data_dir / "x.wav")
    llm = PaidLLMProviderExample()
    with pytest.raises(BudgetDenied):
        llm.complete(ctx, "sys", "user", "test")
    assert svc.db.count("provider_usage") == 0  # denied before the usage row is even written
    assert svc.db.count("ledger", "entry_type='denial'") == 2


def test_negative_budget_clamps_to_zero(svc) -> None:  # noqa: ANN001
    assert BudgetManager(svc.db, -5).monthly_budget_usd == 0.0


def test_financials_tracking(svc) -> None:  # noqa: ANN001
    svc.budget.record_revenue(3.5, note="ad revenue")
    svc.budget.record_owner_injection(0.0, note="none")
    fin = svc.budget.financials()
    assert (
        fin["revenue"] == 3.5
        and fin["operating_spend"] == 0.0
        and fin["profit"] == 3.5
        and fin["reinvested_revenue"] == 0.0
    )
