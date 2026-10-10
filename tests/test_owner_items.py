"""Owner items of 2026-10-09: drop a dead upload for good; the render matrix gives every sample a voice."""

from __future__ import annotations

from typing import Any

import pytest

from aimz.core.attention import attention
from aimz.util import new_id, now_iso


def _failed_pub(svc: Any, status: str = "failed") -> str:
    from tests.test_producer_voice import _script

    script_id = _script(svc)
    vid = new_id("vid")
    svc.db.insert(
        "videos",
        {
            "id": vid,
            "script_id": script_id,
            "idea_id": svc.db.get("scripts", script_id)["idea_id"],
            "title": "The 1945 Borneo POW Camp Rescue",
            "format": "short",
            "resolution": "1080x1920",
            "status": "measured",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    pid = new_id("pub")
    svc.db.insert(
        "publications",
        {
            "id": pid,
            "video_id": vid,
            "platform": "youtube",
            "publisher": "YouTubePublisher",
            "mode": "public",
            "idempotency_key": f"{vid}:youtube:{getattr(svc.publishers['youtube'], 'mode', 'package')}",
            "status": status,
            "attempts": 1,
            "last_error": "RefreshError: invalid_grant",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
    )
    return pid


def test_owner_drops_a_failed_upload_for_good(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _orch

    pid = _failed_pub(svc)
    assert attention(svc.db)["uploads_failed_no_retry"]
    orch = _orch(svc)
    orch.publisher.drop(pid, "owner: let it go")
    row = svc.db.get("publications", pid)
    assert row["status"] == "dropped" and "owner: let it go" in row["last_error"]
    assert "uploads_failed_no_retry" not in attention(svc.db)
    # Never re-sent by a cycle, but the owner can still change their mind.
    with svc.tracker.run("p") as run:
        res = orch.publisher.publish(run, row["video_id"], platforms=["youtube"])
    assert res[0]["status"] == "dropped" and "skipped" in res[0]
    orch.publisher.requeue(pid)
    assert svc.db.get("publications", pid)["status"] == "pending"


def test_only_unsent_uploads_can_be_dropped(svc) -> None:  # noqa: ANN001
    from tests.test_reliability import _orch

    pid = _failed_pub(svc, status="published")
    with pytest.raises(ValueError, match="published"):
        _orch(svc).publisher.drop(pid, "x")


def test_cli_publish_drop(svc) -> None:  # noqa: ANN001
    from typer.testing import CliRunner

    from aimz.cli import app

    pid = _failed_pub(svc)
    out = CliRunner().invoke(app, ["publish", "drop", pid, "--reason", "let it go"])
    assert out.exit_code == 0, out.output
    assert svc.db.get("publications", pid)["status"] == "dropped"


def test_matrix_gives_non_voice_samples_a_present_voice() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "render_matrix", Path(__file__).resolve().parents[1] / "scripts" / "render_matrix.py"
    )
    assert spec and spec.loader
    rm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rm)
    assert rm.sample_voice(["en_US-john-medium", "kokoro:af_bella"], "kokoro:af_bella") == "kokoro:af_bella"
    assert rm.sample_voice(["en_US-john-medium"], "kokoro:af_bella") == "en_US-john-medium"
    assert rm.sample_voice([], "kokoro:af_bella") is None
