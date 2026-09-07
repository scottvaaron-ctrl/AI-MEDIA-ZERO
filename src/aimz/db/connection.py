"""Thin SQLite wrapper.

Design notes for a later Postgres move:

* All ids are TEXT, all timestamps ISO-8601 TEXT, all JSON is TEXT. Nothing relies on
  SQLite-specific types or rowid.
* Only ``?`` placeholders are used; a Postgres adapter would swap them for ``%s``.
* Migrations are numbered ``.sql`` files applied in order and recorded in ``schema_migrations``.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aimz.db.migrations import apply_migrations

Row = sqlite3.Row


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    # -- lifecycle -------------------------------------------------------------------
    def migrate(self) -> list[str]:
        with self._lock:
            return apply_migrations(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- primitives ------------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, [tuple(r) for r in rows])
            self._conn.commit()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.one(sql, params)
        if row is None:
            return default
        val = row[0]
        return default if val is None else val

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # -- helpers ---------------------------------------------------------------------
    def insert(self, table: str, values: dict[str, Any]) -> None:
        cols = ", ".join(values.keys())
        marks = ", ".join("?" for _ in values)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))

    def upsert(self, table: str, values: dict[str, Any], key: str = "id") -> None:
        cols = ", ".join(values.keys())
        marks = ", ".join("?" for _ in values)
        sets = ", ".join(f"{c}=excluded.{c}" for c in values if c != key)
        self.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT({key}) DO UPDATE SET {sets}",
            list(values.values()),
        )

    def update(self, table: str, id_value: str, values: dict[str, Any], key: str = "id") -> None:
        if not values:
            return
        sets = ", ".join(f"{c}=?" for c in values)
        self.execute(f"UPDATE {table} SET {sets} WHERE {key}=?", [*values.values(), id_value])

    def get(self, table: str, id_value: str, key: str = "id") -> sqlite3.Row | None:
        return self.one(f"SELECT * FROM {table} WHERE {key}=?", [id_value])

    def count(self, table: str, where: str = "1=1", params: Sequence[Any] = ()) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}", params, 0))

    # -- key/value system state -------------------------------------------------------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM system_state WHERE key=?", [key])
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        from aimz.util import now_iso

        self.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            [key, value, now_iso()],
        )


def connect(path: Path | str, migrate: bool = True) -> Database:
    db = Database(path)
    if migrate:
        db.migrate()
    return db


def rows_to_dicts(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
