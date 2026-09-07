"""Experiment engine: explicit hypotheses, balanced arm assignment, uncertainty-aware evaluation.

An experiment varies one attribute (``variable``, e.g. ``hook_type``) between ``control`` and
``treatment``. Ideas selected for production are assigned an arm (balanced), the value is
forced onto the idea, and after publication the per-arm KPI is compared with a Welch t-test.
Nothing is concluded before ``min_sample`` per arm, and confidence is reported as a probability,
never as a causal certainty.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from typing import Any

from aimz.db import Database
from aimz.domain.models import ExperimentProposal
from aimz.util import new_id, now_iso

KPI_FIELDS = {
    "3_second_retention": "retention_3s",
    "retention_3s": "retention_3s",
    "avg_percent_viewed": "avg_percent_viewed",
    "average_percentage_viewed": "avg_percent_viewed",
    "completion_rate": "completion_rate",
    "shares_per_1000": "shares_per_1000",
    "subscribers_per_1000": "subs_per_1000",
    "subs_per_1000": "subs_per_1000",
    "followers_per_1000": "subs_per_1000",
    "views": "views",
    "score": "score",
    "performance_score": "score",
}


def kpi_value(row: dict[str, Any], kpi: str) -> float | None:
    field = KPI_FIELDS.get(kpi.strip().lower(), kpi)
    views = row.get("views") or 0
    if field == "shares_per_1000":
        return (row["shares"] / views * 1000) if views and row.get("shares") is not None else None
    if field == "subs_per_1000":
        gained = row.get("subscribers_gained")
        if gained is None:
            gained = row.get("followers_gained")
        return (gained / views * 1000) if views and gained is not None else None
    val = row.get(field)
    return float(val) if val is not None else None


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class ArmSummary:
    n: int
    mean: float
    sd: float


def summarize(values: list[float]) -> ArmSummary:
    n = len(values)
    if n == 0:
        return ArmSummary(0, 0.0, 0.0)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    return ArmSummary(n, mean, sd)


def welch_confidence(a: ArmSummary, b: ArmSummary) -> float:
    """Probability that treatment (b) beats control (a), via a Welch t approximation."""
    if a.n < 2 or b.n < 2:
        return 0.5
    se = math.sqrt((a.sd**2 / a.n) + (b.sd**2 / b.n))
    if se == 0:
        return 0.5 if a.mean == b.mean else (0.99 if b.mean > a.mean else 0.01)
    z = (b.mean - a.mean) / se
    return _norm_cdf(z)


ASSIGNABLE_VARIABLES: dict[str, set[str] | None] = {
    "hook_type": None,  # validated against config hook_types
    "runtime": None,  # integer seconds
    "target_platform": {"tiktok", "youtube_shorts", "both"},
}


class ExperimentEngine:
    def __init__(
        self,
        db: Database,
        default_min_sample: int = 8,
        max_concurrent: int = 2,
        min_confidence: float = 0.85,
        hook_types: list[str] | None = None,
    ):
        self.db = db
        self.hook_types = set(hook_types or [])
        self.default_min_sample = default_min_sample
        self.max_concurrent = max_concurrent
        self.min_confidence = min_confidence

    # -- lifecycle -------------------------------------------------------------------
    def running(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.query("SELECT * FROM experiments WHERE status='running' ORDER BY started_at")
        ]

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM experiments ORDER BY created_at DESC")]

    def validate(self, p: ExperimentProposal) -> str | None:
        """Return a rejection reason if the Editor-in-Chief could not actually run this experiment."""
        var = p.variable.strip().lower()
        if var not in ASSIGNABLE_VARIABLES:
            return f"variable {p.variable!r} is not assignable (allowed: {sorted(ASSIGNABLE_VARIABLES)})"
        control, treatment = p.control.strip().lower(), p.treatment.strip().lower()
        if control == treatment:
            return "control and treatment are identical"
        if var == "hook_type" and self.hook_types and not {control, treatment} <= self.hook_types:
            return f"hook_type arms must be in {sorted(self.hook_types)}"
        if var == "runtime" and not (control.isdigit() and treatment.isdigit()):
            return "runtime arms must be integer seconds"
        allowed = ASSIGNABLE_VARIABLES.get(var)
        if allowed and not {control, treatment} <= allowed:
            return f"{var} arms must be in {sorted(allowed)}"
        if not p.primary_kpi.strip():
            return "primary_kpi required"
        return None

    def propose(
        self, p: ExperimentProposal, created_by: str = "ai", min_sample: int | None = None, start: bool = True
    ) -> str | None:
        if self.validate(p) is not None:
            return None
        p = p.model_copy(
            update={
                "variable": p.variable.strip().lower(),
                "control": p.control.strip().lower(),
                "treatment": p.treatment.strip().lower(),
            }
        )
        if start and len(self.running()) >= self.max_concurrent:
            return None
        # don't duplicate a running experiment on the same variable
        for r in self.running():
            if r["variable"] == p.variable:
                return None
        eid = new_id("exp")
        self.db.insert(
            "experiments",
            {
                "id": eid,
                "name": p.name[:120],
                "hypothesis": p.hypothesis,
                "variable": p.variable,
                "control": p.control,
                "treatment": p.treatment,
                "primary_kpi": p.primary_kpi,
                "secondary_kpi": p.secondary_kpi or None,
                "min_sample": int(min_sample or self.default_min_sample),
                "status": "running" if start else "proposed",
                "started_at": now_iso() if start else None,
                "created_by": created_by,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
        return eid

    def retire(self, experiment_id: str, conclusion: str = "retired by strategy update") -> None:
        self.db.update(
            "experiments",
            experiment_id,
            {
                "status": "retired",
                "concluded_at": now_iso(),
                "conclusion": conclusion,
                "updated_at": now_iso(),
            },
        )

    # -- assignment ------------------------------------------------------------------
    def assign_arm(self, experiment: dict[str, Any], rng: random.Random | None = None) -> tuple[str, str]:
        """Return (arm, value) balancing arm counts across all ideas assigned so far."""
        rng = rng or random.Random()
        counts = {"control": 0, "treatment": 0}
        for r in self.db.query(
            "SELECT experiment_arm, COUNT(*) c FROM ideas WHERE experiment_id=? GROUP BY experiment_arm",
            [experiment["id"]],
        ):
            if r["experiment_arm"] in counts:
                counts[r["experiment_arm"]] = int(r["c"])
        if counts["control"] < counts["treatment"]:
            arm = "control"
        elif counts["treatment"] < counts["control"]:
            arm = "treatment"
        else:
            arm = rng.choice(["control", "treatment"])
        return arm, experiment[arm]

    # -- evaluation ------------------------------------------------------------------
    def evaluate(self, experiment: dict[str, Any], perf_rows: list[dict[str, Any]]) -> dict[str, Any]:
        kpi = experiment["primary_kpi"]
        arms: dict[str, list[float]] = {"control": [], "treatment": []}
        for row in perf_rows:
            if row.get("experiment_id") != experiment["id"] or row.get("experiment_arm") not in arms:
                continue
            v = kpi_value(row, kpi)
            if v is not None:
                arms[row["experiment_arm"]].append(v)
        a, b = summarize(arms["control"]), summarize(arms["treatment"])
        conf = welch_confidence(a, b)
        min_n = int(experiment["min_sample"])
        enough = a.n >= min_n and b.n >= min_n
        result = {
            "kpi": kpi,
            "control": {"n": a.n, "mean": round(a.mean, 4), "sd": round(a.sd, 4)},
            "treatment": {"n": b.n, "mean": round(b.mean, 4), "sd": round(b.sd, 4)},
            "p_treatment_better": round(conf, 4),
            "min_sample": min_n,
            "enough_sample": enough,
        }
        status = experiment["status"]
        conclusion = experiment.get("conclusion")
        if enough:
            if conf >= self.min_confidence:
                status, conclusion = (
                    "concluded",
                    f"Treatment '{experiment['treatment']}' likely better on {kpi} (p={conf:.2f}, n={a.n}/{b.n}). Correlational; keep monitoring.",
                )
            elif conf <= 1 - self.min_confidence:
                status, conclusion = (
                    "concluded",
                    f"Control '{experiment['control']}' likely better on {kpi} (p={1 - conf:.2f}, n={a.n}/{b.n}). Correlational; keep monitoring.",
                )
            elif a.n >= 3 * min_n and b.n >= 3 * min_n:
                status, conclusion = (
                    "concluded",
                    f"No detectable effect of {experiment['variable']} on {kpi} after {a.n}/{b.n} samples.",
                )
        self.db.update(
            "experiments",
            experiment["id"],
            {
                "result_json": json.dumps(result),
                "confidence": conf,
                "status": status,
                "conclusion": conclusion,
                "concluded_at": now_iso()
                if status == "concluded" and experiment["status"] != "concluded"
                else experiment.get("concluded_at"),
                "updated_at": now_iso(),
            },
        )
        result["status"] = status
        result["conclusion"] = conclusion
        return result

    def evaluate_all(self, perf_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for e in self.running():
            proposal = ExperimentProposal(
                name=e["name"],
                hypothesis=e["hypothesis"],
                variable=e["variable"],
                control=e["control"],
                treatment=e["treatment"],
                primary_kpi=e["primary_kpi"],
            )
            reason = self.validate(proposal)
            if reason:
                self.retire(e["id"], f"retired automatically: {reason}")
        return [{"id": e["id"], "name": e["name"], **self.evaluate(e, perf_rows)} for e in self.running()]
