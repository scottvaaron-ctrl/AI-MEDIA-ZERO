"""Guideline change proposals: a won experiment becomes a proposal; the owner approves or rejects it.

Approving rewrites that single item in ``guideline.md`` (the rest of the file stays byte-identical) and
stores the text as a new version with source ``proposal:<id>``. Rejecting leaves the guideline unchanged,
keeps the owner's note for the strategist and blocks the item for the cooldown (read by
``ExperimentEngine.cooldown_left``). An owner's direct edit closes running experiments whose item changed
(``sync_version``, called by the orchestrator at cycle start).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from aimz import guideline
from aimz.db import Database
from aimz.experiments.settings import CATALOG
from aimz.guideline import RUNTIME_LIMIT_MAX, RUNTIME_LIMIT_MIN, Guideline, GuidelineError
from aimz.util import iso_ago, loads, new_id, now_iso

SEEN_KEY = "guideline_seen_version"
REJECTED_KEY = "last_rejected_guideline_proposal"
OVERDUE_DAYS = 14
# An approved G-runtime variant was made at its tested runtime with a 0.6x-1.4x spoken-word band
# (agents/script.py); the new range is that band.
RUNTIME_BAND = (0.6, 1.4)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
# Evidence keys whose values are identifiers, not numbers the summary may quote.
_ID_KEYS = {"video_ids", "experiment_id", "item"}


# -- evidence and summary ---------------------------------------------------------------------
def _video_ids(db: Database, experiment_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"control": [], "treatment": []}
    for r in db.query(
        "SELECT v.id, i.experiment_arm FROM videos v JOIN ideas i ON i.id = v.idea_id "
        "WHERE i.experiment_id=? AND v.status IN ('published','measured') ORDER BY v.created_at",
        [experiment_id],
    ):
        if r["experiment_arm"] in out:
            out[r["experiment_arm"]].append(r["id"])
    return out


def _evidence(db: Database, experiment: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    vids = _video_ids(db, str(experiment["id"]))
    ev: dict[str, Any] = {
        "experiment_id": experiment["id"],
        "item": experiment["variable"],
        "before": experiment["control"],
        "after": experiment["treatment"],
        "kpi": result.get("kpi", experiment.get("primary_kpi")),
        "p_treatment_better": result.get("p_treatment_better"),
    }
    for arm in ("control", "treatment"):
        a = result.get(arm) or {}
        ev[arm] = {
            "n": a.get("n"),
            "mean": a.get("mean"),
            "mean_avg_percent_viewed": a.get("mean_avg_percent_viewed"),
            "video_ids": vids[arm],
        }
    return ev


def _fmt(x: Any) -> str:
    return "n/a" if x is None else f"{round(float(x), 2):g}"


def _pct(x: Any) -> str:
    return "n/a" if x is None else f"{_fmt(x)}%"


def template_summary(evidence: dict[str, Any]) -> str:
    c, t = evidence.get("control") or {}, evidence.get("treatment") or {}
    p = evidence.get("p_treatment_better")
    p_txt = "n/a" if p is None else f"{round(float(p) * 100):d}"
    return (
        f"{evidence.get('item')}: variant '{evidence.get('after')}' scored {_fmt(t.get('mean'))} "
        f"vs guideline {_fmt(c.get('mean'))} (n={t.get('n')}/{c.get('n')}), {p_txt}% likely better; "
        f"average viewed {_pct(t.get('mean_avg_percent_viewed'))} vs {_pct(c.get('mean_avg_percent_viewed'))}."
    )


def _evidence_numbers(obj: Any, key: str = "") -> list[float]:
    if key in _ID_KEYS:
        return []
    if isinstance(obj, bool) or obj is None:
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, str):
        return [float(m.rstrip("%")) for m in _NUM_RE.findall(obj)]
    if isinstance(obj, dict):
        return [n for k, v in obj.items() for n in _evidence_numbers(v, str(k))]
    if isinstance(obj, (list, tuple)):
        return [n for v in obj for n in _evidence_numbers(v, key)]
    return []


def check_summary(summary: str, evidence: dict[str, Any]) -> bool:
    """True when every number in ``summary`` is a number in ``evidence`` at the precision shown.

    A percent may also be a fraction in the evidence (0.97 shown as 97%).
    """
    known = _evidence_numbers(evidence)
    for token in _NUM_RE.findall(summary):
        pct = token.endswith("%")
        num = token.rstrip("%")
        places = len(num.split(".")[1]) if "." in num else 0
        value = float(num)
        candidates = known + ([k * 100 for k in known] if pct else [])
        if not any(abs(round(c, places) - value) < 1e-9 for c in candidates):
            return False
    return True


# -- proposals ------------------------------------------------------------------------------
def create_from_experiment(
    db: Database, experiment: dict[str, Any], result: dict[str, Any], ai_summary: str
) -> str:
    """Store a pending proposal for a won guideline experiment; an item has at most one pending."""
    item = str(experiment["variable"])
    existing = db.one(
        "SELECT id FROM guideline_proposals WHERE status='pending' AND (item=? OR experiment_id=?) "
        "ORDER BY created_at LIMIT 1",
        [item, experiment["id"]],
    )
    if existing:
        return str(existing["id"])
    evidence = _evidence(db, experiment, result)
    summary = (ai_summary or "").strip()
    if not summary or not check_summary(summary, evidence):
        summary = template_summary(evidence)
    pid = new_id("gp")
    db.insert(
        "guideline_proposals",
        {
            "id": pid,
            "experiment_id": experiment["id"],
            "item": item,
            "before": experiment["control"],
            "after": experiment["treatment"],
            "evidence_json": json.dumps(evidence),
            "summary": summary,
            "status": "pending",
            "created_at": now_iso(),
        },
    )
    return pid


def pending(db: Database) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in db.query("SELECT * FROM guideline_proposals WHERE status='pending' ORDER BY created_at")
    ]


def _pending_row(db: Database, proposal_id: str) -> dict[str, Any]:
    row = db.get("guideline_proposals", proposal_id)
    if row is None:
        raise ValueError(f"no guideline proposal {proposal_id!r}")
    if row["status"] != "pending":
        raise ValueError(f"proposal {proposal_id} is already {row['status']}")
    return dict(row)


def approve(db: Database, config_dir: Path, proposal_id: str) -> int:
    """Rewrite the proposal's item in ``guideline.md`` and store it as a new version; returns that version."""
    row = _pending_row(db, proposal_id)
    # A pending owner edit in the file is recorded as its own version first, so it is not filed under
    # this proposal.
    # The capability set is a stand-in, so the unavailable-items warning is left as the cycle wrote it.
    guideline.load(db, config_dir, CATALOG, set(), record_unavailable=False)
    p = guideline.path(config_dir)
    if not p.exists():
        raise ValueError(f"no guideline file at {p}")
    text = p.read_text(encoding="utf-8")
    item = str(row["item"])
    try:
        now = current_value(guideline.parse(text, CATALOG, _all_caps()), item)
    except GuidelineError as e:
        raise ValueError(f"guideline.md is not valid, fix it first: {e}") from e
    if row["before"] is not None and now != str(row["before"]):
        # The owner changed this item after the experiment started; approving would overwrite that edit.
        supersede(db, [proposal_id])
        raise ValueError(
            f"the owner changed {item} since this proposal was made; it is superseded and not applied"
        )
    new_text = rewrite_item(text, item, str(row["after"]))
    version = guideline.write(
        db,
        config_dir,
        new_text,
        source=f"proposal:{proposal_id}",
        reason=f"owner approved {row['item']}",
    )
    db.update("guideline_proposals", proposal_id, {"status": "approved", "decided_at": now_iso()})
    return version


SUPERSEDED_NOTE = "baseline changed by owner"


def supersede(db: Database, proposal_ids: list[str]) -> None:
    """Close pending proposals whose item the owner edited directly; they can no longer be approved."""
    for pid in proposal_ids:
        db.update(
            "guideline_proposals",
            pid,
            {"status": "superseded", "owner_note": SUPERSEDED_NOTE, "decided_at": now_iso()},
        )


def supersede_for_items(db: Database, items: set[str]) -> list[str]:
    """Supersede every pending proposal on one of ``items``; returns their ids."""
    ids = [str(r["id"]) for r in pending(db) if r["item"] in items]
    supersede(db, ids)
    return ids


def current_value(g: Guideline, item: str) -> str | None:
    """``item``'s value in ``g`` written as a proposal's ``before`` (an experiment's control)."""
    if item == "G-runtime":
        return f"{g.runtime_min}-{g.runtime_max}"
    if item == "G-genre":
        return g.genre
    if item.startswith("G-") and item[2:] in guideline.CHECK_KEYS:
        v = g.checks.get(item[2:])
        return None if v is None else guideline.encode_check(v)
    if item.startswith("G-") and item[2:] in CATALOG:
        v = g.locked.get(item[2:])
        return None if v is None else str(v)
    rule = g.rules.get(item)
    return None if rule is None else rule.text


def reject(db: Database, proposal_id: str, note: str) -> None:
    """Keep the guideline; record the owner's note. ``cooldown_left`` reads ``decided_at``."""
    row = _pending_row(db, proposal_id)
    db.update(
        "guideline_proposals",
        proposal_id,
        {"status": "rejected", "owner_note": note, "decided_at": now_iso()},
    )
    db.set_state(
        REJECTED_KEY,
        f"{row['item']}: variant '{row['after']}' was rejected by the owner. Owner's note: {note or '(none)'}",
    )


# -- surgical rewrite --------------------------------------------------------------------------
def _yaml_scalar(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_.+-]+", value) else json.dumps(value)


def _yaml_span(text: str) -> tuple[int, int]:
    m = guideline._YAML_RE.search(text)  # noqa: SLF001 - the same fence the parser reads
    if not m:
        raise GuidelineError("no YAML block")
    return m.start(1), m.end(1)


def _block(yaml_text: str, key: str) -> tuple[int, int, str]:
    """(start, end, header line) of a top-level YAML key's block within ``yaml_text``."""
    m = re.search(rf"^{re.escape(key)}:[^\n]*$", yaml_text, re.MULTILINE)
    if not m:
        raise GuidelineError(f"no '{key}:' in the YAML block")
    nxt = re.search(r"^\S", yaml_text[m.end() + 1 :], re.MULTILINE)
    end = m.end() + 1 + nxt.start() if nxt else len(yaml_text)
    return m.start(), min(end, len(yaml_text)), m.group(0)


def _set_in_block(block: str, key: str, value: str) -> str:
    pat = re.compile(rf"^([ \t]+{re.escape(key)}:[ \t]*)([^#\n]*?)([ \t]*(?:#[^\n]*)?)$", re.MULTILINE)
    new, n = pat.subn(lambda m: m.group(1) + value + m.group(3), block, count=1)
    if n:
        return new
    flow = re.compile(rf"(\b{re.escape(key)}:[ \t]*)([^,}}\n]+?)([ \t]*[,}}])")
    new, n = flow.subn(lambda m: m.group(1) + value + m.group(3), block, count=1)
    if not n:
        raise GuidelineError(f"'{key}' not found to rewrite")
    return new


def _rewrite_yaml(text: str, top: str, values: dict[str, str]) -> str:
    ys, ye = _yaml_span(text)
    y = text[ys:ye]
    bs, be, _ = _block(y, top)
    block = y[bs:be]
    for k, v in values.items():
        block = _set_in_block(block, k, v)
    return text[:ys] + y[:bs] + block + y[be:] + text[ye:]


def _runtime_range(after: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", after)
    if m:
        return int(m.group(1)), int(m.group(2))
    seconds = int(round(float(after.lower().rstrip("s").strip())))
    lo = max(RUNTIME_LIMIT_MIN, int(round(seconds * RUNTIME_BAND[0])))
    hi = min(RUNTIME_LIMIT_MAX, int(round(seconds * RUNTIME_BAND[1])))
    return lo, hi


def _rewrite_rule(text: str, rule_id: str, new_body: str) -> str:
    ys, ye = _yaml_span(text)
    body_from = ye
    heads = list(guideline._RULE_RE.finditer(text, body_from))  # noqa: SLF001
    h = next((x for x in heads if x.group(1) == rule_id), None)
    if h is None:
        raise GuidelineError(f"rule {rule_id} not found")
    nxt = guideline._ANY_HEADING_RE.search(text, h.end())  # noqa: SLF001 - any heading ends the rule
    end = nxt.start() if nxt else len(text)
    chunk = text[h.end() : end]
    sm = re.search(r"^[ \t]*stage:[^\n]*(\n|$)", chunk, re.MULTILINE)
    if not sm:
        raise GuidelineError(f"{rule_id}: no stage line")
    body = chunk[sm.end() :]
    lead = len(body) - len(body.lstrip())
    trail = len(body.rstrip())
    if trail <= lead:  # empty body; keep the line structure
        new_chunk = chunk[: sm.end()] + new_body.strip() + "\n" + body
    else:
        new_chunk = chunk[: sm.end()] + body[:lead] + new_body.strip() + body[trail:]
    return text[: h.end()] + new_chunk + text[end:]


def rewrite_item(text: str, item: str, after: str) -> str:
    """``text`` with only ``item`` changed to ``after``; validated before it is returned."""
    check = item[2:] if item.startswith("G-") and item[2:] in guideline.CHECK_KEYS else ""
    if item == "G-runtime":
        lo, hi = _runtime_range(after)
        new = _rewrite_yaml(text, "runtime_s", {"min": str(lo), "max": str(hi)})
    elif item == "G-genre":
        new = _rewrite_yaml(text, "guideline", {"genre": json.dumps(after.strip())})
    elif check:
        value, why = guideline.check_variant(check, after)
        if why:
            raise GuidelineError(why)
        new = _rewrite_yaml(text, "checks", {check: guideline.encode_check(value)})
    elif item.startswith("G-") and item[2:] in CATALOG:
        new = _rewrite_yaml(text, "locked", {item[2:]: _yaml_scalar(after)})
    else:
        new = _rewrite_rule(text, item, after)
    g = guideline.parse(new, CATALOG, _all_caps())
    if item == "G-runtime":
        ok = (g.runtime_min, g.runtime_max) == _runtime_range(after)
    elif item == "G-genre":
        ok = g.genre == after.strip()
    elif check:
        ok = g.checks.get(check) == guideline.check_variant(check, after)[0]
    elif item.startswith("G-") and item[2:] in CATALOG:
        norm, _ = CATALOG[item[2:]].check(after)
        ok = str(g.locked.get(item[2:])) == str(norm)
    else:
        ok = item in g.rules and g.rules[item].text == after.strip()
    if not ok:
        raise GuidelineError(f"rewriting {item} did not give the approved value")
    return new


# -- owner edits ---------------------------------------------------------------------------------
def _all_caps() -> set[str]:
    """Every capability, so a parsed version keeps every locked setting for comparison."""
    return {d.requires for d in CATALOG.values() if d.requires}


def changed_items(old: Guideline, new: Guideline) -> set[str]:
    out = {rid for rid in {*old.rules, *new.rules} if old.rules.get(rid) != new.rules.get(rid)}
    old_set = {**{k: None for k in old.unavailable}, **old.locked}
    new_set = {**{k: None for k in new.unavailable}, **new.locked}
    out |= {
        f"G-{k}" for k in {*old_set, *new_set} if old_set.get(k, "<absent>") != new_set.get(k, "<absent>")
    }
    if (old.runtime_min, old.runtime_max) != (new.runtime_min, new.runtime_max):
        out.add("G-runtime")
    if old.genre != new.genre:
        out.add("G-genre")
    out |= {f"G-{k}" for k in guideline.CHECK_KEYS if old.checks.get(k) != new.checks.get(k)}
    return out


def on_version_change(db: Database, old: Guideline, new: Guideline, engine: Any) -> list[str]:
    """Close running experiments, and supersede pending proposals, whose item the owner changed.

    Returns the closed experiment ids. A superseded proposal can no longer be approved, so it cannot
    overwrite the owner's newer edit.
    """
    changed = changed_items(old, new)
    if not changed:
        return []
    supersede_for_items(db, changed)
    return engine.close_for_baseline_change(changed)


def sync_version(db: Database, engine: Any) -> list[str]:
    """At cycle start: apply owner edits stored since the last seen version; returns closed experiment ids.

    The first activation (no seen version) closes nothing. Each ``owner_edit`` version since the seen
    one is compared with the version before it; ``proposal:`` and ``initial`` versions close nothing.
    """
    g = getattr(engine, "guideline", None)
    if g is None:
        return []
    seen_raw = db.get_state(SEEN_KEY)
    closed: list[str] = []
    if seen_raw and int(seen_raw) < g.version:
        rows = [
            dict(r)
            for r in db.query(
                "SELECT version, source, content FROM guideline_versions WHERE version>=? AND version<=? "
                "ORDER BY version",
                [int(seen_raw), g.version],
            )
        ]
        caps = _all_caps()
        for prev, cur in zip(rows, rows[1:], strict=False):
            if cur["source"] != "owner_edit":
                continue
            try:
                old = guideline.parse(prev["content"], CATALOG, caps)
                new = guideline.parse(cur["content"], CATALOG, caps)
            except GuidelineError:
                continue
            closed += on_version_change(db, old, new, engine)
    if seen_raw != str(g.version):
        db.set_state(SEEN_KEY, str(g.version))
    return closed


# -- one-time switch-over -----------------------------------------------------------------------
MIGRATED_KEY = "guideline_migrated"
RESCOPE_REASON = "rescope 2026-10-07"


def migrate_to_guideline(
    db: Database, settings_engine: Any, experiment_engine: Any, g: Guideline | None
) -> bool:
    """The switch to a guideline (spec 2026-10-07 section 5), run once; returns True when it ran.

    Retires every running experiment, turns ``off`` each ``setting_space`` row the guideline locks (its
    values and the per-video ``video_settings`` evidence are kept) and records what it did in the
    ``guideline_migrated`` state.
    """
    if g is None or db.get_state(MIGRATED_KEY):
        return False
    retired = []
    for e in experiment_engine.running():
        experiment_engine.retire(e["id"], RESCOPE_REASON)
        retired.append(e["id"])
    space = settings_engine.space()
    off: list[str] = []
    refused: dict[str, str] = {}
    for var in sorted(g.locked):
        row = space.get(var)
        if row is None or row["status"] == "off":
            continue
        why = settings_engine.change(var, "off", by="guideline")
        if why:  # e.g. a dial with no default; the guideline's locked value still outranks it
            refused[var] = why
        else:
            off.append(var)
    db.set_state(
        MIGRATED_KEY,
        json.dumps(
            {
                "at": now_iso(),
                "guideline_version": g.version,
                "reason": RESCOPE_REASON,
                "retired": retired,
                "settings_off": off,
                "settings_refused": refused,
            }
        ),
    )
    return True


# -- status -----------------------------------------------------------------------------------
def attention_entries(db: Database) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    overdue_before = iso_ago(days=OVERDUE_DAYS)
    waiting = []
    for r in pending(db):
        e: dict[str, Any] = {
            "proposal": r["id"],
            "item": r["item"],
            "change": f"{str(r['before'] or '')[:80]} -> {str(r['after'])[:80]}",
            "summary": r["summary"],
            "created_at": r["created_at"],
            "fix": f"python -m aimz guideline approve {r['id']}",
        }
        if r["created_at"] < overdue_before:
            e["note"] = "overdue"
        waiting.append(e)
    out["guideline_changes_waiting"] = waiting
    err = db.get_state(guideline.ERROR_KEY)
    if err:
        out["guideline_invalid"] = [{"error": err, "fix": "edit config/guideline.md"}]
    unavailable = loads(db.get_state(guideline.UNAVAILABLE_KEY), {}) or {}
    if unavailable:
        out["guideline_items_unavailable"] = [
            {
                "setting": setting,
                "why": f"the guideline locks {setting}, but it {why}; the renderer default is used instead",
                "fix": _unavailable_fix(setting),
            }
            for setting, why in sorted(unavailable.items())
        ]
    return out


# What installs each capability a locked setting can need (experiments/settings.py ``capabilities``).
_CAPABILITY_FIX = {
    "stock_video": "add a Pexels or Pixabay API key to .env (stock video clips)",
    "sfx": "add a 'whoosh' sound to assets/sfx/licenses.json",
}


def _unavailable_fix(setting: str) -> str:
    d = CATALOG.get(setting)
    need = d.requires if d is not None else ""
    install = _CAPABILITY_FIX.get(need, f"install the '{need}' capability")
    return f"{install}, or remove {setting} from locked: in config/guideline.md"


def evidence_of(row: dict[str, Any]) -> dict[str, Any]:
    return loads(row.get("evidence_json"), {})
