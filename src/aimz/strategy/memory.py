"""Strategy memory.

The structured state lives in ``strategy_versions.state_json`` (every change is a new version,
attributed to ``ai``/``owner``/``system``). A human-readable rendering is written to
``config/strategy.md`` after each save. Agents receive :meth:`StrategyMemory.prompt_summary`
in their prompts, and the Editor-in-Chief / allocation engine read the structured fields.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from aimz.db import Database
from aimz.domain.models import StrategyUpdate
from aimz.settings import ContentFamily
from aimz.util import new_id, now_iso


def default_state(families: list[ContentFamily], explore_ratio: float = 0.7) -> dict[str, Any]:
    return {
        "version": 0,
        "audience_model": "Unknown. Cold start: assume curious, general-interest short-form viewers until data says otherwise.",
        "explore_ratio": explore_ratio,
        "families": {
            f.key: {
                "label": f.label,
                "description": f.description,
                "status": "hypothesis",
                "n": 0,
                "mean_score": None,
                "note": "",
            }
            for f in families
        },
        "strong_hooks": [],
        "weak_hooks": [],
        "runtime_observations": "None yet.",
        "source_observations": "None yet.",
        "production_bottlenecks": "None recorded.",
        "audience_requests": [],
        "active_experiments": [],
        "retired_experiments": [],
        "confidence": 0.05,
        "notes": "",
        "last_updated": now_iso(),
    }


class StrategyMemory:
    def __init__(
        self,
        db: Database,
        config_dir: Path,
        families: list[ContentFamily],
        explore_ratio: float = 0.7,
        min_measured_for_exploit: int = 12,
    ):
        self.db = db
        self.config_dir = config_dir
        self.families = families
        self.explore_ratio = explore_ratio
        self.min_measured_for_exploit = min_measured_for_exploit

    # -- read ----------------------------------------------------------------------------
    def latest_row(self) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM strategy_versions ORDER BY version DESC LIMIT 1")
        return dict(row) if row else None

    def current(self) -> dict[str, Any]:
        row = self.latest_row()
        if row:
            state = json.loads(row["state_json"])
            # merge in any families added to config since
            for f in self.families:
                state["families"].setdefault(
                    f.key,
                    {
                        "label": f.label,
                        "description": f.description,
                        "status": "hypothesis",
                        "n": 0,
                        "mean_score": None,
                        "note": "",
                    },
                )
            return state
        state = default_state(self.families, self.explore_ratio)
        self.save(state, created_by="system", change_summary="initial cold-start strategy")
        return state

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.query(
                "SELECT id, version, created_by, run_id, change_summary, created_at FROM strategy_versions ORDER BY version DESC LIMIT ?",
                [limit],
            )
        ]

    # -- write ---------------------------------------------------------------------------
    def save(
        self, state: dict[str, Any], created_by: str, run_id: str | None = None, change_summary: str = ""
    ) -> int:
        prev = self.latest_row()
        version = (int(prev["version"]) + 1) if prev else 0
        state["version"] = version
        state["last_updated"] = now_iso()
        md = render_markdown(state)
        self.db.insert(
            "strategy_versions",
            {
                "id": new_id("strat"),
                "version": version,
                "created_by": created_by,
                "run_id": run_id,
                "state_json": json.dumps(state, ensure_ascii=False),
                "markdown": md,
                "change_summary": change_summary,
                "created_at": now_iso(),
            },
        )
        with contextlib.suppress(OSError):
            (self.config_dir / "strategy.md").write_text(md, encoding="utf-8")
        return version

    def apply_update(
        self,
        state: dict[str, Any],
        upd: StrategyUpdate,
        family_stats: dict[str, Any],
        experiments: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge an AI-proposed update into the state, keeping computed statistics authoritative."""
        new = json.loads(json.dumps(state))
        new["audience_model"] = upd.audience_model.strip() or new["audience_model"]
        for fu in upd.family_updates:
            fam = new["families"].setdefault(
                fu.key,
                {
                    "label": fu.key.replace("_", " ").title(),
                    "description": "",
                    "status": "new",
                    "n": 0,
                    "mean_score": None,
                    "note": "",
                },
            )
            st = family_stats.get(fu.key)
            n = int(getattr(st, "n", 0) or 0)
            # Guard: the AI may not declare a winner/loser or retire a family without enough samples.
            if fu.status in {"winning", "losing", "retired"} and n < 3:
                fam["status"] = "testing" if n else "hypothesis"
                fam["note"] = f"(kept as {fam['status']}; only {n} measured) {fu.note}".strip()
            else:
                fam["status"] = fu.status
                fam["note"] = fu.note
        for key, st in family_stats.items():
            fam = new["families"].setdefault(
                key,
                {"label": key, "description": "", "status": "new", "n": 0, "mean_score": None, "note": ""},
            )
            fam["n"] = int(getattr(st, "n", 0) or 0)
            fam["mean_score"] = round(float(getattr(st, "mean", 0.0)), 3) if fam["n"] else None
            if fam["n"] and fam["status"] == "hypothesis":
                fam["status"] = "testing"
        new["strong_hooks"] = upd.strong_hooks[:8]
        new["weak_hooks"] = upd.weak_hooks[:8]
        new["runtime_observations"] = upd.runtime_observations or new["runtime_observations"]
        new["source_observations"] = upd.source_observations or new["source_observations"]
        new["production_bottlenecks"] = upd.production_bottlenecks or new["production_bottlenecks"]
        new["audience_requests"] = list(
            dict.fromkeys([*new.get("audience_requests", []), *upd.audience_requests])
        )[-12:]
        new["active_experiments"] = experiments.get("active", [])
        new["retired_experiments"] = experiments.get("retired", [])
        new["confidence"] = float(upd.confidence)
        new["notes"] = upd.notes[:2000]
        # Explore ratio: the AI may suggest, but with little evidence it cannot reduce exploration.
        measured = sum(int(getattr(st, "n", 0) or 0) for st in family_stats.values())
        suggested = float(upd.explore_ratio_suggestion)
        new["explore_ratio"] = (
            suggested if measured >= self.min_measured_for_exploit else max(suggested, self.explore_ratio)
        )
        return new

    # -- prompts -----------------------------------------------------------------------
    def prompt_summary(self, state: dict[str, Any], max_chars: int = 3500) -> str:
        fams = "\n".join(
            f"- {k}: status={v.get('status')} n={v.get('n', 0)} mean_score={v.get('mean_score')} note={v.get('note', '')[:80]}"
            for k, v in state["families"].items()
        )
        text = (
            f"STRATEGY MEMORY v{state.get('version', 0)} (confidence {state.get('confidence', 0):.2f})\n"
            f"Audience model: {state.get('audience_model', '')}\n"
            f"Explore ratio: {state.get('explore_ratio', 0.7):.0%}\n"
            f"Content families:\n{fams}\n"
            f"Strong hooks: {', '.join(state.get('strong_hooks', [])) or 'unknown'}\n"
            f"Weak hooks: {', '.join(state.get('weak_hooks', [])) or 'unknown'}\n"
            f"Runtime: {state.get('runtime_observations', '')}\n"
            f"Sources: {state.get('source_observations', '')}\n"
            f"Bottlenecks: {state.get('production_bottlenecks', '')}\n"
            f"Audience requests: {'; '.join(state.get('audience_requests', [])) or 'none'}\n"
            f"Notes: {state.get('notes', '')}\n"
        )
        return text[:max_chars]


def render_markdown(state: dict[str, Any]) -> str:
    fams = state.get("families", {})
    winners = [
        f"{k} (n={v.get('n', 0)}, score={v.get('mean_score')})"
        for k, v in fams.items()
        if v.get("status") == "winning"
    ]
    losers = [
        f"{k} (n={v.get('n', 0)}, score={v.get('mean_score')})"
        for k, v in fams.items()
        if v.get("status") in {"losing", "retired"}
    ]
    testing = [
        f"{k} (n={v.get('n', 0)}, score={v.get('mean_score')})"
        for k, v in fams.items()
        if v.get("status") in {"testing", "new"}
    ]
    hyp = [k for k, v in fams.items() if v.get("status") == "hypothesis"]

    def bullets(items: list[str], empty: str = "None") -> str:
        return "\n".join(f"- {i}" for i in items) if items else f"_{empty}_"

    return (
        f"# Strategy Memory (AI-editable within the constitution)\n\n"
        f"_Version {state.get('version', 0)} — updated {state.get('last_updated', '')} — confidence {state.get('confidence', 0):.2f}_\n\n"
        f"## Current audience model\n{state.get('audience_model', '')}\n\n"
        f"## Exploration ratio\n{state.get('explore_ratio', 0.7):.0%} of production slots explore.\n\n"
        f"## Winning content families\n{bullets(winners)}\n\n"
        f"## Losing / retired content families\n{bullets(losers)}\n\n"
        f"## Families under test\n{bullets(testing)}\n\n"
        f"## Untested hypotheses\n{bullets(hyp)}\n\n"
        f"## Strong hooks\n{bullets(state.get('strong_hooks', []), 'No evidence yet')}\n\n"
        f"## Weak hooks\n{bullets(state.get('weak_hooks', []), 'No evidence yet')}\n\n"
        f"## Runtime observations\n{state.get('runtime_observations', '')}\n\n"
        f"## Source quality observations\n{state.get('source_observations', '')}\n\n"
        f"## Production bottlenecks\n{state.get('production_bottlenecks', '')}\n\n"
        f"## Recurring audience requests\n{bullets(state.get('audience_requests', []))}\n\n"
        f"## Active experiments\n{bullets(state.get('active_experiments', []))}\n\n"
        f"## Retired experiments\n{bullets(state.get('retired_experiments', []))}\n\n"
        f"## Notes\n{state.get('notes', '')}\n"
    )
