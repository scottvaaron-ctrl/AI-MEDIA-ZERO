"""Shared fixtures: an isolated project root with config copied in, offline providers, tiny budget."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway project directory with real config files and offline providers."""
    shutil.copytree(ROOT / "config", tmp_path / "config")
    (tmp_path / "data").mkdir()
    env_vars = {
        "AIMZ_PROJECT_ROOT": str(tmp_path),
        "AIMZ_DATA_DIR": str(tmp_path / "data"),
        "AIMZ_DB_PATH": str(tmp_path / "data" / "test.sqlite3"),
        "AIMZ_CONFIG_DIR": str(tmp_path / "config"),
        "MONTHLY_BUDGET_USD": "0.00",
        "ALLOW_PAID_PROVIDERS": "false",
        "LLM_PROVIDER": "fixture",
        "TTS_PROVIDER": "silent",
        "YOUTUBE_MODE": "draft",
        "YOUTUBE_ENABLED": "false",
        "AIMZ_LOG_LEVEL": "WARNING",
    }
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def svc(project: Path):  # noqa: ANN201
    from aimz.providers.registry import build_services

    s = build_services(project, quiet_logs=True)
    # disable network asset providers in tests
    s.assets = [p for p in s.assets if p.name == "OwnerLibraryAssetProvider"]
    yield s
    s.close()


@pytest.fixture()
def ffmpeg_available() -> bool:
    from aimz.providers.video.ffmpeg_renderer import resolve_ffmpeg

    return resolve_ffmpeg(os.environ.get("FFMPEG_BIN", "")) is not None
