"""Bounded retry for *idempotent* operations only (feed fetches, model calls, asset lookups).

Uploads and other side-effecting writes must NOT use this helper; they are handled by the
publication state machine, which records a failure and waits for a human or a fresh cycle.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

log = logging.getLogger("aimz.retry")


def retry[T](
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    label: str = "operation",
) -> T:
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:  # noqa: PERF203
            last = exc
            if i == attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (i - 1))) * (0.7 + random.random() * 0.6)
            log.warning("%s failed (attempt %d/%d): %s; retrying in %.1fs", label, i, attempts, exc, delay)
            time.sleep(delay)
    assert last is not None
    raise last
