"""Structured logging: JSON lines to ``data/logs/aimz.jsonl`` plus a readable console stream."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("run_id", "agent", "operation", "content_id", "provider", "duration_ms", "cost_usd"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = []
        for key in ("run_id", "agent", "provider"):
            val = getattr(record, key, None)
            if val:
                ctx.append(f"{key}={val}")
        ctx_s = f" [{' '.join(ctx)}]" if ctx else ""
        base = f"{datetime.now().strftime('%H:%M:%S')} {record.levelname:<7} {record.name}{ctx_s}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(data_dir: Path, level: str = "INFO", quiet: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fh = logging.FileHandler(log_dir / "aimz.jsonl", encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)

    if not quiet:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(ConsoleFormatter())
        root.addHandler(ch)

    for noisy in ("httpx", "httpcore", "urllib3", "googleapiclient", "PIL", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
