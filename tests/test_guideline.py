from __future__ import annotations

from pathlib import Path

import pytest

from aimz import guideline as gl
from aimz.experiments.settings import CATALOG
from aimz.guideline import GuidelineError, load, parse, path

SAMPLE = (Path(__file__).parent / "fixtures" / "guideline_sample.md").read_text(encoding="utf-8")
CAPS = {"stock_video", "sfx"}


def _with(old: str, new: str) -> str:
    assert old in SAMPLE
    return SAMPLE.replace(old, new)


def test_parse_sample():
    g = parse(SAMPLE, CATALOG, CAPS)
    assert g.locked["caption_max_words"] == 3
    assert g.runtime_min == 20
    assert {"G-hook", "G-caption_max_words", "G-runtime"} <= g.item_ids()
    assert g.checks["title_max_chars"] == 60
    assert [r.stage for r in g.rules.values()] == ["topic", "script", "title", "cover"]


def test_rejects_unknown_setting():
    with pytest.raises(GuidelineError, match="nonsense"):
        parse(_with("  music: off", "  nonsense: 1"), CATALOG, CAPS)


def test_rejects_out_of_bounds():
    with pytest.raises(GuidelineError):
        parse(_with("caption_max_words: 3", "caption_max_words: 99"), CATALOG, CAPS)
    with pytest.raises(GuidelineError):
        parse(_with("max: 45", "max: 300"), CATALOG, CAPS)
    with pytest.raises(GuidelineError):
        parse(_with("min: 20", "min: 50"), CATALOG, CAPS)


def test_rejects_duplicate_rule_id():
    with pytest.raises(GuidelineError, match="duplicate"):
        parse(SAMPLE + "\n### G-hook: Again\nstage: script\nText.\n", CATALOG, CAPS)


def test_rejects_bad_stage_and_empty_text():
    with pytest.raises(GuidelineError, match="stage"):
        parse(_with("stage: topic", "stage: music"), CATALOG, CAPS)
    with pytest.raises(GuidelineError, match="empty"):
        parse(SAMPLE + "\n### G-x: Empty\nstage: topic\n", CATALOG, CAPS)


def test_rejects_missing_yaml():
    with pytest.raises(GuidelineError):
        parse("### G-a: A\nstage: topic\nText\n", CATALOG, CAPS)


def test_unavailable_capability():
    g = parse(_with("  music: off", "  clip_scenes: on"), CATALOG, set())
    assert "clip_scenes" in g.unavailable
    assert "clip_scenes" not in g.locked


def test_load_none_without_file(svc):
    assert load(svc.db, svc.env.config_dir, CATALOG, CAPS) is None
    assert svc.db.count("guideline_versions") == 0


def test_load_saves_initial_version(svc):
    path(svc.env.config_dir).write_text(SAMPLE, encoding="utf-8")
    g = load(svc.db, svc.env.config_dir, CATALOG, CAPS)
    assert g.version == 1
    rows = svc.db.query("SELECT * FROM guideline_versions")
    assert len(rows) == 1 and rows[0]["source"] == "initial"
    assert load(svc.db, svc.env.config_dir, CATALOG, CAPS).version == 1
    assert svc.db.count("guideline_versions") == 1


def test_owner_edit_creates_version(svc):
    p = path(svc.env.config_dir)
    p.write_text(SAMPLE, encoding="utf-8")
    load(svc.db, svc.env.config_dir, CATALOG, CAPS)
    p.write_text(_with("caption_max_words: 3", "caption_max_words: 4"), encoding="utf-8")
    g = load(svc.db, svc.env.config_dir, CATALOG, CAPS)
    assert g.version == 2 and g.locked["caption_max_words"] == 4
    row = svc.db.one("SELECT * FROM guideline_versions WHERE version=2")
    assert row["source"] == "owner_edit"


def test_bad_edit_keeps_last_valid(svc):
    p = path(svc.env.config_dir)
    p.write_text(SAMPLE, encoding="utf-8")
    load(svc.db, svc.env.config_dir, CATALOG, CAPS)
    p.write_text(SAMPLE.replace("```yaml", "```text"), encoding="utf-8")
    g = load(svc.db, svc.env.config_dir, CATALOG, CAPS)
    assert g.version == 1
    assert "YAML" in svc.db.get_state("guideline_error")
    p.write_text(SAMPLE, encoding="utf-8")
    load(svc.db, svc.env.config_dir, CATALOG, CAPS)
    assert not svc.db.get_state("guideline_error")


def test_deleted_file_uses_last_version(svc):
    p = path(svc.env.config_dir)
    p.write_text(SAMPLE, encoding="utf-8")
    load(svc.db, svc.env.config_dir, CATALOG, CAPS)
    p.unlink()
    g = load(svc.db, svc.env.config_dir, CATALOG, CAPS)
    assert g is not None and g.version == 1


def test_write_validates_and_versions(svc):
    with pytest.raises(GuidelineError):
        gl.write(svc.db, svc.env.config_dir, "nope", "ai_proposal", "x")
    v = gl.write(svc.db, svc.env.config_dir, SAMPLE, "ai_proposal", "approved")
    assert v == 1 and path(svc.env.config_dir).read_text(encoding="utf-8") == SAMPLE


def test_prompt_block_replace():
    g = parse(SAMPLE, CATALOG, CAPS)
    block = g.prompt_block("script", {"G-hook": "Open on a question."})
    assert "Open on a question." in block
    assert "most surprising fact" not in block
    assert "most surprising fact" in g.prompt_block("script")
