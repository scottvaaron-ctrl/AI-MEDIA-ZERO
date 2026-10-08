"""Shared fixtures: an isolated project root with config copied in, offline providers, tiny budget."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _never_download_siglip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tests must never fetch the picture-matching model (378 MB), touch the network, or load the real
    model from the live checkout: the model dir is an empty per-test folder (tests needing a model build
    their own and pass it explicitly)."""
    monkeypatch.setenv("SIGLIP_AUTO_DOWNLOAD", "false")
    monkeypatch.setenv("SIGLIP_MODEL_DIR", str(tmp_path / "no-model"))


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
        "SIGLIP_AUTO_DOWNLOAD": "false",
        "AIMZ_INSTANCE": "main",
        "AIMZ_INSTANCES_DIR": str(tmp_path / "instances"),  # never the real instances/ folder
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


def relax_posting_limits(svc) -> None:  # noqa: ANN001
    """For tests about something else (retries, idempotency) that post several times in a row.

    The owner's posting limits themselves are tested in tests/test_posting_limits.py.
    """
    svc.config.raw.setdefault("publishing", {})["limits"] = {
        "posts_per_run_per_platform": 1000,
        "min_hours_between_runs": 0,
        "max_runs_per_day": 1000,
        "backlog_days": 1000,
    }
