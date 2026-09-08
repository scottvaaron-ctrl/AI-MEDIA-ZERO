"""Configuration loading.

Two layers:

* ``.env`` (secrets, budget, paths, provider selection) -> :class:`EnvSettings`
* ``config/config.yaml`` (operating parameters, content families, safety) -> plain dict via :class:`AppConfig`

The budget and paid-provider switch live only in ``.env`` so that the AI, which can
edit strategy memory but never ``.env``, cannot touch them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "").lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = _env(name, "")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class EnvSettings:
    """Values sourced from the process environment / ``.env``. Immutable at runtime."""

    project_root: Path
    data_dir: Path
    db_path: Path
    config_dir: Path
    log_level: str

    monthly_budget_usd: float
    allow_paid_providers: bool

    llm_provider: str
    ollama_host: str
    ollama_model: str
    ollama_fast_model: str
    ollama_timeout_s: int
    ollama_num_ctx: int

    tts_provider: str
    piper_voice: str
    piper_voices_dir: Path
    piper_auto_download: bool
    piper_length_scale: float

    ffmpeg_bin: str
    ffprobe_bin: str
    font_file: str

    youtube_mode: str
    youtube_default_privacy: str
    youtube_enabled: bool
    youtube_client_secret_file: Path
    youtube_token_file: Path
    youtube_analytics_enabled: bool
    autopublish_consent: bool
    tiktok_mode: str
    tiktok_client_key: str
    tiktok_client_secret: str
    tiktok_redirect_uri: str
    tiktok_token_file: Path
    tiktok_privacy_level: str
    tiktok_analytics_enabled: bool
    bluesky_enabled: bool
    bluesky_handle: str
    bluesky_app_password: str
    bluesky_pds_url: str
    bluesky_session_file: Path
    bluesky_lang: str
    bluesky_sources_reply: bool
    bluesky_analytics_enabled: bool

    dashboard_host: str
    dashboard_port: int
    user_agent: str

    @property
    def env_file(self) -> Path:
        return self.project_root / ".env"


def _resolve(root: Path, raw: str, default: str) -> Path:
    p = Path(raw or default)
    return p if p.is_absolute() else (root / p).resolve()


def load_env_settings(project_root: Path | None = None, env_file: Path | None = None) -> EnvSettings:
    root = (project_root or Path(os.environ.get("AIMZ_PROJECT_ROOT", os.getcwd()))).resolve()
    env_path = env_file or (root / ".env")
    if env_path.exists():
        load_dotenv(env_path, override=False)
    return EnvSettings(
        project_root=root,
        data_dir=_resolve(root, _env("AIMZ_DATA_DIR"), "data"),
        db_path=_resolve(root, _env("AIMZ_DB_PATH"), "data/aimz.sqlite3"),
        config_dir=_resolve(root, _env("AIMZ_CONFIG_DIR"), "config"),
        log_level=_env("AIMZ_LOG_LEVEL", "INFO").upper(),
        monthly_budget_usd=max(0.0, _env_float("MONTHLY_BUDGET_USD", 0.0)),
        allow_paid_providers=_env_bool("ALLOW_PAID_PROVIDERS", False),
        llm_provider=_env("LLM_PROVIDER", "ollama").lower(),
        ollama_host=_env("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=_env("OLLAMA_MODEL", "qwen3:8b"),
        ollama_fast_model=_env("OLLAMA_FAST_MODEL", ""),
        ollama_timeout_s=_env_int("OLLAMA_TIMEOUT_S", 600),
        ollama_num_ctx=_env_int("OLLAMA_NUM_CTX", 8192),
        tts_provider=_env("TTS_PROVIDER", "piper").lower(),
        piper_voice=_env("PIPER_VOICE", "en_US-lessac-medium"),
        piper_voices_dir=_resolve(root, _env("PIPER_VOICES_DIR"), "data/voices"),
        piper_auto_download=_env_bool("PIPER_AUTO_DOWNLOAD", True),
        piper_length_scale=_env_float("PIPER_LENGTH_SCALE", 1.0),
        ffmpeg_bin=_env("FFMPEG_BIN", ""),
        ffprobe_bin=_env("FFPROBE_BIN", ""),
        font_file=_env("FONT_FILE", "C:/Windows/Fonts/arialbd.ttf"),
        youtube_mode=_env("YOUTUBE_MODE", "draft").lower(),
        youtube_default_privacy=_env("YOUTUBE_DEFAULT_PRIVACY", "private").lower(),
        youtube_enabled=_env_bool("YOUTUBE_ENABLED", False),
        youtube_client_secret_file=_resolve(
            root, _env("YOUTUBE_CLIENT_SECRET_FILE"), "secrets/client_secret.json"
        ),
        youtube_token_file=_resolve(root, _env("YOUTUBE_TOKEN_FILE"), "secrets/youtube_token.json"),
        youtube_analytics_enabled=_env_bool("YOUTUBE_ANALYTICS_ENABLED", False),
        autopublish_consent=_env_bool("AUTOPUBLISH_CONSENT", False),
        tiktok_mode=_env("TIKTOK_MODE", "package").lower(),
        tiktok_client_key=_env("TIKTOK_CLIENT_KEY", ""),
        tiktok_client_secret=_env("TIKTOK_CLIENT_SECRET", ""),
        tiktok_redirect_uri=_env("TIKTOK_REDIRECT_URI", ""),
        tiktok_token_file=_resolve(root, _env("TIKTOK_TOKEN_FILE"), "secrets/tiktok_token.json"),
        tiktok_privacy_level=_env("TIKTOK_PRIVACY_LEVEL", "").upper(),
        tiktok_analytics_enabled=_env_bool("TIKTOK_ANALYTICS_ENABLED", False),
        bluesky_enabled=_env_bool("BLUESKY_ENABLED", False),
        bluesky_handle=_env("BLUESKY_HANDLE", ""),
        bluesky_app_password=_env("BLUESKY_APP_PASSWORD", ""),
        bluesky_pds_url=_env("BLUESKY_PDS_URL", "https://bsky.social").rstrip("/"),
        bluesky_session_file=_resolve(root, _env("BLUESKY_SESSION_FILE"), "secrets/bluesky_session.json"),
        bluesky_lang=_env("BLUESKY_LANG", "en"),
        bluesky_sources_reply=_env_bool("BLUESKY_SOURCES_REPLY", True),
        bluesky_analytics_enabled=_env_bool("BLUESKY_ANALYTICS_ENABLED", False),
        dashboard_host=_env("DASHBOARD_HOST", "127.0.0.1"),
        dashboard_port=_env_int("DASHBOARD_PORT", 8420),
        user_agent=_env("AIMZ_USER_AGENT", "AIMediaZero/0.1 (local research bot; contact owner)"),
    )


@dataclass
class ContentFamily:
    key: str
    label: str
    description: str = ""


@dataclass
class AppConfig:
    """Parsed ``config/config.yaml`` with typed accessors for the parts the code relies on."""

    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def starting_families(self) -> list[ContentFamily]:
        fams = self.get("content.starting_families", []) or []
        return [
            ContentFamily(key=f["key"], label=f.get("label", f["key"]), description=f.get("description", ""))
            for f in fams
        ]

    @property
    def hook_types(self) -> list[str]:
        return list(self.get("content.hook_types", []) or [])

    @property
    def short_form(self) -> dict[str, Any]:
        return dict(self.get("content.short_form", {}) or {})

    @property
    def elevated_review_topics(self) -> list[str]:
        return list(self.get("safety.elevated_review_topics", []) or [])

    @property
    def banned_patterns(self) -> list[str]:
        return [str(p).lower() for p in (self.get("safety.banned_patterns", []) or [])]


def load_app_config(config_dir: Path) -> AppConfig:
    path = config_dir / "config.yaml"
    if not path.exists():
        return AppConfig(raw={})
    with open(path, encoding="utf-8") as fh:
        return AppConfig(raw=yaml.safe_load(fh) or {})


def load_feeds(config_dir: Path) -> list[dict[str, Any]]:
    path = config_dir / "feeds.yaml"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return list(data.get("feeds", []) or [])


def save_feeds(config_dir: Path, feeds: list[dict[str, Any]]) -> None:
    path = config_dir / "feeds.yaml"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Research feeds (owner-editable). Free, official/public sources only.\n")
        yaml.safe_dump({"feeds": feeds}, fh, sort_keys=False, allow_unicode=True)


def load_pronunciations(config_dir: Path) -> dict[str, str]:
    path = config_dir / "pronunciations.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {str(k): str(v) for k, v in (data.get("overrides", {}) or {}).items()}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def update_env_file(env_path: Path, updates: dict[str, str]) -> None:
    """Owner-only helper: rewrite selected keys in ``.env`` preserving everything else.

    Used by the dashboard for budget / publishing-mode changes. The AI never calls this.
    """
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
