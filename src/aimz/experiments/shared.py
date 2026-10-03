"""What channel instances share with each other (plan stage 4): niches and production-settings evidence.

Each instance writes only its own file (``instances/_shared/<name>.json``) and reads the others'. Production
settings (voice, pacing, captions, visuals) are assumed to work similarly on every channel, so their
evidence is pooled, with the channel as a control in the regression. Content choices (niche, angle, hook)
stay per channel.
"""

from __future__ import annotations

import json
from typing import Any

from aimz import identity, instances
from aimz.db import Database
from aimz.experiments.settings import SettingsEngine

USED_SOURCES_SHARED = 1000  # most recent source links this channel has made a video from


def production_variables(engine: SettingsEngine) -> list[str]:
    return [v for v, d in engine.catalog.items() if d.kind == "production"]


def payload(state: dict[str, Any], own_rows: list[dict[str, Any]], engine: SettingsEngine) -> dict[str, Any]:
    """This channel's shared summary: its niches, and its scored videos' production settings."""
    niches = [
        {"niche": k, "status": v.get("status"), "n": int(v.get("n") or 0), "mean_score": v.get("mean_score")}
        for k, v in (state.get("families") or {}).items()
    ]
    scored = [
        r for r in own_rows if r.get("score") is not None and r.get("video_id") and not r.get("channel")
    ]
    settings = engine.settings_for([str(r["video_id"]) for r in scored])
    wanted = set(production_variables(engine))
    samples = [
        {
            "video_id": r["video_id"],
            "score": r["score"],
            "content_family": r.get("content_family"),
            "duration_s": r.get("duration_s"),
            "settings": {k: v for k, v in settings.get(str(r["video_id"]), {}).items() if k in wanted},
        }
        for r in scored
    ]
    out: dict[str, Any] = {"niches": niches, "settings_samples": samples}
    out["identity"] = {k: v for k, v in identity.load(engine.db).items() if k != "history"}
    out["used_sources"] = used_sources(engine.db)
    return out


def used_sources(db: Database) -> list[str]:
    """url_hash of every source this channel has written a script from, newest first."""
    rows = db.query(
        "SELECT i.source_item_ids_json, i.updated_at FROM ideas i WHERE i.status IN "
        "('scripted','produced','published','measured') OR EXISTS (SELECT 1 FROM scripts s WHERE s.idea_id=i.id) "
        "ORDER BY i.updated_at DESC"
    )
    ids: list[str] = []
    for r in rows:
        try:
            ids += [str(x) for x in json.loads(r["source_item_ids_json"] or "[]")]
        except ValueError:
            continue
    out: list[str] = []
    for sid in ids:
        row = db.one("SELECT url_hash FROM source_items WHERE id=?", [sid])
        if row and row["url_hash"] and row["url_hash"] not in out:
            out.append(row["url_hash"])
        if len(out) >= USED_SOURCES_SHARED:
            break
    return out


def sources_used_elsewhere(name: str) -> set[str]:
    """Source links another channel has already made a video from (never two channels on one story)."""
    taken: set[str] = set()
    for info in instances.read_others(name).values():
        taken |= {str(h) for h in info.get("used_sources", [])}
    return taken


def publish(name: str, state: dict[str, Any], own_rows: list[dict[str, Any]], engine: SettingsEngine) -> None:
    instances.write_shared(name, payload(state, own_rows, engine))


def pooled_rows(name: str, own_rows: list[dict[str, Any]], engine: SettingsEngine) -> list[dict[str, Any]]:
    """Own per-video rows plus other channels' scored samples, which the engine learns to look up."""
    rows = list(own_rows)
    for other, info in instances.read_others(name).items():
        for sample in info.get("settings_samples", []):
            vid = f"{other}:{sample.get('video_id')}"
            engine.external[vid] = {k: str(v) for k, v in (sample.get("settings") or {}).items()}
            rows.append(
                {
                    "video_id": vid,
                    "score": sample.get("score"),
                    "content_family": sample.get("content_family"),
                    "duration_s": sample.get("duration_s"),
                    "channel": other,
                }
            )
    return rows


def other_channels_text(name: str) -> str:
    """Other channels' niches, for the strategist and ideation prompts."""
    lines = []
    for other, info in instances.read_others(name).items():
        held = [
            f"{n['niche']} (n={n.get('n', 0)}, {n.get('status')})"
            for n in info.get("niches", [])
            if int(n.get("n") or 0) > 0
        ]
        lines.append(f"- {other}: {', '.join(held) or 'no measured niches yet'}")
    return "\n".join(lines)
