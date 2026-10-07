"""Per-channel guideline: locked render settings plus craft rules, parsed from ``guideline.md``.

The owner edits the file; every valid text is stored as a numbered version. A broken edit never
replaces the last valid version: ``load`` keeps serving it and records the problem in the
``guideline_error`` system state (shown by ``aimz status``).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from aimz.db import Database
from aimz.experiments.settings import CATALOG, SettingDef
from aimz.util import new_id, now_iso

STAGES = ("topic", "script", "title", "cover")
# Platform limits, same as config/config.yaml content.short_form.
RUNTIME_LIMIT_MIN, RUNTIME_LIMIT_MAX = 10, 180
ERROR_KEY = "guideline_error"

_YAML_RE = re.compile(r"```yaml[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)
_RULE_RE = re.compile(r"^###[ \t]+(G-[A-Za-z0-9_-]+):[ \t]*(.*?)[ \t]*$", re.MULTILINE)
_STAGE_RE = re.compile(r"^stage:[ \t]*(\S*)[ \t]*$")
_ANY_HEADING_RE = re.compile(r"^#{1,6}[ \t]", re.MULTILINE)


class GuidelineError(ValueError):
    """The guideline text is not valid; the message names the problem."""


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    stage: str
    text: str


@dataclass(frozen=True)
class Guideline:
    version: int
    channel: str
    genre: str
    locked: dict[str, Any]
    runtime_min: int
    runtime_max: int
    checks: dict[str, Any]
    rules: dict[str, Rule]
    unavailable: dict[str, str] = field(default_factory=dict)
    content: str = ""

    def item_ids(self) -> set[str]:
        return {*self.rules, *(f"G-{k}" for k in self.locked), "G-runtime"}

    def rules_for(self, stage: str, replace: dict[str, str] | None = None) -> list[Rule]:
        """Rules of a stage; ``replace`` maps a rule id to variant text used instead of the rule's own."""
        variants = replace or {}
        out = []
        for r in self.rules.values():
            if r.stage != stage:
                continue
            out.append(Rule(r.id, r.title, r.stage, variants[r.id]) if r.id in variants else r)
        return out

    def prompt_block(self, stage: str, replace: dict[str, str] | None = None) -> str:
        rules = self.rules_for(stage, replace)
        if not rules:
            return ""
        lines = [f"Channel guideline ({self.channel}, v{self.version}) - rules for the {stage} stage:"]
        lines += [f"- {r.id} {r.title}: {r.text}" for r in rules]
        return "\n".join(lines)


def path(config_dir: Path) -> Path:
    return Path(config_dir) / "guideline.md"


NOT_APPROVED_MARKER = "NOT APPROVED"


def is_approved(config_dir: Path) -> bool:
    """The channel's guideline file exists and the owner has removed its ``NOT APPROVED`` marker."""
    p = path(config_dir)
    if not p.exists():
        return False
    return NOT_APPROVED_MARKER not in p.read_text(encoding="utf-8")


def parse(text: str, catalog: dict[str, SettingDef], caps: set[str]) -> Guideline:
    m = _YAML_RE.search(text)
    if not m:
        raise GuidelineError("no YAML block: the file must open with a fenced ```yaml block")
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        raise GuidelineError(f"YAML block does not parse: {e}") from e
    if not isinstance(data, dict):
        raise GuidelineError("YAML block must be a mapping")

    head = data.get("guideline") or {}
    if not isinstance(head, dict):
        raise GuidelineError("'guideline' must be a mapping with channel and genre")

    locked_raw = data.get("locked") or {}
    if not isinstance(locked_raw, dict):
        raise GuidelineError("'locked' must be a mapping of setting to value")
    locked: dict[str, Any] = {}
    unavailable: dict[str, str] = {}
    for key, value in locked_raw.items():
        key = str(key)
        sd = catalog.get(key)
        if sd is None:
            raise GuidelineError(f"locked: unknown setting {key!r}")
        if isinstance(value, bool) and sd.value_type == "choice":
            value = "on" if value else "off"  # YAML reads bare on/off as booleans
        norm, err = sd.check(value)
        if err:
            raise GuidelineError(f"locked: {err}")
        if sd.requires and sd.requires not in caps:
            unavailable[key] = f"needs capability '{sd.requires}', which is not available on this machine"
            continue
        locked[key] = norm

    rt = data.get("runtime_s")
    if not isinstance(rt, dict) or "min" not in rt or "max" not in rt:
        raise GuidelineError("runtime_s must give min and max seconds")
    try:
        rmin, rmax = int(rt["min"]), int(rt["max"])
    except (TypeError, ValueError) as e:
        raise GuidelineError("runtime_s min and max must be whole numbers") from e
    if rmin > rmax:
        raise GuidelineError(f"runtime_s: min {rmin} is greater than max {rmax}")
    if rmin < RUNTIME_LIMIT_MIN or rmax > RUNTIME_LIMIT_MAX:
        raise GuidelineError(
            f"runtime_s {rmin}-{rmax} is outside the short_form limits {RUNTIME_LIMIT_MIN}-{RUNTIME_LIMIT_MAX} s"
        )

    checks = data.get("checks") or {}
    if not isinstance(checks, dict):
        raise GuidelineError("'checks' must be a mapping")

    return Guideline(
        version=0,
        channel=str(head.get("channel", "")),
        genre=str(head.get("genre", "")),
        locked=locked,
        runtime_min=rmin,
        runtime_max=rmax,
        checks=dict(checks),
        rules=_parse_rules(text[m.end() :]),
        unavailable=unavailable,
        content=text,
    )


def _parse_rules(body: str) -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    heads = list(_RULE_RE.finditer(body))
    for i, h in enumerate(heads):
        rid, title = h.group(1), h.group(2)
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        chunk = _ANY_HEADING_RE.split(body[h.end() : end], maxsplit=1)[0]  # other headings end the rule
        lines = chunk.strip().splitlines()
        sm = _STAGE_RE.match(lines[0].strip()) if lines else None
        if not sm:
            raise GuidelineError(f"{rid}: the line after the heading must be 'stage: <stage>'")
        stage = sm.group(1)
        if stage not in STAGES:
            raise GuidelineError(f"{rid}: bad stage {stage!r}, expected one of {', '.join(STAGES)}")
        text = "\n".join(lines[1:]).strip()
        if not text:
            raise GuidelineError(f"{rid}: empty rule text")
        if rid in rules:
            raise GuidelineError(f"duplicate rule id {rid}")
        rules[rid] = Rule(rid, title, stage, text)
    return rules


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _latest(db: Database):  # noqa: ANN202
    return db.one("SELECT * FROM guideline_versions ORDER BY version DESC LIMIT 1")


def save_version(db: Database, content: str, source: str, reason: str) -> int:
    row = _latest(db)
    version = (int(row["version"]) if row else 0) + 1
    db.insert(
        "guideline_versions",
        {
            "id": new_id("gv"),
            "version": version,
            "source": source,
            "reason": reason,
            "content": content,
            "content_hash": _hash(content),
            "created_at": now_iso(),
        },
    )
    return version


def _stored(row, catalog: dict[str, SettingDef], caps: set[str]) -> Guideline:  # noqa: ANN001
    return replace(parse(row["content"], catalog, caps), version=int(row["version"]))


def load(db: Database, config_dir: Path, catalog: dict[str, SettingDef], caps: set[str]) -> Guideline | None:
    """The active guideline, recording a new version when the owner's file changed.

    A file that fails validation never replaces the last valid version; the problem is recorded in
    the ``guideline_error`` state. Without a file, the last stored version (if any) is used.
    """
    p = path(config_dir)
    latest = _latest(db)
    if not p.exists():
        return _stored(latest, catalog, caps) if latest else None

    text = p.read_text(encoding="utf-8")
    try:
        g = parse(text, catalog, caps)
    except GuidelineError as e:
        db.set_state(ERROR_KEY, str(e))
        return _stored(latest, catalog, caps) if latest else None

    db.set_state(ERROR_KEY, "")
    if latest is not None and latest["content_hash"] == _hash(text):
        return replace(g, version=int(latest["version"]))
    if latest is None:
        version = save_version(db, text, "initial", "first activation")
    else:
        version = save_version(db, text, "owner_edit", "file edited by owner")
    return replace(g, version=version)


def write(db: Database, config_dir: Path, content: str, source: str, reason: str) -> int:
    """Validate ``content``, write the file and store it as a new version.

    Capability availability is not a write-time error (it lands in ``unavailable``), so validation
    here needs no capability set.
    """
    parse(content, CATALOG, set())
    p = path(config_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return save_version(db, content, source, reason)


def for_services(svc: Any) -> Guideline | None:
    """The active guideline for a services bundle: the one place that knows how to call ``load``."""
    from aimz.experiments.settings import capabilities  # local import: keeps module import order simple

    return load(svc.db, svc.env.config_dir, CATALOG, capabilities(svc))


def variant_for(idea: dict[str, Any], db: Database) -> dict[str, str]:
    """``{rule_id: variant_text}`` when the idea is on the treatment arm of a running craft-rule experiment.

    A craft-rule experiment's variable is a guideline rule id ("G-..."), other than G-runtime and the
    G-<setting> ids of locked settings. Its treatment column holds the variant text.
    """
    exp_id = idea.get("experiment_id")
    if not exp_id or idea.get("experiment_arm") != "treatment":
        return {}
    exp = db.get("experiments", exp_id)
    if not exp or exp["status"] != "running":
        return {}
    var = str(exp["variable"])
    if not var.startswith("G-") or var == "G-runtime" or var[2:] in CATALOG:
        return {}
    return {var: str(exp["treatment"])}


def runtime_variant(idea: dict[str, Any], db: Database) -> int | None:
    """The tested runtime in seconds when the idea is on the treatment arm of a running G-runtime experiment.

    That runtime replaces the guideline's runtime range for this idea only (controller ruling R6): the
    script agent does not clamp to the range and the critic does not gate on it. Control-arm ideas
    follow the guideline range.
    """
    exp_id = idea.get("experiment_id")
    if not exp_id or idea.get("experiment_arm") != "treatment":
        return None
    exp = db.get("experiments", exp_id)
    if not exp or exp["status"] != "running" or exp["variable"] != "G-runtime":
        return None
    try:
        return int(exp["treatment"])
    except (TypeError, ValueError):
        return None


def section(g: Guideline | None, stages: tuple[str, ...], replace: dict[str, str] | None = None) -> str:
    """The prompt text for the given stages, headed for the model; empty without a guideline or rules."""
    if g is None:
        return ""
    blocks = [b for b in (g.prompt_block(s, replace) for s in stages) if b]
    if not blocks:
        return ""
    return "Channel guideline (owner-approved; follow it):\n" + "\n".join(blocks)
