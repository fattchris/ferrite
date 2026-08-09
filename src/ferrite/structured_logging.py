"""Structured JSON logging for Ferrite.

Per spec §8: structured logging with timestamp, level, module, message,
and extra fields (fact_id, episode_id, entity_name, duration_ms, error).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator


class FerriteJSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        # Add Ferrite-specific fields if present
        for field in (
            "fact_id",
            "episode_id",
            "entity_name",
            "duration_ms",
            "error",
        ):
            val = getattr(record, field, None)
            if val is not None:
                entry[field] = val

        # Capture exception info if present
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str)


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """Configure structured JSON logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional file path. If None, logs to stderr.
    """
    formatter = FerriteJSONFormatter()

    handlers: list[logging.Handler] = []
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    handlers.append(console)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)


@contextmanager
def log_duration(operation_name: str, logger: logging.Logger | None = None) -> Iterator[None]:
    """Context manager that logs operation duration.

    Usage:
        with log_duration("extraction", logger):
            do_extraction()
    """
    log = logger or logging.getLogger(__name__)
    start = time.monotonic()
    try:
        yield
    except Exception as e:
        duration = (time.monotonic() - start) * 1000
        log.error(
            f"{operation_name} failed after {duration:.0f}ms",
            extra={"duration_ms": duration, "error": str(e)},
        )
        raise
    else:
        duration = (time.monotonic() - start) * 1000
        log.info(
            f"{operation_name} completed in {duration:.0f}ms",
            extra={"duration_ms": duration},
        )
