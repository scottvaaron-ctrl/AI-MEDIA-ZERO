"""Experiment engine: explicit hypotheses, balanced arm assignment, uncertainty-aware evaluation.

An experiment varies one attribute (``variable``, e.g. ``hook_type``) between ``control`` and
``treatment``. Ideas selected for production are assigned an arm (balanced), the value is
forced onto the idea, and after publication the per-arm KPI is compared with a Welch t-test.
Nothing is concluded before ``min_sample`` per arm, and confidence is reported as a probability,
never as a causal certainty.
"""

from __future__ import annotations

import contextlib
import json
import math
import random
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from aimz import guideline
from aimz.db import Database
from aimz.domain.models import ExperimentProposal
from aimz.experiments.settings import CATALOG
from aimz.guideline import RUNTIME_LIMIT_MAX, RUNTIME_LIMIT_MIN, Guideline
from aimz.util import new_id, now_iso

# Only metrics a connected platform actually returns. 3-second retention and completion rate are not in
# any API (YouTube gives average percentage viewed; TikTok posts are private), so an experiment on them
# would stay at n=0 forever. Add them back only when a platform provides them.
KPI_FIELDS = {
    "avg_percent_viewed": "avg_percent_viewed",
    "average_percentage_viewed": "avg_percent_viewed",
    "shares_per_1000": "shares_per_1000",
    "subscribers_per_1000": "subs_per_1000",
    "subs_per_1000": "subs_per_1000",
    "followers_per_1000": "subs_per_1000",
    "views": "views",
    "score": "score",
    "performance_score": "score",
    # The strategist names the composite score in several ways; an unrecognised name used to read as
    # "no data" forever, so its first experiment could never collect a sample.
    "mean_score": "score",
    "avg_score": "score",
    "average_score": "score",
    "composite_score": "score",
    "engagement_score": "score",
}


def kpi_field(kpi: str) -> str | None:
    return KPI_FIELDS.get(kpi.strip().lower().replace(" ", "_").replace("-", "_"))


def kpi_value(row: dict[str, Any], kpi: str) -> float | None:
    """A KPI for one performance row, read from the snapshot the video is scored on.

    Rows from ``video_performance`` carry ``scored_metrics`` (None while the video is too young to
    compare); other rows are read as they are.
    """
    field = kpi_field(kpi) or kpi
    if field == "score":
        val = row.get("score")
        return float(val) if val is not None else None
    if "scored_metrics" in row:
        scored = row["scored_metrics"]
        if scored is None:
            return None
        row = scored
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


MEASURABLE_KPIS = (
    "score",
    "avg_percent_viewed",
    "shares_per_1000",
    "subscribers_per_1000",
    "views",
)


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
    "hook_type": None,  # any label the channel uses (validated against config hook_types only if set)
    "runtime": None,  # integer seconds
    # target_platform is left out until publishing routes a video to its target platform only; today every
    # video goes to every platform, so an experiment on it would compare nothing.
}

# Ideas that will never become a video. They do not count toward arm balance.
DEAD_IDEA_STATUSES = ("rejected", "killed")

# With a channel guideline (spec 2026-10-07 section 3): one experiment per channel on one guideline item.
GUIDELINE_MIN_SAMPLE = 10
# The guideline bar is fixed, not the legacy experiments.min_confidence (controller ruling R7).
# Owner decision 2026-10-07: 90% (lowered from 95%).
GUIDELINE_MIN_CONFIDENCE = 0.90
# A variant whose text asks for any of these contradicts the constitution (sourced facts only, no
# deception). A phrase screen matched on whole words, so "inventions" or "debunk fake claims" pass; the
# critic's policy checks still run on every script.
CONSTITUTION_PHRASES = (
    "fabricate",
    "fabricated",
    "invented facts",
    "invent facts",
    "no source",
    "unsourced",
    "clickbait",
    "fake quote",
    "fake quotes",
    "make up",
)
_CONSTITUTION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(x).replace(r"\ ", r"\s+") for x in CONSTITUTION_PHRASES) + r")\b",
    re.IGNORECASE,
)
# Conclusions of a closed guideline experiment that start the item's cooldown (controller ruling R11),
# besides the inconclusive status.
COOLDOWN_CONCLUSIONS = ("Guideline won", "Safety stop")
# Video statuses that count as published for the cooldown.
PUBLISHED_VIDEO_STATUSES = ("published", "measured")
# Experiment statuses that close a guideline experiment.
CLOSED_STATUSES = ("concluded", "variant_won", "inconclusive", "retired")


def contradicts_constitution(text: str) -> bool:
    """True when ``text`` asks for something the constitution forbids (whole-word phrase screen)."""
    return bool(_CONSTITUTION_RE.search(text))


class ExperimentEngine:
    def __init__(
        self,
        db: Database,
        default_min_sample: int = 8,
        max_concurrent: int = 2,
        min_confidence: float = 0.85,
        hook_types: list[str] | None = None,
        guideline: Guideline | None = None,
        safety_min_sample: int = 6,
        max_sample: int = 30,
        cooldown_videos: int = 20,
    ):
        self.db = db
        self.hook_types = set(hook_types or [])
        self.default_min_sample = default_min_sample
        self.max_concurrent = max_concurrent
        self.min_confidence = min_confidence
        # Without a guideline the legacy ASSIGNABLE_VARIABLES path runs unchanged.
        self.guideline = guideline
        self.safety_min_sample = safety_min_sample
        self.max_sample = max_sample
        self.cooldown_videos = cooldown_videos

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
        if self.guideline is not None:
            return self._validate_guideline(p)
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
        if kpi_field(p.primary_kpi) is None:
            return f"primary_kpi {p.primary_kpi!r} is not a measured metric (allowed: {', '.join(MEASURABLE_KPIS)})"
        return None

    # -- guideline experiments ---------------------------------------------------------
    @staticmethod
    def _item_variant(p: ExperimentProposal) -> tuple[str, str]:
        """The guideline item and variant; a proposal that left the new fields empty uses variable/treatment."""
        return (p.guideline_item or p.variable).strip(), (p.variant or p.treatment).strip()

    def _check_item(self, item: str, variant: str) -> tuple[str, str | None, str | None]:
        """(control, normalised variant, reason) for testing ``variant`` against the guideline's ``item``."""
        g = self.guideline
        assert g is not None
        setting = item[2:] if item.startswith("G-") else ""
        if item not in g.item_ids():
            if setting in g.unavailable:
                return "", None, f"variant needs {CATALOG[setting].requires}, which is not installed"
            return "", None, "unknown guideline item"
        if item == "G-runtime":
            control = f"{g.runtime_min}-{g.runtime_max}"
            try:
                seconds = int(round(float(variant.lower().rstrip("s").strip())))
            except (ValueError, OverflowError):
                return control, None, f"variant out of bounds: runtime {variant!r} is not whole seconds"
            if not RUNTIME_LIMIT_MIN <= seconds <= RUNTIME_LIMIT_MAX:
                return (
                    control,
                    None,
                    (
                        f"variant out of bounds: runtime {seconds}s is outside the short_form limits "
                        f"{RUNTIME_LIMIT_MIN}-{RUNTIME_LIMIT_MAX} s"
                    ),
                )
            return control, str(seconds), None
        if item == "G-genre":
            control = g.genre
            if not variant.strip():
                return control, None, "variant text required"
            if variant.strip() == control.strip():
                return control, None, "control and treatment are identical"
            return control, variant.strip(), None
        if setting in guideline.CHECK_KEYS:
            control = guideline.encode_check(g.checks[setting])
            value, why = guideline.check_variant(setting, variant)
            if why:
                return control, None, f"variant out of bounds: {why}"
            encoded = guideline.encode_check(value)
            if encoded == control:
                return control, None, "control and treatment are identical"
            return control, encoded, None
        if setting in g.locked:
            control = str(g.locked[setting])
            try:
                norm, why = CATALOG[setting].check(variant)
            except OverflowError:  # e.g. "inf" for a whole-number setting
                norm, why = None, f"{setting}: {variant!r} is not a finite number"
            if why:
                return control, None, f"variant out of bounds: {why}"
            if str(norm) == control:
                return control, None, "control and treatment are identical"
            return control, str(norm), None
        rule = g.rules[item]
        if rule.stage == "cover":
            return rule.text, None, f"{item} is a cover rule, which the pipeline does not apply yet"
        control = rule.text
        if not variant:
            return control, None, "variant text required"
        if variant == control:
            return control, None, "control and treatment are identical"
        return control, variant, None

    def _validate_guideline(self, p: ExperimentProposal) -> str | None:
        item, variant = self._item_variant(p)
        _control, _norm, why = self._check_item(item, variant)
        if why:
            return why
        if contradicts_constitution(variant):
            return "variant contradicts the constitution"
        if self.running():
            return "an experiment is already running on this channel"
        left = self.cooldown_left(item)
        if left > 0:
            return f"{item} is cooling down for {left} more videos"
        if self.has_pending_proposal(item):
            # While the owner decides, the channel may test a different item (spec section 4).
            return f"{item} has a change proposal waiting for the owner"
        return None

    def has_pending_proposal(self, item: str) -> bool:
        """True when ``item`` has a guideline change proposal waiting for the owner."""
        # The proposals table is written by the approval flow; tolerate a database without it.
        with contextlib.suppress(sqlite3.OperationalError):
            return bool(
                self.db.scalar(
                    "SELECT 1 FROM guideline_proposals WHERE item=? AND status='pending' LIMIT 1", [item]
                )
            )
        return False

    def cooldown_left(self, item: str) -> int:
        """Videos still to publish before ``item`` may be tested again.

        The count restarts at the item's last inconclusive experiment, last experiment the guideline won
        or safety-stopped (ruling R11), or last rejected proposal.
        """
        lost = " OR ".join("conclusion LIKE ?" for _ in COOLDOWN_CONCLUSIONS)
        marks = [
            self.db.scalar(
                "SELECT MAX(COALESCE(concluded_at, updated_at)) FROM experiments WHERE variable=? AND "
                f"(status='inconclusive' OR (status='concluded' AND ({lost})))",
                [item, *(f"{c}%" for c in COOLDOWN_CONCLUSIONS)],
            )
        ]
        # The proposals table is written by the approval flow; tolerate a database without it.
        with contextlib.suppress(sqlite3.OperationalError):
            marks.append(
                self.db.scalar(
                    "SELECT MAX(COALESCE(decided_at, created_at)) FROM guideline_proposals "
                    "WHERE item=? AND status='rejected'",
                    [item],
                )
            )
        since = max((m for m in marks if m), default=None)
        if since is None:
            return 0
        published = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM videos v WHERE v.status IN (?, ?) AND COALESCE("
                "(SELECT MIN(p.posted_at) FROM publications p WHERE p.video_id = v.id), v.updated_at) > ?",
                [*PUBLISHED_VIDEO_STATUSES, since],
                default=0,
            )
            or 0
        )
        return max(0, self.cooldown_videos - published)

    def setting_override(self, idea: dict[str, Any]) -> dict[str, Any] | None:
        """``{variable, value}`` for a treatment-arm idea of a running ``G-<setting>`` experiment, else None."""
        exp = self._running_for(idea)
        if exp is None or idea.get("experiment_arm") != "treatment":
            return None
        setting = str(exp["variable"])[2:] if str(exp["variable"]).startswith("G-") else ""
        d = CATALOG.get(setting)
        if d is None:
            return None
        norm, why = d.check(exp["treatment"])
        return None if why else {"variable": setting, "value": norm}

    def _running_for(self, idea: dict[str, Any]) -> dict[str, Any] | None:
        exp_id = idea.get("experiment_id")
        if not exp_id:
            return None
        exp = self.db.get("experiments", exp_id)
        return dict(exp) if exp and exp["status"] == "running" else None

    def topic_experiment(self) -> dict[str, Any] | None:
        """The running experiment on a topic-stage guideline rule, if any."""
        g = self.guideline
        if g is None:
            return None
        for e in self.running():
            rule = g.rules.get(str(e["variable"]))
            # A genre experiment is a topic experiment: ideas are written under one arm's genre or the other's.
            if e["variable"] == "G-genre" or (rule is not None and rule.stage == "topic"):
                return e
        return None

    def topic_arm_counts(
        self, experiment: dict[str, Any], statuses: tuple[str, ...] | None = None
    ) -> dict[str, int]:
        """Ideas per arm for an experiment; ``statuses`` limits which idea statuses count."""
        where = "experiment_id=? AND status NOT IN " + str(DEAD_IDEA_STATUSES)
        params: list[Any] = [experiment["id"]]
        if statuses:
            where += f" AND status IN ({', '.join('?' for _ in statuses)})"
            params += list(statuses)
        counts = {"control": 0, "treatment": 0}
        for r in self.db.query(
            f"SELECT experiment_arm, COUNT(*) c FROM ideas WHERE {where} GROUP BY experiment_arm", params
        ):
            if r["experiment_arm"] in counts:
                counts[r["experiment_arm"]] = int(r["c"])
        return counts

    def topic_arm_for_new_idea(self) -> tuple[str, str] | None:
        """(arm, rule text) for ideation's next idea under a topic-rule experiment, alternating arms.

        The arm with fewer labelled ideas goes next (control on a tie); None when no topic rule is tested.
        """
        exp = self.topic_experiment()
        if exp is None:
            return None
        counts = self.topic_arm_counts(exp)
        arm = "treatment" if counts["treatment"] < counts["control"] else "control"
        return arm, str(exp[arm])

    def close_for_baseline_change(self, changed_items: set[str]) -> list[str]:
        """Close running experiments whose guideline item the owner changed; returns their ids."""
        closed = []
        for e in self.running():
            if e["variable"] in changed_items:
                self.retire(e["id"], "baseline changed by owner")
                closed.append(e["id"])
        return closed

    def _propose_guideline(self, p: ExperimentProposal, created_by: str, start: bool) -> str | None:
        g = self.guideline
        assert g is not None
        if self._validate_guideline(p) is not None:
            return None
        item, variant = self._item_variant(p)
        control, treatment, _ = self._check_item(item, variant)
        eid = new_id("exp")
        self.db.insert(
            "experiments",
            {
                "id": eid,
                "name": p.name[:120],
                "hypothesis": p.hypothesis,
                "variable": item,
                "control": control,
                "treatment": treatment,
                # One sample per scored video, on the composite score (spec section 3).
                "primary_kpi": "score",
                "secondary_kpi": "avg_percent_viewed",
                "min_sample": GUIDELINE_MIN_SAMPLE,
                "guideline_version": g.version,
                "status": "running" if start else "proposed",
                "started_at": now_iso() if start else None,
                "created_by": created_by,
                "created_at": now_iso(),
                "updated_at": now_iso(),
            },
        )
        return eid

    def propose(
        self, p: ExperimentProposal, created_by: str = "ai", min_sample: int | None = None, start: bool = True
    ) -> str | None:
        if self.guideline is not None:
            return self._propose_guideline(p, created_by, start)
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
        # Ideas not yet being written are freed, so they do not publish under a retired experiment's
        # label. Ideas already in production keep theirs: the arm shaped what was written.
        # Rejected ideas are freed too: they never became a video, so they carry no result.
        self.db.execute(
            "UPDATE ideas SET experiment_id=NULL, experiment_arm=NULL, updated_at=? "
            "WHERE experiment_id=? AND status IN ('candidate','selected','rejected','killed')",
            [now_iso(), experiment_id],
        )

    # -- assignment ------------------------------------------------------------------
    def next_for_assignment(self) -> dict[str, Any] | None:
        """The running experiment with the fewest live ideas assigned, so a second one is not starved."""
        running = self.running()
        if not running:
            return None
        counts = {
            r["experiment_id"]: int(r["c"])
            for r in self.db.query(
                "SELECT experiment_id, COUNT(*) c FROM ideas WHERE experiment_id IS NOT NULL "
                f"AND status NOT IN {DEAD_IDEA_STATUSES} GROUP BY experiment_id"
            )
        }
        return min(running, key=lambda e: (counts.get(e["id"], 0), e["started_at"] or ""))

    def assign_arm(self, experiment: dict[str, Any], rng: random.Random | None = None) -> tuple[str, str]:
        """Return (arm, value) balancing arms on ideas that published or still can.

        Rejected ideas are not counted: an arm whose scripts fail QA more often would otherwise get fewer
        videos while looking balanced.
        """
        rng = rng or random.Random()
        counts = {"control": 0, "treatment": 0}
        for r in self.db.query(
            "SELECT experiment_arm, COUNT(*) c FROM ideas WHERE experiment_id=? "
            f"AND status NOT IN {DEAD_IDEA_STATUSES} GROUP BY experiment_arm",
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
        # One sample per video: a video measured on two platforms is not two independent results.
        per_video: dict[tuple[str, str], list[float]] = {}
        # A video narrated with the fallback voice (Kokoro unavailable) differs in a second variable: left out.
        fallback = {
            str(r["video_id"])
            for r in self.db.query("SELECT video_id FROM video_settings WHERE variable='voice_fallback'")
        }
        excluded: set[str] = set()
        for row in perf_rows:
            if row.get("experiment_id") != experiment["id"] or row.get("experiment_arm") not in arms:
                continue
            if row.get("video_id") is not None and str(row["video_id"]) in fallback:
                excluded.add(str(row["video_id"]))
                continue
            v = kpi_value(row, kpi)
            if v is not None:
                key = (str(row["experiment_arm"]), str(row.get("video_id") or id(row)))
                per_video.setdefault(key, []).append(v)
        for (arm, _vid), vals in per_video.items():
            arms[arm].append(sum(vals) / len(vals))
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
            "excluded_voice_fallback": len(excluded),
        }
        status = experiment["status"]
        conclusion = experiment.get("conclusion")
        if self._is_guideline_experiment(experiment):
            # Each result also reports the mean average % viewed per arm (spec section 3).
            apv = self._mean_percent_viewed(experiment["id"], perf_rows)
            for arm in ("control", "treatment"):
                result[arm]["mean_avg_percent_viewed"] = apv[arm]
            status, conclusion = self._guideline_outcome(experiment, a, b, conf, status, conclusion)
        elif enough:
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
                if status in CLOSED_STATUSES and experiment["status"] != status
                else experiment.get("concluded_at"),
                "updated_at": now_iso(),
            },
        )
        result["status"] = status
        result["conclusion"] = conclusion
        return result

    @staticmethod
    def _is_guideline_experiment(experiment: dict[str, Any]) -> bool:
        return experiment.get("guideline_version") is not None

    @staticmethod
    def _mean_percent_viewed(exp_id: str, perf_rows: list[dict[str, Any]]) -> dict[str, float | None]:
        """Mean average % viewed per arm, one value per video (None when no video has it yet)."""
        per_video: dict[tuple[str, str], list[float]] = {}
        for row in perf_rows:
            arm = row.get("experiment_arm")
            if row.get("experiment_id") != exp_id or arm not in ("control", "treatment"):
                continue
            v = kpi_value(row, "avg_percent_viewed")
            if v is not None:
                per_video.setdefault((str(arm), str(row.get("video_id") or id(row))), []).append(v)
        out: dict[str, float | None] = {}
        for arm in ("control", "treatment"):
            vals = [sum(v) / len(v) for (a, _), v in per_video.items() if a == arm]
            out[arm] = round(sum(vals) / len(vals), 2) if vals else None
        return out

    def _guideline_outcome(
        self,
        experiment: dict[str, Any],
        a: ArmSummary,
        b: ArmSummary,
        conf: float,
        status: str,
        conclusion: str | None,
    ) -> tuple[str, str | None]:
        """Variant wins / guideline wins / safety stop / inconclusive (spec section 3); else unchanged."""
        min_n = int(experiment["min_sample"])
        kpi = experiment["primary_kpi"]
        n = f"n={a.n}/{b.n}"
        win = GUIDELINE_MIN_CONFIDENCE
        lose = 1 - GUIDELINE_MIN_CONFIDENCE
        if a.n >= min_n and b.n >= min_n and conf >= win:
            return "variant_won", (
                f"Variant won: '{experiment['treatment']}' beat the guideline on {kpi} (p={conf:.2f}, {n}). "
                "Correlational; the owner decides."
            )
        if a.n >= min_n and b.n >= min_n and conf <= lose:
            return "concluded", f"Guideline won: the variant did worse on {kpi} (p={1 - conf:.2f}, {n})."
        if a.n >= self.safety_min_sample and b.n >= self.safety_min_sample and conf <= lose:
            return "concluded", (
                f"Safety stop: the variant was clearly worse on {kpi} (p={1 - conf:.2f}, {n}); stopped early."
            )
        if a.n >= self.max_sample and b.n >= self.max_sample:
            return "inconclusive", f"Inconclusive: no decision on {kpi} after {n} (p={conf:.2f})."
        return status, conclusion

    def evaluate_all(self, perf_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for e in self.running():
            if self.guideline is not None:
                # Only the item itself is re-checked: the one-running and cooldown refusals are about
                # starting an experiment, not keeping one. A legacy experiment is not a guideline item.
                _c, _v, reason = self._check_item(str(e["variable"]), str(e["treatment"]))
                if reason:
                    self.retire(e["id"], f"retired automatically: {reason}")
                continue
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
