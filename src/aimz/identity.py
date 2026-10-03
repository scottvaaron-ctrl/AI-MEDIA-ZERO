"""Channel identity: each channel's genre, voice and look (owner decisions 2026-10-01; plan stage C11).

The owner wants every channel to be clearly distinct from the others in genre, voice and look (YouTube's
spam policy covers "coordinated networks of channels"; its monetization policy judges a channel as a
whole), and wants the AI to be able to experiment with all three. So:

* The strategist chooses this channel's identity and may change it. A genre change waits until the channel
  has published ``GENRE_DWELL_VIDEOS`` videos under the current genre, so a channel stays recognisable
  long enough for its results to mean something.
* No two channels may hold the same genre, the same voice, or the same look (template + colour).
* Until a channel has an identity, the strategist is shown ``channels.starting_genres`` from config.yaml:
  the owner's starting guidance. The AI may take one, adapt it, or name its own.
* The identity's voice and look are used for every video (``ProducerAgent``); its genre steers ideation.
"""

from __future__ import annotations

import json
import re
from typing import Any

from aimz import instances
from aimz.db import Database
from aimz.util import now_iso

IDENTITY_KEY = "channel_identity"
REJECTED_KEY = "last_rejected_identity"
GENRE_DWELL_VIDEOS = 10
LOOK_VARIABLES = ("template", "palette_hue")


def norm_genre(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def load(db: Database) -> dict[str, Any]:
    try:
        return json.loads(db.get_state(IDENTITY_KEY) or "{}")
    except ValueError:
        return {}


def published_count(db: Database) -> int:
    row = db.one("SELECT COUNT(*) c FROM videos WHERE status IN ('published','measured')")
    return int(row["c"]) if row else 0


def others(name: str) -> dict[str, dict[str, Any]]:
    """Other channels' identities, from their shared summaries."""
    return {k: v.get("identity") or {} for k, v in instances.read_others(name).items()}


def taken_voices(name: str) -> set[str]:
    return {str(i["voice"]) for i in others(name).values() if i.get("voice")}


def _look(identity: dict[str, Any]) -> tuple[Any, ...] | None:
    if not any(identity.get(k) is not None for k in LOOK_VARIABLES):
        return None
    hue = identity.get("palette_hue")
    # Hues within about 20 degrees (1/18 of the wheel) count as the same colour.
    return (identity.get("template"), round(float(hue) * 18) % 18 if hue is not None else None)


def propose(
    db: Database, name: str, proposal: dict[str, Any], catalog: dict[str, Any] | None = None
) -> str | None:
    """Apply the strategist's identity change. Returns why it was refused, or None when applied."""
    current = load(db)
    new = {**current}
    for key in ("genre", "genre_note", "voice", "template", "palette_hue"):
        value = proposal.get(key)
        if value not in (None, ""):
            new[key] = value
    genre = norm_genre(str(new.get("genre") or ""))
    if not genre:
        return "a channel identity needs a genre"
    if catalog is not None:
        for var in ("voice", "template", "palette_hue"):
            if new.get(var) is not None and var in catalog:
                value, why = catalog[var].check(new[var])
                if why:
                    return why
                new[var] = value
    count = published_count(db)
    changing_genre = current.get("genre") and norm_genre(str(current["genre"])) != genre
    if changing_genre:
        since = count - int(current.get("videos_at_genre_start") or 0)
        if since < GENRE_DWELL_VIDEOS:
            return (
                f"genre stays '{current['genre']}' until {GENRE_DWELL_VIDEOS} videos are published under it "
                f"({since} so far)"
            )
    for other, ident in others(name).items():
        if ident.get("genre") and norm_genre(str(ident["genre"])) == genre:
            return f"genre '{new['genre']}' is channel '{other}''s; choose a different one"
        if new.get("voice") and ident.get("voice") == new["voice"]:
            return f"voice {new['voice']} is channel '{other}''s; choose a different one"
        if _look(new) is not None and _look(new) == _look(ident):
            return f"template and colour match channel '{other}''s; choose a different look"
    if not current.get("genre") or changing_genre:
        new["videos_at_genre_start"] = count
        history = list(current.get("history") or [])
        if current.get("genre"):
            history.append({"genre": current["genre"], "until": now_iso(), "videos": count})
        new["history"] = history[-10:]
    new["updated_at"] = now_iso()
    db.set_state(IDENTITY_KEY, json.dumps(new))
    return None


def fixed_settings(db: Database) -> dict[str, Any]:
    """The identity's voice and look, used for every video of this channel."""
    ident = load(db)
    return {k: ident[k] for k in ("voice", *LOOK_VARIABLES) if ident.get(k) is not None}


def prompt_text(db: Database, name: str, starting_genres: list[str]) -> str:
    ident = load(db)
    lines = []
    if ident.get("genre"):
        since = published_count(db) - int(ident.get("videos_at_genre_start") or 0)
        lines.append(
            f"This channel's identity: genre '{ident['genre']}'"
            + (f" ({ident['genre_note']})" if ident.get("genre_note") else "")
            + f"; voice {ident.get('voice') or 'not chosen'}; template {ident.get('template') or 'not chosen'}; "
            f"palette hue {ident.get('palette_hue') if ident.get('palette_hue') is not None else 'not chosen'}. "
            f"{since} videos published under this genre"
            + (
                f" (a genre change is possible after {GENRE_DWELL_VIDEOS})."
                if since < GENRE_DWELL_VIDEOS
                else "; you may change it if the evidence says so."
            )
        )
    else:
        lines.append(
            "This channel has no identity yet. Choose one now in channel_identity: a genre a viewer would "
            "subscribe for, a voice, a template and a palette hue. Starting options from the owner (take one, "
            "adapt it, or name your own): " + ("; ".join(starting_genres) or "none")
        )
    taken = {k: v for k, v in others(name).items() if v}
    if taken:
        lines.append(
            "Other channels (their genre, voice and look are taken): "
            + "; ".join(
                f"{k}: {v.get('genre')}, voice {v.get('voice')}, template {v.get('template')}, hue {v.get('palette_hue')}"
                for k, v in taken.items()
            )
        )
    rejected = db.get_state(REJECTED_KEY)
    if rejected:
        lines.append(f"Your last identity change that was refused: {rejected}")
    return "\n".join(lines)


def genre_line(db: Database) -> str:
    ident = load(db)
    if not ident.get("genre"):
        return ""
    note = f" ({ident['genre_note']})" if ident.get("genre_note") else ""
    return f"This channel's genre is '{ident['genre']}'{note}. Every idea must fit it.\n"
