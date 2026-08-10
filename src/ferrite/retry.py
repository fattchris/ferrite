"""Simple retry decorator with exponential backoff (Issue 18).

Usage:
    from .retry import retry

    @retry(max_attempts=3, backoff_base=0.5)
    def my_neo4j_write(driver, ...):
        ...
"""

import functools
import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default retryable exceptions for Neo4j and HTTP calls.
# We retry on transient failures (timeouts, connection errors) but not on
# data-level errors (ValueError, KeyError, etc.).
# Includes neo4j.exceptions.ServiceUnavailable for stale connection recovery.
try:
    from neo4j.exceptions import ServiceUnavailable, TransientError, DatabaseError
    _RETRYABLE_EXCEPTIONS = (
        ConnectionError,
        TimeoutError,
        OSError,
        ServiceUnavailable,
        TransientError,
        DatabaseError,
    )
except ImportError:
    # Fallback if neo4j not installed (e.g., test env)
    _RETRYABLE_EXCEPTIONS = (
        ConnectionError,
        TimeoutError,
        OSError,
    )


def retry(
    max_attempts: int = 3,
    backoff_base: float = 0.5,
    retryable_exceptions: tuple = _RETRYABLE_EXCEPTIONS,
) -> Callable:
    """Retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including the first).
        backoff_base: Base for exponential backoff: delay = backoff_base * 2^(attempt-1).
        retryable_exceptions: Tuple of exception types that should trigger a retry.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    exc = e
                    if attempt < max_attempts:
                        delay = backoff_base * (2 ** (attempt - 1))
                        logger.warning(
                            "Attempt %d/%d for %s failed: %s — retrying in %.1fs",
                            attempt, max_attempts, func.__name__, e, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            "Attempt %d/%d for %s failed: %s — giving up",
                            attempt, max_attempts, func.__name__, e,
                        )
            raise exc  # type: ignore[misc]

        return wrapper

    return decorator
