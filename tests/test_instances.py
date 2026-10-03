"""Stage 4 of docs/PLAN_FULL_CONTROL.md: several channels, one instance each."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from aimz import instances, scheduler
from aimz.experiments import shared
from aimz.experiments.settings import SettingsEngine, enc

MAIN_ENV = """MONTHLY_BUDGET_USD=0.00
ALLOW_PAID_PROVIDERS=false
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3:8b
PIPER_VOICE=en_US-lessac-medium
AUTOPUBLISH_CONSENT=true
YOUTUBE_MODE=public
BLUESKY_ENABLED=true
BLUESKY_HANDLE=owner.bsky.social
BLUESKY_APP_PASSWORD=secret-app-password
TIKTOK_CLIENT_SECRET=tiktok-secret
DASHBOARD_PORT=8420
"""


@pytest.fixture()
def main_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / ".env").write_text(MAIN_ENV, encoding="utf-8")
    (root / "config" / "config.yaml").write_text(
        "project:\n  name: AI Media Zero\n  timezone: x\n", encoding="utf-8"
    )
    (root / "config" / "feeds.yaml").write_text("feeds: []\n", encoding="utf-8")
    (root / "config" / "constitution.md").write_text("rules", encoding="utf-8")
    (root / "config" / "strategy.md").write_text("main's strategy", encoding="utf-8")
    return root


def _env(path: Path) -> dict[str, str]:
    return instances._read_env(path)


# -- creating an instance ----------------------------------------------------------------------------------


def test_create_copies_machine_settings_but_no_account_secrets(svc, main_root: Path) -> None:  # noqa: ANN001
    root = instances.create("space", "Deep Space Daily", main_root)
    env = _env(root / ".env")
    assert env["AIMZ_INSTANCE"] == "space"
    assert env["MONTHLY_BUDGET_USD"] == "0.00" and env["ALLOW_PAID_PROVIDERS"] == "false"
    # Consent to automated uploads is per channel and never copied (main has it); no default voice either.
    assert env["AUTOPUBLISH_CONSENT"] == "false" and env["YOUTUBE_ENABLED"] == "true"
    assert "PIPER_VOICE" not in env
    assert env["BLUESKY_ENABLED"] == "false" and env["TIKTOK_MODE"] == "package"
    for secret in ("BLUESKY_APP_PASSWORD", "BLUESKY_HANDLE", "TIKTOK_CLIENT_SECRET"):
        assert secret not in env
    assert env["AIMZ_CONSTITUTION_FILE"].endswith("config/constitution.md")
    assert env["PIPER_VOICES_DIR"].endswith("data/voices")
    assert env["DASHBOARD_PORT"] == "8421"
    assert "Deep Space Daily" in (root / "config" / "config.yaml").read_text(encoding="utf-8")
    assert env["AIMZ_BASE_CONFIG"].endswith("config/config.yaml")
    assert not (root / "config" / "strategy.md").exists()  # each channel writes its own
    assert not (root / "config" / "constitution.md").exists()  # the owner's one file is shared
    assert (root / "data").is_dir() and (root / "secrets").is_dir()
    second = instances.create("history", "History Shorts", main_root)
    assert _env(second / ".env")["DASHBOARD_PORT"] == "8422"
    assert instances.list_instances() == ["main", "history", "space"]


def test_set_env_replaces_one_key_and_drops_duplicates(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "# c\nAUTOPUBLISH_CONSENT=false\nX=1\nAUTOPUBLISH_CONSENT=true\n", encoding="utf-8"
    )
    instances.set_env(tmp_path, "AUTOPUBLISH_CONSENT", "true")
    instances.set_env(tmp_path, "NEW", "v")
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "# c\nAUTOPUBLISH_CONSENT=true\nX=1\nNEW=v\n"


def test_upload_refused_when_login_points_at_another_channel(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from aimz.core.errors import PermanentPublishError
    from aimz.providers.publishers import youtube as yt_mod

    class Exec:
        def __init__(self, payload: dict) -> None:  # noqa: ANN401
            self.payload = payload

        def execute(self) -> dict:
            return self.payload

    class FakeYT:
        def channels(self) -> FakeYT:
            return self

        def list(self, **_: object) -> Exec:
            return Exec({"items": [{"id": "UC_other"}]})

        def videos(self) -> FakeYT:
            raise AssertionError("must not upload")

    monkeypatch.setattr(yt_mod, "load_credentials", lambda *a, **k: object())
    monkeypatch.setattr(yt_mod, "build_youtube", lambda creds: FakeYT())
    pub = yt_mod.YouTubePublisher(
        "public", "private", True, Path("cs.json"), Path("tok.json"), consented_channel_id="UC_mine"
    )
    pkg = svc.env.data_dir / "pkg"
    video = {"id": "v1", "file_path": str(pkg / "x.mp4"), "title": "T", "duration_s": 30}
    meta = {"title": "Title here", "description": "d", "tags": [], "owner_approved": True}
    with pytest.raises(PermanentPublishError, match="consented"):
        pub.publish(svc.ctx(), video, {"id": "s1"}, meta, pkg)


@pytest.mark.parametrize("name", ["main", "Space", "x", "a b", "../up", "_shared"])
def test_bad_instance_names_are_refused(svc, main_root: Path, name: str) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        instances.create(name, "x", main_root)


def test_an_instance_loads_its_own_settings(svc, main_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from aimz.settings import load_env_settings

    root = instances.create("space", "Deep Space Daily", main_root)
    for key in ("AIMZ_DATA_DIR", "AIMZ_DB_PATH", "AIMZ_CONFIG_DIR", "AIMZ_INSTANCE"):
        monkeypatch.delenv(key, raising=False)
    env = load_env_settings(root)
    assert env.instance == "space"
    assert env.db_path == (root / "data" / "aimz.sqlite3").resolve()
    assert env.youtube_token_file == (root / "secrets" / "youtube_token.json").resolve()
    assert env.constitution_file == (main_root / "config" / "constitution.md").resolve()
    assert env.youtube_client_secret_file == (main_root / "secrets" / "client_secret.json").resolve()


def test_cli_refuses_an_unknown_instance(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    result = CliRunner().invoke(app, ["--instance", "nope", "status"])
    assert result.exit_code == 1 and "no instance 'nope'" in result.output


# -- scheduling ------------------------------------------------------------------------------------------------


def test_task_names_per_instance() -> None:
    assert scheduler.task_name() == "AI Media Zero"
    assert scheduler.task_name(instance="space") == "AI Media Zero - space"
    assert scheduler.instance_of("AI Media Zero - space") == "space"
    assert scheduler.instance_of("AI Media Zero 0900") == "main"


def test_installing_one_channel_never_removes_another(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tasks = ["AI Media Zero", "AI Media Zero 0900", "AI Media Zero - space", "AI Media Zero - history"]
    removed: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(scheduler.os, "name", "nt")
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *a, **k: Proc())
    monkeypatch.setattr(scheduler, "list_tasks", lambda: list(tasks))

    def fake_remove(names: list[str] | None = None, instance: str | None = None) -> list[str]:
        removed.append(list(names or []))
        for n in names or []:
            tasks.remove(n)
        return list(names or [])

    monkeypatch.setattr(scheduler, "remove", fake_remove)
    (tmp_path / "space").mkdir()
    scheduler.install(tmp_path, ["09:15"], "space", tmp_path / "space")
    assert removed == [[]]
    scheduler.install(tmp_path, ["09:00"], "main")
    assert removed[-1] == ["AI Media Zero 0900"]  # main's old layout only
    script = (tmp_path / "space" / "run-cycle.ps1").read_text(encoding="utf-8")
    assert (
        "--instance space run" in script and "AIMediaZeroCycle-space" in script and "AIMediaZeroGPU" in script
    )


# -- niches ------------------------------------------------------------------------------------------------------


def _share(name: str, niches: list[tuple[str, int]], samples: list[dict[str, Any]] | None = None) -> None:
    instances.write_shared(
        name,
        {
            "niches": [{"niche": k, "n": n, "status": "testing"} for k, n in niches],
            "settings_samples": samples or [],
        },
    )


def test_a_niche_belongs_to_the_channel_with_more_measured_videos(svc) -> None:  # noqa: ANN001
    _share("space", [("astronomy", 5), ("history", 2)])
    _share("sport", [("baseball", 3), ("tennis", 3)])
    claimed = instances.claimed_niches("main", {"astronomy": 1, "baseball": 6, "tennis": 3})
    # history has too few videos to claim; baseball is main's (more videos); tennis ties and 'main' < 'sport'
    assert claimed == {"astronomy": "space"}


def test_ideation_drops_ideas_in_another_channels_niche(svc, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from tests.test_pipeline_offline import FixtureFeedProvider

    from aimz.pipeline.orchestrator import Orchestrator

    fp = FixtureFeedProvider()
    svc.research = {k: fp for k in fp.kinds}
    orch = Orchestrator(svc, seed=7)
    state = orch.strategy.current()

    class Everything(dict):  # every niche the model could name is held by another channel
        def __contains__(self, key: object) -> bool:
            return True

    monkeypatch.setattr(instances, "claimed_niches", lambda name, counts: Everything(space="space"))
    with svc.tracker.run("t") as run:
        orch.research.run(run)
        created = orch.ideation.run(run, state)
    assert created == []


# -- pooled production evidence --------------------------------------------------------------------------------------


def _own_video(svc: Any, vid: str, settings: dict[str, Any]) -> dict[str, Any]:
    from aimz.util import now_iso

    for var, val in settings.items():
        svc.db.execute(
            "INSERT INTO video_settings (video_id, variable, value, source, created_at) VALUES (?,?,?,?,?)",
            [vid, var, enc(val), "bandit", now_iso()],
        )
    return {"video_id": vid, "score": 0.3, "content_family": "astronomy", "duration_s": 40}


def test_production_evidence_is_pooled_and_content_stays_per_channel(svc) -> None:  # noqa: ANN001
    eng = SettingsEngine(svc.db)
    own = [_own_video(svc, f"v{i}", {"caption_color": "white", "hook_type": "numeric"}) for i in range(3)]
    other = [
        {
            "video_id": f"x{i}",
            "score": 0.5,
            "content_family": "sport",
            "duration_s": 30,
            "settings": {"caption_color": enc("yellow"), "hook_type": enc("question")},
        }
        for i in range(4)
    ]
    _share("sport", [("sport", 4)], other)
    rows = shared.pooled_rows("main", own, eng)
    stats = eng.value_stats(rows)
    colours = {s.value: s.n for s in stats["caption_color"]}
    assert colours == {"white": 3, "yellow": 4}
    assert "hook_type" not in stats or {s.value for s in stats["hook_type"]} == {"numeric"}
    assert "sibling channels (sport)" in eng.evidence_text(rows)


def test_regression_controls_for_the_channel(svc) -> None:  # noqa: ANN001
    import random

    eng = SettingsEngine(svc.db)
    rng = random.Random(2)
    own = []
    for i in range(20):
        speed = [1.0, 1.2][i % 2]
        own.append(
            {
                **_own_video(svc, f"v{i}", {"speech_length_scale": speed}),
                "score": 0.2 + (0.05 if speed == 1.2 else 0) + rng.gauss(0, 0.01),
            }
        )
    samples = []
    for i in range(20):
        speed = [1.0, 1.2][i % 2]
        samples.append(
            {
                "video_id": f"x{i}",
                "score": 0.4 + (0.05 if speed == 1.2 else 0) + rng.gauss(0, 0.01),  # a better channel overall
                "content_family": "astronomy",
                "duration_s": 40,
                "settings": {"speech_length_scale": enc(speed)},
            }
        )
    _share("space", [], samples)
    reg = eng.regression(shared.pooled_rows("main", own, eng))
    assert reg is not None and reg["n"] == 40
    eff = {(e["variable"], e["value"]): e for e in reg["effects"]}
    assert 0.03 < eff[("speech_length_scale", 1.2)]["effect"] < 0.07
    assert ("channel", "this") in eff or ("channel", "space") in eff


def test_the_analyst_publishes_its_summary(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _orch

    orch = _orch(svc)
    with svc.tracker.run("learn") as run:
        orch.analyst.learn(run, orch.strategy.current())
    data = json.loads((instances.shared_dir() / "main.json").read_text(encoding="utf-8"))
    assert data["instance"] == "main" and "niches" in data and "settings_samples" in data


def test_an_instance_inherits_the_main_config_and_overrides_its_name(
    svc, main_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    from aimz.settings import load_app_config

    (main_root / "config" / "config.yaml").write_text(
        "project:\n  name: AI Media Zero\npublishing:\n  limits:\n    max_runs_per_day: 4\n", encoding="utf-8"
    )
    root = instances.create("space", "Deep Space Daily", main_root)
    monkeypatch.setenv("AIMZ_BASE_CONFIG", _env(root / ".env")["AIMZ_BASE_CONFIG"])
    cfg = load_app_config(root / "config")
    assert cfg.get("project.name") == "Deep Space Daily"
    assert cfg.get("publishing.limits.max_runs_per_day") == 4  # the owner's limits reach every channel
