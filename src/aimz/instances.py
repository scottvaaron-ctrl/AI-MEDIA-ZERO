"""Several channels from one codebase: one instance per channel (plan stage 4).

* ``main`` is the original channel and lives at the repository root.
* Every other channel lives in ``instances/<name>/`` with its own ``.env``, ``config/`` (``config.yaml``,
  ``feeds.yaml``, its own ``strategy.md``), ``data/`` (database, videos, logs) and ``secrets/`` (its own
  YouTube token). The constitution, the Google OAuth client, the voices and the code are shared.
* ``python -m aimz --instance <name> ...`` runs any command for one channel. One process serves one
  instance, because ``.env`` is loaded into the process environment.
* Instances share only small JSON files in ``instances/_shared/<name>.json``, each written by its own
  instance: its niches (so channels do not compete for the same niche) and its scored production
  settings (so every channel learns from every channel's voice, caption and pacing tests).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from aimz.util import now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN = "main"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,29}$")
FIRST_INSTANCE_PORT = 8421

# .env keys copied from the main channel into a new instance: how the machine works, never whose account.
SHARED_ENV_KEYS = (
    "MONTHLY_BUDGET_USD",
    "ALLOW_PAID_PROVIDERS",
    "LLM_PROVIDER",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
    "OLLAMA_FAST_MODEL",
    "OLLAMA_TIMEOUT_S",
    "OLLAMA_NUM_CTX",
    "TTS_PROVIDER",
    "PIPER_AUTO_DOWNLOAD",
    "PIPER_LENGTH_SCALE",
    "FFMPEG_BIN",
    "FFPROBE_BIN",
    "FONT_FILE",
    "AIMZ_LOG_LEVEL",
    "AIMZ_USER_AGENT",
    # Never AUTOPUBLISH_CONSENT: consent to automated uploads is per channel (YouTube Developer Policies
    # III.I, owner decision 2026-10-01). A new instance starts without it; `aimz instance consent` grants it
    # after showing which channel the login posts to. Never PIPER_VOICE either: there is no default voice.
    "YOUTUBE_MODE",
    "YOUTUBE_DEFAULT_PRIVACY",
    # Stock media keys are the owner's, shared by every channel (one rate limit across all of them).
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
)


def instances_dir() -> Path:
    return Path(os.environ.get("AIMZ_INSTANCES_DIR") or (REPO_ROOT / "instances"))


def shared_dir() -> Path:
    return instances_dir() / "_shared"


def current() -> str:
    return os.environ.get("AIMZ_INSTANCE") or MAIN


def valid_name(name: str) -> bool:
    return name != MAIN and bool(_NAME_RE.match(name))


def root_of(name: str) -> Path:
    return REPO_ROOT if name == MAIN else instances_dir() / name


def list_instances() -> list[str]:
    out = [MAIN]
    d = instances_dir()
    if d.exists():
        out += sorted(
            p.name for p in d.iterdir() if p.is_dir() and (p / ".env").exists() and valid_name(p.name)
        )
    return out


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def used_ports() -> set[int]:
    ports = set()
    for name in list_instances():
        raw = _read_env(root_of(name) / ".env").get("DASHBOARD_PORT")
        ports.add(int(raw) if raw and raw.isdigit() else 8420)
    return ports


def create(name: str, channel_name: str, main_root: Path = REPO_ROOT) -> Path:
    """Create ``instances/<name>`` for a new channel. The owner then authorises its YouTube account.

    The new ``.env`` copies only machine settings from the main one (budget included, unchanged): no
    account credentials. YouTube is on and needs `aimz --instance <name> youtube auth`; TikTok and
    Bluesky are off until the owner sets them up for this channel.
    """
    if not valid_name(name):
        raise ValueError("instance names are 2-30 lowercase letters, digits, '-' or '_', and not 'main'")
    root = instances_dir() / name
    if root.exists():
        raise ValueError(f"instance {name!r} already exists at {root}")
    main_env = _read_env(main_root / ".env")
    port = FIRST_INSTANCE_PORT
    taken = used_ports()
    while port in taken:
        port += 1
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "secrets").mkdir()
    feeds = main_root / "config" / "feeds.yaml"
    if feeds.exists():  # research sources: the channel's own copy, to edit for its niches
        shutil.copyfile(feeds, root / "config" / "feeds.yaml")
    # Only this channel's differences: everything else (posting limits, scoring, stock switch, ...) is read
    # from the main config.yaml, so one owner edit applies to every channel.
    (root / "config" / "config.yaml").write_text(
        "# This channel's differences from the main config/config.yaml (which applies to everything else).\n"
        "project:\n"
        f"  name: {json.dumps(channel_name)}\n",
        encoding="utf-8",
    )
    lines = [
        f"# Instance '{name}' ({channel_name}), created {now_iso()} by `aimz instance create`.",
        "# Machine settings copied from the main channel; account settings are this channel's own.",
        f"AIMZ_INSTANCE={name}",
        f"AIMZ_CONSTITUTION_FILE={(main_root / 'config' / 'constitution.md').as_posix()}",
        f"AIMZ_BASE_CONFIG={(main_root / 'config' / 'config.yaml').as_posix()}",
        f"PIPER_VOICES_DIR={(main_root / 'data' / 'voices').as_posix()}",
        f"YOUTUBE_CLIENT_SECRET_FILE={(main_root / 'secrets' / 'client_secret.json').as_posix()}",
        f"DASHBOARD_PORT={port}",
        *(f"{k}={main_env[k]}" for k in SHARED_ENV_KEYS if k in main_env),
        "AUTOPUBLISH_CONSENT=false",
        "YOUTUBE_ENABLED=true",
        "YOUTUBE_ANALYTICS_ENABLED=true",
        "TIKTOK_MODE=package",
        "BLUESKY_ENABLED=false",
    ]
    (root / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def set_env(root: Path, key: str, value: str) -> None:
    """Set one key in an instance's ``.env`` (owner commands only), keeping every other line."""
    path = root / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue  # a duplicate of the key is dropped, so the value set here is the one that applies
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# -- shared summaries ----------------------------------------------------------------------------------


def write_shared(name: str, payload: dict[str, Any]) -> Path:
    d = shared_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({**payload, "instance": name, "updated_at": now_iso()}, indent=1), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def read_others(name: str) -> dict[str, dict[str, Any]]:
    """Every other instance's shared summary (read-only)."""
    out: dict[str, dict[str, Any]] = {}
    d = shared_dir()
    if not d.exists():
        return out
    for path in sorted(d.glob("*.json")):
        if path.stem == name:
            continue
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return out


CLAIM_MIN_VIDEOS = 3


def claimed_niches(name: str, own_counts: dict[str, int]) -> dict[str, str]:
    """Niches another channel already exploits: niche -> that channel.

    A channel holds a niche once it has CLAIM_MIN_VIDEOS measured videos in it and more than this
    channel has there (a tie goes to the name that sorts first), so two channels never keep making
    the same niche.
    """
    out: dict[str, str] = {}
    for other, info in read_others(name).items():
        for niche in info.get("niches", []):
            key, n = str(niche.get("niche") or ""), int(niche.get("n") or 0)
            if not key or n < CLAIM_MIN_VIDEOS or niche.get("status") == "retired":
                continue
            mine = own_counts.get(key, 0)
            if n > mine or (n == mine and other < name):
                out[key] = other
    return out
