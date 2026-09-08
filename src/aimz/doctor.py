"""`aimz doctor`: checks every local dependency and (with --fix) downloads free assets such as the Piper voice."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from aimz.providers.registry import Services
from aimz.providers.video.ffmpeg_renderer import resolve_ffmpeg, resolve_ffprobe


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True


def run_checks(svc: Services, fix: bool = False) -> list[Check]:
    env = svc.env
    checks: list[Check] = []

    # Python
    ok = sys.version_info >= (3, 11)
    checks.append(
        Check(
            "Python",
            ok,
            f"{platform.python_version()} ({sys.executable})",
            "Install Python 3.11+ from python.org",
        )
    )

    # FFmpeg
    ff = resolve_ffmpeg(env.ffmpeg_bin)
    if ff:
        try:
            ver = subprocess.run(
                [ff, "-version"], capture_output=True, text=True, timeout=20
            ).stdout.splitlines()[0]
        except Exception as exc:  # pragma: no cover
            ver = f"present but failed: {exc}"
        checks.append(Check("FFmpeg", True, f"{ver} -> {ff}"))
    else:
        checks.append(
            Check(
                "FFmpeg",
                False,
                "not found on PATH or via imageio-ffmpeg",
                "winget install Gyan.FFmpeg   (or: pip install imageio-ffmpeg)",
            )
        )
    fp = resolve_ffprobe(env.ffprobe_bin, ff)
    checks.append(
        Check(
            "ffprobe",
            bool(fp),
            fp or "not found (durations parsed from ffmpeg output instead)",
            "winget install Gyan.FFmpeg",
            required=False,
        )
    )

    # Ollama
    if env.llm_provider == "fixture":
        checks.append(Check("LLM", True, "fixture provider (offline, deterministic)", required=False))
    else:
        h = svc.llm.health()
        checks.append(Check("Ollama", h.ok, h.detail, h.fix or "winget install Ollama.Ollama ; ollama serve"))
        checks.append(
            Check(
                "Ollama model",
                h.ok,
                env.ollama_model if h.ok else f"{env.ollama_model} (see above)",
                f"ollama pull {env.ollama_model}",
            )
        )
        checks.append(
            Check(
                "ollama CLI",
                bool(shutil.which("ollama")),
                shutil.which("ollama") or "not on PATH (server may still be running)",
                "restart the terminal after installing Ollama",
                required=False,
            )
        )

    # Piper
    if env.tts_provider == "silent":
        checks.append(Check("TTS", True, "silent provider (tests only)", required=False))
    else:
        h = svc.tts.health()
        if not h.ok and fix and "voice" in h.detail.lower():
            try:
                svc.tts.ensure_voice()  # type: ignore[attr-defined]
                h = svc.tts.health()
            except Exception as exc:
                h.detail += f" (auto-download failed: {exc})"
        checks.append(
            Check("Piper TTS", h.ok, h.detail, h.fix or "pip install piper-tts ; aimz doctor --fix")
        )

    # Fonts / Pillow
    h = svc.cards.health()
    checks.append(
        Check("Pillow / font", h.ok, h.detail, "set FONT_FILE in .env to a .ttf path", required=False)
    )

    # Database
    try:
        n = svc.db.count("schema_migrations")
        checks.append(Check("Database", True, f"{env.db_path} ({n} migrations applied)"))
    except Exception as exc:
        checks.append(Check("Database", False, str(exc), "aimz init"))

    # Config files
    for name in ("config.yaml", "feeds.yaml", "constitution.md"):
        p = env.config_dir / name
        checks.append(Check(f"config/{name}", p.exists(), str(p) if p.exists() else "missing", "aimz init"))
    checks.append(
        Check(
            ".env",
            env.env_file.exists(),
            str(env.env_file) if env.env_file.exists() else "missing (defaults in use)",
            "copy .env.example .env",
            required=False,
        )
    )

    # Budget & kill switch
    snap = svc.budget.snapshot()
    checks.append(
        Check(
            "Budget",
            True,
            f"${snap.budget_usd:.2f}/month; spent ${snap.spent_usd:.2f}; denials {snap.denied_count}",
            required=False,
        )
    )
    ks = svc.killswitch.status()
    checks.append(
        Check("Kill switch", True, "ENGAGED" if ks.engaged else "off", "aimz resume", required=False)
    )

    # Publishing
    yt = svc.publishers["youtube"].health()
    checks.append(
        Check(
            "YouTube", yt.ok, yt.detail, yt.fix, required=env.youtube_enabled and env.youtube_mode != "draft"
        )
    )
    if env.youtube_enabled or env.youtube_analytics_enabled:
        try:
            import googleapiclient  # noqa: F401

            checks.append(Check("google-api-python-client", True, "installed", required=False))
        except ImportError:
            checks.append(
                Check("google-api-python-client", False, "missing", "pip install ai-media-zero[youtube]")
            )
    checks.append(Check("TikTok", True, svc.publishers["tiktok"].health().detail, required=False))
    bluesky = svc.publishers.get("bluesky") or svc.remote_analytics.get("bluesky")
    if bluesky is not None:
        h = bluesky.health()
        checks.append(Check("Bluesky", h.ok, h.detail, h.fix, required=env.bluesky_enabled))
    else:
        checks.append(
            Check(
                "Bluesky",
                True,
                "disabled (BLUESKY_ENABLED=false)",
                "free and needs no platform audit; see docs/AUTONOMOUS_SETUP.md",
                required=False,
            )
        )

    # Assets network (optional)
    for ap in svc.assets:
        h = ap.health()
        checks.append(Check(ap.name, h.ok, h.detail, h.fix, required=False))

    return checks


def summarize(checks: list[Check]) -> tuple[bool, str]:
    lines = []
    all_ok = True
    for c in checks:
        mark = "OK " if c.ok else ("!! " if c.required else "-- ")
        if not c.ok and c.required:
            all_ok = False
        line = f"[{mark}] {c.name}: {c.detail}"
        if not c.ok and c.fix:
            line += f"\n       fix: {c.fix}"
        lines.append(line)
    return all_ok, "\n".join(lines)


def ensure_dirs(root: Path) -> None:
    for d in (
        "data",
        "data/videos",
        "data/packages",
        "data/voices",
        "data/assets",
        "data/logs",
        "data/reports",
        "secrets",
        "assets/owner",
    ):
        (root / d).mkdir(parents=True, exist_ok=True)
