"""Single-instance lock for the autonomous cycle.

An operating-system file lock, so it is released the moment the process dies (shutdown, crash, the
task's time limit), and a stale lock file can never block the next cycle.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from aimz.core.errors import AimzError


class CycleAlreadyRunning(AimzError):
    """Another cycle holds the lock; this one must not run alongside it."""


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")  # noqa: SIM115 - held open for the life of the lock
    try:
        fh.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise CycleAlreadyRunning(f"another cycle is running (lock held on {path})") from exc
        try:
            yield
        finally:
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
