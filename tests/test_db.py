from __future__ import annotations

import sqlite3
from pathlib import Path

from aimz.db import connect
from aimz.db.migrations import list_migrations


def test_migrations_apply_once(tmp_path: Path) -> None:
    db = connect(tmp_path / "a.sqlite3")
    applied_first = {r["name"] for r in db.query("SELECT name FROM schema_migrations")}
    assert "initial" in applied_first
    assert db.migrate() == []  # idempotent
    expected = {
        "sources",
        "source_items",
        "ideas",
        "scripts",
        "claims",
        "scenes",
        "assets",
        "videos",
        "publications",
        "metrics",
        "comments",
        "experiments",
        "strategy_versions",
        "agent_runs",
        "provider_usage",
        "ledger",
        "errors",
        "configuration_versions",
        "system_state",
        "runs",
    }
    have = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert expected <= have
    db.close()


def test_migration_files_are_well_named() -> None:
    migs = list_migrations()
    assert migs and migs[0][0] == 1


def test_helpers_and_state(tmp_path: Path) -> None:
    db = connect(tmp_path / "b.sqlite3")
    db.set_state("k", "v")
    assert db.get_state("k") == "v"
    db.set_state("k", "w")
    assert db.get_state("k") == "w"
    db.insert("errors", {"id": "e1", "message": "m", "created_at": "2026-01-01T00:00:00+00:00"})
    db.update("errors", "e1", {"message": "n"})
    assert db.get("errors", "e1")["message"] == "n"
    assert db.count("errors") == 1
    db.close()


def test_unique_url_hash(tmp_path: Path) -> None:
    db = connect(tmp_path / "c.sqlite3")
    db.insert("sources", {"id": "s", "name": "n", "kind": "rss", "created_at": "x", "updated_at": "x"})
    row = {
        "id": "i1",
        "source_id": "s",
        "url": "u",
        "url_hash": "h",
        "title": "t",
        "ingested_at": "x",
        "dedupe_key": "d",
    }
    db.insert("source_items", row)
    try:
        db.insert("source_items", {**row, "id": "i2"})
        raise AssertionError("duplicate url_hash should fail")
    except sqlite3.IntegrityError:
        pass
    db.close()
