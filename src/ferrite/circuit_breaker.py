"""Circuit breaker for Ferrite service calls.

Per spec §8.1:
State machine:
  CLOSED → (N failures in window) → OPEN → (cooldown) → HALF_OPEN → success → CLOSED
                                                              → failure → OPEN

When OPEN:
  - All MCP/API calls return immediately with fallback response
  - Agent degrades to local memory only (no crash, no hang)
  - Alert fired to monitoring

Parameters (tunable via config):
  failure_threshold: 5 consecutive failures
  cooldown_seconds: 60
  half_open_max_calls: 3
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

from .models import CircuitBreakerState

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


T = TypeVar("T")


class CircuitBreaker:
    """Circuit breaker for protecting against cascading failures.

    When OPEN, all calls return immediately with a fallback response.
    After cooldown, transitions to HALF_OPEN for trial calls.
    Success in HALF_OPEN → CLOSED. Failure → OPEN.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60,
        half_open_max_calls: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state, accounting for cooldown transition."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info("Circuit breaker: OPEN → HALF_OPEN")
        return self._state

    def can_execute(self) -> bool:
        """Check if a call can be executed (not tripped)."""
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self.half_open_max_calls
        return False  # OPEN

    def call(
        self,
        func: Callable[..., T],
        *args,
        fallback: Optional[T] = None,
        **kwargs,
    ) -> Optional[T]:
        """Execute a function through the circuit breaker.

        If circuit is open, returns fallback immediately.
        On success, increments success counter.
        On failure, increments failure counter and may trip.
        """
        if not self.can_execute():
            logger.debug("Circuit breaker OPEN — returning fallback")
            return fallback

        if self.state == CircuitState.HALF_OPEN:
            self._half_open_calls += 1

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            logger.warning("Circuit breaker caught failure: %s", e)
            return fallback

    def _on_success(self) -> None:
        """Record a successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.half_open_max_calls:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                logger.info("Circuit breaker: HALF_OPEN → CLOSED")
        else:
            self._failure_count = 0  # reset on any success in CLOSED

    def _on_failure(self) -> None:
        """Record a failed call."""
        self._last_failure_time = time.time()
        self._success_count = 0  # reset successes

        if self.state == CircuitState.HALF_OPEN:
            # Failure in half-open → back to open
            self._state = CircuitState.OPEN
            logger.warning("Circuit breaker: HALF_OPEN → OPEN (failure)")
        else:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.error(
                    "Circuit breaker: CLOSED → OPEN "
                    "(%d consecutive failures)",
                    self._failure_count,
                )

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = None
        logger.info("Circuit breaker: reset to CLOSED")

    def get_state(self) -> CircuitBreakerState:
        """Get current state for monitoring."""
        return CircuitBreakerState(
            state=self.state.value,
            failure_count=self._failure_count,
            success_count=self._success_count,
            failure_threshold=self.failure_threshold,
            cooldown_seconds=self.cooldown_seconds,
            half_open_calls=self._half_open_calls,
            last_failure_time=self._last_failure_time,
        )


# Singleton instance for the Ferrite service
_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get the singleton circuit breaker instance."""
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker
