"""Per-video settings: every video tests every open setting at once (plan stage 1, 2026-09-30).

Engineering defines the dials and their bounds (``CATALOG``); the AI decides which to open, which values
to test, and when to lock one. For each video, every *open* production setting gets a value chosen
independently of the idea, so its effect is not confounded with the topic:

* an exploration floor: a value with fewer than ``FLOOR_SCORED`` scored videos is picked at random with
  enough probability that it gets at least 1/(2k) of videos;
* otherwise Thompson sampling on the per-video score.

``locked`` settings always use their locked value, ``off`` settings the renderer default. Every value
used is written to ``video_settings`` together with the AI's content choices (niche, angle, hook type),
which the analysis uses as controls.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from typing import Any

from aimz.db import Database
from aimz.providers.audio.library import music_moods, sfx
from aimz.providers.tts.piper import COMMERCIAL_SAFE_VOICES
from aimz.providers.video.ffmpeg_renderer import CAPTION_COLORS, CAPTION_MARGINS
from aimz.providers.video.fonts import shipped_fonts
from aimz.util import now_iso

FLOOR_SCORED = 8  # a value is explored on purpose until it has this many scored videos
MIN_VALUES, MAX_VALUES = 2, 4  # an open setting tests 2-4 values at once
REGRESSION_MIN_VIDEOS = 30
STATUSES = ("off", "open", "locked")


@dataclass(frozen=True)
class SettingDef:
    variable: str
    kind: str  # production | content | distribution
    description: str
    value_type: str  # number | choice
    bounds: dict[str, Any] = field(default_factory=dict)  # number: min/max; choice: choices
    integer: bool = False  # a number used as a count (rounded to a whole number)
    requires: str = ""  # a capability the dial needs (e.g. 'stock_video'); hidden without it
    # No renderer default exists (e.g. voice: owner decision 2026-10-01). While the AI has not opened or
    # locked it, every allowed value is in play; it can never be turned off.
    always_on: bool = False

    def check(self, value: Any) -> tuple[Any, str | None]:
        """The value normalised, or a reason it is out of bounds."""
        if self.value_type == "number":
            try:
                v = int(round(float(value))) if self.integer else round(float(value), 3)
            except (TypeError, ValueError):
                return None, f"{self.variable}: {value!r} is not a number"
            lo, hi = float(self.bounds["min"]), float(self.bounds["max"])
            if not lo <= v <= hi:
                return None, f"{self.variable}: {v} is outside the engineering bounds {lo}-{hi}"
            return v, None
        choices = [str(c) for c in self.bounds.get("choices", [])]
        if str(value) not in choices:
            return None, f"{self.variable}: {value!r} is not one of {choices}"
        return str(value), None


# Bounds are engineering limits only (legibility, platform limits, what the renderer can do). The values
# inside them are the AI's to choose. Stage 2 of the plan adds the remaining dials here.
CATALOG: dict[str, SettingDef] = {
    d.variable: d
    for d in (
        SettingDef(
            "speech_length_scale",
            "production",
            "Narration speed as Piper's length_scale: 1.0 is the voice's normal pace, above 1 is slower, below 1 faster.",
            "number",
            {"min": 0.75, "max": 1.35},
        ),
        SettingDef(
            "sentence_pause_s",
            "production",
            "Silence between sentences inside a beat, in seconds.",
            "number",
            {"min": 0.0, "max": 1.0},
        ),
        SettingDef(
            "beat_pause_s",
            "production",
            "Silence at the end of each beat before the next visual, in seconds.",
            "number",
            {"min": 0.0, "max": 1.2},
        ),
        SettingDef(
            "voice",
            "production",
            "Narration voice. Only licence-cleared voices (public-domain recordings, commercial use allowed). "
            "There is no default: until you open or lock it, every cleared voice is tried.",
            "choice",
            {"choices": list(COMMERCIAL_SAFE_VOICES)},
            always_on=True,
        ),
        SettingDef(
            "caption_font",
            "production",
            "Font of the burned narration captions ('default' is the renderer's own; others are free-licensed and shipped).",
            "choice",
            {"choices": ["default", *shipped_fonts()]},
        ),
        SettingDef(
            "caption_size",
            "production",
            "Caption text height as a fraction of the frame height (0.036 is about 69 px on 1920).",
            "number",
            {"min": 0.028, "max": 0.06},
        ),
        SettingDef(
            "caption_position",
            "production",
            "Caption height above the bottom edge: bottom (10%), low (17%), mid_low (24%). All stay below the card text.",
            "choice",
            {"choices": list(CAPTION_MARGINS)},
        ),
        SettingDef(
            "caption_color",
            "production",
            "Caption text colour (black outline).",
            "choice",
            {"choices": list(CAPTION_COLORS)},
        ),
        SettingDef(
            "caption_outline",
            "production",
            "Caption outline width in pixels.",
            "number",
            {"min": 2, "max": 8},
            integer=True,
        ),
        SettingDef(
            "caption_max_words",
            "production",
            "Most words shown in one caption at a time; longer spoken chunks are split and timed by length.",
            "number",
            {"min": 2, "max": 14},
            integer=True,
        ),
        SettingDef(
            "motion_style",
            "production",
            "Camera move on each visual when the script does not set one for the beat: alternate (in, out, in...), in, out or none.",
            "choice",
            {"choices": ["alternate", "in", "out", "none"]},
        ),
        SettingDef(
            "max_scene_s",
            "production",
            "Longest time one visual stays on screen; a longer beat is cut into shots of equal length with a new camera move.",
            "number",
            {"min": 2.5, "max": 15.0},
        ),
        SettingDef(
            "palette_hue",
            "production",
            "Hue of the card background and accent colours (0-1 around the colour wheel). Off: a different hue per video.",
            "number",
            {"min": 0.0, "max": 1.0},
        ),
        SettingDef(
            "headline_on_cards",
            "production",
            "Whether the beat's caption is printed on photo cards (cards without a photo always show it).",
            "choice",
            {"choices": ["on", "off"]},
        ),
        SettingDef(
            "progress_counter",
            "production",
            "A '3/7' beat counter in the top corner of every card.",
            "choice",
            {"choices": ["off", "on"]},
        ),
        SettingDef(
            "template",
            "production",
            "Visual layout: card (photo in the top part over a colour card), full_bleed (photo fills the frame, "
            "text over a dark gradient), ranking (card with a big #N for each beat).",
            "choice",
            {"choices": ["card", "full_bleed", "ranking"]},
        ),
        SettingDef(
            "clip_scenes",
            "production",
            "Use licensed stock video clips instead of still photos where one matches the beat (falls back to a photo).",
            "choice",
            {"choices": ["off", "on"]},
            requires="stock_video",
        ),
        SettingDef(
            "music",
            "production",
            "Background music bed by mood (original, rights-free), ducked under the narration; off = no music.",
            "choice",
            {"choices": ["off", *music_moods()]},
        ),
        SettingDef(
            "music_volume_db",
            "production",
            "Music gain in dB before ducking. Measured under a narrator at about -16 dB RMS: -30 gives about "
            "-43 dB (barely there), -14 about -27 dB (a quiet bed), -6 about -19 dB (clearly present).",
            "number",
            {"min": -30.0, "max": -6.0},
        ),
        SettingDef(
            "sfx",
            "production",
            "A soft whoosh at every cut between visuals.",
            "choice",
            {"choices": ["off", "on"]},
            requires="sfx",
        ),
        SettingDef(
            "ending",
            "production",
            "plain, or loop: the video ends on its opening frame so a replay starts seamlessly.",
            "choice",
            {"choices": ["plain", "loop"]},
        ),
    )
}


def capabilities(svc: Any) -> set[str]:
    """What this machine can do for the dials that need something extra."""
    caps: set[str] = set()
    if any(hasattr(p, "search_videos") for p in getattr(svc, "assets", [])):
        caps.add("stock_video")
    if sfx("whoosh") is not None:
        caps.add("sfx")
    return caps


# The AI's content choices, recorded per video so the analysis can control for them.
CONTENT_VARIABLES = ("niche", "angle", "hook_type")


def enc(value: Any) -> str:
    return json.dumps(value)


def dec(text: str | None) -> Any:
    return json.loads(text) if text is not None else None


@dataclass
class ValueStat:
    value: Any
    n: int
    mean: float | None
    p_best: float | None = None


@dataclass
class Assignment:
    values: dict[str, Any]
    sources: dict[str, str]


class SettingsEngine:
    def __init__(
        self, db: Database, catalog: dict[str, SettingDef] | None = None, caps: set[str] | None = None
    ):
        self.db = db
        # Dials that need a capability this machine lacks (e.g. stock video without API keys) are hidden:
        # the AI cannot spend videos testing a setting that would change nothing.
        caps = caps if caps is not None else {"sfx"}
        self.catalog = {k: d for k, d in (catalog or CATALOG).items() if not d.requires or d.requires in caps}
        self._synced = False
        # Other channels' videos (id -> {variable: JSON value}), added by experiments/shared.py.
        self.external: dict[str, dict[str, str]] = {}

    # -- the space ---------------------------------------------------------------------------
    def sync_catalog(self) -> None:
        """Insert new dials as ``off`` and refresh definitions; the AI's status and values are kept."""
        if self._synced:
            return
        now = now_iso()
        for d in self.catalog.values():
            self.db.execute(
                "INSERT INTO setting_space (variable, kind, description, value_type, bounds_json, status, "
                "updated_by, updated_at) VALUES (?,?,?,?,?,'off','system',?) "
                "ON CONFLICT(variable) DO UPDATE SET kind=excluded.kind, description=excluded.description, "
                "value_type=excluded.value_type, bounds_json=excluded.bounds_json",
                [d.variable, d.kind, d.description, d.value_type, json.dumps(d.bounds), now],
            )
        self._synced = True

    def space(self) -> dict[str, dict[str, Any]]:
        self.sync_catalog()
        out: dict[str, dict[str, Any]] = {}
        for r in self.db.query("SELECT * FROM setting_space ORDER BY variable"):
            if r["variable"] not in self.catalog:
                continue  # a dial removed from the code is ignored, not deleted
            row = dict(r)
            row["values"] = json.loads(row["values_json"] or "[]")
            row["bounds"] = json.loads(row["bounds_json"] or "{}")
            row["locked"] = dec(row["locked_value"])
            out[row["variable"]] = row
        return out

    # -- the AI's control ------------------------------------------------------------------------
    def change(
        self,
        variable: str,
        action: str,
        values: list[Any] | None = None,
        locked_value: Any = None,
        by: str = "ai",
    ) -> str | None:
        """Open, lock or turn off a dial. Returns why it was refused, or None when applied."""
        var = variable.strip().lower()
        d = self.catalog.get(var)
        if d is None:
            return f"{variable!r} is not a setting (available: {', '.join(sorted(self.catalog))})"
        action = action.strip().lower()
        self.sync_catalog()
        update: dict[str, Any] = {"updated_by": by, "updated_at": now_iso()}
        if action == "open":
            checked: list[Any] = []
            for v in values or []:
                norm, why = d.check(v)
                if why:
                    return why
                if norm not in checked:
                    checked.append(norm)
            if not MIN_VALUES <= len(checked) <= MAX_VALUES:
                return f"{var}: open needs {MIN_VALUES}-{MAX_VALUES} distinct values, got {len(checked)}"
            update.update(status="open", values_json=json.dumps(checked), locked_value=None)
        elif action == "lock":
            norm, why = d.check(locked_value)
            if why:
                return why
            update.update(status="locked", locked_value=enc(norm))
        elif action == "off":
            if d.always_on:
                return f"{var}: has no default and cannot be turned off; open or lock it instead"
            update.update(status="off", locked_value=None)
        else:
            return f"{var}: action must be open, lock or off, not {action!r}"
        sets = ", ".join(f"{k}=?" for k in update)
        self.db.execute(f"UPDATE setting_space SET {sets} WHERE variable=?", [*update.values(), var])
        return None

    # -- assignment ------------------------------------------------------------------------------
    def assign(
        self,
        video_id: str,
        idea: dict[str, Any],
        defaults: dict[str, Any],
        scored: list[dict[str, Any]],
        rng: random.Random | None = None,
        fixed: dict[str, Any] | None = None,
        exclude: dict[str, set[Any]] | None = None,
    ) -> Assignment:
        """Choose and record every setting for one video. ``scored``: per-video rows with ``video_id``/``score``.

        ``defaults`` are the renderer's values for dials that are off (recorded, so every video has a full row set).
        """
        rng = rng or random.Random()
        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        fixed = fixed or {}
        exclude = exclude or {}
        for var, row in self.space().items():
            d = self.catalog[var]
            # The channel's identity (aimz/identity.py) fixes its voice and look for every video.
            if var in fixed and d.check(fixed[var])[1] is None:
                values[var], sources[var] = d.check(fixed[var])[0], "identity"
                continue
            # Values saved earlier are re-checked: bounds can narrow (e.g. a voice found not cleared).
            banned = exclude.get(var, set())  # e.g. voices other channels hold
            open_values = [v for v in row["values"] if d.check(v)[1] is None and v not in banned]
            locked = (
                row["locked"] if row["locked"] is not None and d.check(row["locked"])[1] is None else None
            )
            if row["status"] == "open" and len(open_values) >= MIN_VALUES:
                values[var], sources[var] = self._choose(var, open_values, scored, rng)
            elif row["status"] == "locked" and locked is not None:
                values[var], sources[var] = locked, "locked"
            elif d.always_on:
                options = [v for v in d.bounds.get("choices", []) if v not in banned] or list(
                    d.bounds.get("choices", [])
                )
                values[var], sources[var] = self._choose(var, options, scored, rng)
            else:
                values[var], sources[var] = defaults.get(var), "default"
        content = {
            "niche": idea.get("content_family"),
            "angle": idea.get("angle"),
            "hook_type": idea.get("hook_type"),
        }
        for var, val in content.items():
            if val:
                values[var], sources[var] = val, "ai"
        now = now_iso()
        for var, val in values.items():
            if val is None and var not in self.catalog:
                continue  # a content choice the idea did not make; a dial's None default is recorded
            self.db.execute(
                "INSERT OR REPLACE INTO video_settings (video_id, variable, value, source, created_at) VALUES (?,?,?,?,?)",
                [video_id, var, enc(val), sources[var], now],
            )
        return Assignment(values, sources)

    def _choose(
        self, var: str, options: list[Any], scored: list[dict[str, Any]], rng: random.Random
    ) -> tuple[Any, str]:
        by_value = self.scores_by_value(var, scored)
        k = len(options)
        under = [v for v in options if len(by_value.get(enc(v), [])) < FLOOR_SCORED]
        # A value under the floor is picked on purpose with probability |under|/(2k), so each gets at least
        # 1/(2k) of videos on top of whatever Thompson sampling gives it.
        if under and rng.random() < len(under) / (2 * k):
            counts = self.assigned_counts(var)
            low = min(counts.get(enc(v), 0) for v in under)
            return rng.choice([v for v in under if counts.get(enc(v), 0) == low]), "random"
        prior_mean, prior_sd, sigma = self._prior(scored)
        draws = {
            enc(v): _posterior_draw(by_value.get(enc(v), []), prior_mean, prior_sd, sigma, rng)
            for v in options
        }
        best = max(options, key=lambda v: draws[enc(v)])
        return best, "bandit"

    def record(self, video_id: str, variable: str, value: Any, source: str) -> None:
        """Record a value that was not assigned here (e.g. the hour a video was posted). First write wins."""
        self.db.execute(
            "INSERT OR IGNORE INTO video_settings (video_id, variable, value, source, created_at) VALUES (?,?,?,?,?)",
            [video_id, variable, enc(value), source, now_iso()],
        )

    # -- data ---------------------------------------------------------------------------------------
    def assigned_counts(self, var: str) -> dict[str, int]:
        """How many rendered (or later) videos used each value of ``var``."""
        return {
            str(r["value"]): int(r["c"])
            for r in self.db.query(
                "SELECT vs.value, COUNT(*) c FROM video_settings vs JOIN videos v ON v.id = vs.video_id "
                "WHERE vs.variable=? AND v.status NOT IN ('failed','superseded') GROUP BY vs.value",
                [var],
            )
        }

    def settings_for(self, video_ids: list[str]) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {v: dict(self.external[v]) for v in video_ids if v in self.external}
        video_ids = [v for v in video_ids if v not in self.external]
        if not video_ids:
            return out
        marks = ",".join("?" for _ in video_ids)
        for r in self.db.query(
            f"SELECT video_id, variable, value FROM video_settings WHERE video_id IN ({marks})", video_ids
        ):
            out.setdefault(str(r["video_id"]), {})[str(r["variable"])] = str(r["value"])
        return out

    def scores_by_value(self, var: str, scored: list[dict[str, Any]]) -> dict[str, list[float]]:
        rows = [r for r in scored if r.get("score") is not None and r.get("video_id")]
        settings = self.settings_for([str(r["video_id"]) for r in rows])
        out: dict[str, list[float]] = {}
        for r in rows:
            val = settings.get(str(r["video_id"]), {}).get(var)
            if val is not None:
                out.setdefault(val, []).append(float(r["score"]))
        return out

    @staticmethod
    def _prior(scored: list[dict[str, Any]]) -> tuple[float, float, float]:
        xs = [float(r["score"]) for r in scored if r.get("score") is not None]
        if len(xs) < 3:
            return 0.25, 0.1, 0.08
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
        return m, max(sd, 0.02), max(sd, 0.02)

    # -- analysis ---------------------------------------------------------------------------------
    def value_stats(self, scored: list[dict[str, Any]], seed: int = 0) -> dict[str, list[ValueStat]]:
        """Per variable and value: n, mean score and the posterior probability of being best."""
        rng = random.Random(seed)
        prior_mean, prior_sd, sigma = self._prior(scored)
        out: dict[str, list[ValueStat]] = {}
        variables = [*self.catalog, "angle", "hook_type", "post_hour"]
        for var in variables:
            # Other channels' videos inform production settings only; content choices are per channel.
            rows = scored if var in self.catalog else [r for r in scored if not r.get("channel")]
            by_value = self.scores_by_value(var, rows)
            if not by_value:
                continue
            stats = [
                ValueStat(dec(v), len(xs), round(sum(xs) / len(xs), 4) if xs else None)
                for v, xs in by_value.items()
            ]
            if len(stats) > 1:
                wins = dict.fromkeys(by_value, 0)
                draws = 2000
                for _ in range(draws):
                    d = {
                        v: _posterior_draw(xs, prior_mean, prior_sd, sigma, rng) for v, xs in by_value.items()
                    }
                    wins[max(d, key=lambda k: d[k])] += 1
                for st in stats:
                    st.p_best = round(wins[enc(st.value)] / draws, 3)
            out[var] = sorted(stats, key=lambda s: -s.n)
        return out

    def regression(self, scored: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Main effects of every production setting on score, controlling for niche and runtime.

        Categorical coding against each variable's most common value; no interactions (plan: not before
        n >= 150). Returns None below REGRESSION_MIN_VIDEOS scored videos.
        """
        import numpy as np

        rows = [r for r in scored if r.get("score") is not None and r.get("video_id")]
        if len(rows) < REGRESSION_MIN_VIDEOS:
            return None
        settings = self.settings_for([str(r["video_id"]) for r in rows])
        columns: list[tuple[str, str, str]] = []  # (variable, value, kind) per dummy
        levels: dict[str, list[str]] = {}

        def level_of(r: dict[str, Any], var: str) -> str | None:
            if var == "niche":
                return str(r.get("content_family") or "unknown")
            if var == "runtime":
                return _runtime_bucket(r.get("duration_s"))
            if var == "channel":
                return str(r.get("channel") or "this")
            return settings.get(str(r["video_id"]), {}).get(var)

        tested = [v for v, d in self.catalog.items() if d.kind == "production"]
        for var in [*tested, "niche", "runtime", "channel"]:
            counts: dict[str, int] = {}
            for r in rows:
                lv = level_of(r, var)
                if lv is not None:
                    counts[lv] = counts.get(lv, 0) + 1
            if var == "niche":  # rare niches are pooled so a control does not eat all the degrees of freedom
                counts = _pool_rare(counts, 3)
            if len(counts) < 2:
                continue
            ref = max(counts, key=lambda k: counts[k])
            levels[var] = [ref, *[k for k in counts if k != ref]]
            kind = "control" if var in {"niche", "runtime", "channel"} else "setting"
            columns.extend((var, lv, kind) for lv in levels[var][1:])
        if not columns:
            return None
        x = np.ones((len(rows), len(columns) + 1))
        y = np.array([float(r["score"]) for r in rows])
        for i, r in enumerate(rows):
            for j, (var, lv, _) in enumerate(columns, start=1):
                val = level_of(r, var)
                if var == "niche" and val not in levels[var]:
                    val = "other"
                x[i, j] = 1.0 if val == lv else 0.0
        n, p = x.shape
        if n <= p + 5:
            return None
        beta, *_ = np.linalg.lstsq(x, y, rcond=None)
        resid = y - x @ beta
        sigma2 = float(resid @ resid) / (n - p)
        cov = sigma2 * np.linalg.pinv(x.T @ x)
        effects = []
        for j, (var, lv, kind) in enumerate(columns, start=1):
            se = math.sqrt(max(float(cov[j, j]), 0.0))
            b = float(beta[j])
            effects.append(
                {
                    "variable": var,
                    "value": dec(lv) if kind == "setting" else lv,
                    "vs": dec(levels[var][0]) if kind == "setting" else levels[var][0],
                    "kind": kind,
                    "effect": round(b, 4),
                    "ci95": [round(b - 1.96 * se, 4), round(b + 1.96 * se, 4)],
                }
            )
        return {"n": n, "parameters": p, "residual_sd": round(math.sqrt(sigma2), 4), "effects": effects}

    def evidence_text(self, scored: list[dict[str, Any]], max_lines: int = 40) -> str:
        """The "Settings evidence" section for strategy memory and the strategist's prompt."""
        space = self.space()
        stats = self.value_stats(scored)
        lines: list[str] = []
        others = sorted(
            {str(r["channel"]) for r in scored if r.get("channel") and r.get("score") is not None}
        )
        if others:
            n_other = sum(1 for r in scored if r.get("channel") and r.get("score") is not None)
            lines.append(
                f"Production settings pool {n_other} scored videos from your sibling channels ({', '.join(others)}); "
                "the regression controls for the channel."
            )
        for var, row in space.items():
            d = self.catalog[var]
            state = (
                f"open {row['values']}"
                if row["status"] == "open"
                else f"locked at {row['locked']}"
                if row["status"] == "locked"
                else "off (renderer default)"
            )
            lines.append(f"- {var} [{state}; bounds {_bounds_text(d)}]: {d.description}")
            for st in stats.get(var, []):
                pb = f" P(best)={st.p_best:.2f}" if st.p_best is not None else ""
                lines.append(f"    {st.value}: n={st.n} mean={st.mean}{pb}")
        for var in ("angle", "hook_type", "post_hour"):  # niches have their own section (families)
            parts = [f"{st.value} n={st.n} mean={st.mean}" for st in stats.get(var, [])[:6]]
            if parts:
                lines.append(f"- {var} (your content choice, recorded as a control): " + "; ".join(parts))
        reg = self.regression(scored)
        if reg:
            lines.append(
                f"Regression on {reg['n']} scored videos (main effects, niche and runtime as controls, residual sd {reg['residual_sd']}):"
            )
            for e in reg["effects"]:
                if e["kind"] == "setting":
                    lines.append(
                        f"    {e['variable']}={e['value']} vs {e['vs']}: {e['effect']:+.3f} (95% {e['ci95'][0]:+.3f} to {e['ci95'][1]:+.3f})"
                    )
        else:
            n = sum(1 for r in scored if r.get("score") is not None)
            lines.append(f"Regression: not yet ({n} scored videos; runs from {REGRESSION_MIN_VIDEOS}).")
        return "\n".join(lines[:max_lines])


def _posterior_draw(
    xs: list[float], prior_mean: float, prior_sd: float, sigma: float, rng: random.Random
) -> float:
    """A draw from a normal posterior of a value's mean score (known-variance normal model)."""
    n = len(xs)
    prec = 1 / prior_sd**2 + n / sigma**2
    mean = (prior_mean / prior_sd**2 + sum(xs) / sigma**2) / prec
    return rng.gauss(mean, math.sqrt(1 / prec))


def _runtime_bucket(d: Any) -> str:
    if d is None:
        return "unknown"
    d = float(d)
    return "<35s" if d < 35 else "35-60s" if d < 60 else ">60s"


def _pool_rare(counts: dict[str, int], min_n: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, c in counts.items():
        key = k if c >= min_n else "other"
        out[key] = out.get(key, 0) + c
    return out


def _bounds_text(d: SettingDef) -> str:
    if d.value_type == "number":
        return f"{d.bounds['min']}-{d.bounds['max']}"
    return "/".join(str(c) for c in d.bounds.get("choices", []))
