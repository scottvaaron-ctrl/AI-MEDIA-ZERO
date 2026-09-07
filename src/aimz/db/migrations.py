"""File-based migration runner.

Migrations live in ``aimz/db/migrations/NNNN_name.sql`` and are applied in numeric
order inside a transaction. Applied versions are recorded in ``schema_migrations``.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    found: list[tuple[int, str, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        m = _NAME_RE.match(path.name)
        if not m:
            raise ValueError(f"Bad migration filename: {path.name} (expected NNNN_name.sql)")
        found.append((int(m.group(1)), m.group(2), path))
    versions = [v for v, _, _ in found]
    if len(versions) != len(set(versions)):
        raise ValueError("Duplicate migration versions detected")
    return found


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    return {int(r[0]) for r in conn.execute("SELECT version FROM schema_migrations")}


def apply_migrations(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    from aimz.util import now_iso

    done = applied_versions(conn)
    applied: list[str] = []
    for version, name, path in list_migrations(directory):
        if version in done:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
        except Exception:
            conn.rollback()
            raise
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, now_iso()),
        )
        conn.commit()
        applied.append(f"{version:04d}_{name}")
    return applied
