"""SQLite persistence with file-based migrations (Postgres-portable SQL subset)."""

from aimz.db.connection import Database, connect

__all__ = ["Database", "connect"]
